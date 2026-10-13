"""Topluluk + duzeltme + A/B + seri + derleme (mock, ag yok)."""

import httpx

import devbrief.db as db
from devbrief.config import get_settings

TOKEN = "test-token"


def _api_env(monkeypatch):
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    get_settings.cache_clear()


def _app(tmp_path, monkeypatch):
    from devbrief.api.routes import create_app

    _api_env(monkeypatch)
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    conn.close()
    return create_app(dbpath)


def _client(app):
    from httpx import ASGITransport

    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _auth(h=None):
    h = dict(h or {})
    h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _episode(conn, **kw):
    row = {"cluster_id": None, "day": kw.get("day", "2026-10-09"),
           "slug": kw.get("slug", "s"), "status": kw.get("status", "rendered"),
           "script_json": kw.get("script_json", '{"hook": "x"}'),
           "title": kw.get("title", ""), "metadata_json": kw.get("metadata_json", ""),
           "video_path": "", "thumb_path": "",
           "created_at": db.now_iso(), "updated_at": db.now_iso()}
    cols, vals = zip(*row.items(), strict=True)
    with conn:
        cur = conn.execute(f"INSERT INTO episodes ({','.join(cols)}) "
                           f"VALUES ({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)


async def test_suggest_open_and_validated(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    async with _client(app) as c:
        r = await c.post("/suggest", json={"title": "Rust videosu"})
        assert r.status_code == 200 and r.json()["ok"] and r.json()["id"] == 1
        assert (await c.post("/suggest", json={"title": ""})).status_code == 422
        assert (await c.post("/suggest", json={"title": "x" * 201})).status_code == 422
        r = await c.post("/suggest", json={"title": "t", "url": "ftp://x"})
        assert r.status_code == 422
        r = await c.post("/suggest", json={"title": "t", "note": "n" * 2001})
        assert r.status_code == 422


async def test_suggestions_review_flow(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    async with _client(app) as c:
        h = _auth()
        await c.post("/suggest", json={"title": "A"})
        await c.post("/suggest", json={"title": "B"})
        assert len((await c.get("/suggestions", headers=h)).json()) == 2
        r = await c.post("/suggestions/1/review", json={"status": "reviewed"},
                         headers=h)
        assert r.json() == {"ok": True, "id": 1, "status": "reviewed"}
        only = await c.get("/suggestions?status=reviewed", headers=h)
        assert [s["id"] for s in only.json()] == [1]
        assert (await c.post("/suggestions/1/review", json={"status": "x"},
                             headers=h)).status_code == 422
        assert (await c.post("/suggestions/99/review", json={"status": "dismissed"},
                             headers=h)).status_code == 404
        assert (await c.get("/suggestions", headers={})).status_code == 401


async def test_correction_flow_and_package_note(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    e_ok = _episode(conn, status="approved", slug="ok")
    e_draft = _episode(conn, status="draft", slug="d")
    conn.close()
    async with _client(app) as c:
        h = _auth()
        assert (await c.post("/episodes/9999/correction", json={"note": "x"},
                             headers=h)).status_code == 404
        assert (await c.post(f"/episodes/{e_draft}/correction", json={"note": "x"},
                             headers=h)).status_code == 422
        assert (await c.post(f"/episodes/{e_ok}/correction", json={"note": ""},
                             headers=h)).status_code == 422
        r = await c.post(f"/episodes/{e_ok}/correction", json={"note": "surum 2.1 degil 2.0"},
                         headers=h)
        assert r.json() == {"ok": True, "id": 1}
        notes = await c.get(f"/episodes/{e_ok}/corrections", headers=h)
        assert [n["note"] for n in notes.json()] == ["surum 2.1 degil 2.0"]
        pkg = (await c.get(f"/episodes/{e_ok}/package", headers=h)).json()
        assert "Düzeltme: surum 2.1 degil 2.0" in pkg["platforms"]["youtube"]["description"]


async def test_approve_assigns_ab_group(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    e1 = _episode(conn, status="rendered", slug="a", metadata_json='{"title": "t"}')
    e2 = _episode(conn, status="rendered", slug="b", metadata_json='{"title": "t"}')
    conn.close()
    async with _client(app) as c:
        h = _auth()
        assert (await c.post(f"/episodes/{e1}/approve", headers=h)).status_code == 200
        assert (await c.post(f"/episodes/{e2}/approve", headers=h)).status_code == 200
    conn = db.connect(tmp_path / "t.db")
    groups = {r["id"]: r["ab_group"] for r in conn.execute(
        "SELECT id, ab_group FROM episodes").fetchall()}
    conn.close()
    assert groups[e1] != groups[e2] and {groups[e1], groups[e2]} <= {"A", "B"}


def test_series_cli_and_report_ab(monkeypatch, tmp_path):
    from datetime import date

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    today = date.today().isoformat()
    with conn:
        for i, (slug, grp) in enumerate((("a", "A"), ("b", "B")), start=1):
            conn.execute(
                "INSERT INTO episodes (id, cluster_id, day, slug, status, title,"
                " ab_group, created_at, updated_at) VALUES (?, NULL, ?, ?,"
                " 'published', ?, ?, 't', 't')",
                (i, today, slug, f"T{i}", grp))
            conn.execute(
                "INSERT INTO publications (id, episode_id, platform, external_id,"
                " url, published_at) VALUES (?, ?, 'youtube', ?, 'u', 't')",
                (i, i, f"y{i}"))
            conn.execute(
                "INSERT INTO metrics (publication_id, day, views, avg_view_pct, likes)"
                f" VALUES ({i}, '{today}', {100 * i}, 40.0, {i})")
    conn.close()
    runner = CliRunner()
    r = runner.invoke(app, ["report", "--metrics"])
    assert r.exit_code == 0, r.output
    assert "A: 1 bolum, 100 izlenme, %40 tutma" in r.output
    assert "B: 1 bolum, 200 izlenme, %40 tutma" in r.output
    r = runner.invoke(app, ["series", "--episode", "1", "--name", "surumler"])
    assert r.exit_code == 0 and "surumler" in r.output
    r = runner.invoke(app, ["series", "--list"])
    assert r.exit_code == 0 and "surumler: 1" in r.output
    r = runner.invoke(app, ["series", "--episode", "99", "--name", "x"])
    assert r.exit_code == 1
    r = runner.invoke(app, ["series", "--episode", "1", "--name", "x" * 61])
    assert r.exit_code == 1


def test_digest_groups_and_corrections(monkeypatch, tmp_path):
    from datetime import date

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    today = date.today().isoformat()
    script = ('{"hook": "Kanca", "impact": "Etki buyuk.",'
              ' "source_url": "https://x/kaynak"}')
    with conn:
        conn.execute(
            "INSERT INTO episodes (id, cluster_id, day, slug, status, title, series,"
            " script_json, created_at, updated_at) VALUES (1, NULL, ?, 'e1',"
            " 'published', 'T1', 'surumler', ?, 't', 't')", (today, script))
        conn.execute(
            "INSERT INTO episodes (id, cluster_id, day, slug, status, title,"
            " script_json, created_at, updated_at) VALUES (2, NULL, '2000-01-01', 'eski',"
            " 'published', 'T0', ?, 't', 't')", (script,))
        conn.execute(
            "INSERT INTO corrections (episode_id, note, created_at)"
            " VALUES (1, 'surum duzeltildi', 't')")
    conn.close()
    out = tmp_path / "bulten.md"
    r = CliRunner().invoke(
        app, ["digest", "--days", "7", "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text(encoding="utf-8")
    assert "- bolum: 1" in text  # pencere disi bolum elendi
    assert "## surumler" in text and "**Kanca**" in text
    assert "Etki buyuk." in text and "Kaynak: https://x/kaynak" in text
    assert "Düzeltme: surum duzeltildi" in text


def test_correction_cli_gates(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'approved', 't', 't')")
        epid = int(cur.lastrowid)
    conn.close()
    runner = CliRunner()
    r = runner.invoke(app, ["correction", "--episode", str(epid), "--note", "n1"])
    assert r.exit_code == 0 and "#1" in r.output
    r = runner.invoke(app, ["correction", "--episode", "9999", "--note", "n"])
    assert r.exit_code == 1
    r = runner.invoke(app, ["correction", "--episode", str(epid), "--note", " "])
    assert r.exit_code == 1


def test_suggestions_cli_lists(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    db.add_suggestion(conn, "Rust videosu", "https://x", "")
    conn.close()
    r = CliRunner().invoke(app, ["suggestions"])
    assert r.exit_code == 0 and "#1 [new] Rust videosu" in r.output
