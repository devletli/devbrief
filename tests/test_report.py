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


def test_report_metrics_weekly(monkeypatch, tmp_path):
    from datetime import date, timedelta

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=30)).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO episodes (id, cluster_id, day, slug, status, title,"
            " created_at, updated_at) VALUES (1, NULL, '2026-10-09', 'ep1',"
            " 'published', 'T1', 't', 't')")
        conn.execute(
            "INSERT INTO episodes (id, cluster_id, day, slug, status, title,"
            " created_at, updated_at) VALUES (2, NULL, '2026-10-09', 'ep2',"
            " 'published', 'T2', 't', 't')")
        conn.execute(
            "INSERT INTO episodes (id, cluster_id, day, slug, status, title,"
            " created_at, updated_at) VALUES (3, NULL, '2026-10-09', 'ep3',"
            " 'published', 'T3', 't', 't')")
        conn.execute(
            "INSERT INTO publications (id, episode_id, platform, external_id, url,"
            " published_at) VALUES (1, 1, 'youtube', 'y1', 'u1', 't')")
        conn.execute(
            "INSERT INTO publications (id, episode_id, platform, external_id, url,"
            " published_at) VALUES (2, 1, 'tiktok', 't1', 'u2', 't')")
        conn.execute(
            "INSERT INTO publications (id, episode_id, platform, external_id, url,"
            " published_at) VALUES (3, 2, 'youtube', 'y2', 'u3', 't')")
        conn.execute(
            "INSERT INTO metrics (publication_id, day, views, avg_view_pct, likes)"
            f" VALUES (1, '{today}', 100, 40.0, 5)")
        conn.execute(
            "INSERT INTO metrics (publication_id, day, views, avg_view_pct, likes)"
            f" VALUES (1, '{old}', 9999, 10.0, 99)")
        conn.execute(
            "INSERT INTO metrics (publication_id, day, views, avg_view_pct, likes)"
            f" VALUES (2, '{today}', 50, NULL, 2)")
        conn.execute(
            "INSERT INTO metrics (publication_id, day, views, avg_view_pct, likes)"
            f" VALUES (3, '{old}', 70, 20.0, 1)")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["report", "--metrics"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "son 7 gun" in out
    assert "#1 ep1 (youtube): 100 izlenme, %40 tutma, 5 begeni" in out
    assert "#1 ep1 (tiktok): 50 izlenme, tutma yok, 2 begeni" in out
    assert "#2 ep2 (youtube): 0 izlenme" in out  # yalnizca pencere disi olcum
    assert "9999" not in out and ", 99 begeni" not in out
    assert "youtube: 100 izlenme, 2 yayin" in out
    assert "tiktok: 50 izlenme, 1 yayin" in out
    assert "#3 ep3" in out  # olcum bekleyen


def test_report_metrics_empty(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["report", "--metrics"])
    assert result.exit_code == 0, result.output
    assert "kayit yok" in result.output
