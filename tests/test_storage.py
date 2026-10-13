"""Medya deposu: SigV4 vektor + respx mock PUT + API/CLI (gercek ag yok)."""

import httpx
import respx

import devbrief.db as db
from devbrief.config import get_settings
from devbrief.publish import storage as st

TOKEN = "test-token"


def _s3env(monkeypatch, tmp_path=None):
    monkeypatch.setenv("MEDIA_S3_ENDPOINT", "https://s3.ornek.test")
    monkeypatch.setenv("MEDIA_S3_BUCKET", "videolar")
    monkeypatch.setenv("MEDIA_S3_REGION", "us-east-1")
    monkeypatch.setenv("MEDIA_S3_ACCESS_KEY", "AKID")
    monkeypatch.setenv("MEDIA_S3_SECRET_KEY", "SECRET")
    monkeypatch.setenv("MEDIA_S3_PUBLIC_BASE", "https://medya.ornek.test")
    monkeypatch.setenv("MEDIA_S3_PREFIX", "devbrief")
    get_settings.cache_clear()


def _no_s3env(monkeypatch):
    for k in ("MEDIA_S3_ENDPOINT", "MEDIA_S3_BUCKET", "MEDIA_S3_REGION",
              "MEDIA_S3_ACCESS_KEY", "MEDIA_S3_SECRET_KEY", "MEDIA_S3_PUBLIC_BASE",
              "MEDIA_S3_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()


def test_sigv4_matches_reference_vector():
    """IAM ListUsers ornegi: imza bagimsiz referansla (botocore SigV4Auth,
    ayni girdi) birebir tutmali. Vektor degismez; imza sabitlenir."""
    headers = st.sigv4_headers(
        "GET", "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08",
        b"", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "us-east-1", service="iam", amz_date="20150830T123600Z",
        include_content_sha=False,
    )
    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=b2e4af44cfad96d9ffa3c5653674a927b9b0995c33de22e1f843745ce37c1d5e"
    )


def test_object_key_and_public_url(monkeypatch):
    _s3env(monkeypatch)
    key = st.object_key("ab" * 16, "video.mp4")
    assert key == f"devbrief/{'ab' * 16}/video.mp4"
    assert st.public_url(key) == f"https://medya.ornek.test/{key}"


def test_upload_gate_rejects(monkeypatch):
    _s3env(monkeypatch)
    ok, reason = st.upload_gate({"status": "approved"}, "live")
    assert ok and reason == ""
    ok, reason = st.upload_gate({"status": "draft"}, "live")
    assert not ok and "onay" in reason
    ok, reason = st.upload_gate({"status": "approved"}, "shadow")
    assert not ok and "shadow" in reason
    ok, reason = st.upload_gate({"status": "approved", "low_source": 1}, "live")
    assert not ok and "low_source" in reason
    ok, reason = st.upload_gate({"status": "approved", "is_demo": 1}, "live")
    assert not ok and "demo" in reason
    ok, _ = st.upload_gate({"status": "approved"}, "private")
    assert ok  # private: yukleme serbest (not ile)


async def test_upload_file_puts_with_sigv4(monkeypatch, tmp_path):
    _s3env(monkeypatch)
    f = tmp_path / "video.mp4"
    f.write_bytes(b"0123456789")
    key = "devbrief/2026-10-09/s/video.mp4"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.put(f"https://s3.ornek.test/videolar/{key}").mock(
            return_value=httpx.Response(200))
        url = await st.upload_file(f, key, "video/mp4")
        assert url == f"https://medya.ornek.test/{key}"
        assert route.call_count == 1
        req = route.calls[0].request
        assert bytes(req.content) == b"0123456789"
        assert req.headers["content-type"] == "video/mp4"
        assert req.headers["authorization"].startswith("AWS4-HMAC-SHA256")
        assert "x-amz-date" in req.headers and "x-amz-content-sha256" in req.headers


async def test_upload_file_http_error(monkeypatch, tmp_path):
    _s3env(monkeypatch)
    f = tmp_path / "video.mp4"
    f.write_bytes(b"x")
    with respx.mock(assert_all_called=False) as mock:
        mock.put("https://s3.ornek.test/videolar/k/video.mp4").mock(
            return_value=httpx.Response(403))
        try:
            await st.upload_file(f, "k/video.mp4", "video/mp4")
        except RuntimeError as e:
            assert "403" in str(e)
            return
        raise AssertionError("RuntimeError bekleniyordu")


async def test_upload_file_unconfigured(monkeypatch, tmp_path):
    _no_s3env(monkeypatch)
    assert st.storage_settings() is None
    try:
        await st.upload_file(tmp_path / "yok.mp4", "k/v.mp4", "video/mp4")
    except RuntimeError:
        return
    raise AssertionError("RuntimeError bekleniyordu")


async def test_upload_episode_files_thumb_optional(monkeypatch, tmp_path):
    _s3env(monkeypatch)
    v = tmp_path / "video.mp4"
    v.write_bytes(b"v")
    t = tmp_path / "thumb.png"
    t.write_bytes(b"t")
    with respx.mock(assert_all_called=False) as mock:
        video_route = mock.put(
            "https://s3.ornek.test/videolar/devbrief/tok123/video.mp4").mock(
            return_value=httpx.Response(200))
        thumb_route = mock.put(
            "https://s3.ornek.test/videolar/devbrief/tok123/thumb.png").mock(
            return_value=httpx.Response(200))
        both = await st.upload_episode_files("tok123", v, t)
        assert both["video_url"].endswith("/video.mp4")
        assert both["thumb_url"].endswith("/thumb.png")
        assert video_route.call_count == 1 and thumb_route.call_count == 1
        only = await st.upload_episode_files("tok123", v, None)
        assert only["thumb_url"] is None
        assert video_route.call_count == 2 and thumb_route.call_count == 1


def _api_env(monkeypatch):
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    get_settings.cache_clear()


_n = {"i": 0}


def _seed_episode(dbpath, status="approved", video=b"v", thumb=b"t"):
    from pathlib import Path as _P

    _n["i"] += 1
    conn = db.connect(dbpath)
    db.migrate(conn)
    vpath, tpath = "", ""
    if video is not None:
        vpath = str(dbpath.parent / f"video{_n['i']}.mp4")
        _P(vpath).write_bytes(video)
    if thumb is not None:
        tpath = str(dbpath.parent / f"thumb{_n['i']}.png")
        _P(tpath).write_bytes(thumb)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, video_path,"
            " thumb_path, created_at, updated_at)"
            " VALUES (NULL, '2026-10-09', ?, ?, ?, ?, ?, ?)",
            (f"s{_n['i']}", status, vpath, tpath, db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    conn.close()
    return epid


async def test_publish_media_flow(monkeypatch, tmp_path):
    import httpx as _httpx
    from httpx import ASGITransport

    from devbrief.api.routes import create_app

    _api_env(monkeypatch)
    app = create_app(tmp_path / "t.db")
    h = {"Authorization": f"Bearer {TOKEN}"}
    e_draft = _seed_episode(tmp_path / "t.db", status="draft")

    async def _post(client, epid):
        return await client.post(f"/episodes/{epid}/publish-media", headers=h)

    async with _httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://t") as c:
        assert (await _post(c, 9999)).status_code == 404
        assert (await _post(c, e_draft)).status_code == 409
        assert "onay" in (await _post(c, e_draft)).json()["detail"]
        e_ok = _seed_episode(tmp_path / "t.db", status="approved")
        assert (await _post(c, e_ok)).status_code == 409  # depolama yok
        e_nofile = _seed_episode(tmp_path / "t.db", status="approved", video=None,
                                 thumb=None)
        _s3env(monkeypatch)
        assert (await _post(c, e_nofile)).status_code == 404
        with respx.mock(assert_all_called=False) as mock:
            video_route = mock.put(
                url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/video\.mp4",
            ).mock(return_value=httpx.Response(200))
            mock.put(
                url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/thumb\.png",
            ).mock(return_value=httpx.Response(200))
            r = await _post(c, e_ok)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] and body["video_url"].endswith("/video.mp4")
            assert body["thumb_url"].endswith("/thumb.png")
            assert video_route.call_count == 1
            put_url = str(video_route.calls[0].request.url)
            assert "/s2/" not in put_url and "2026-10-09" not in put_url  # slug/gun yok
            # yeniden yukleme ayni tokeni kullanir
            r2 = await _post(c, e_ok)
            assert r2.status_code == 200 and r2.json()["video_url"] == body["video_url"]
            assert video_route.call_count == 2


async def test_publish_media_gates_and_delete(monkeypatch, tmp_path):
    import httpx as _httpx
    from httpx import ASGITransport

    from devbrief.api.routes import create_app

    _api_env(monkeypatch)
    monkeypatch.setenv("PUBLISH_MODE", "shadow")
    get_settings.cache_clear()
    app = create_app(tmp_path / "t.db")
    e_ok = _seed_episode(tmp_path / "t.db", status="approved")
    e_demo = _seed_episode(tmp_path / "t.db", status="approved")
    conn = db.connect(tmp_path / "t.db")
    with conn:
        conn.execute("UPDATE episodes SET is_demo = 1 WHERE id = ?", (e_demo,))
    conn.close()
    h = {"Authorization": f"Bearer {TOKEN}"}

    async with _httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://t") as c:
        r = await c.post(f"/episodes/{e_ok}/publish-media", headers=h)
        assert r.status_code == 409 and "shadow" in r.json()["detail"]
        _s3env(monkeypatch)
        monkeypatch.setenv("PUBLISH_MODE", "live")
        monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
        get_settings.cache_clear()
        r = await c.post(f"/episodes/{e_demo}/publish-media", headers=h)
        assert r.status_code == 409 and "demo" in r.json()["detail"]
        with respx.mock(assert_all_called=False) as mock:
            put_route = mock.put(
                url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/",
            ).mock(return_value=httpx.Response(200))
            del_route = mock.delete(
                url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/",
            ).mock(return_value=httpx.Response(204))
            r = await c.post(f"/episodes/{e_ok}/publish-media", headers=h)
            assert r.status_code == 200, r.text
            assert put_route.call_count == 2  # video + kapak
            conn = db.connect(tmp_path / "t.db")
            row = conn.execute("SELECT media_token, media_uploaded_at FROM episodes"
                               " WHERE id = ?", (e_ok,)).fetchone()
            conn.close()
            assert len(row["media_token"]) == 32 and row["media_uploaded_at"]
            d = await c.delete(f"/episodes/{e_ok}/publish-media", headers=h)
            assert d.status_code == 200, d.text
            assert len(d.json()["deleted"]) == 2
            assert del_route.call_count == 2
            conn = db.connect(tmp_path / "t.db")
            row = conn.execute("SELECT media_uploaded_at FROM episodes WHERE id = ?",
                               (e_ok,)).fetchone()
            conn.close()
            assert row["media_uploaded_at"] is None
        monkeypatch.setenv("PUBLISH_MODE", "private")
        get_settings.cache_clear()
        with respx.mock(assert_all_called=False) as mock:
            mock.put(
                url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/",
            ).mock(return_value=httpx.Response(200))
            r = await c.post(f"/episodes/{e_ok}/publish-media", headers=h)
            assert r.status_code == 200, r.text
            assert r.json().get("note") == "yalnizca private modda"
        assert (await c.delete("/episodes/9999/publish-media", headers=h)
                ).status_code == 404


async def test_media_prune_and_dry_run(monkeypatch, tmp_path):
    from devbrief.publish import storage as _storage

    _s3env(monkeypatch)
    conn = db.connect(":memory:")
    db.migrate(conn)
    old = "2020-01-01T00:00:00+00:00"
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, media_token,"
            " media_uploaded_at, created_at, updated_at)"
            " VALUES (NULL, 'd', 'eski', 'approved', 'aa0123456789abcdef0123456789ab',"
            f" '{old}', 't', 't')")
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, media_token,"
            " media_uploaded_at, created_at, updated_at)"
            " VALUES (NULL, 'd', 'yeni', 'approved', 'bb0123456789abcdef0123456789ab',"
            " '2999-01-01T00:00:00+00:00', 't', 't')")
    with respx.mock(assert_all_called=False) as mock:
        del_route = mock.delete(
            url__regex=r"https://s3\.ornek\.test/videolar/devbrief/aa012.*",
        ).mock(return_value=httpx.Response(204))
        out = await _storage.prune_expired_media(conn)
        assert out["episodes"] == [1] and len(out["deleted"]) == 2 and not out["errors"]
        assert del_route.call_count == 2
        row = conn.execute("SELECT media_uploaded_at, media_token FROM episodes"
                           " WHERE id = 1").fetchone()
        assert row["media_uploaded_at"] is None and row["media_token"]
        row = conn.execute("SELECT media_uploaded_at FROM episodes WHERE id = 2"
                           ).fetchone()
        assert row["media_uploaded_at"] is not None
    conn.close()
    # dry-run: ag cagrisi yok, imza+URL basiliyor
    from typer.testing import CliRunner

    from devbrief.cli import app as cli_app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    conn = db.connect()
    db.migrate(conn)
    v = tmp_path / "video.mp4"
    v.write_bytes(b"v")
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, video_path,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'approved', ?, ?, ?)",
            (str(v), db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    conn.close()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.put(
            url__regex=r"https://s3\.ornek\.test/.*",
        ).mock(return_value=httpx.Response(200))
        route2 = mock.delete(
            url__regex=r"https://s3\.ornek\.test/.*").mock(
            return_value=httpx.Response(204))
        r = CliRunner().invoke(
            cli_app, ["media-upload", "--episode", str(epid), "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "dry-run:" in r.output and "gonderilmedi" in r.output
        assert route.call_count == 0 and route2.call_count == 0


def test_cli_media_upload(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _s3env(monkeypatch)
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    get_settings.cache_clear()
    conn = db.connect()
    db.migrate(conn)
    v = tmp_path / "video.mp4"
    v.write_bytes(b"v")
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, video_path,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'approved', ?, ?, ?)",
            (str(v), db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    conn.close()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.put(
            url__regex=r"https://s3\.ornek\.test/videolar/devbrief/[0-9a-f]{32}/video\.mp4",
        ).mock(return_value=httpx.Response(200))
        r = CliRunner().invoke(app, ["media-upload", "--episode", str(epid)])
    assert r.exit_code == 0, r.output
    import re as _re

    assert route.call_count == 1
    put_url = str(route.calls[0].request.url)
    assert _re.search(r"devbrief/[0-9a-f]{32}/video\.mp4", put_url)
    assert "/d/s/" not in put_url  # slug/gun anahtarda yok
    assert _re.search(r"https://medya\.ornek\.test/devbrief/[0-9a-f]{32}/video\.mp4",
                      r.output)
    r = CliRunner().invoke(app, ["media-upload", "--episode", "9999"])
    assert r.exit_code == 1 and "yok" in r.output


def test_cli_media_gates_selftest_delete_prune(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app as cli_app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _s3env(monkeypatch)
    conn = db.connect()
    db.migrate(conn)
    v = tmp_path / "video.mp4"
    v.write_bytes(b"v")
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, video_path,"
            " created_at, updated_at) VALUES (NULL, 'd', 'sdemo', 'approved', ?, ?, ?)",
            (str(v), db.now_iso(), db.now_iso()))
        e_demo = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, video_path,"
            " created_at, updated_at) VALUES (NULL, 'd', 'splain', 'approved', ?, ?, ?)",
            (str(v), db.now_iso(), db.now_iso()))
        e_plain = int(cur.lastrowid)
        conn.execute("UPDATE episodes SET is_demo = 1 WHERE id = ?", (e_demo,))
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, media_token,"
            " media_uploaded_at, created_at, updated_at)"
            " VALUES (NULL, 'd', 'sold', 'approved', 'cc0123456789abcdef0123456789ab',"
            " '2020-01-01T00:00:00+00:00', 't', 't')")
    conn.close()
    runner = CliRunner()
    with respx.mock(assert_all_called=False) as mock:
        put_self = mock.put(
            url__regex=r"https://s3\.ornek\.test/videolar/devbrief/selftest/.*",
        ).mock(return_value=httpx.Response(200))
        del_self = mock.delete(
            url__regex=r"https://s3\.ornek\.test/videolar/devbrief/selftest/.*",
        ).mock(return_value=httpx.Response(204))
        r = runner.invoke(cli_app, ["media-upload", "--episode", str(e_plain),
                                    "--selftest"])
        assert r.exit_code == 1 and "is_demo" in r.output
        r = runner.invoke(cli_app, ["media-upload", "--episode", str(e_demo),
                                    "--selftest"])
        assert r.exit_code == 0, r.output
        assert "selftest: tamam" in r.output
        assert put_self.call_count == 1 and del_self.call_count == 1
        assert "TEST-probe" in str(put_self.calls[0].request.url)
    monkeypatch.setenv("PUBLISH_MODE", "shadow")
    get_settings.cache_clear()
    r = runner.invoke(cli_app, ["media-upload", "--episode", str(e_plain)])
    assert r.exit_code == 1 and "shadow" in r.output
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    get_settings.cache_clear()
    with respx.mock(assert_all_called=False) as mock:
        del_route = mock.delete(
            url__regex=r"https://s3\.ornek\.test/.*",
        ).mock(return_value=httpx.Response(204))
        r = runner.invoke(cli_app, ["media-delete", "--episode", str(e_plain)])
        assert r.exit_code == 0, r.output
        assert "kayit yok" in r.output and del_route.call_count == 0
    with respx.mock(assert_all_called=False) as mock:
        del_route = mock.delete(
            url__regex=r"https://s3\.ornek\.test/.*",
        ).mock(return_value=httpx.Response(204))
        r = runner.invoke(cli_app, ["media-prune"])
        assert r.exit_code == 0, r.output
        assert "2 nesne" in r.output and del_route.call_count == 2


def test_deploy_check_storage(monkeypatch, tmp_path):
    from devbrief import deploy as _deploy

    assert _deploy.check_storage({})[0].name == "depolama-kapali"
    partial = {"MEDIA_S3_ENDPOINT": "https://x", "MEDIA_S3_BUCKET": "b"}
    assert _deploy.check_storage(partial)[0].level == "FAIL"
    full = {"MEDIA_S3_ENDPOINT": "https://x", "MEDIA_S3_BUCKET": "b",
            "MEDIA_S3_ACCESS_KEY": "AKIDORNEK123", "MEDIA_S3_SECRET_KEY": "GIZLIORNEK456",
            "MEDIA_S3_PUBLIC_BASE": "https://m.ornek", "PUBLISH_MODE": "shadow"}
    by_name = {f.name: f for f in _deploy.check_storage(full, n8n_dir=tmp_path)}
    assert by_name["depolama-shadow"].level == "WARN"
    assert by_name["depolama-listeleme"].level == "WARN"
    nd = tmp_path / "n8n"
    nd.mkdir()
    (nd / "ak.json").write_text('{"k": "GIZLIORNEK456"}', encoding="utf-8")
    by_name = {f.name: f for f in _deploy.check_storage(full, n8n_dir=nd)}
    assert by_name["depolama-sir"].level == "FAIL"
    live = dict(full, PUBLISH_MODE="live")
    assert _deploy.check_storage(live, n8n_dir=tmp_path)[0].level == "PASS"
