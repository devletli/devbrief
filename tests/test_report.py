"""report --llm: gecici DB ile tablo (ag yok)."""

import devbrief.db as db


def test_report_llm_table(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    slot = db.reserve_llm_slot(conn, "t", "m1", 400)
    db.finish_llm_slot(conn, slot, ok=True, ms=10, tokens=5, status="ok")
    slot2 = db.reserve_llm_slot(conn, "t", "m1", 400)
    db.finish_llm_slot(conn, slot2, ok=False, ms=10, status="http_429")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["report", "--llm"])
    assert result.exit_code == 0, result.output
    assert "kalan 398" in result.output
    assert "m1" in result.output and "http_429" in result.output
