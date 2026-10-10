"""Metadata: mock writer ile sema + kayit (ag yok)."""

import devbrief.db as db
from devbrief.script import metadata as _mm
from devbrief.script.metadata import Metadata


def _meta(**over):
    d = {"title": "Vite 8.3.4 cikti https://x",
         "description": "Detaylar https://x aynnt",
         "hashtags": ["vite"], "x_post": "Vite 8.3.4 https://x",
         "linkedin_post": "Vite 8.3.4 surumu https://x"}
    d.update(over)
    return Metadata(**d)


def _script():
    return {"hook": "Vite kanca",
            "scenes": [{"voice": "Vite 8.3.4 yama surumu yayinlandi."}],
            "claims": [{"text": "8.3.4 yayinda."}],
            "impact": "Etki.", "action": "Aksiyon."}


_SRC = "Vite 8.3.4 yama surumu yayinlandi. Indirme boyutu 31 MB."


def test_check_accepts_sourced_numbers():
    assert _mm.check_metadata(_meta(), _script(), _SRC) == []


def test_check_rejects_unknown_version():
    bad = _meta(title="Vite 9.9 cikti https://x")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("9.9" in e for e in errs)


def test_hashtags_exempt_from_numbers():
    m = _meta(hashtags=["Python3", "v9.9"])
    assert _mm.check_metadata(m, _script(), _SRC) == []


def test_check_rejects_missing_link():
    bad = _meta(x_post="Vite 8.3.4 cikti", linkedin_post="Vite 8.3.4 surumu")
    errs = _mm.check_metadata(bad, _script(), _SRC)
    assert any("kaynak linki yok" in e for e in errs)


def test_hype_warns_not_rejects():
    hyped = _meta(title="Inanilmaz Vite 8.3.4 sok https://x")
    warns = _mm.check_hype(hyped, _script(), _SRC)
    assert warns == ["hype_title:inanilmaz"] or any(w.startswith("hype_title:") for w in warns)
    assert _mm.check_metadata(hyped, _script(), _SRC) == []


def test_ai_note_excluded():
    from devbrief.publish.platforms import AI_NOTE

    m = _meta(description=f"Vite 8.3.4 detay https://x. {AI_NOTE}")
    assert _mm.check_metadata(m, _script(), _SRC) == []


async def test_write_metadata_validates_and_saves(monkeypatch):
    import devbrief.script.metadata as mm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        assert prompt_name == "write_metadata_v1"
        assert "script" in variables
        return Metadata(title="Kisa baslik", description="Aciklama https://x aynnt",
                        hashtags=["a", "b"], x_post="kisa", linkedin_post="uzunca metin")

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
        " VALUES (NULL, 'd', 's', 'verified', ?, ?)", (db.now_iso(), db.now_iso()))
    meta = await mm.write_metadata(_script(), conn=conn)
    assert meta.title == "Kisa baslik"
    mm.save_metadata(conn, 1, meta)
    row = conn.execute("SELECT title, description FROM episodes WHERE id = 1").fetchone()
    assert row["title"] == "Kisa baslik" and "https://x" in row["description"]


async def test_write_metadata_rejects_missing_link(monkeypatch):
    import devbrief.script.metadata as mm

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        return Metadata(title="T", description="link yok", hashtags=[],
                        x_post="x", linkedin_post="y")

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    try:
        await mm.write_metadata(_script())
    except ValueError as e:
        assert "kaynak linki" in str(e)
    else:
        raise AssertionError("link sarti bekleniyordu")


async def test_produce_retries_then_passes(monkeypatch):
    import devbrief.script.metadata as mm

    calls = []

    async def fake_write(script, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _meta(title="Vite 9.9 cikti https://x")
        return _meta()

    monkeypatch.setattr(mm, "write_metadata", fake_write)
    meta, warns = await mm.produce(_script(), _SRC)
    assert meta.title.startswith("Vite 8.3.4") and len(calls) == 2


async def test_produce_fails_and_caps(monkeypatch):
    import devbrief.script.metadata as mm
    from devbrief.llm.client import CallCap

    async def always_bad(script, **kw):
        return _meta(title="Vite 9.9 cikti https://x")

    monkeypatch.setattr(mm, "write_metadata", always_bad)
    try:
        await mm.produce(_script(), _SRC)
    except mm.MetadataFailed as e:
        assert "9.9" in str(e)
    else:
        raise AssertionError("MetadataFailed bekleniyordu")
    # cap=0: ilk sarjda patlar, hic uretim olmaz.
    try:
        await mm.produce(_script(), _SRC, cap=CallCap(0))
    except Exception as e:
        assert "call_cap" in str(e).lower() or "CallCap" in type(e).__name__
    else:
        raise AssertionError("cap bekleniyordu")


def test_save_and_fail_helpers():
    import devbrief.script.metadata as mm

    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
        " VALUES (NULL, 'd', 's', 'verified', ?, ?)", (db.now_iso(), db.now_iso()))
    mm.save_metadata(conn, 1, _meta(), ["hype_title:sok"])
    row = conn.execute(
        "SELECT title, metadata_json, metadata_warnings FROM episodes WHERE id = 1").fetchone()
    assert row["title"].startswith("Vite 8.3.4")
    assert "hype_title:sok" in row["metadata_warnings"]
    mm.fail_metadata(conn, 1, "kotu")
    row = conn.execute("SELECT status, reject_reason FROM episodes WHERE id = 1").fetchone()
    assert (row["status"], row["reject_reason"]) == ("metadata_failed", "kotu")
