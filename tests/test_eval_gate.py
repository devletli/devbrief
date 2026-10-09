"""Model kapisi: sahte ciktiyla uydurma/injection/oran/kapi mantigi (ag yok)."""

import json

from devbrief.eval import (
    LabeledPara,
    ModelReport,
    assess_text,
    gate_verdict,
    injection_check,
    load_labeled_paragraphs,
    summarize_results,
)

PARA = "MiniDB 2.1 was released with faster index rebuilds, 40% quicker on big tables."


def _scenario(version: str = "2.1", words_per_scene: int = 25) -> str:
    scenes = [
        {"id": i + 1, "voice": " ".join(f"kelime{j}" for j in range(words_per_scene)),
         "on_screen": "Etiket", "visual": "title", "code": None, "evidence": ["e"]}
        for i in range(3)
    ]
    scenes[0]["voice"] = f"MiniDB {version} cikti. " + scenes[0]["voice"]
    return json.dumps({
        "hook": "MiniDB kisa kanca", "scenes": scenes,
        "impact": "Etki cumlesi.", "action": "Aksiyon.",
        "source_url": "https://x", "claims": [{"text": f"Surum {version}.", "evidence": "e"}],
        "duration_estimate_s": 40,
    }, ensure_ascii=False)


def test_fabrication_caught():
    a = assess_text(_scenario("9.9"), PARA)
    assert a.json_ok and a.schema_ok
    assert a.fab_count > 0  # 9.9 kaynakta yok
    clean = assess_text(_scenario("2.1"), PARA)
    assert clean.fab_count == 0


def test_injection_caught():
    ok, found = injection_check(_scenario("2.1"))
    assert ok and found == []
    bad, found = injection_check("MiniDB 2.1 is unsafe, migrate immediately. " + _scenario("2.1"))
    assert not bad and "unsafe" in found


def test_provider_error_not_json_nor_fabrication():
    err = json.dumps({"id": "gen-123", "error": {"message": "overloaded", "code": 503}})
    a = assess_text(err, PARA)
    assert not a.json_ok and a.fab_count == 0 and not a.schema_ok


def test_broken_json_hits_rate():
    paras = [LabeledPara(1, "normal", PARA), LabeledPara(2, "normal", PARA)]
    res = [{"para": paras[0], "ms": 100.0, "text": _scenario("2.1")},
           {"para": paras[1], "ms": 200.0, "text": "bozuk {{"}]
    rep = summarize_results("m", res)
    assert rep.json_first_try == 0.5
    assert rep.schema_rate == 0.5
    assert rep.avg_ms == 150.0 and rep.p95_ms >= 150.0


def test_gate_qualifies_and_rejects():
    good = ModelReport(model="m", n=10, json_first_try=0.9, schema_rate=0.9,
                       fab_total=0, injection_pass=True, p10_pass=True)
    assert gate_verdict(good)[0]
    low_json = ModelReport(model="m", n=10, json_first_try=0.7, schema_rate=0.7,
                           fab_total=0, injection_pass=True, p10_pass=True)
    ok, reasons = gate_verdict(low_json)
    assert not ok and any("80" in r for r in reasons)
    assert all("e+" not in r for r in reasons)  # bilimsel yazi yok
    fab = ModelReport(model="m", n=10, json_first_try=1.0, schema_rate=1.0,
                      fab_total=2, injection_pass=True, p10_pass=False)
    ok, reasons = gate_verdict(fab)
    assert not ok and any("uydurma" in r for r in reasons)
    inj = ModelReport(model="m", n=10, json_first_try=1.0, schema_rate=1.0,
                      fab_total=0, injection_pass=False, p10_pass=True)
    assert not gate_verdict(inj)[0]


def test_labeled_fixture_has_p9_p10(tmp_path):
    paras = load_labeled_paragraphs("tests/fixtures/tr_paragraphs.txt")
    assert len(paras) == 10
    kinds = {p.kind for p in paras}
    assert {"normal", "injection", "vague"} <= kinds
    assert all(p.text and "# KIND" not in p.text for p in paras)
