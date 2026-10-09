import devbrief.db as db


def test_migrate_and_job_lock():
    conn = db.connect(":memory:")
    v = db.migrate(conn)
    assert v >= 1
    j1 = db.try_acquire_job(conn)
    assert j1 is not None
    assert db.try_acquire_job(conn) is None  # ikinci running alinamaz
    db.finish_job(conn, j1, "done")
    j2 = db.try_acquire_job(conn)
    assert j2 is not None


def test_llm_budget_slot():
    conn = db.connect(":memory:")
    db.migrate(conn)
    slot = db.reserve_llm_slot(conn, "t", "m", budget=1)
    db.finish_llm_slot(conn, slot, ok=True, ms=10)
    try:
        db.reserve_llm_slot(conn, "t", "m", budget=1)
    except db.BudgetExceeded:
        pass
    else:
        raise AssertionError("butce asimi bekleniyordu")


def test_clusters_roundtrip():
    conn = db.connect(":memory:")
    db.migrate(conn)
    i1 = db.upsert_item(conn, source="s", url="https://x/1", title="T1", prescore=2.0)
    i2 = db.upsert_item(conn, source="s", url="https://x/2", title="T2", prescore=1.0)
    db.save_classification(conn, i1, "release", 5, True, "n", "m")
    cid = db.create_cluster(conn, "2026-10-09", "T1", 2.0)
    db.add_cluster_item(conn, cid, i1)
    db.add_cluster_item(conn, cid, i2)
    db.add_cluster_item(conn, cid, i1)  # tekrar yazilmaz
    db.set_cluster_selected(conn, cid, True)
    got = db.get_day_clusters(conn, "2026-10-09")
    assert len(got) == 1
    assert got[0]["selected"] == 1
    assert sorted(int(x) for x in got[0]["item_ids"].split(",")) == sorted([i1, i2])
    assert got[0]["avg_importance"] == 5.0
    conn = db.connect(":memory:")
    db.migrate(conn)
    slot = db.reserve_llm_slot(conn, "t", "m", budget=1)
    db.finish_llm_slot(conn, slot, ok=True, ms=10)
    try:
        db.reserve_llm_slot(conn, "t", "m", budget=1)
    except db.BudgetExceeded:
        pass
    else:
        raise AssertionError("butce asimi bekleniyordu")


def test_upsert_item_idempotent_and_canonical():
    conn = db.connect(":memory:")
    db.migrate(conn)
    i1 = db.upsert_item(
        conn, source="rss", url="https://example.com/a", title="T", summary="S"
    )
    i2 = db.upsert_item(
        conn, source="rss", url="https://example.com/a", title="T2", summary="S"
    )
    assert i1 == i2  # ayni URL tekrar yazilmaz
    row = conn.execute("SELECT canonical_url, title FROM items WHERE id = ?", (i1,)).fetchone()
    assert row["canonical_url"] == "https://example.com/a"
    assert row["title"] == "T2"
    # utm/fragment varyanti ayri satir ama ayni canonical (kumelenmede birlesir)
    i3 = db.upsert_item(
        conn, source="rss", url="https://Example.com/a/?utm_source=x#frag", title="T"
    )
    assert i3 != i1
    c3 = conn.execute("SELECT canonical_url FROM items WHERE id = ?", (i3,)).fetchone()
    assert c3["canonical_url"] == "https://example.com/a"
    assert db.canonical_url("https://A.com:80/x/?utm_medium=y&k=1") == "https://a.com/x?k=1"


def test_get_new_items_orders_by_prescore():
    conn = db.connect(":memory:")
    db.migrate(conn)
    db.upsert_item(conn, source="s", url="https://x/1", prescore=0.1)
    db.upsert_item(conn, source="s", url="https://x/2", prescore=0.9)
    got = db.get_new_items(conn, limit=2)
    assert [r["url"] for r in got] == ["https://x/2", "https://x/1"]


def test_save_classification_and_episode_status():
    conn = db.connect(":memory:")
    db.migrate(conn)
    i = db.upsert_item(conn, source="s", url="https://x/9")
    db.save_classification(conn, i, "release", 5, True, "neden", "m")
    db.save_classification(conn, i, "security", 4, False, "n2", "m2")
    row = conn.execute("SELECT * FROM classifications WHERE item_id = ?", (i,)).fetchone()
    assert row["category"] == "security" and row["importance"] == 4
    conn.execute(
        "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
        " VALUES (NULL, '2026-10-09', 's1', 'draft', ?, ?)",
        (db.now_iso(), db.now_iso()),
    )
    ep = conn.execute("SELECT id FROM episodes WHERE slug = 's1'").fetchone()["id"]
    db.set_episode_status(conn, ep, "verified")
    st = conn.execute("SELECT status FROM episodes WHERE id = ?", (ep,)).fetchone()["status"]
    assert st == "verified"
