"""Guvenli devreye alma: modlar, demo, callback basligi, yedek, deploy-check (mock)."""

import httpx
import respx

import devbrief.db as db
from devbrief.config import Settings
from devbrief.publish import backup as _backup
from devbrief.publish import platforms as _plat

EP = {"id": 1, "slug": "s", "title": "Baslik",
      "script_json": '{"hook": "Kanca", "source_url": "https://x"}'}


def test_modes_and_demo():
    shadow = _plat.build_package(EP, "http://v", mode="shadow")
    assert shadow["publish_allowed"] is False and shadow["platforms"] == {}
    priv = _plat.build_package(EP, "http://v", mode="private")
    assert set(priv["platforms"]) == {"youtube"}
    assert priv["platforms"]["youtube"]["privacy"] == "private"
    live = _plat.build_package(EP, "http://v", mode="live")
    assert set(live["platforms"]) > {"youtube"}
    assert live["platforms"]["youtube"]["privacy"] == "public"
    demo = _plat.build_package({**EP, "is_demo": 1}, "http://v", mode="live")
    assert demo["platforms"]["youtube"]["privacy"] == "private"
    assert any("demo" in w for w in demo["warnings"])
    s = Settings(PUBLISH_MODE="live", LIVE_CONFIRM="")
    assert s.effective_mode()[0] == "private"
    assert Settings(PUBLISH_MODE="live", LIVE_CONFIRM="EVET_CANLI").effective_mode()[0] == "live"
    assert Settings(PUBLISH_MODE="bozuk").effective_mode()[0] == "shadow"


async def test_shadow_published_409(tmp_path, monkeypatch):
    from httpx import ASGITransport

    from devbrief.api.routes import create_app

    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "t")
    monkeypatch.setenv("PUBLISH_MODE", "shadow")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
            " VALUES (NULL, 'd', 's', 'approved', ?, ?)", (db.now_iso(), db.now_iso()))
    conn.close()
    app = create_app(dbpath)
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        h = {"Authorization": "Bearer t"}
        r = await c.post("/episodes/1/published", json={"results": []}, headers=h)
        assert r.status_code == 409
        pkg = (await c.get("/episodes/1/package", headers=h)).json()
        assert pkg["publish_allowed"] is False and pkg["publish_mode"] == "shadow"


async def test_callback_sends_secret_header():
    from devbrief.api import callback as _cb

    seen = {}
    with respx.mock(assert_all_called=False) as mock:
        def _cap(request):
            seen.update(request.headers)
            return httpx.Response(200)

        mock.post("http://n8n/hook").mock(side_effect=_cap)
        assert await _cb.post_result("http://n8n/hook", "gizli", {"a": 1})
    assert seen.get("x-callback-secret") == "gizli"


def test_backup_restore_integrity_with_writes(tmp_path):
    import threading

    src = tmp_path / "a.db"
    conn = db.connect(src)
    db.migrate(conn)
    db.upsert_item(conn, source="s", url="https://x/0", title="T0")
    conn.close()
    stop = threading.Event()

    def _writer():
        c = db.connect(src)
        i = 0
        while not stop.is_set() and i < 50:
            db.upsert_item(c, source="s", url=f"https://x/w{i}", title=f"W{i}")
            i += 1
        c.close()

    th = threading.Thread(target=_writer)
    th.start()
    dest = _backup.take_backup(src, tmp_path / "backups")
    stop.set()
    th.join()
    assert dest.is_file() and _backup.check_integrity(dest)
    restored = tmp_path / "r.db"
    restored.write_bytes(b"")
    bak = _backup.restore(dest, restored)
    assert bak.is_file() and _backup.check_integrity(restored)
    c2 = db.connect(restored)
    n = c2.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    c2.close()
    assert n >= 1
    assert not _backup.check_integrity(tmp_path / "yok.db")


def test_backup_rotation(tmp_path):
    import time

    d = tmp_path / "b"
    d.mkdir()
    old = d / "devbrief-20200101-0000.db"
    old.write_bytes(b"x")
    ancient = time.time() - 8 * 86400
    import os

    os.utime(old, (ancient, ancient))
    assert _backup.rotate(d) == [old.name]
    assert not old.exists()


def test_deploy_check_rules(tmp_path):
    from devbrief import deploy as _deploy

    bad_compose = tmp_path / "c.yml"
    bad_compose.write_text(
        "services:\n  devbrief-api:\n    image: x\n    ports: ['8000:8000']\n"
        "    volumes: []\n  n8n:\n    image: n8nio/n8n:latest\n", encoding="utf-8")
    levels = {f.name: f.level for f in _deploy.check_compose(bad_compose)}
    assert levels["n8n-imaji"] == "FAIL" and levels["api-port"] == "FAIL"
    assert levels["volume-data"] == "FAIL"
    files = [".env", "src/a.py", "data/x.db"]
    got = {f.name: f.level for f in _deploy.check_git(files, gitignore_text="out/\n")}
    assert got["git-sir"] == "FAIL" and got["ignore-.env"] == "FAIL"
    env = {"DEVBRIEF_API_TOKEN": "kisa"}
    assert any(f.level == "FAIL" for f in _deploy.check_env(env))
    nd = tmp_path / "n8n"
    nd.mkdir()
    (nd / "bad.json").write_text(
        '{"nodes": [{"name": "A", "type": "t"}], "connections": {},'
        ' "x": "sk-or-SECRET xyz, 12345678:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,'
        ' \\"privacyStatus\\": \\"public\\" DEGISTIR"}', encoding="utf-8")
    (nd / "bozuk.json").write_text("{degil", encoding="utf-8")
    findings = _deploy.check_n8n(nd)
    by_name = {}
    for f in findings:
        by_name.setdefault(f.name, []).append(f.level)
    assert "FAIL" in by_name.get("n8n-json:bozuk.json", [])
    assert "FAIL" in by_name.get("n8n-sir:bad.json", [])
    assert "FAIL" in by_name.get("n8n-public:bad.json", [])
    assert "n8n-yertutucu:bad.json" in by_name


def test_seed_demo_episode_mocked(monkeypatch, tmp_path):
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app
    from devbrief.render import compose as _compose
    from devbrief.render import slides as _slides
    from devbrief.render import tts as _tts

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "s:free")

    async def _fake_synth(scenes, voice, out_dir):
        out = []
        for i, _ in enumerate(scenes):
            mp3 = Path(out_dir) / f"s{i}.mp3"
            mp3.parent.mkdir(parents=True, exist_ok=True)
            mp3.write_bytes(b"x")
            out.append({"mp3": str(mp3), "words": [{"word": "a", "start": 0.0, "end": 1.0}]})
        return out

    def _fake_png(*a, **k):
        p = Path("out") / "t.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(_tts, "synth_script", _fake_synth)
    monkeypatch.setattr(_compose, "probe_duration", lambda p: 2.0)
    monkeypatch.setattr(_slides, "render_scene", _fake_png)
    monkeypatch.setattr(_slides, "render_thumb", _fake_png)
    monkeypatch.setattr(_compose, "build",
                        lambda *a, **k: {"total_s": 8.0})
    fixture = str(Path(__file__).parent / "fixtures" / "sample_script.json")
    result = CliRunner().invoke(app, ["seed-demo-episode", "--fixture", fixture])
    assert result.exit_code == 0, result.output
    conn = db.connect()
    row = conn.execute("SELECT status, is_demo, title FROM episodes").fetchone()
    assert row["status"] == "rendered" and row["is_demo"] == 1
    assert row["title"].startswith("[TEST]")
    conn.close()
