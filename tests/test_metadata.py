"""Metadata: mock writer/verifier ile sema + kayit (ag yok).

Kurallar (duzeltme sonrasi):
- LLM cumle taslagi uretir; kod sirayla ekleyip butceyi asmayan en uzun oneki
  alir. Govde uzunluguna guvenilmez.
- Her govde cumlesi senaryo iddialarindan/kaynaktan desteklenmeli:
  deterministik kapsama (%70) + tek LLM dogrulama (verify_script sozlesmesi).
  Desteksiz cumle atilir; bosalan platform yeniden uretim.
- Kaynak linki + beyan KOD ekler; LLM yazmaz.
- Baslik ana varlik + ana eylem tasir (ret); fiilsiz yigin yalnizca uyari.
- Dayanaklar `sentence_support` alaninda saklanir, onay + audit gosterir.
"""

import json as _json

from pydantic import ValidationError

import devbrief.db as db
from devbrief.script import metadata as _mm
from devbrief.script.metadata import Metadata, MetadataDraft

SRC_URL = "https://ornek.test/vite-8-3-4"


def _draft(**over):
    """LLM taslagi: X/LinkedIn cumle listeli, linksiz/beyansiz, destekli."""
    d = {"title": "Vite 8.3.4 yayinda",
         "description": "Vite 8.3.4 yayinlandi. Yama surumu yayinlandi.",
         "hashtags": ["vite"],
         "x_sentences": ["Vite 8.3.4 yayinda.", "Yama surumu yayinlandi.",
                         "Indirme boyutu 31 MB."],
         "linkedin_sentences": ["Vite 8.3.4 surumu yayinlandi.",
                                "Yama surumu yayinlandi.",
                                "Indirme boyutu 31 MB."]}
    d.update(over)
    return MetadataDraft(**d)


def _meta(**over):
    from devbrief.script.metadata import note_for

    long_n, short_n = note_for("description"), note_for("x")
    d = {"title": "Vite 8.3.4 yayinda",
         "description": f"Detaylar.\n\nKaynak: {SRC_URL}\n{long_n}",
         "hashtags": ["vite"],
         "x_post": f"Vite 8.3.4 yayinda {SRC_URL}\n{short_n}",
         "linkedin_post": f"Vite 8.3.4 surumu.\n\nKaynak: {SRC_URL}\n{long_n}"}
    d.update(over)
    return Metadata(**d)


def _body(**over):
    """Derlenmis govde (link/beyan henuz eklenmemis)."""
    d = {"title": "Vite 8.3.4 yayinda",
         "description": "Detaylar burada.",
         "hashtags": ["vite"], "x_post": "Vite 8.3.4 yayinda",
         "linkedin_post": "Vite 8.3.4 surumu burada."}
    d.update(over)
    return Metadata(**d)


def _script(url: str = SRC_URL):
    return {"hook": "Vite kanca",
            "source_url": url,
            "scenes": [{"voice": "Vite 8.3.4 yama surumu yayinlandi."}],
            "claims": [{"text": "8.3.4 yayinda."}],
            "impact": "Etki.", "action": "Aksiyon."}


_SRC = "Vite 8.3.4 yama surumu yayinlandi. Indirme boyutu 31 MB."


async def _verify_entailed(source_text, claims, **kw):
    """Sahte dogrulayici: tum cumleler entailed (ag yok)."""
    from devbrief.script.schemas import IndexVerdict, VerifyOut

    return VerifyOut(
        verdicts=[IndexVerdict(i=i, verdict="entailed", note="ok")
                  for i in range(len(claims))],
        overall="pass")


def _mock_verify(monkeypatch, fake=None):
    import devbrief.script.verifier as _ver

    monkeypatch.setattr(_ver, "verify_llm", fake or _verify_entailed)


def test_check_accepts_sourced_numbers():
    assert _mm.check_metadata(_meta(), _script(), _SRC) == []


def test_check_rejects_unknown_version():
    bad = _meta(title="Vite 9.9 yayinda")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("9.9" in e for e in errs)


def test_hashtags_exempt_from_numbers():
    m = _meta(hashtags=["Python3", "v9.9"])
    assert _mm.check_metadata(m, _script(), _SRC) == []


def test_check_rejects_missing_link_final():
    bad = _meta(x_post="Vite 8.3.4 yayinda", linkedin_post="Vite 8.3.4 surumu",
                description="Detaylar burada.")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("kaynak linki yok" in e for e in errs)


def test_check_rejects_missing_note_final():
    from devbrief.script.metadata import note_for

    long_n = note_for("description")
    bad = _meta(description="Detaylar burada.\n\nKaynak: " + SRC_URL)
    assert long_n not in bad.description
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("beyan cumlesi yok" in e for e in errs)


def test_title_has_no_link_requirement():
    # Baslikta link sarti yok; link iceren baslik ise ret.
    m = _meta(title="Vite 8.3.4 yayinda")
    assert not [e for e in _mm.check_metadata(m, _script(), _SRC) if e.startswith("title:")]
    bad = _meta(title=f"Vite 8.3.4 yayinda {SRC_URL}")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("link icermemeli" in e for e in errs)


def test_title_missing_entity_rejected():
    bad = _meta(title="Yama surumu yayinlandi")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("ana varlik" in e for e in errs)


def test_title_missing_action_rejected():
    bad = _meta(title="Vite 8.3.4 burada")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("ana eylem" in e for e in errs)
    assert not [e for e in errs if "ana varlik" in e]


def test_title_basis_values():
    assert _mm.title_basis(_script()) == ("vit", "yay")
    assert _mm.title_basis({"hook": "", "claims": [], "scenes": []}) == (None, None)


def test_title_grammar_suspect_is_warning_only():
    m = _meta(title="Vite yama surum yayin")
    assert _mm.check_title_warnings(m, _script(), _SRC) == ["title_grammar_suspect"]
    assert _mm.check_title_warnings(_meta(), _script(), _SRC) == []
    # Uyari ret degil: kural denetimi temiz kalir.
    assert not [e for e in _mm.check_metadata(m, _script(), _SRC) if "grammar" in e]


def test_hype_warns_not_rejects():
    hyped = _meta(title="Inanilmaz Vite 8.3.4 yayinda")
    warns = _mm.check_hype(hyped, _script(), _SRC)
    assert warns == ["hype_title:inanilmaz"] or any(w.startswith("hype_title:") for w in warns)
    assert _mm.check_metadata(hyped, _script(), _SRC) == []


def test_ai_note_excluded():
    from devbrief.publish.platforms import AI_NOTE

    m = _meta(description=f"Vite 8.3.4 detay.\n\nKaynak: {SRC_URL}\n{AI_NOTE}")
    assert _mm.check_metadata(m, _script(), _SRC) == []


def test_attach_adds_link_and_note_by_code():
    from devbrief.script.metadata import note_for

    final = _mm.attach_source(_body(), SRC_URL)
    assert SRC_URL in final.description
    assert SRC_URL in final.x_post
    assert SRC_URL in final.linkedin_post
    assert note_for("description") in final.description
    assert note_for("linkedin") in final.linkedin_post
    assert note_for("x") in final.x_post  # X kisa beyan
    assert "http" not in final.title
    _mm.assert_final_lengths(final)
    assert _mm.check_metadata(final, _script(), _SRC) == []


def test_fabricated_url_rejected():
    bad_body = _body(x_post="Vite 8.3.4 yayinda https://uydurma.test/x")
    final = _mm.attach_source(bad_body, SRC_URL)
    errs = _mm.check_metadata(final, _script(), _SRC)
    assert any("uydurma URL" in e and "uydurma.test" in e for e in errs)


def test_sentence_start_not_proper_noun():
    # "Modeli" cumle basinda: ret yok.
    from devbrief.script.metadata import note_for

    long_n, short_n = note_for("description"), note_for("x")
    src = "Model yeni surumu yayinlandi. Etkisi buyuk."
    script = {"hook": "k", "source_url": SRC_URL, "scenes": [],
              "claims": [], "impact": "i", "action": "a"}
    m = _meta(title="Kisa baslik",
              description=f"Modeli burada anlatiyoruz.\n\nKaynak: {SRC_URL}\n{long_n}",
              x_post=f"Modeli burada anlatiyoruz {SRC_URL}\n{short_n}",
              linkedin_post=f"Modeli burada anlatiyoruz.\n\nKaynak: {SRC_URL}\n{long_n}")
    errs = [e for e in _mm.check_metadata(m, script, src) if "ozel ad" in e]
    assert errs == []


def test_claude_suffix_matches_source():
    from devbrief.script.metadata import note_for

    long_n, short_n = note_for("description"), note_for("x")
    src = "Claude modeli yeni ozellikler sundu."
    script = {"hook": "k", "source_url": SRC_URL, "scenes": [],
              "claims": [], "impact": "i", "action": "a"}
    m = _meta(title="Kisa baslik",
              description=f"Burada Claude'un ozellikleri var.\n\nKaynak: {SRC_URL}\n{long_n}",
              x_post=f"Burada Claude'un ozellikleri var {SRC_URL}\n{short_n}",
              linkedin_post=f"Burada Claude'un ozellikleri var.\n\nKaynak: {SRC_URL}\n{long_n}")
    errs = [e for e in _mm.check_metadata(m, script, src) if "ozel ad" in e]
    assert errs == [], errs


def test_unknown_proper_noun_rejected_with_sentence():
    from devbrief.script.metadata import note_for

    long_n, short_n = note_for("description"), note_for("x")
    src = "Vite 8.3.4 yama surumu yayinlandi."
    script = _script()
    m = _meta(description=f"Burada Zyxel cihazlari anlatildi.\n\nKaynak: {SRC_URL}\n{long_n}",
              x_post=f"Burada Zyxel cihazlari anlatildi {SRC_URL}\n{short_n}",
              linkedin_post=f"Vite surumu.\n\nKaynak: {SRC_URL}\n{long_n}")
    errs = _mm.check_metadata(m, script, src)
    prop = [e for e in errs if "Zyxel" in e]
    assert prop, errs
    assert any("cumle:" in e for e in prop)


def test_sentence_coverage_levels():
    union = _mm._support_union(_script(), _SRC)
    roots = _mm._ref_roots(union)
    assert _mm.sentence_coverage("Vite 8.3.4 yayinda.", roots, union) >= 0.70
    assert _mm.sentence_coverage("Yama surumu yayinlandi.", roots, union) >= 0.70
    assert _mm.sentence_coverage("Burada Zyxel cihazlari anlatildi.", roots, union) < 0.70
    assert _mm.sentence_coverage("", roots, union) == 0.0


def test_change_marker_without_support_rejected():
    units = [{"field": "x_post", "sentence": "Vite yama yeni surum yayinlandi."}]
    kept, dropped, _basis = _mm.filter_supported(units, _script(), _SRC)
    assert kept == []
    assert any("degisim" in d and "yeni" in d for d in dropped)


def test_change_marker_supported_passes():
    script2 = {"hook": "k", "source_url": SRC_URL, "scenes": [],
               "claims": [{"text": "Yeni yama surumu."}], "impact": "", "action": ""}
    src2 = "Yeni yama surumu yayinlandi."
    units = [{"field": "x_post", "sentence": "Yeni yama surumu."}]
    kept, dropped, basis = _mm.filter_supported(units, script2, src2)
    assert len(kept) == 1 and dropped == []
    assert basis[("x_post", "Yeni yama surumu.")]["basis"] == "claim#0"


def test_change_marker_artik_rejected():
    units = [{"field": "x_post", "sentence": "Vite yama artık surum yayinlandi."}]
    kept, dropped, _basis = _mm.filter_supported(units, _script(), _SRC)
    assert kept == []
    assert any("degisim" in d for d in dropped)


def test_x_weighted_limit_with_link_share():
    # X: ham + agirlikli <= 270. Sema 270 oldugu icin asim dogrudan
    # ValidationError verir (kapi), sinirda gecer.
    import pytest

    budgets = _mm.body_budgets(SRC_URL)
    # Sinirda govde + kod eki (link + kisa beyan) tam 270 olur.
    body = "b" * budgets["x_post"]
    m2 = _meta(x_post=f"{body} {SRC_URL}\n{_mm.note_for('x')}")
    assert len(m2.x_post) <= 270
    assert not [e for e in _mm.check_metadata(m2, _script(), _SRC)
                if "agirlikli" in e or "uzunluk" in e]
    with pytest.raises(ValidationError):
        _meta(x_post=f"{'a' * 260} {SRC_URL}")
    # Agirlikli sayim: uzun URL X_URL_LEN sayilir (varsayilan 23).
    assert _mm.x_weighted_len(f"{'c' * 240} {SRC_URL}") == 240 + 1 + _mm.x_url_len()


def test_x_body_budget_long_url_short_note():
    """Uzun URL + kisa beyanla X govdesi >=150 karakter butce alir."""
    from devbrief.script.metadata import note_for

    url = "https://ornek.test/" + "y" * 71  # 90 karakter
    assert len(url) == 90
    budgets = _mm.body_budgets(url)
    assert budgets["x_post"] >= 150
    rep = _mm.budget_report(url)["platforms"]["x_post"]
    assert rep["limit"] == 270
    assert rep["body_budget"] + rep["link_share"] + rep["note_share"] == 270
    assert rep["note_share"] == 1 + len(note_for("x"))


def test_assemble_sentences_prefix_no_midcut():
    sents = ["Kisa cumle burada.", "Ikinci cumle de burada.", "Ucuncu cumle sonda."]
    full = " ".join(sents)
    first_two = " ".join(sents[:2])
    out = _mm.assemble_sentences(sents, len(first_two))
    assert out == first_two  # en uzun onek, ortada kesme yok
    assert _mm.assemble_sentences(sents, 5) == ""  # hicbiri sigmaz
    assert _mm.assemble_sentences(sents, len(full) + 10) == full


def test_normalize_sentences_flattens():
    out = _mm.normalize_sentences(["Ilk cumle. Ikinci cumle.", "  ", "Ucuncu cumle."])
    assert out == ["Ilk cumle.", "Ikinci cumle.", "Ucuncu cumle."]


async def test_write_metadata_returns_draft_with_budgets(monkeypatch):
    import devbrief.script.metadata as mm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        assert prompt_name == "write_metadata_v1"
        assert "script" in variables
        assert "previous_errors" in variables
        for key in ("title_budget", "description_body_budget", "x_body_budget",
                    "linkedin_body_budget", "hashtag_max"):
            assert key in variables, key
        budgets = _mm.body_budgets(SRC_URL)
        assert int(variables["linkedin_body_budget"]) == budgets["linkedin_post"]
        assert int(variables["x_body_budget"]) == budgets["x_post"]
        assert schema is mm.MetadataDraft
        from devbrief.llm.client import load_prompt
        prompt = load_prompt("write_metadata_v1").lower()
        assert "metne url yazma" in prompt
        assert "aŞmamali".lower() in prompt
        assert "previous_errors" in load_prompt("write_metadata_v1")
        return _draft(title="Kisa baslik")

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    conn = db.connect(":memory:")
    db.migrate(conn)
    draft = await mm.write_metadata(_script(), conn=conn)
    assert isinstance(draft, mm.MetadataDraft)
    assert draft.title == "Kisa baslik"
    assert "http" not in " ".join(draft.x_sentences)


async def test_write_metadata_body_without_link_accepted(monkeypatch):
    """Taslak linksiz/beyansiz kabul edilir (kod ekler)."""
    import devbrief.script.metadata as mm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        return _draft()

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    draft = await mm.write_metadata(_script())
    assert "http" not in " ".join(draft.x_sentences)
    meta, errs, warns = mm.assemble_final(draft, _script(), _SRC)
    assert errs == [], errs
    assert _mm.check_metadata(meta, _script(), _SRC) == []


async def test_produce_retries_then_passes(monkeypatch):
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)
    calls = []

    async def fake_write(script, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _draft(title="Vite 9.9 yayinda")
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, budget, support = await mm.produce(_script(), _SRC)
    assert meta.title.startswith("Vite 8.3.4") and len(calls) == 2
    assert SRC_URL in meta.description
    assert budget["platforms"]["x_post"]["limit"] == 270
    assert support and all(s["verdict"] == "entailed" for s in support)
    assert any(s["basis"].startswith("claim#") for s in support)


async def test_produce_fails_and_caps(monkeypatch):
    import devbrief.script.metadata as mm
    from devbrief.llm.client import CallCap

    async def always_bad(script, **kw):
        return _draft(title="Vite 9.9 yayinda")

    monkeypatch.setattr(mm, "write_metadata", always_bad)
    try:
        await mm.produce(_script(), _SRC)
    except mm.MetadataFailed as e:
        assert "9.9" in str(e)
        assert "tur1" in str(e) and "tur" in str(e)
        assert "?" not in str(e)  # durum '?' kalmaz
        assert e.last_errors and any("9.9" in x for x in e.last_errors)
    else:
        raise AssertionError("MetadataFailed bekleniyordu")
    # cap=0: ilk sarjda patlar, hic uretim olmaz.
    try:
        await mm.produce(_script(), _SRC, cap=CallCap(0))
    except Exception as e:
        assert "call_cap" in str(e).lower() or "CallCap" in type(e).__name__
    else:
        raise AssertionError("cap bekleniyordu")


async def test_produce_fail_detail_has_per_call_status(monkeypatch):
    import devbrief.script.metadata as mm

    async def always_bad(script, **kw):
        return _draft(title="Vite 9.9 yayinda")

    monkeypatch.setattr(mm, "write_metadata", always_bad)
    conn = db.connect(":memory:")
    db.migrate(conn)
    # Sahte llm_calls satirlari: status dolu olmali, '?' yok.
    with conn:
        conn.execute("INSERT INTO llm_calls (ts, task, model, ok, ms, tokens, status)"
                     " VALUES (?, 'write_metadata', 'm:free', 0, 5, 0, 'schema_error')",
                     (db.now_iso(),))
    try:
        await mm.produce(_script(), _SRC, conn=conn)
    except mm.MetadataFailed as e:
        assert "tur" in str(e)
        assert "?" not in str(e)
        assert "cache(tekrar-kullanim)" not in str(e)  # yalnizca basarida
    else:
        raise AssertionError("MetadataFailed bekleniyordu")


async def test_cache_hit_label_only_on_success(monkeypatch):
    """Basarili ciktida cache etiketi gorunur, ret detayinda gorunmez."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        info = kw.get("cache_info")
        if info is not None:
            info["hit"] = True
        return _draft()

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    meta, warns, _budget, _support = await mm.produce(_script(), _SRC)
    assert "cache(tekrar-kullanim)" in warns
    assert SRC_URL in meta.x_post


def test_save_and_fail_helpers():
    import devbrief.script.metadata as mm

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
        " VALUES (NULL, 'd', 's', 'verified', ?, ?)", (db.now_iso(), db.now_iso()))
    mm.save_metadata(conn, 1, _meta(), ["hype_title:sok"],
                     _mm.budget_report(SRC_URL),
                     [{"field": "x_post", "sentence": "s", "basis": "claim#0",
                       "coverage": 1.0, "verdict": "entailed"}])
    row = conn.execute(
        "SELECT title, metadata_json, metadata_warnings FROM episodes WHERE id = 1").fetchone()
    assert row["title"].startswith("Vite 8.3.4")
    assert "hype_title:sok" in row["metadata_warnings"]
    saved = _json.loads(row["metadata_json"])
    assert saved["budget"]["platforms"]["x_post"]["limit"] == 270
    assert saved["sentence_support"][0]["basis"] == "claim#0"
    assert saved["title"].startswith("Vite 8.3.4")
    mm.fail_metadata(conn, 1, "kotu")
    row = conn.execute("SELECT status, reject_reason FROM episodes WHERE id = 1").fetchone()
    assert (row["status"], row["reject_reason"]) == ("metadata_failed", "kotu")


def _plain_script():
    return {"hook": "kanca sesi", "source_url": SRC_URL, "scenes": [],
            "claims": [], "impact": "etki burada", "action": "aksiyon burada"}


_PLAIN_SRC = "kanca sesi burada. etki burada. aksiyon burada."


_SENT_POOL = ("kanca sesi burada.", "etki burada.", "aksiyon burada.",
              "ses kanca burada.", "kanca etki burada.", "ses etki burada.")


_VITE_POOL = ("Vite 8.3.4 yayinda.", "Yama surumu yayinlandi.",
              "Indirme boyutu 31 MB.")


def _plain_sentences(n, pool=_SENT_POOL):
    return [pool[i % len(pool)] for i in range(n)]


async def test_long_linkedin_sentences_assembled_to_limit(monkeypatch):
    """Uzun cumle govdesi + link/beyan oteki <=600 olur + uyari (ortada kesme yok)."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)
    budgets = mm.body_budgets(SRC_URL)
    long_sent = ("Vite 8.3.4 yama surumu yayinlandi indirme boyutu 31 MB"
                 " kanca etki aksiyon surum yama vite.")
    sents = [long_sent] * 6
    assert len(" ".join(sents)) > budgets["linkedin_post"]  # butce asimi var

    async def fake_write(script, **kw):
        return _draft(title="Vite 8.3.4 yayinda",
                      description="Vite 8.3.4 yayinlandi. Yama surumu yayinlandi.",
                      hashtags=["a"],
                      x_sentences=list(_VITE_POOL),
                      linkedin_sentences=sents)

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, _support = await mm.produce(_script(), _SRC)
    assert len(meta.linkedin_post) <= 600
    assert SRC_URL in meta.linkedin_post
    assert "truncated:linkedin_post" in warns
    # Kelime/cumle ortasi kesme yok, "…" yok.
    assert "…" not in meta.linkedin_post
    mm.assert_final_lengths(meta)


async def test_no_sentence_fits_triggers_retry(monkeypatch):
    """Hic cumle sigmayinca butce + ilk-cumle kuraliyla yeniden uretim (2 cagri)."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)
    budgets = mm.body_budgets(SRC_URL)
    big = "Vite " + "yama " * 100 + "yayinlandi."
    assert len(big) > budgets["linkedin_post"]
    calls, seen_errors = [], []

    async def fake_write(script, previous_errors="yok", **kw):
        calls.append(1)
        seen_errors.append(previous_errors)
        if len(calls) == 1:
            return _draft(title="Vite 8.3.4 yayinda",
                          description="Vite 8.3.4 yayinlandi.",
                          hashtags=["a"], x_sentences=list(_VITE_POOL),
                          linkedin_sentences=[big, "Yama surumu yayinlandi.",
                                              "Indirme boyutu 31 MB."])
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, _support = await mm.produce(_script(), _SRC)
    assert len(calls) == 2
    assert len(meta.linkedin_post) <= 600
    # Ikinci tur butce + ilk-cumle kuralini aldi.
    assert "ilk cumle en fazla" in seen_errors[1]


async def test_unsupported_change_sentence_dropped(monkeypatch):
    """Kaynakta olmayan 'odul modelini degistirdi' cumlesi atilir, kalan gecer."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)

    async def fake_write(script, **kw):
        return _draft(linkedin_sentences=["Vite 8.3.4 surumu yayinlandi.",
                                          "Vite yama yeni surum yayinlandi.",
                                          "Yama surumu yayinlandi."])

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, support = await mm.produce(_script(), _SRC)
    assert "yeni" not in meta.linkedin_post
    assert "Vite 8.3.4 surumu yayinlandi." in meta.linkedin_post
    assert [s for s in support if s["field"] == "linkedin_post"]


async def test_llm_not_stated_drops_sentence(monkeypatch):
    """Mock LLM not_stated dondururse o cumle elenir, kalan gecer (tek istek)."""
    import devbrief.script.metadata as mm
    from devbrief.script.schemas import IndexVerdict, VerifyOut

    calls = []

    async def fake_verify(source_text, claims, **kw):
        calls.append(1)
        verdicts = [IndexVerdict(i=i,
                                verdict=("not_stated" if i == 4 else "entailed"),
                                note=("yok" if i == 4 else "ok"))
                    for i in range(len(claims))]
        return VerifyOut(verdicts=verdicts, overall="fail")

    import devbrief.script.verifier as _ver

    monkeypatch.setattr(_ver, "verify_llm", fake_verify)

    async def fake_write(script, **kw):
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, support = await mm.produce(_script(), _SRC)
    assert calls == [1]  # tek istek, tum cumleler birlikte
    assert "Yama surumu yayinlandi." not in meta.x_post
    assert "Vite 8.3.4 yayinda." in meta.x_post
    assert "Indirme boyutu 31 MB." in meta.x_post


async def test_all_dropped_retries_with_support_errors(monkeypatch):
    """Hic destekli cumle kalmazsa yeniden uretim; hata listesi somut."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)
    calls, seen_errors = [], []
    bad3 = ["Burada Bilinmeyenmarka gorundu.",
            "Burada Uydurukmarka gosterildi.",
            "Burada Sahtecihaz vitrinde."]

    async def fake_write(script, previous_errors="yok", **kw):
        calls.append(1)
        seen_errors.append(previous_errors)
        if len(calls) == 1:
            return _draft(x_sentences=bad3, linkedin_sentences=bad3)
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, support = await mm.produce(_script(), _SRC)
    assert len(calls) == 2
    assert meta.x_post.startswith("Vite 8.3.4")
    assert "destek" in seen_errors[1]


async def test_verify_call_uses_metadata_cap(monkeypatch):
    """Cumle dogrulama tek istekte, uretici cap'e dahil (nesne ayni)."""
    import devbrief.script.metadata as mm
    from devbrief.llm.client import CallCap

    seen = {}

    async def fake_verify(source_text, claims, **kw):
        seen["cap"] = kw.get("cap")
        seen["n"] = len(claims)
        return await _verify_entailed(source_text, claims)

    import devbrief.script.verifier as _ver

    monkeypatch.setattr(_ver, "verify_llm", fake_verify)

    async def fake_write(script, **kw):
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    cap = CallCap(8)
    meta, warns, _budget, support = await mm.produce(_script(), _SRC, cap=cap)
    assert seen["cap"] is cap
    assert seen["n"] == 1 + 2 + 3 + 3  # baslik + aciklama + x + linkedin
    assert len(support) == len(
        [s for s in support if s["verdict"] == "entailed"])


async def test_validation_error_becomes_retry_then_failed(monkeypatch):
    """Sema/uzunluk hatasi tura dahildir; 2 tur sonu MetadataFailed, '?' yok."""
    import devbrief.script.metadata as mm

    def _boom():
        return MetadataDraft(title="t" * 100, description="d", hashtags=[],
                             x_sentences=["a.", "b.", "c."],
                             linkedin_sentences=["a.", "b.", "c."])

    async def fake_write(script, **kw):
        return _boom()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    try:
        await mm.produce(_plain_script(), _PLAIN_SRC)
    except mm.MetadataFailed as e:
        assert "tur" in str(e) and "?" not in str(e)
        assert e.last_errors
    else:
        raise AssertionError("MetadataFailed bekleniyordu")


async def test_produce_includes_title_grammar_warning(monkeypatch):
    """Fiilsiz baslik ret degil `title_grammar_suspect` uyarisi verir."""
    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)

    async def fake_write(script, **kw):
        return _draft(title="Vite yama surum yayin")

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, _budget, _support = await mm.produce(_script(), _SRC)
    assert meta.title == "Vite yama surum yayin"
    assert "title_grammar_suspect" in warns


def test_cli_failure_prints_reject_list(monkeypatch, tmp_path):
    """Basarisizlikta CLI son turun somut ret listesini basar (cik 1)."""
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.setenv("METADATA_MODE", "generative")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    db.record_eval_gate(conn, "eval-metadata", True, 1.0, 0.0)
    script = {"hook": "kanca sesi", "source_url": SRC_URL, "scenes": [],
              "claims": [], "impact": "etki burada", "action": "aksiyon burada"}
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(script), db.now_iso(), db.now_iso()))
    conn.close()

    import devbrief.script.metadata as mm

    bad3 = ["Burada Bilinmeyenmarka cihazi anlatildi.",
            "Burada Uydurukmarka cihazi gosterildi.",
            "Burada Sahtecihaz vitrinde duruyor."]

    async def always_bad(script, **kw):
        return _draft(title="kanca sesi burada", description="kanca sesi burada.",
                      hashtags=["a"], x_sentences=_plain_sentences(3),
                      linkedin_sentences=bad3)

    monkeypatch.setattr(mm, "write_metadata", always_bad)
    r = CliRunner().invoke(app, ["metadata", "--episode", "1"])
    assert r.exit_code == 1
    out = r.output or ""
    assert "metadata_failed" in out
    assert "ret:" in out  # tek basina metadata_failed degil
    assert "Bilinmeyenmarka" in out  # somut ret: platform/kural/deger
    assert "Traceback" not in out + (getattr(r, "stderr", "") or "")
    conn = db.connect()
    row = conn.execute("SELECT status, reject_reason FROM episodes WHERE id = 1").fetchone()
    conn.close()
    assert row["status"] == "metadata_failed" and (row["reject_reason"] or "").strip()


def test_cli_validation_error_no_leak_and_failed_written(monkeypatch, tmp_path):
    """CLI: ValidationError sizmaz (cikista Traceback yok), metadata_failed yazilir."""
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.setenv("METADATA_MODE", "generative")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    db.record_eval_gate(conn, "eval-metadata", True, 1.0, 0.0)
    script = {"hook": "kanca sesi", "source_url": SRC_URL, "scenes": [],
              "claims": [], "impact": "etki burada", "action": "aksiyon burada"}
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(script), db.now_iso(), db.now_iso()))
    conn.close()

    import devbrief.script.metadata as mm

    async def boom_complete(task, prompt_name, variables, schema, **kw):
        raise ValidationError.from_exception_data(
            "MetadataDraft",
            [{"type": "missing", "loc": ("title",), "input": {}}])

    monkeypatch.setattr(mm, "complete_json", boom_complete)
    r = CliRunner().invoke(app, ["metadata", "--episode", "1"])
    assert r.exit_code == 1
    assert "Traceback" not in (r.output or "") + (getattr(r, "stderr", "") or "")
    assert "metadata_failed" in (r.output or "")
    assert "ret:" in (r.output or "")
    conn = db.connect()
    row = conn.execute("SELECT status, reject_reason FROM episodes WHERE id = 1").fetchone()
    conn.close()
    assert row["status"] == "metadata_failed" and (row["reject_reason"] or "").strip()


def test_x_final_with_link_and_note_within_270():
    import devbrief.script.metadata as mm

    budgets = mm.body_budgets(SRC_URL)
    assert budgets["x_post"] < 270  # link+beyan payi ayrilmis
    meta, errs, _warns = mm.assemble_final(_draft(), _script(), _SRC)
    assert errs == [], errs
    assert len(meta.x_post) <= 270
    assert mm.x_weighted_len(meta.x_post) <= 270
    mm.assert_final_lengths(meta)


def test_final_assert_guards_limits():
    import pytest

    import devbrief.script.metadata as mm

    bad = Metadata.model_construct(title="t", description="d", hashtags=[],
                                   x_post="x" * 271, linkedin_post="l")
    with pytest.raises(AssertionError):
        mm.assert_final_lengths(bad)


def test_disclosure_yaml_matches_legacy_and_split():
    """Beyan yaml'dan okunur; uzun bicim eski AI_NOTE ile aynidir."""
    from devbrief.publish.platforms import AI_NOTE

    d = _mm.load_disclosure()
    assert d["long"] == AI_NOTE
    assert _mm.note_for("x") == d["short"]
    assert _mm.note_for("linkedin") == d["long"]
    assert _mm.note_for("description") == d["long"]
    assert len(d["short"]) < len(d["long"])


def test_budget_saved_and_shown_in_audit(monkeypatch, tmp_path):
    """Butce + dayanak metadata_json'da, audit-pack bolumlerinde gorunur."""
    import devbrief.script.metadata as mm
    from devbrief.cli import _build_audit

    _mock_verify(monkeypatch)

    async def fake_write(script, **kw):
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    import asyncio as _aio

    meta, warns, budget, support = _aio.run(mm.produce(_script(), _SRC, conn=conn))
    mm.save_metadata(conn, epid, meta, warns, budget, support)
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (epid,)).fetchone()
    conn.close()
    _slug, lines = _build_audit(dict(row))
    body = "\n".join(lines)
    assert "## Metadata butcesi" in body
    assert "x_post: govde<=" in body
    assert "link payi" in body and "beyan payi" in body
    assert "## Metadata cumle dayanaklari" in body
    assert "claim#" in body


def test_audit_fallback_support_without_stored(monkeypatch, tmp_path):
    """Kayitli dayanak yoksa audit deterministik olarak yeniden kurar."""
    from pathlib import Path as _P

    from devbrief.cli import _build_audit

    monkeypatch.chdir(tmp_path)
    fetch = _P("out") / "d" / "s" / "fetch.json"
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(_json.dumps({"text": _SRC}), encoding="utf-8")
    row = {"id": 7, "day": "d", "slug": "s", "status": "verified",
           "script_json": _json.dumps(_script()), "verify_json": "",
           "metadata_json": _json.dumps(_meta().model_dump(mode="json")),
           "metadata_warnings": "[]",
           "low_source": 0, "injection_suspected": 0, "is_demo": 0,
           "reject_reason": ""}
    _slug, lines = _build_audit(dict(row))
    body = "\n".join(lines)
    assert "## Metadata cumle dayanaklari" in body
    assert "claim#" in body or "kaynak" in body


async def test_approval_episode_shows_sentence_support(tmp_path, monkeypatch):
    """Onay ciktisi (GET episode) her cumlenin dayanagini tasir."""
    import httpx
    from httpx import ASGITransport

    import devbrief.db as db
    from devbrief.api.routes import create_app

    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)

    import devbrief.script.metadata as mm

    _mock_verify(monkeypatch)

    async def fake_write(script, **kw):
        return _draft()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns, budget, support = await mm.produce(_script(), _SRC)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " title, description, created_at, updated_at)"
            " VALUES (NULL, 'd', 's', 'rendered', ?, ?, ?, ?, ?)",
            (_json.dumps(_script()), meta.title, meta.description,
             db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    mm.save_metadata(conn, epid, meta, warns, budget, support)
    conn.close()
    app = create_app(dbpath)
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        det = (await c.get(f"/episodes/{epid}",
                           headers={"Authorization": "Bearer test-token"})).json()
    assert "claim#" in det["metadata_support_text"]
    assert det["metadata"]["sentence_support"]


def test_approval_message_includes_support():
    """n8n onay mesaji cumle dayanaklarini icerir."""
    import json as _json2
    from pathlib import Path as _P

    flow = _json2.loads((_P(__file__).resolve().parents[1] / "n8n"
                         / "02-approve-and-publish.json").read_text(encoding="utf-8"))
    by_name = {n["name"]: n for n in flow["nodes"]}
    msg = _json2.dumps(by_name["Onay Bekle"])
    assert "metadata_support_text" in msg
    assert "metadata.x_post" in msg


def test_migration_purges_stale_cache():
    """009: eski onbellek satirlari temizlenir, task kolonu gelir."""
    from pathlib import Path as _P

    import devbrief.db as db

    conn = db.connect(":memory:")
    # 009 oncesi sema (001'deki haliyle, task kolonu yok).
    conn.execute("CREATE TABLE llm_cache"
                 " (key TEXT PRIMARY KEY, response TEXT, created_at TEXT)")
    with conn:
        conn.execute("INSERT INTO llm_cache (key, response, created_at)"
                     " VALUES ('k1', '{}', ?)", (db.now_iso(),))
    assert conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 1
    sql = (_P(__file__).resolve().parents[1] / "src" / "devbrief"
           / "migrations" / "009_llm_cache_task.sql")
    conn.executescript(sql.read_text(encoding="utf-8"))
    assert conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0
    cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_cache)").fetchall()]
    assert "task" in cols
    conn.close()


async def test_rejected_answer_not_cached_second_turn_calls_client(monkeypatch):
    """Reddedilen yanit saklanmaz; ikinci tur istemciyi yine cagirir."""
    import httpx
    import respx
    from openai import AsyncOpenAI as _OOAI

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.llm import client as llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    get_settings.cache_clear()
    conn = db.connect(":memory:")
    db.migrate(conn)

    def _payload(content: str) -> dict:
        return {"id": "gen-1", "object": "chat.completion", "created": 1,
                "model": "a:free",
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                          "total_tokens": 10,
                          "completion_tokens_details": {"reasoning_tokens": 0}}}

    bad = _json.dumps({"title": "t", "description": "d", "hashtags": [],
                       "x_sentences": ["a.", "b.", "c."],
                       "linkedin_sentences": ["a.", "b.", "c."]})
    good = _json.dumps({"title": "t", "description": "d", "hashtags": [],
                        "x_sentences": ["g.", "h.", "i."],
                        "linkedin_sentences": ["g.", "h.", "i."]})

    def _validator(obj):
        got = obj.x_sentences if isinstance(obj, MetadataDraft) else []
        return ["kotu cumle"] if got == ["a.", "b.", "c."] else []

    http = httpx.AsyncClient()
    client = _OOAI(base_url="https://openrouter.ai/api/v1", api_key="k",
                   timeout=10.0, max_retries=0, http_client=http)
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(
                "https://openrouter.ai/api/v1/chat/completions").mock(
                    side_effect=[httpx.Response(200, json=_payload(bad)),
                                 httpx.Response(200, json=_payload(good))])
            kw = {"rate_limit_per_min": 60000}
            first = await llm.complete_json(
                "write_metadata", "write_metadata_v1",
                {"script": "{}", "title_budget": "70",
                 "description_body_budget": "4000", "x_body_budget": "150",
                 "linkedin_body_budget": "400", "hashtag_max": "5",
                 "previous_errors": "yok"},
                MetadataDraft, tier="fast", temperature=0.0, conn=conn,
                client=client, cache_validator=_validator,
                cache_salt="turn=1|err=aaa", **kw)
            assert first.x_sentences == ["a.", "b.", "c."]
            # Reddedilen saklanmadi.
            assert conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0
            second = await llm.complete_json(
                "write_metadata", "write_metadata_v1",
                {"script": "{}", "title_budget": "70",
                 "description_body_budget": "4000", "x_body_budget": "150",
                 "linkedin_body_budget": "400", "hashtag_max": "5",
                 "previous_errors": "kotu cumle"},
                MetadataDraft, tier="fast", temperature=0.0, conn=conn,
                client=client, cache_validator=_validator,
                cache_salt="turn=2|err=bbb", **kw)
            assert second.x_sentences == ["g.", "h.", "i."]
            assert route.call_count == 2  # ikinci tur gercekten cagirdi
            # Gecen yanit saklandi; ayni anahtar vurus yapar.
            assert conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 1
            info: dict = {}
            third = await llm.complete_json(
                "write_metadata", "write_metadata_v1",
                {"script": "{}", "title_budget": "70",
                 "description_body_budget": "4000", "x_body_budget": "150",
                 "linkedin_body_budget": "400", "hashtag_max": "5",
                 "previous_errors": "kotu cumle"},
                MetadataDraft, tier="fast", temperature=0.0, conn=conn,
                client=client, cache_validator=_validator,
                cache_salt="turn=2|err=bbb", cache_info=info, **kw)
            assert third.x_sentences == ["g.", "h.", "i."]
            assert route.call_count == 2
            assert info.get("hit") is True
    finally:
        await client.close()
        conn.close()
