"""Pipeline hatti: tum asamalari mock IO ile kostur (ag/LLM/ffmpeg yok)."""

import devbrief.db as db
from devbrief.curate.schemas import (
    ClassifiedItem,
    ClassifyBatchOut,
    Pick,
    SelectDailyOut,
)
from devbrief.pipeline import run_all
from devbrief.script.fetch import FetchResult


def _seed(conn):
    db.upsert_item(conn, source="hn", url="https://x/1", title="Vite 8.3.4",
                   summary="yama", prescore=3.0)
    db.upsert_item(conn, source="hn", url="https://x/2", title="Node 22.19",
                   summary="surum", prescore=2.0)


async def _nocollect(*a, **k):
    return []


async def _fake_rank_run(conn, limit=40, **kw):
    rows = db.get_new_items(conn, limit=limit)
    for r in rows:
        db.save_classification(conn, r["id"], "release", 4, True, "neden", "smart")
    return ClassifyBatchOut(results=[
        ClassifiedItem(id=r["id"], category="release", importance=4,
                       affects_devs=True, reason="neden") for r in rows])


async def _fake_select_day(infos, classes, **kw):
    ids = [int(i.get("id", n)) for n, i in enumerate(infos, 1)]
    return SelectDailyOut(
        picks=[Pick(cluster_id=ids[0], merged_cluster_ids=[], why_today="bugun")],
        backup=Pick(cluster_id=ids[-1], why_today="yedek"))


def _fake_fetch(url, summary=""):
    return FetchResult(url=url, text="Vite 8.3.4 yama surumu yayinlandi.",
                       source_hash="h", low_source=False)


async def test_run_all_end_to_end(monkeypatch, tmp_path):
    import devbrief.pipeline as pl
    from devbrief.render import compose as _compose
    from devbrief.render import slides as _slides
    from devbrief.render import tts as _tts
    from devbrief.script import fetch as _fetch
    from devbrief.script import verifier as _verifier
    from devbrief.script import writer as _writer
    from devbrief.script.schemas import IndexVerdict, VerifyOut

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sources.yaml").write_text("rss: []\nhn: {}\ngithub_releases: []\n")
    monkeypatch.setattr(pl.rss, "collect", _nocollect)
    monkeypatch.setattr(pl.hn, "collect", _nocollect)
    monkeypatch.setattr(pl.github_releases, "collect", _nocollect)
    monkeypatch.setattr(pl._rank, "run", _fake_rank_run)
    monkeypatch.setattr(pl._select, "select_day", _fake_select_day)
    monkeypatch.setattr(_fetch, "fetch_article", _fake_fetch)

    from devbrief.script.schemas import Script

    sc = {
        "hook": "Vite kisa kanca",
        "scenes": [{"id": i + 1, "voice": " ".join(f"kelime{j}" for j in range(25)),
                    "on_screen": "Etiket", "visual": "title", "code": None,
                    "evidence": ["Vite 8.3.4 yama surumu yayinlandi."]}
                   for i in range(3)],
        "impact": "Etki.", "action": "Aksiyon.", "source_url": "https://x/1",
        "claims": [{"text": "8.3.4 yayinda.", "evidence": "Vite 8.3.4 yama surumu yayinlandi."}],
        "duration_estimate_s": 40,
    }

    async def _fake_write(*a, **k):
        # Dolgu icerik: src'siz legacy sekliyle dogrulanir (tesisat testi).
        return Script.model_validate(sc, context={"legacy": True})

    async def _fake_verify_llm(source_text, claims, **kw):
        return VerifyOut(verdicts=[IndexVerdict(i=0, verdict="entailed", note="ok")],
                         overall="pass")

    monkeypatch.setattr(_writer, "write_script", _fake_write)
    monkeypatch.setattr(_verifier, "verify_llm", _fake_verify_llm)

    from devbrief.script.metadata import Metadata

    async def _fake_produce(script, source_text, **kw):
        return Metadata(title="T", description="D https://x", hashtags=[],
                        x_post="x https://x", linkedin_post="y https://x"), [], {}, []

    monkeypatch.setattr(pl._meta, "produce_auto", _fake_produce)

    async def _fake_synth(scenes, voice, out_dir):
        from pathlib import Path as _P

        out = []
        for i, _s in enumerate(scenes):
            mp3 = _P(out_dir) / f"scene_{i + 1}.mp3"
            mp3.parent.mkdir(parents=True, exist_ok=True)
            mp3.write_bytes(b"x")
            out.append({"mp3": str(mp3),
                        "words": [{"word": "a", "start": 0.0, "end": 1.0}]})
        return out

    def _fake_file(*a, **k):
        from pathlib import Path as _P

        p = _P("out") / "t.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(_tts, "synth_script", _fake_synth)
    monkeypatch.setattr(_compose, "probe_duration", lambda p: 2.0)
    monkeypatch.setattr(_slides, "render_scene", _fake_file)
    monkeypatch.setattr(_slides, "render_thumb", _fake_file)

    def _fake_build(scenes, audio, slides, **kw):
        from pathlib import Path as _P

        v = _P(kw.get("out_root", "out")) / kw.get("day", "d") / kw.get("slug", "s") / "video.mp4"
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_bytes(b"v")
        return {"total_s": 6.0}

    monkeypatch.setattr(_compose, "build", _fake_build)

    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    _seed(conn)
    conn.close()

    done = await run_all(dbpath, max_episodes=3)
    assert len(done) == 2  # secilen + yedek kuemeden iki bolum
    conn = db.connect(dbpath)
    for epid in done:
        row = conn.execute(
            "SELECT status, video_path FROM episodes WHERE id = ?", (epid,)).fetchone()
        assert row["status"] == "rendered" and row["video_path"].endswith("video.mp4")
    assert conn.execute("SELECT COUNT(*) FROM clusters WHERE selected = 1").fetchone()[0] >= 1
    conn.close()
