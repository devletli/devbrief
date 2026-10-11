"""Bicim hatasi retry + FP ayrimi + runs + cost (ag yok, mock)."""

import devbrief.db as db
from devbrief import eval as evmod
from devbrief.script import verifier as ver
from devbrief.script.schemas import VerifyOut


def _claims(n=2):
    return [{"text": f"iddia {i}", "evidence": "kanit cumlesi burada gecer",
             "kind": "fact"} for i in range(n)]


def test_format_error_list():
    good = VerifyOut.model_validate(
        {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"},
                      {"i": 1, "verdict": "entailed", "note": "n"}], "overall": "pass"})
    assert ver.format_error_list(good, _claims(2)) == []
    missing = VerifyOut.model_validate(
        {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"}], "overall": "pass"})
    errs = ver.format_error_list(missing, _claims(2))
    assert any("1 eksik" in e for e in errs)
    dup = VerifyOut.model_validate(
        {"verdicts": [{"i": 0, "verdict": "entailed", "note": "a"},
                      {"i": 0, "verdict": "entailed", "note": "b"},
                      {"i": 1, "verdict": "entailed", "note": "c"}], "overall": "pass"})
    assert any("iki kez" in e for e in ver.format_error_list(dup, _claims(2)))
    oob = VerifyOut.model_validate(
        {"verdicts": [{"i": 5, "verdict": "entailed", "note": "n"}], "overall": "pass"})
    assert any("aralik-disi" in e for e in ver.format_error_list(oob, _claims(2)))
    assert ver.format_error_list(None, _claims(2)) == []


async def test_verify_llm_retries_once_same_chain(monkeypatch):
    from devbrief.llm import client as llm

    calls = []

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        calls.append((task, dict(variables)))
        if len(calls) == 1:
            return VerifyOut.model_validate(
                {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"}],
                 "overall": "pass"})
        return VerifyOut.model_validate(
            {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"},
                          {"i": 1, "verdict": "entailed", "note": "n"}],
             "overall": "pass"})

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    out = await ver.verify_llm("kaynak", _claims(2))
    assert len(calls) == 2
    assert calls[0][0] == "verify_script"
    assert calls[1][0] == "verify_script:format_retry"
    assert "indeks 1 eksik" in calls[1][1]["format_errors"]
    assert out.overall == "pass"


async def test_verify_llm_still_broken_unsupported(monkeypatch):
    from devbrief.llm import client as llm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        return VerifyOut.model_validate(
            {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"}],
             "overall": "pass"})

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    out = await ver.verify_llm("kaynak", _claims(2))
    norm = ver.normalize_verdicts(out, _claims(2))
    assert norm.overall == "fail"  # eksik indeks unsupported, guvenlik korunur
    assert any(v.verdict == "not_stated" for v in norm.verdicts)


async def test_format_retry_counts_toward_cap(monkeypatch):
    from devbrief.llm import client as llm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        cap = kw.get("cap")
        if cap is not None:
            cap.charge()
        if "format_retry" in str(task):
            raise llm.CallCapExceeded("cap")
        return VerifyOut.model_validate(
            {"verdicts": [{"i": 0, "verdict": "entailed", "note": "n"}],
             "overall": "pass"})

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    try:
        await ver.verify_llm("kaynak", _claims(2), cap=llm.CallCap(1))
    except llm.CallCapExceeded:
        pass
    else:
        raise AssertionError("cap asimi bekleniyordu (retry cap'e dahil)")


def test_fp_content_format_split():
    import json

    # Temiz vaka, tek claim; model eksik indeks -> bicim kaynakli FP.
    clean = {"para_id": 1, "kind": "clean", "para": "MiniDB 2.1 yayinda burada.",
             "script": {"claims": [{"text": "2.1 yayinda.", "evidence": "MiniDB 2.1 yayinda"}]},
             "bad": []}
    raw_missing = json.dumps({"verdicts": [], "overall": "pass"})
    a = evmod.assess_verdict(raw_missing, clean)
    # Bos verdicts: tum fact eksik -> bicim hatasi + FP (unsupported).
    assert a["has_format_error"] and a["fp"] and a["fp_format"] and not a["fp_content"]
    assert any(d["layer"] == "biçim" for d in a["fp_details"])
    # Icerik FP: kural yakalar, bicim temiz.
    clean2 = {"para_id": 1, "kind": "clean", "para": "sade metin burada.",
              "script": {"scenes": [{"id": 1, "voice": "Surum 9.9 cikti burada.",
                                     "on_screen": "E", "visual": "title",
                                     "code": None, "evidence": ["e"]}],
                         "claims": [{"text": "9.9 yayinda.", "evidence": "e"}]},
              "bad": []}
    raw_ok = json.dumps({"verdicts": [{"i": 0, "verdict": "entailed", "note": "ok"}],
                         "overall": "pass"})
    b = evmod.assess_verdict(raw_ok, clean2)
    assert b["fp"] and b["fp_content"] and not b["fp_format"]


def test_runs_flaky_counter():
    def res(case, **a):
        base = {"json_ok": True, "caught": False, "fp": False, "index_ok": True,
                "index_first_ok": True, "llm_caught": False, "combined": {}}
        base.update(a)
        return {"case": case, "ms": 10.0, "assessment": base}

    c1 = {"para_id": 1, "kind": "wrong_number", "bad": [0]}
    c2 = {"para_id": 2, "kind": "clean", "bad": []}
    results = [res(c1, caught=True, llm_caught=True),
               res(c1, caught=False, llm_caught=False),
               res(c2, fp=False), res(c2, fp=False)]
    rep = evmod.summarize_verifier("m", results)
    assert rep.runs == 2
    assert rep.flaky == 1  # P1 karari degisti, P2 degismedi
    assert rep.recall == 0.5  # ortalama


def test_cost_unknown_when_missing():
    rep = evmod.summarize_verifier("m", [])
    assert rep.cost_total is None and rep.cost_avg is None
    table = evmod.render_verifier_table([rep])
    assert "bilinmiyor" in table


def test_extract_cost():
    from devbrief.llm.client import extract_cost

    class U:
        cost = 0.00014

    class R:
        usage = U()

    assert extract_cost(R()) == 0.00014
    assert extract_cost({"usage": {"cost": 0.001}}) == 0.001
    assert extract_cost({"usage": {}}) is None
    assert extract_cost(object()) is None


def test_report_llm_cost_unknown(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    slot = db.reserve_llm_slot(conn, "t", "m1", 400)
    db.finish_llm_slot(conn, slot, ok=True, ms=10, tokens=5, status="ok")
    conn.close()
    result = CliRunner().invoke(app, ["report", "--llm"])
    assert result.exit_code == 0, result.output
    assert "bilinmiyor" in result.output


def test_llm_cost_column_migration():
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)").fetchall()}
    assert "cost" in cols
    slot = db.reserve_llm_slot(conn, "t", "m", 400)
    db.finish_llm_slot(conn, slot, ok=True, ms=5, cost=0.00014)
    row = conn.execute("SELECT cost FROM llm_calls").fetchone()
    assert abs(float(row["cost"]) - 0.00014) < 1e-9
    conn.close()
