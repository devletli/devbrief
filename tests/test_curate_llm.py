"""Curate LLM'li kisimlar: sahte complete_json ile (ag yok)."""

import devbrief.db as db
from devbrief.curate import rank, schemas, select

_TOPICS = ["vite surum notlari", "python dil ozellikleri", "keman resital programi",
             "uzay roket firlatma", "zeytinyagli yemek tarifi", "elektrikli araba motoru",
             "satranc turnuva sonucu", "caz festival bileti", "dagcilik rota rehberi",
             "seramik firin teknigi"]


def _db_items(n: int):
    conn = db.connect(":memory:")
    db.migrate(conn)
    for i in range(n):
        db.upsert_item(conn, source="hn" if i % 2 else "rss", url=f"https://x/{i}",
                       title=_TOPICS[i % len(_TOPICS)] + f" {i // len(_TOPICS)}",
                       summary="ozet", prescore=float(n - i))
    return conn


async def _fake_classify(task, prompt_name, variables, schema, **kw):
    import re

    ids = [int(x) for x in re.findall(r"\[(\d+)\]", variables["items"])]
    return schemas.ClassifyBatchOut(results=[
        schemas.ClassifiedItem(id=i, category="release", importance=4,
                               affects_devs=True, reason="neden") for i in ids] + [
        schemas.ClassifiedItem(id=999999, category="ai", importance=5,
                               affects_devs=True, reason="halusinasyon")])


async def test_rank_batches_and_drops_unknown_ids(monkeypatch):
    monkeypatch.setattr(rank, "complete_json", _fake_classify)
    conn = _db_items(25)
    out = await rank.run(conn, limit=40)
    assert len(out.results) == 27  # 25 + batch basina 1 halusinasyon (yazilmaz)
    n = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    assert n == 25
    calls = {"n": 0}

    async def counting(*a, **k):
        calls["n"] += 1
        return await _fake_classify(*a, **k)

    monkeypatch.setattr(rank, "complete_json", counting)
    conn2 = _db_items(25)
    await rank.run(conn2, limit=40)
    assert calls["n"] == 2  # 20 + 5


async def _fake_select(task, prompt_name, variables, schema, **kw):
    assert "clusters" in variables
    return schemas.SelectDailyOut(
        picks=[schemas.Pick(cluster_id=1, merged_cluster_ids=[2, 999], why_today="bugun onemli")],
        backup=schemas.Pick(cluster_id=3, why_today="yedek"),
    )


async def test_select_persists_and_filters_merged(monkeypatch):
    monkeypatch.setattr(select, "complete_json", _fake_select)
    conn = _db_items(6)
    rows = db.get_new_items(conn, limit=40)
    infos = select.make_clusters(rows)
    assert len(infos) >= 1
    persisted = select.persist_clusters(conn, "2026-10-09", infos)
    classes = {}
    out = await select.select_day(persisted, classes, conn=conn)
    assert out.picks[0].merged_cluster_ids == [2]  # 999 elendi
    select.apply_selection(conn, out)
    sel = {r["id"]: r["selected"]
           for r in conn.execute("SELECT id, selected FROM clusters")}
    assert sel[1] == 1 and sel[3] == 1
    assert all(v == 0 for k, v in sel.items() if k not in (1, 3))


def test_persist_replaces_day_clusters():
    """Ayni gun ikinci yazim ikiz uretmez; bolumlu kume korunur."""
    conn = _db_items(6)
    rows = db.get_new_items(conn, limit=40)
    infos = select.make_clusters(rows)
    first = select.persist_clusters(conn, "2026-10-09", infos)
    n1 = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    assert n1 == len(first) and n1 >= 1
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status,"
            " created_at, updated_at) VALUES (?, '2026-10-09', 'ep-korumali',"
            " 'failed', 't', 't')", (first[0]["id"],))
    second = select.persist_clusters(conn, "2026-10-09", infos)
    n2 = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    assert n2 == len(second) + 1  # korumali + yeni kume
    kept = conn.execute("SELECT id FROM clusters WHERE id = ?",
                        (first[0]["id"],)).fetchone()
    assert kept is not None  # bolumlu kume silinmedi
    assert len(second) == len(first)


def test_curate_dry_run_writes_nothing(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.setattr(rank, "complete_json", _fake_classify)
    monkeypatch.setattr(select, "complete_json", _fake_select)
    monkeypatch.chdir(tmp_path)
    # CliRunner gecici dizinde kosar; DB oraya acilir, disari yazilmaz.
    runner = CliRunner()
    (tmp_path / "sources.yaml").write_text("rss: []\nhn: {}\ngithub_releases: []\n")
    (tmp_path / "data").mkdir()
    result = runner.invoke(app, ["curate", "--dry-run", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 0
