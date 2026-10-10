"""Metadata kapisi: render/approve/pipeline engeli + demo istisnasi (mock)."""

import devbrief.db as db


def _ctx(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "s:free")
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "t")
    monkeypatch.setenv("N8N_CALLBACK_SECRET", "x")
    monkeypatch.setenv("N8N_ENCRYPTION_KEY", "y")
    return CliRunner(), app


def _episode(conn, **kw):
    row = {"cluster_id": None, "day": "d", "slug": kw.get("slug", "s"),
           "status": kw.get("status", "rendered"),
           "script_json": kw.get("script_json", '{"hook": "x"}'),
           "verify_json": "", "source_text_hash": "", "video_path": "", "thumb_path": "",
           "title": "", "description": "", "reject_reason": "",
           "metadata_json": kw.get("metadata_json", ""),
           "created_at": db.now_iso(), "updated_at": db.now_iso()}
    cols, vals = zip(*row.items(), strict=True)
    with conn:
        cur = conn.execute(f"INSERT INTO episodes ({','.join(cols)}) "
                           f"VALUES ({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)


def test_render_refuses_without_metadata(monkeypatch, tmp_path):
    runner, app = _ctx(monkeypatch, tmp_path)
    conn = db.connect()
    db.migrate(conn)
    e1 = _episode(conn)
    e2 = _episode(conn, slug="s2", status="metadata_failed")
    conn.close()
    r = runner.invoke(app, ["render", "--episode", str(e1)])
    assert r.exit_code == 1 and "metadatasiz" in r.output
    r = runner.invoke(app, ["render", "--episode", str(e2)])
    assert r.exit_code == 1 and "metadata_failed" in r.output
    r = runner.invoke(app, ["render", "--episode", str(e1), "--allow-no-metadata"])
    assert r.exit_code == 1 and "metadatasiz" in r.output  # demo degil: bayrak islemez


async def test_approve_409_without_metadata_and_pipeline_skip(tmp_path, monkeypatch):
    import httpx
    from httpx import ASGITransport

    from devbrief import pipeline as _pl
    from devbrief.api.routes import create_app

    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "t")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    e = _episode(conn)
    conn.close()
    app = create_app(dbpath)
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        h = {"Authorization": "Bearer t"}
        r = await c.post(f"/episodes/{e}/approve", headers=h)
        assert r.status_code == 409
    assert await _pl.render_episode(str(dbpath), e) is False


def test_demo_bypasses_metadata_gate():
    from devbrief.pipeline import render_allowed

    assert render_allowed({"status": "verified", "is_demo": 0,
                           "metadata_json": ""}) == "metadatasiz"
    assert render_allowed({"status": "metadata_failed", "is_demo": 0,
                           "metadata_json": ""}) == "metadata_failed"
    assert render_allowed({"status": "verified", "is_demo": 1,
                           "metadata_json": ""}) is None
    assert render_allowed({"status": "verified", "is_demo": 0,
                           "metadata_json": "{}"}) is None


def test_audit_log_shows_failed_metadata(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, reject_reason,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'metadata_failed', 'r', ?, ?)",
            (db.now_iso(), db.now_iso()))
    conn.close()
    r = CliRunner().invoke(app, ["audit-log"])
    assert r.exit_code == 0, r.output
    body = (tmp_path / "out" / "audit" / "summary.md").read_text(encoding="utf-8")
    assert "toplam bolum: 1" in body and "failed (metadata)" in body


def test_probe_model_list(monkeypatch, tmp_path):
    import httpx
    import respx
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("LLM_MODELS_VERIFY", "")
    monkeypatch.setenv("LLM_PROBE_MODELS", "solo:free")
    seen = []
    with respx.mock(assert_all_called=False) as mock:
        def _cap(request):
            import json as _j

            seen.append(_j.loads(request.content)["model"])
            return httpx.Response(200, json={"ok": True})

        mock.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=_cap)
        r = CliRunner().invoke(app, ["probe"])
    assert r.exit_code == 0, r.output
    assert sorted(set(seen)) == ["solo:free"]
