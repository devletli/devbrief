"""eval-metadata: fixture semasi, mock akis, kategori/kapi mantigi (ag yok)."""

import json as _json
import re as _re

from devbrief import eval as evmod

FIXTURE = "tests/fixtures/metadata_cases.json"
FIELDS = {"title", "description", "x_post", "linkedin_post"}
KINDS = {"clean", "invented", "paraphrase", "misnumbered"}


def _cases():
    return evmod.load_metadata_cases(FIXTURE)


def _entailed_json(n):
    return _json.dumps({"verdicts": [{"i": i, "verdict": "entailed", "note": "ok"}
                                     for i in range(n)],
                        "overall": "pass"})


def _mock_post_all_entailed():
    """Prompttaki iddia indekslerine gore hepsi-entailed doner; cagri sayar."""

    async def post(model, prompt):
        post.calls.append(prompt)
        idx = sorted({int(m) for m in _re.findall(r"\[(\d+)\]", prompt)})
        payload = {"verdicts": [{"i": i, "verdict": "entailed", "note": "ok"}
                                for i in idx],
                   "overall": "pass"}
        return _json.dumps(payload), 5.0

    post.calls = []
    return post


def test_fixture_schema_24_cases():
    cases = _cases()
    assert len(cases) == 24
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == 24
    pairs: dict[int, set] = {}
    for c in cases:
        assert c["kind"] in KINDS, c["id"]
        assert isinstance(c["pair"], int) and 1 <= c["pair"] <= 6
        pairs.setdefault(c["pair"], set()).add(c["kind"])
        assert isinstance(c["source"], str) and c["source"].strip()
        assert isinstance(c["script"], dict) and c["script"].get("claims")
        sents = c["sentences"]
        assert len(sents) >= 2
        for s in sents:
            assert s["field"] in FIELDS and s["text"].strip()
        for b in c["bad"]:
            assert 0 <= b < len(sents), (c["id"], b)
        if c["kind"] == "clean":
            assert c["bad"] == []
        else:
            assert len(c["bad"]) >= 1
    assert all(v == KINDS for v in pairs.values()), pairs


def test_fixture_det_intent():
    """Kilitli niyet: temiz gecer; uydurma det'te; parafraz/sayi LLM'e kalir."""
    # dogrudan cekirdek uzerinden (ag yok):
    from devbrief.script import metadata as _mm

    for c in _cases():
        script, source = c["script"], c["source"]
        union, roots, ref_toks, refs = _mm.support_context(script, source)
        layers = {}
        for i, s in enumerate(c["sentences"]):
            layer, _info, _msg = _mm.check_unit_support(
                {"field": s["field"], "sentence": s["text"]},
                union, roots, ref_toks, refs)
            layers[i] = layer
        bad = c["bad"]
        if c["kind"] == "clean":
            assert all(v is None for v in layers.values()), (c["id"], layers)
        elif c["kind"] == "invented":
            assert all(layers[b] in ("kural", "kapsama") for b in bad), (c["id"], layers)
            assert all(v is None for i, v in layers.items() if i not in bad)
        else:  # paraphrase / misnumbered: det atlatir, LLM yakalar
            assert all(v is None for v in layers.values()), (c["id"], layers)


async def test_mock_flow_all_entailed():
    """Mock akis: uydurma det'te yakalanir; parafraz/sayi kararir; temiz FP yok."""
    post = _mock_post_all_entailed()
    results = []
    for c in _cases():
        results.append(await evmod.run_metadata_case(c, "m:free", post))
    assert len(post.calls) == 24  # vaka basina tek istek (hepsinde kalan var)
    by_id = {r["case"]["id"]: r["assessment"] for r in results}
    for c in _cases():
        a = by_id[c["id"]]
        if c["kind"] == "clean":
            assert not a["fp"], (c["id"], a["fp_notes"])
        elif c["kind"] == "invented":
            assert a["caught"], c["id"]
            for b in c["bad"]:
                assert a["combined"][b] != ["LLM"], c["id"]
        else:
            assert not a["caught"], c["id"]  # mock entailed: LLM kacirir
            assert a["combined"] == {}, c["id"]


async def test_mock_llm_catches_det_passing_sentence():
    """Det'i gecen bozuk cumleyi LLM (mock not_stated) eler."""
    post = _mock_post_all_entailed()
    cases = {c["id"]: c for c in _cases()}
    c = cases["M1c"]  # bad=[3], det geciyor

    async def selective(model, prompt):
        text, ms = await post(model, prompt)
        payload = _json.loads(text)
        for v in payload["verdicts"]:
            if v["i"] == 3:
                v["verdict"] = "not_stated"
                v["note"] = "kaynakta yok"
        payload["overall"] = "fail"
        return _json.dumps(payload), ms

    res = await evmod.run_metadata_case(c, "m:free", selective)
    a = res["assessment"]
    assert a["caught"] and a["combined"] == {3: ["LLM"]}
    assert not a["llm_caught"] or True
    assert a["llm_caught"]  # tek kotu LLM'de yakalandi
    assert len(post.calls) == 1


async def test_no_survivors_skips_llm():
    """Hic kalan yoksa LLM cagrilmaz; cikti None, ms 0."""

    async def boom(model, prompt):
        raise AssertionError("LLM cagrilmamali")

    case = {"id": "X1", "pair": 9, "kind": "invented", "source": "Kisa metin burada.",
            "script": {"hook": "k", "claims": []},
            "sentences": [{"field": "x_post", "text": "Zxq qwerty asdfgh."}],
            "bad": [0]}
    res = await evmod.run_metadata_case(case, "m:free", boom)
    assert res["output"] is None and res["ms"] == 0.0
    assert res["assessment"]["combined"] == {0: ["kapsama"]}


def _res(kind, **a):
    base = {"json_ok": True, "caught": False, "det_caught": False, "llm_caught": False,
            "fp": False, "fp_notes": [], "index_ok": True, "combined": {}}
    base.update(a)
    bad = [] if kind == "clean" else [0]
    sents = [{"field": "x_post", "text": f"cumle {kind}"}]
    return {"case": {"id": f"T-{kind}", "kind": kind, "bad": bad, "sentences": sents},
            "ms": 10.0, "assessment": base}


def test_category_math():
    results = ([_res("clean")] * 5 + [_res("clean", fp=True, fp_notes=["#0 kapsama"])]
               + [_res("invented", caught=True, det_caught=True, combined={0: ["kapsama"]})] * 6
               + [_res("paraphrase", caught=True, llm_caught=True,
                       combined={0: ["LLM"]})] * 5
               + [_res("paraphrase")]  # 1 kaciran
               + [_res("misnumbered", caught=True, llm_caught=True,
                       combined={0: ["LLM"]})] * 6)
    rep = evmod.summarize_metadata("m", results)
    assert rep.n == 24
    assert rep.recall == 17 / 18
    assert rep.recall_det == 6 / 18
    assert rep.recall_llm == 11 / 18
    assert rep.fp_rate == 1 / 6
    assert rep.cat["invented"]["recall"] == 1.0
    assert rep.cat["paraphrase"]["recall"] == 5 / 6
    assert rep.cat["paraphrase"]["llm"] == 5 / 6
    assert rep.cat["misnumbered"] == {"n": 6, "recall": 1.0, "det": 0.0, "llm": 1.0}
    assert not rep.qualifies  # FP %16.7 > %10
    assert any("yanlis-red" in r for r in rep.gate_reasons)


def test_gate_logic():
    ok = [_res("clean")] * 6 + [_res("invented", caught=True, det_caught=True,
                                     combined={0: ["kapsama"]})] * 6 + [
        _res("paraphrase", caught=True, llm_caught=True, combined={0: ["LLM"]})] * 6 + [
        _res("misnumbered", caught=True, llm_caught=True, combined={0: ["LLM"]})] * 6
    rep = evmod.summarize_metadata("m", ok)
    assert rep.recall == 1.0 and rep.fp_rate == 0.0
    assert rep.qualifies and rep.gate_reasons == []
    low = [_res("clean")] * 6 + [_res("invented", caught=True, det_caught=True,
                                      combined={0: ["kapsama"]})] * 6 + [
        _res("paraphrase")] * 6 + [_res("misnumbered")] * 6
    rep2 = evmod.summarize_metadata("m", low)
    assert rep2.recall == 6 / 18 and not rep2.qualifies
    assert any("recall" in r for r in rep2.gate_reasons)


def test_miss_reasons_content():
    results = [_res("clean"),
               _res("paraphrase", combined={}) | {"case": {
                   "id": "M9c", "kind": "paraphrase", "bad": [2],
                   "sentences": [{"field": "x_post", "text": "a"},
                                 {"field": "x_post", "text": "b"},
                                 {"field": "x_post", "text": "Bozuk cumle burada."}]}}]
    rep = evmod.summarize_metadata("m", results)
    assert rep.details and rep.details[0]["case"] == "M9c"
    missed = rep.details[0]["missed"][0]
    assert missed["idx"] == 2 and "Bozuk cumle" in missed["sentence"]
    assert missed["katman"] == "hicbiri"


async def test_miss_detail_shows_each_layer_verdict():
    """Kacan vakada det ve LLM'in ne dedigi ayri gorunur."""
    post = _mock_post_all_entailed()
    cases = {c["id"]: c for c in _cases()}
    res = await evmod.run_metadata_case(cases["M1c"], "m:free", post)
    rep = evmod.summarize_metadata("m", [res])
    assert rep.details and rep.details[0]["case"] == "M1c"
    missed = rep.details[0]["missed"][0]
    assert missed["katman"] == "hicbiri"
    assert missed["det"] == "gecti" and missed["llm"] == "entailed"
    assert "dagitima odaklandi" in missed["sentence"]


def test_prompt_builder_index_and_evidence():
    cases = {c["id"]: c for c in _cases()}
    prompt, order = evmod.build_metadata_verify_prompt(cases["M1b"])
    assert order == [0, 1, 2, 4, 5]  # 3 det'te dustu
    assert "[0] Vite 8.3.4 yayinda || kanit:" in prompt
    assert "[3] Indirme boyutu 31 MB." in prompt  # kalan-sira indeksi
    assert cases["M1b"]["source"] in prompt


def test_cli_help_and_arg_guards(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from devbrief.cli import app

    r = CliRunner().invoke(app, ["eval-metadata", "--help"])
    assert r.exit_code == 0 and "--any-model" in r.output
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    r = CliRunner().invoke(app, ["eval-metadata", "--only", "M99", "--yes"])
    assert r.exit_code == 2 and "M1a" in r.output
    r = CliRunner().invoke(app, ["eval-metadata", "--any-model", "ucretli-model", "--yes"])
    assert r.exit_code == 2 and "ucretli korumasi" in r.output


def test_cli_summarize_only_offline(tmp_path, monkeypatch):
    """Kayitli cikti + mock'suz ozet (ag yok)."""
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    out = Path("out/eval/m")
    out.mkdir(parents=True)
    clean = {"model": "m", "case": "M1a", "ms": 4,
             "output": _entailed_json(6)}
    bad = {"model": "m", "case": "M1b", "ms": 6,
           "output": _entailed_json(5)}  # kotu det'te dustu, kalan entailed
    (out / "M1a.json").write_text(_json.dumps(clean), encoding="utf-8")
    (out / "M1b.json").write_text(_json.dumps(bad), encoding="utf-8")
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    r = CliRunner().invoke(app, ["eval-metadata", "--cases", fx,
                                 "--summarize-only"])
    assert r.exit_code == 0, r.output
    body = (Path("out/eval/summary_metadata.md")).read_text(encoding="utf-8")
    assert "m" in body and "recall" in body.lower()
