"""Dogrulama sertlestirme: siki kanit + cift-model + flaky + pair + resume (mock, ag yok)."""

import json

from devbrief import eval as evmod
from devbrief.script import verifier as ver
from devbrief.script.schemas import IndexVerdict, VerifyOut

SRC = "MiniDB 2.1 was released with faster index rebuilds, 40% quicker on big tables."
EV = "MiniDB 2.1 was released with faster index rebuilds, 40% quicker on big tables."
# Katmanlar ayni-dil varsayar (Turkce ek soyma); birim testleri Turkce kaynakla.
SRC_TR = "MiniDB 2.1 cikti, hiz %40 artti. Dizin yeniden olusturma dort kat hizli."
EV_TR = "MiniDB 2.1 cikti, hiz %40 artti."


def _script(claim_text, evidence):
    return {"hook": "x", "source_url": "https://x",
            "scenes": [{"id": 1, "voice": "MiniDB 2.1 cikti.", "on_screen": "E",
                        "visual": "title", "code": None, "evidence": ["e"]}],
            "impact": "i", "action": "a",
            "claims": [{"text": claim_text, "evidence": evidence}],
            "duration_estimate_s": 40}


def _out(verdicts, overall="pass"):
    return VerifyOut(
        verdicts=[IndexVerdict(i=i, verdict=v, note=f"n{i}") for i, v in verdicts],
        overall=overall)


def test_evidence_not_in_source_unsupported():
    sc = _script("MiniDB 2.1 cikti.",
                 "MiniDB tamamen yeniden yazildi ve artik bulutlarda uciyor.")
    issues = ver.check_grounding(sc, SRC_TR)
    assert any(i.kind == "evidence_not_verbatim" for i in issues)
    # Birebir kanit gecer.
    sc2 = _script("MiniDB 2.1 cikti.", EV_TR)
    kinds = {i.kind for i in ver.check_grounding(sc2, SRC_TR)}
    assert "evidence_not_verbatim" not in kinds
    assert "evidence_coverage" not in kinds
    assert "claim_evidence_gap" not in kinds


def test_evidence_coverage_numbers_must_match():
    # Claim'deki sayi kanitta yoksa unsupported.
    sc = _script("Hiz %90 artti.", EV_TR)
    assert any(i.kind == "evidence_coverage"
               for i in ver.check_grounding(sc, SRC_TR))
    # Sayilar kanitta varsa kapsama gecer.
    sc2 = _script("Hiz %40 artti.", EV_TR)
    assert not [i for i in ver.check_grounding(sc2, SRC_TR)
                if i.kind == "evidence_coverage"]


def test_claim_gap_unsupported():
    # Kanitta karsiligi olmayan >=2 kok: uydurma sinyali.
    sc = _script("Bulutflare yeni uydu destegi ekledi.", EV_TR)
    issues = ver.check_grounding(sc, SRC_TR)
    assert any(i.kind == "claim_evidence_gap" for i in issues)
    # Tek karsiliksiz kok tolere edilir.
    sc2 = _script("MiniDB 2.1 muhtesem cikti.", EV_TR)
    assert not [i for i in ver.check_grounding(sc2, SRC_TR)
                if i.kind == "claim_evidence_gap"]


async def test_dual_one_dissent_unsupported(monkeypatch):
    from devbrief.llm import client as llm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        m = kw.get("model", "")
        v = "entailed" if m == "m1:free" else "not_stated"
        return _out([(0, v)])

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    claims = [{"text": "MiniDB 2.1 cikti.", "evidence": EV, "kind": "fact"}]
    combined, info = await ver.verify_with_models(SRC, claims, ["m1:free", "m2:free"])
    assert combined.overall == "fail"
    assert combined.verdicts[0].verdict == "not_stated"
    assert "cift-model" in combined.verdicts[0].note
    assert info["models"] == ["m1:free", "m2:free"]


async def test_dual_both_entailed_passes(monkeypatch):
    from devbrief.llm import client as llm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        return _out([(0, "entailed")])

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    claims = [{"text": "MiniDB 2.1 cikti.", "evidence": EV, "kind": "fact"}]
    combined, _ = await ver.verify_with_models(SRC, claims, ["m1:free", "m2:free"])
    assert combined.overall == "pass"


async def test_single_model_flaky_unsupported(monkeypatch):
    from devbrief.llm import client as llm

    calls = []

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        calls.append(kw.get("cache_salt", ""))
        v = "entailed" if len(calls) == 1 else "contradicted"
        return _out([(0, v)])

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    claims = [{"text": "MiniDB 2.1 cikti.", "evidence": EV, "kind": "fact"}]
    combined, info = await ver.verify_with_models(
        SRC, claims, ["m1:free"], flaky_double=True)
    assert combined.overall == "fail"
    assert "flaky" in combined.verdicts[0].note
    assert info["flaky"] == [0]
    # Iki ayri cagri farkli salt ile (onbellek carpisma yok).
    assert len(set(calls)) == 2


async def test_verify_cap_bounds_dual(monkeypatch):
    from devbrief.llm import client as llm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        return _out([(0, "entailed")])

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    claims = [{"text": "MiniDB 2.1 cikti.", "evidence": EV, "kind": "fact"}]
    try:
        await ver.verify_with_models(SRC, claims, ["m1:free", "m2:free"],
                                     verify_cap=llm.CallCap(1))
    except llm.CallCapExceeded:
        pass
    else:
        raise AssertionError("VERIFY_CALL_CAP asimi bekleniyordu")


def test_pair_report_layers_and_invented_reasons():
    def _inv(text):
        return {"para_id": 1, "kind": "invented",
                "para": "Surum 2.1 yayinda burada.",
                "script": {"claims": [{"text": text, "evidence": "e"}]},
                "bad": [0]}

    a_ent = json.dumps({"verdicts": [{"i": 0, "verdict": "entailed", "note": "ok"}],
                        "overall": "pass"})
    a_no = json.dumps({"verdicts": [{"i": 0, "verdict": "not_stated", "note": "yok"}],
                       "overall": "fail"})
    hit = _inv("Uydu destegi eklendi.")
    ap_hit = evmod.assess_pair({"m1": a_ent, "m2": a_no}, hit, ["m1", "m2"])
    assert ap_hit["caught"]  # muhalif cift yakalama sayilir
    assert ap_hit["combined"][0] == ["çift-model"]
    miss = _inv("Karanlik mod eklendi.")
    ap_miss = evmod.assess_pair({"m1": a_ent, "m2": a_ent}, miss, ["m1", "m2"])
    assert not ap_miss["caught"]
    rep = evmod.summarize_verifier("m1+m2", [
        {"case": hit, "ms": 10.0, "assessment": ap_hit},
        {"case": miss, "ms": 10.0, "assessment": ap_miss}])
    assert rep.layer_table.get("çift-model", {}).get("caught") == 1
    assert len(rep.invented_misses) == 1
    assert "hicbir katman" in rep.invented_misses[0]["neden"]
    # Kapi yalnizca birlesik sayiya: recall dusuk -> kalir.
    assert rep.recall == 0.5 and not rep.qualifies


def _cli_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    monkeypatch.setenv("LLM_MODELS_VERIFY", "")
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    monkeypatch.setenv("LLM_RATE_PER_MINUTE", "60000")


def _tiny_cases(tmp_path):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps({"cases": [
        {"para_id": 1, "kind": "clean", "para": "MiniDB 2.1 yayinda.",
         "script": {"claims": [{"text": "2.1 yayinda.", "evidence": "e"}]}, "bad": []},
        {"para_id": 2, "kind": "wrong_number", "para": "Surum 2.1 yayinda.",
         "script": {"claims": [{"text": "9.9 yayinda.", "evidence": "e"}]}, "bad": [0]},
    ]}), encoding="utf-8")
    return str(p)


def _ok_route(mock):
    import httpx

    return mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "g", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": '{"verdicts": [{"i": 0, "verdict": "entailed", "note": "ok"}]}'},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))


def test_resume_skips_existing(monkeypatch, tmp_path):
    import respx
    from typer.testing import CliRunner

    from devbrief.cli import app

    _cli_env(monkeypatch, tmp_path)
    cases = _tiny_cases(tmp_path)
    mdir = tmp_path / "out" / "ev" / "sa_free"
    mdir.mkdir(parents=True)
    (mdir / "P1_clean.json").write_text(
        json.dumps({"model": "sa:free", "case": "P1_clean", "trial": 1,
                    "ms": 5, "output": '{"verdicts": [], "overall": "pass"}'}),
        encoding="utf-8")
    with respx.mock(assert_all_called=False) as mock:
        route = _ok_route(mock)
        result = CliRunner().invoke(app, [
            "eval-verifier", "--cases", cases, "--models", "sa:free", "--yes",
            "--resume", "--out-dir", "out/ev"])
    assert result.exit_code == 0, result.output
    # 2 vaka vardi, biri atlandi: tek istek (bicim-retry yok, indeks tam).
    assert route.call_count == 1, result.output
    assert "atlandi (resume)" in result.output


def test_no_yes_no_run(monkeypatch, tmp_path):
    import respx
    from typer.testing import CliRunner

    from devbrief.cli import app

    _cli_env(monkeypatch, tmp_path)
    cases = _tiny_cases(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        route = _ok_route(mock)
        result = CliRunner().invoke(app, [
            "eval-verifier", "--cases", cases, "--models", "sa:free",
            "--out-dir", "out/ev"])
    assert result.exit_code == 2, result.output
    assert "--yes" in result.output
    assert route.call_count == 0
