"""audit-pack --day + audit-log (DB + dosya, ag yok)."""

import json

import devbrief.db as db


def _seed(tmp_path):
    conn = db.connect()
    db.migrate(conn)
    script = {"hook": "Kanca", "source_url": "https://x",
              "scenes": [{"id": 1, "voice": "Vite 8.3.4 cikti.",
                          "on_screen": "Etiket", "visual": "title",
                          "code": None, "evidence": ["Vite 8.3.4 cikti."]}],
              "impact": "E.", "action": "A.",
              "claims": [{"text": "8.3.4 yayinda.", "evidence": "Vite 8.3.4 cikti."}],
              "duration_estimate_s": 40}
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json, created_at,"
            " updated_at) VALUES (NULL, '2026-10-09', 'ep1', 'verified', ?, ?, ?)",
            (json.dumps(script), db.now_iso(), db.now_iso()))
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, reject_reason, created_at,"
            " updated_at) VALUES (NULL, '2026-10-09', 'ep2', 'failed', 'kotu surum', ?, ?)",
            (db.now_iso(), db.now_iso()))
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
            " VALUES (NULL, '2026-10-09', 'ep3', 'draft', ?, ?)", (db.now_iso(), db.now_iso()))
    conn.close()
    fetchdir = tmp_path / "out" / "2026-10-09" / "ep1"
    fetchdir.mkdir(parents=True)
    (fetchdir / "fetch.json").write_text(json.dumps(
        {"url": "https://x", "text": "Vite 8.3.4 cikti.", "source_hash": "h",
         "low_source": False, "reason": ""}))


def _ctx(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "s:free")
    return CliRunner(), app


def test_audit_pack_day_and_log(monkeypatch, tmp_path):
    runner, app = _ctx(monkeypatch, tmp_path)
    _seed(tmp_path)
    r = runner.invoke(app, ["audit-pack", "--day", "2026-10-09"])
    assert r.exit_code == 0, r.output
    for slug in ("ep1", "ep2", "ep3"):
        assert (tmp_path / "out" / "2026-10-09" / slug / "audit.md").exists()
    text = (tmp_path / "out" / "2026-10-09" / "ep1" / "audit.md").read_text(encoding="utf-8")
    assert "## Kural denetimi" in text and "## Metadata" in text
    assert "## Voice kapsama" in text and "metrik=" in text
    r = runner.invoke(app, ["audit-log"])
    assert r.exit_code == 0, r.output
    summary = tmp_path / "out" / "audit" / "summary.md"
    assert summary.exists()
    body = summary.read_text(encoding="utf-8")
    assert "toplam bolum: 3" in body and "kotu surum" in body
    r = runner.invoke(app, ["audit-pack", "--day", "2020-01-01"])
    assert r.exit_code == 1
    r = runner.invoke(app, ["audit-pack"])
    assert r.exit_code == 2
