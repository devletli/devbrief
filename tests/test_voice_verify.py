"""Voice-seviyesi dogrulama + v2/holdout/doctor (mock, ag yok)."""

import json

from devbrief import eval as evmod
from devbrief.script import verifier as ver

SRC_TR = ("HizliKurye 4.0 yayinlandi ve ortalama teslim suresi 25 dakikaya dustu. "
          "Kurye komisyonu yuzde 12 olarak sabitlendi. "
          "Destek ekibi gunun her saati canli yardim sunuyor.")


def _script(voice, claims, hook="Kisa kanca"):
    return {"hook": hook,
            "scenes": [{"id": 1, "voice": voice, "on_screen": "Etiket",
                        "visual": "title", "code": None, "evidence": ["e"]}],
            "impact": "Etkisi genis.", "action": "Notu okuyun.",
            "claims": claims, "source_url": "https://x"}


def test_omission_voice_caught_without_claim():
    sc = _script(
        "HizliKurye 4.0 yayinlandi ve ortalama teslim suresi 25 dakikaya dustu. "
        "Uydu destegi de eklendi ve cevrimdisi kullanim basladi.",
        [{"text": "Teslim suresi dustu.", "evidence": "e", "kind": "fact"}])
    issues, derived = ver.check_voice_grounding(sc, SRC_TR)
    assert any(i.kind == "voice_coverage" for i in issues)
    combined, w_idx, dll = ver.build_llm_claims(sc, SRC_TR)
    assert len(combined) > len(w_idx)  # turetilmisler tek listede
    entailed = json.dumps(
        {"verdicts": [{"i": i, "verdict": "entailed", "note": "ok"}
                      for i in range(len(combined))], "overall": "pass"})
    a = evmod.assess_v2({"m": entailed},
                        {"para_id": 1, "kind": "claim-omission", "para": SRC_TR,
                         "script": sc, "bad": [],
                         "bad_sentences": ["Uydu destegi de eklendi ve "
                                           "cevrimdisi kullanim basladi."]})
    assert a["caught"]
    assert any("voice" in layer for layers in a["combined"].values()
               for layer in layers)


def test_clean_voice_all_evidence():
    sc = _script(
        "HizliKurye 4.0 yayinlandi ve ortalama teslim suresi 25 dakikaya dustu. "
        "Kurye komisyonu yuzde 12 olarak sabitlendi.",
        [{"text": "Teslim suresi dustu.", "evidence": "e", "kind": "fact"}])
    issues, derived = ver.check_voice_grounding(sc, SRC_TR)
    assert issues == []
    voice_derived = [d for d in derived if d["field"] == "scenes[0].voice"]
    assert len(voice_derived) == 2 and all(d["status"] == "ok" for d in voice_derived)


def test_inference_exemption_capped_and_numberless():
    inf = [{"text": "Etkisi genis olacak.", "evidence": "e", "kind": "inference"},
           {"text": "Kullanicilar memnun kalacak.", "evidence": "e", "kind": "inference"}]
    sc = _script(
        "Etkisi genis olacak ve kullanim artiyor. "
        "Kullanicilar memnun kalacak ve uygulama ovuluyor. "
        "Teslim suresi dustu ve sure kisaldi.",
        inf)
    _, derived = ver.check_voice_grounding(sc, SRC_TR)
    substantive = [d for d in derived if d["field"] == "scenes[0].voice"]
    assert [d["status"] for d in substantive] == ["yorum", "yorum", "ok"]
    # Sayili yorum adayi muaf degil: kanit penceresinde sayi yoksa desteksiz.
    sc2 = _script("Etkisi 9 kat genis olacak.", inf)
    issues2, derived2 = ver.check_voice_grounding(sc2, SRC_TR)
    hit = [d for d in derived2 if d["text"] == "Etkisi 9 kat genis olacak."]
    assert hit and hit[0]["status"] == "desteksiz"
    assert any(i.kind == "voice_numbers" for i in issues2)


def test_cross_language_proper_exempt_digits_enforced():
    # Uretim vakasi (EN kaynak + TR voice): cevrilmis ozel adlar
    # ("Almanca"/"German") ret degil; sayilar hâlâ denetlenir.
    src = ("Whistle transcribes English, German and French. "
           "It is one 16.9 MB file and runs on the CPU.")
    sc = _script(
        "Whistle Almanca, Fransizca ve Ingilizceyi yaziya dokuyor. "
        "Tek 16,9 MB dosya CPU uzerinde calisiyor.",
        [{"text": "Almanca destekleniyor.", "evidence": "e", "kind": "fact"}])
    issues, _ = ver.check_voice_grounding(sc, src)
    assert not [i for i in issues if i.kind == "voice_numbers"]
    # Sayi tutmazsa (a) yakalar: 19,9 kaynakta yok.
    sc2 = _script("Tek 19,9 MB dosya CPU uzerinde calisiyor.",
                  [{"text": "x.", "evidence": "e", "kind": "fact"}])
    issues2, _ = ver.check_voice_grounding(sc2, src)
    assert any(i.kind == "voice_numbers" and "199" in i.value for i in issues2)


def test_infra_flake_retries_within_turns(monkeypatch):
    """Altyapi arızasi (AllModelsFailed) bolumu oldurmez, tur icinde dener."""
    import devbrief.script.writer as _w
    from devbrief.llm.client import AllModelsFailed
    from devbrief.script import verifier as ver
    from devbrief.script.schemas import IndexVerdict, VerifyOut

    calls = {"n": 0}

    async def flaky_then_ok(source_text, claims, models=None, **kw):
        if models is not None and calls["n"] == 0:
            calls["n"] += 1
            raise AllModelsFailed("m: ValueError bos yanit")
        n = len([c for c in claims
                 if str(c.get("kind") or "fact") != "inference"])
        return VerifyOut(
            verdicts=[IndexVerdict(i=i, verdict="entailed", note="ok")
                      for i in range(n)], overall="pass"), {"models": models or []}

    async def fake_write(*a, **k):
        from devbrief.script.schemas import Script

        voice5 = " ".join(["MiniDB 2.1 yayinda burada."] * 5)
        scenes = [{"id": i + 1, "voice": voice5,
                   "on_screen": f"E{i}", "visual": "title", "code": None,
                   "evidence": ["e"], "src": [0, 0, 0, 0, 0]} for i in range(3)]
        return Script.model_validate({
            "hook": "Kanca", "source_url": "https://x", "scenes": scenes,
            "impact": "Etki.", "action": "Aksiyon.",
            "claims": [{"text": "2.1 yayinda.", "evidence": "e"}],
            "duration_estimate_s": 40})

    monkeypatch.setattr(ver, "verify_with_models", flaky_then_ok)
    monkeypatch.setattr(_w, "write_script", fake_write)
    import asyncio

    script = {"hook": "Kanca", "source_url": "https://x",
              "scenes": [{"id": i + 1,
                          "voice": " ".join(["MiniDB 2.1 yayinda burada."] * 5),
                          "on_screen": f"E{i}", "visual": "title", "code": None,
                          "evidence": ["e"]} for i in range(3)],
              "impact": "Etki.", "action": "Aksiyon.",
              "claims": [{"text": "2.1 yayinda.", "evidence": "e"}]}

    async def go():
        return await ver.verify_episode(
            script, "MiniDB 2.1 yayinda burada.",
            verify_models=["m1:free", "m2:free"])

    status, final, _, _ = asyncio.run(go())
    assert calls["n"] == 1
    assert status == "verified" and final is not None


def test_doctor_fail_exit_code(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    from typer.testing import CliRunner

    from devbrief.cli import app
    from devbrief.llm import key as _key

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "t")
    monkeypatch.setenv("N8N_CALLBACK_SECRET", "s")
    monkeypatch.setenv("N8N_ENCRYPTION_KEY", "e")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    monkeypatch.setattr("devbrief.cli._tool_ok", lambda *a, **k: True)

    exp = (datetime.now(UTC) + timedelta(days=2.7)).isoformat()
    monkeypatch.setattr(_key, "fetch_key_status", lambda *a, **k: {
        "limit": 2, "limit_remaining": 1.0, "usage_daily": 0, "usage_weekly": 0,
        "expires_at": exp,
        "free_model_daily_requests": {"used": 0, "limit": 1000, "remaining": 1000}})
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output and "doctor: 1 FAIL" in result.output


def test_fixture_v2_schema():
    cases = evmod.load_verifier_cases("tests/fixtures/verifier_cases_v2.json")
    assert len(cases) == 72
    splits = [c.get("split") for c in cases]
    assert splits.count("dev") == 48 and splits.count("holdout") == 24
    assert {c.get("source_id") for c in cases} == {f"S{i:02d}" for i in range(1, 13)}
    assert {c.get("kind") for c in cases} == {
        "temiz", "wrong_number", "invented", "invented-ince",
        "claim-omission", "inverted"}
    for c in cases:
        words = sum(len(s["voice"].split()) for s in c["script"]["scenes"])
        assert 3 <= len(c["script"]["scenes"]) <= 4, c["para_id"]
        assert 70 <= words <= 100, (c["para_id"], c["kind"], words)
        for j in c.get("bad", []):
            assert 0 <= j < len(c["script"]["claims"])
    # Temiz vakalar: iddia kanitlari kaynakta birebir + deterministik temiz.
    for c in cases:
        if c.get("kind") != "temiz":
            continue
        for cl in c["script"]["claims"]:
            ok, _info = ver.evidence_verbatim(cl["evidence"], c["para"])
            assert ok, (c["para_id"], cl["text"][:60])
        assert ver.check_grounding(c["script"], c["para"]) == []
        assert ver.check_voice_grounding(c["script"], c["para"])[0] == []


def _v2_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    monkeypatch.setenv("LLM_MODELS_VERIFY", "")
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    monkeypatch.setenv("LLM_RATE_PER_MINUTE", "60000")


def _tiny_v2(tmp_path):
    p = tmp_path / "v2.json"
    p.write_text(json.dumps({"cases": [
        {"para_id": 1, "kind": "temiz", "split": "dev", "source_id": "S01",
         "para": "MiniDB 2.1 yayinda ve hiz %40 artti.",
         "script": {"hook": "x", "scenes": [{"id": 1, "voice": "MiniDB 2.1 yayinda.",
                                             "on_screen": "E", "visual": "title",
                                             "code": None, "evidence": ["e"]}],
                    "claims": [{"text": "2.1 yayinda.", "evidence": "e"}]},
         "bad": [], "bad_sentences": [], "note": "t"},
        {"para_id": 2, "kind": "temiz", "split": "holdout", "source_id": "S09",
         "para": "MiniDB 2.1 yayinda ve hiz %40 artti.",
         "script": {"hook": "x", "scenes": [{"id": 1, "voice": "MiniDB 2.1 yayinda.",
                                             "on_screen": "E", "visual": "title",
                                             "code": None, "evidence": ["e"]}],
                    "claims": [{"text": "2.1 yayinda.", "evidence": "e"}]},
         "bad": [], "bad_sentences": [], "note": "t"},
    ]}), encoding="utf-8")
    return str(p)


def _ok_route(mock):
    import httpx

    return mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "g", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {
                "role": "assistant", "content": json.dumps({
                    "verdicts": [{"i": i, "verdict": "entailed", "note": "ok"}
                                 for i in range(12)],
                    "overall": "pass"})},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))


def test_holdout_dev_enforcement(monkeypatch, tmp_path):
    import respx
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    _v2_env(monkeypatch, tmp_path)
    cases = _tiny_v2(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _ok_route(mock)
        r1 = CliRunner().invoke(app, [
            "eval-verifier", "--cases", cases, "--models", "sa:free", "--yes",
            "--split", "dev", "--out-dir", "out/ev"])
    assert r1.exit_code == 0, r1.output
    assert "kapı sayılmaz" in r1.output or "kapi sayilmaz" in r1.output
    conn = db.connect()
    db.migrate(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM eval_gates WHERE task = 'eval-verifier'").fetchone()[0] == 0
    conn.close()
    with respx.mock(assert_all_called=False) as mock:
        _ok_route(mock)
        r2 = CliRunner().invoke(app, [
            "eval-verifier", "--cases", cases, "--models", "sa:free", "--yes",
            "--split", "holdout", "--out-dir", "out/ev2"])
    assert r2.exit_code == 0, r2.output
    conn = db.connect()
    row = conn.execute(
        "SELECT passed FROM eval_gates WHERE task = 'eval-verifier'").fetchone()
    conn.close()
    assert row is not None  # holdout kapisi yazildi
