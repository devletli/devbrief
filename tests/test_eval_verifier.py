"""eval-verifier metrikleri + runs oranlari + VERIFY config (ag yok)."""

from devbrief import eval as evmod
from devbrief.config import Settings


def _raw(verdicts, overall="pass"):
    import json

    return json.dumps({"verdicts": [{"i": i, "verdict": v, "note": "n"} for i, v in verdicts],
                       "overall": overall})


def test_assess_verdict_recall_and_fp():
    broken = {"para_id": 1, "kind": "wrong_number", "para": "PixelOS 4.3 yayinda.",
              "script": {"claims": [{"text": "4.3 yayinda."}]}, "bad": [0]}
    hit = evmod.assess_verdict(_raw([(0, "contradicted")]), broken)
    assert hit["caught"] and not hit["fp"] and hit["index_ok"] and hit["json_ok"]
    assert hit["llm_caught"]
    clean = {"para_id": 1, "kind": "clean", "para": "PixelOS 4.2 yayinda.",
             "script": {"claims": [{"text": "4.2 yayinda."}]}, "bad": []}
    fp = evmod.assess_verdict(_raw([(0, "not_stated")], overall="fail"), clean)
    assert fp["fp"] and not fp["caught"]
    ok = evmod.assess_verdict(_raw([(0, "entailed")]), clean)
    assert not ok["fp"] and ok["index_ok"]
    bad_idx = evmod.assess_verdict(_raw([(5, "entailed")]), clean)
    assert not bad_idx["index_ok"]
    assert not evmod.assess_verdict("bozuk", clean)["json_ok"]


def test_assess_layers_rule_and_combined():
    # Kural tek basina yakalar (sayi tutmazligi) -> birlesik de yakalar.
    case = {"para_id": 1, "kind": "wrong_number", "para": "Surum 2.1 yayinda.",
            "script": {"claims": [{"text": "9.9 yayinda."}]}, "bad": [0]}
    a = evmod.assess_verdict(_raw([(0, "entailed")]), case)
    assert not a["llm_caught"] and a["caught"]  # LLM kacirdi, kural yakaladi
    assert a["combined"] == {0: ["kural"]}


def test_summarize_verifier_gate():
    def res(kind, **a):
        base = {"json_ok": True, "caught": False, "fp": False, "index_ok": True,
                "llm_caught": False, "combined": {}}
        base.update(a)
        return {"case": {"kind": kind}, "ms": 100.0, "assessment": base}

    results = [res("clean")] * 10 + [res("wrong_number", caught=True)] * 27 + [
        res("wrong_number", caught=False)] * 3
    rep = evmod.summarize_verifier("m", results)
    assert rep.recall == 0.9 and rep.fp_rate == 0.0 and rep.qualifies
    bad = [res("clean", fp=True)] * 2 + [res("clean")] * 8 + [res("wrong_number", caught=True)] * 30
    rep2 = evmod.summarize_verifier("m", bad)
    assert rep2.fp_rate == 0.2 and not rep2.qualifies


def _valid_text() -> str:
    import json

    scenes = [
        {"id": i + 1, "voice": " ".join(f"kelime{j}" for j in range(25)),
         "on_screen": "Etiket", "visual": "title", "code": None, "evidence": ["e"]}
        for i in range(3)
    ]
    return json.dumps({
        "hook": "MiniDB kisa kanca", "scenes": scenes,
        "impact": "Etki.", "action": "Aksiyon.", "source_url": "https://x",
        "claims": [{"text": "MiniDB 2.1.", "evidence": "e"}],
        "duration_estimate_s": 40}, ensure_ascii=False)


def test_runs_rates_reported():
    from devbrief.eval import LabeledPara, summarize_results

    para = LabeledPara(9, "injection", "MiniDB 2.1 metni.")
    res = [{"para": para, "ms": 10.0, "text": _valid_text()},
           {"para": para, "ms": 10.0, "text": "unsafe icerik burada"}]
    rep = summarize_results("m", res)
    assert rep.inj_rate == "ham 1/1/2 · hat 0/0/0" and not rep.injection_pass


def test_no_output_is_na_not_pass():
    from devbrief.eval import LabeledPara, gate_verdict, summarize_results

    inj = LabeledPara(9, "injection", "MiniDB 2.1 metni.")
    vague = LabeledPara(10, "vague", "Sade metin.")
    res = [{"para": inj, "ms": 10.0, "text": "HTTP 429: busy"},
           {"para": vague, "ms": 10.0, "text": ""}]
    rep = summarize_results("m", res)
    assert not rep.injection_pass and not rep.p10_pass
    assert rep.inj_valid == 0 and rep.p10_valid == 0
    ok, reasons = gate_verdict(rep)
    assert not ok and any("çıktı yok" in r for r in reasons)


def test_error_categories():
    from devbrief.eval import slot_status

    assert slot_status(None, "TimeoutException: x", None, "TimeoutException") == "timeout"
    assert slot_status(429, "HTTP 429: busy", None, None) == "http_429"
    assert slot_status(503, "HTTP 503", None, None) == "http_5xx"
    assert slot_status(200, "", None, None) == "empty_content"
    assert slot_status(200, "", "length", None) == "length"
    assert slot_status(200, "bozuk", None, None) == "invalid_json"
    assert slot_status(200, '{"a": 1}', None, None) is None


def test_tier_timeouts():
    from devbrief.config import Settings

    s = Settings(LLM_TIMEOUT_FAST_S="30", LLM_TIMEOUT_SMART_S="200",
                 LLM_MODELS_FAST="f1", LLM_MODELS_SMART="s1")
    assert s.timeout_for("fast") == 30 and s.timeout_for("smart") == 200
    assert s.tier_of("f1") == "fast" and s.tier_of("zzz") == "smart"


def test_only_and_models_filters_error(tmp_path, monkeypatch):
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    fixture = str(Path(__file__).parent / "fixtures" / "tr_paragraphs.txt")
    runner = CliRunner()
    r = runner.invoke(app, ["eval-models", "--file", fixture, "--only", "P99", "--yes"])
    assert r.exit_code == 2 and "P1" in r.output
    r = runner.invoke(app, ["eval-models", "--file", fixture, "--models", "yokmodel", "--yes"])
    assert r.exit_code == 2 and "fa:free" in r.output


def test_verify_models_fallback():
    assert Settings(LLM_MODELS_VERIFY="", LLM_MODELS_SMART="").verify_models == []
    s = Settings(LLM_MODELS_VERIFY="v1,v2", LLM_MODELS_SMART="s1")
    assert s.verify_models == ["v1", "v2"]
    assert Settings(LLM_MODELS_VERIFY="", LLM_MODELS_SMART="s1").verify_models == ["s1"]


def test_fab_details_and_ambiguous_split():
    from devbrief.eval import assess_text

    para = "Vite 8.3.4 12 hata duzeltti, 140 katilimci vardi."
    out = ('{"hook": "x", "scenes": [{"id": 1, "voice": "Vite 9.9 cikti, '
           '40 katilimci vardi.", "on_screen": "E", "visual": "title", '
           '"code": null, "evidence": ["e"]}], "impact": "i", "action": "a", '
           '"source_url": "https://x", "claims": [{"text": "c.", "evidence": "e"}], '
           '"duration_estimate_s": 40}')
    a = assess_text(out, para)
    assert a.fab_count >= 1  # 9.9 miss
    assert a.amb_count >= 1  # 40 ~ 140 belirsiz
    assert a.fab_details and all(
        {"value", "sentence", "nearest"} <= set(d) for d in a.fab_details)
