"""API: auth, kilit, durum makinesi, callback, paket (sahte is hatti)."""

import httpx
import respx
from httpx import ASGITransport

import devbrief.db as db
from devbrief.api import jobs as jobs_mod
from devbrief.api.routes import create_app

TOKEN = "test-token"


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    conn.close()
    return create_app(dbpath)


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _auth(h: dict | None = None) -> dict:
    h = dict(h or {})
    h["Authorization"] = f"Bearer {TOKEN}"
    return h


_n = {"i": 0}


def _episode(conn, **kw):
    _n["i"] += 1
    row = {"cluster_id": None, "day": "2026-10-09", "slug": kw.get("slug", f"s{_n['i']}"),
           "status": kw.get("status", "rendered"),
           "script_json": kw.get("script_json", '{"hook": "x", "source_url": "https://x",'
                          '"claims": [{"text": "c", "evidence": "e"}]}'),
           "verify_json": kw.get("verify_json", ""), "source_text_hash": "",
           "video_path": kw.get("video_path", ""), "thumb_path": kw.get("thumb_path", ""),
           "title": kw.get("title", ""), "description": kw.get("description", ""),
           "reject_reason": "", "created_at": db.now_iso(), "updated_at": db.now_iso()}
    cols, vals = zip(*row.items(), strict=True)
    with conn:
        cur = conn.execute(f"INSERT INTO episodes ({','.join(cols)}) "
                           f"VALUES ({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)


async def test_health_open_and_auth_required(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    async with _client(app) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/episodes")).status_code == 401
        bad = await c.get("/episodes", headers={"Authorization": "Bearer yanlis"})
        assert bad.status_code == 401


async def test_double_run_409(tmp_path, monkeypatch):
    import asyncio

    app = _app(tmp_path, monkeypatch)
    started = asyncio.Event()

    @jobs_mod.stage("bekle")
    async def slow(ctx):
        started.set()
        await asyncio.sleep(5)

    orig_run = jobs_mod.run_job

    async def fake_run(db_path, job_id, stages, callback_url="", max_episodes=3):
        await orig_run(db_path, job_id, [slow], callback_url, max_episodes)

    monkeypatch.setattr(jobs_mod, "run_job", fake_run)
    # routes `run_job`'u modulden cagirir; yama routes'a da yansir (ayni obje).
    import devbrief.api.routes as routes_mod

    monkeypatch.setattr(routes_mod._jobs, "run_job", fake_run)
    async with _client(app) as c:
        r1 = await c.post("/run", json={}, headers=_auth())
        assert r1.status_code == 202
        await asyncio.wait_for(started.wait(), timeout=10)
        r2 = await c.post("/run", json={}, headers=_auth())
        assert r2.status_code == 409
        jid = r1.json()["job_id"]
        job = await c.get(f"/jobs/{jid}", headers=_auth())
        assert job.json()["status"] == "running"


async def test_approve_only_rendered(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    e1 = _episode(conn, status="draft")
    e2 = _episode(conn, status="rendered")
    conn.close()
    async with _client(app) as c:
        h = _auth()
        assert (await c.post(f"/episodes/{e1}/approve", headers=h)).status_code == 422
        assert (await c.post(f"/episodes/{e2}/approve", headers=h)).status_code == 200
        det = (await c.get(f"/episodes/{e2}", headers=h)).json()
        assert det["status"] == "approved"
        assert (await c.post(f"/episodes/{e2}/reject", json={"reason": "x"}, headers=h)
                ).status_code == 200


async def test_published_flow_and_package_limits(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    long_title = "B" * 200
    e = _episode(conn, status="approved", title=long_title)
    conn.close()
    async with _client(app) as c:
        h = _auth()
        pkg = (await c.get(f"/episodes/{e}/package", headers=h)).json()
        assert pkg["platforms"]["youtube"]["title"].endswith("…")
        assert "Kaynak: https://x" in pkg["platforms"]["youtube"]["description"]
        assert "Yapay zekâ" in pkg["platforms"]["youtube"]["description"]
        assert pkg["warnings"]
        body = {"results": [{"platform": "youtube", "ok": True, "url": "http://u",
                             "external_id": "e1", "error": ""}]}
        r = await c.post(f"/episodes/{e}/published", json=body, headers=h)
        assert r.json()["saved"] == 1
        det = (await c.get(f"/episodes/{e}", headers=h)).json()
        assert det["status"] == "published"
        m = await c.post("/metrics", json={"platform": "youtube", "external_id": "e1",
                                           "day": "2026-10-09", "views": 10,
                                           "avg_view_pct": 50.0, "likes": 1}, headers=h)
        assert m.json() == {"ok": True}


async def test_callback_retries_then_marks(tmp_path, monkeypatch):
    import devbrief.api.callback as cb

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://n8n/hook").mock(side_effect=[
            httpx.Response(500), httpx.Response(500),
            httpx.Response(200),
        ])
        assert await cb.post_result("http://n8n/hook", "s", {"a": 1})
        assert route.call_count == 3
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://n8n/hook").mock(return_value=httpx.Response(500))
        assert not await cb.post_result("http://n8n/hook", "s", {"a": 1})
        assert route.call_count == 3


async def test_jobs_recover_and_range(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0123456789ABCDEF")
    e = _episode(conn, status="approved", video_path=str(vid))
    with conn:
        conn.execute("INSERT INTO jobs (status, stage, created_at, updated_at)"
                     " VALUES ('running', 'x', ?, ?)", (db.now_iso(), db.now_iso()))
    conn.close()
    assert jobs_mod.recover(tmp_path / "t.db") == 1
    async with _client(app) as c:
        h = _auth()
        full = await c.get(f"/episodes/{e}/video", headers=h)
        assert full.status_code == 200 and full.headers["Accept-Ranges"] == "bytes"
        part = await c.get(f"/episodes/{e}/video", headers={**h, "range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"0123"
        assert (await c.get("/episodes/9999/video", headers=h)).status_code == 404
