"""Uretim hatti: collect -> curate -> script -> render (idempotent).

Her asama DB'deki durumu okuyup yazar; ayni girdiyle tekrar kosum
cift kayit uretmez. jobs.run_job asamalari buradan alir (`make_stages`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import devbrief.db as db
from devbrief.api import jobs as _jobs
from devbrief.collect import arxiv, github_releases, hn, rss
from devbrief.collect.base import FetchError
from devbrief.config import get_settings, load_sources
from devbrief.curate import prefilter as _pf
from devbrief.curate import rank as _rank
from devbrief.curate import select as _select
from devbrief.render import compose as _compose
from devbrief.render import slides as _slides
from devbrief.render import subtitles as _subs
from devbrief.render import tts as _tts
from devbrief.script import fetch as _fetch
from devbrief.script import injection as _inj
from devbrief.script import metadata as _meta
from devbrief.script import verifier as _verifier
from devbrief.script import writer as _writer


def today() -> str:
    return datetime.now(UTC).date().isoformat()


# Curate payi (baz): siniflandirma ~2 batch (20'serli) + secim 1; onarimlar haric.
CURATE_CALLS = 3


def run_call_cap(max_episodes: int = 3) -> int:
    """Kosu butcesi: max_episodes x bolum kotasi + curate payi.

    Bolum kotasi `verifier.EPISODE_CALL_CAP` ile TEK kaynaktan gelir;
    tutarsizlik testle kilitlidir (`test_run_cap_consistency`).
    """
    return int(max_episodes) * _verifier.EPISODE_CALL_CAP + CURATE_CALLS


def source_weights() -> dict[str, float]:
    cfg = load_sources()
    weights: dict[str, float] = {}
    for f in cfg.get("rss") or []:
        if isinstance(f, dict) and f.get("name"):
            weights[str(f["name"])] = float(f.get("weight", 0.5))
    for g in cfg.get("github_releases") or []:
        if isinstance(g, dict) and g.get("repo"):
            weights[f"github:{g['repo']}"] = float(g.get("weight", 0.5))
    ax = cfg.get("arxiv")
    if isinstance(ax, dict):
        for c in ax.get("categories") or []:
            weights[f"arxiv:{c}"] = float(ax.get("weight", 0.5))
    weights.setdefault("hn", 0.7)
    return weights


def _hn_points(raw_json: str) -> int:
    try:
        return int((json.loads(raw_json or "{}")).get("points", 0))
    except (ValueError, AttributeError):
        return 0


@_jobs.stage("collect")
async def collect_stage(ctx: dict) -> None:
    get_settings.cache_clear()
    token = get_settings().github_token or None
    cfg = load_sources()
    conn = db.connect(ctx["db_path"])
    try:
        db.migrate(conn)
        for f in cfg.get("rss") or []:
            if not isinstance(f, dict) or f.get("enabled", True) is False:
                continue
            name = str(f.get("name", f.get("url", "?")))
            try:
                items = await rss.collect({"name": name, "url": f["url"], "max_age_hours": 30})
                for it in items:
                    db.upsert_item(conn, source=name, url=it.url, title=it.title,
                                   summary=it.summary, published_at=it.published_at,
                                   raw_json=it.raw_json)
                db.log_source_run(conn, name, True, len(items))
            except (FetchError, KeyError) as e:  # noqa: BLE001 - kaynak izole
                db.log_source_run(conn, name, False, 0, str(e))
        try:
            hn_items = await hn.collect(dict(cfg.get("hn") or {}))
            for it in hn_items:
                db.upsert_item(conn, source="hn", url=it.url, title=it.title,
                               summary=it.summary, published_at=it.published_at,
                               raw_json=it.raw_json)
            db.log_source_run(conn, "hn", True, len(hn_items))
        except FetchError as e:  # noqa: BLE001
            db.log_source_run(conn, "hn", False, 0, str(e))
        for g in cfg.get("github_releases") or []:
            if not isinstance(g, dict) or not g.get("repo"):
                continue
            name = f"github:{g['repo']}"
            try:
                items = await github_releases.collect(dict(g), token=token)
                for it in items:
                    db.upsert_item(conn, source=name, url=it.url, title=it.title,
                                   summary=it.summary, published_at=it.published_at,
                                   raw_json=it.raw_json)
                db.log_source_run(conn, name, True, len(items))
            except (FetchError, KeyError) as e:  # noqa: BLE001
                db.log_source_run(conn, name, False, 0, str(e))
        ax = cfg.get("arxiv")
        if isinstance(ax, dict) and ax.get("enabled", True) is not False:
            try:
                items = await arxiv.collect(dict(ax))
                per_cat: dict[str, int] = {}
                for it in items:
                    db.upsert_item(conn, source=it.source, url=it.url, title=it.title,
                                   summary=it.summary, published_at=it.published_at,
                                   raw_json=it.raw_json)
                    per_cat[it.source] = per_cat.get(it.source, 0) + 1
                for name, n in sorted(per_cat.items()):
                    db.log_source_run(conn, name, True, n)
            except (FetchError, KeyError) as e:  # noqa: BLE001
                db.log_source_run(conn, "arxiv", False, 0, str(e))
    finally:
        conn.close()


@_jobs.stage("curate")
async def curate_stage(ctx: dict) -> None:
    s = get_settings()
    weights = source_weights()
    conn = db.connect(ctx["db_path"])
    try:
        db.migrate(conn)
        for r in db.get_new_items(conn, limit=500):
            total, _ = _pf.score(
                r["title"] or "", r["summary"] or "", source=str(r["source"] or ""),
                source_weight=weights.get(r["source"], 0.5),
                hn_points=_hn_points(r["raw_json"] or ""),
                published_at=r["published_at"], w_source=s.prefilter_w_source,
                w_pop=s.prefilter_w_pop, w_fresh=s.prefilter_w_fresh, w_kw=s.prefilter_w_kw)
            db.update_prescore(conn, int(r["id"]), total)
        classified = await _rank.run(conn, limit=40)
        classes = {c.id: {"importance": c.importance, "category": c.category}
                   for c in classified.results}
        rows = db.get_new_items(conn, limit=40)
        infos = _select.make_clusters(rows, jaccard_thr=s.dedupe_jaccard,
                                      difflib_thr=s.dedupe_difflib, max_size=s.dedupe_max_size)
        infos = _select.persist_clusters(conn, today(), infos)
        out = await _select.select_day(infos, classes, conn=conn)
        _select.apply_selection(conn, out)
    finally:
        conn.close()


@_jobs.stage("script")
async def script_stage(ctx: dict) -> None:
    max_episodes = int(ctx.get("max_episodes") or 3)
    conn = db.connect(ctx["db_path"])
    try:
        db.migrate(conn)
        day = today()
        for r in conn.execute(
                "SELECT id, topic FROM clusters WHERE day = ? AND selected = 1", (day,)):
            exists = conn.execute(
                "SELECT id FROM episodes WHERE cluster_id = ?", (r["id"],)).fetchone()
            if exists:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", str(r["topic"] or "").lower()).strip("-")[:50]
            slug = f"{slug}-c{r['id']}" if slug else f"gun-{day}-c{r['id']}"
            with conn:
                conn.execute(
                    "INSERT INTO episodes (cluster_id, day, slug, status, created_at,"
                    " updated_at) VALUES (?, ?, ?, 'draft', ?, ?)",
                    (r["id"], day, slug, db.now_iso(), db.now_iso()))
        ep_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM episodes WHERE day = ? AND status = 'draft'"
            " ORDER BY id ASC LIMIT ?", (day, max_episodes))]
    finally:
        conn.close()
    for epid in ep_ids:
        await script_episode(ctx["db_path"], epid)


async def script_episode(db_path: str, episode_id: int) -> str:
    """Tek bolum: cek -> yaz -> dogrula. Donus: durum."""
    conn = db.connect(db_path)
    try:
        db.migrate(conn)
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            return "missing"
        day, slug = row["day"] or "undated", row["slug"] or f"ep-{episode_id}"
        target = Path("out") / day / slug / "fetch.json"
        source_text, source_url = "", ""
        if target.exists():
            try:
                saved = json.loads(target.read_text(encoding="utf-8"))
                source_text, source_url = saved.get("text", ""), saved.get("url", "")
            except ValueError:
                pass
        if not source_text:
            items = conn.execute(
                "SELECT i.url, i.summary FROM cluster_items c JOIN items i ON i.id = c.item_id"
                " WHERE c.cluster_id = ? ORDER BY i.id ASC LIMIT 3", (row["cluster_id"],))
            items = items.fetchall()
            if not items or not items[0]["url"]:
                db.set_episode_status(conn, episode_id, "failed", reason="kaynak URL yok")
                return "failed"
            res = _fetch.fetch_article(items[0]["url"], items[0]["summary"] or "")
            source_text, source_url = res.text, res.url
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(
                {"url": res.url, "text": res.text, "source_hash": res.source_hash,
                 "low_source": res.low_source, "reason": res.reason},
                ensure_ascii=False, indent=2), encoding="utf-8")
        if not source_text.strip():
            db.set_episode_status(conn, episode_id, "failed", reason="kaynak metin bos")
            return "failed"
        conn.execute("UPDATE episodes SET source_text_hash = ?, updated_at = ? WHERE id = ?",
                     (_fetch.text_hash(source_text), db.now_iso(), episode_id))
        conn.commit()
        clean_text, removed = _inj.scan(source_text)
        if removed:
            _inj.record_flag(conn, episode_id, removed)
        try:
            first = await _writer.write_script(clean_text, source_url, conn=conn, removed=removed)
        except Exception as e:  # noqa: BLE001
            db.set_episode_status(conn, episode_id, "failed",
                                  reason=f"writer: {type(e).__name__}")
            return "failed"
        if first.insufficient_source:
            with conn:
                conn.execute("UPDATE episodes SET script_json = ?, low_source = 1,"
                             " updated_at = ? WHERE id = ?",
                             (first.model_dump_json(), db.now_iso(), episode_id))
            return "low_source"
        status, final, verdict, reason = await _verifier.verify_episode(
            first.model_dump(), clean_text, conn=conn, removed=removed)
        with conn:
            if status == "verified" and final is not None:
                conn.execute(
                    "UPDATE episodes SET script_json = ?, verify_json = ?, status = ?,"
                    " updated_at = ? WHERE id = ?",
                    (json.dumps(final, ensure_ascii=False),
                     verdict.model_dump_json() if verdict else "", status,
                     db.now_iso(), episode_id))
            elif final is not None:
                rows = _verifier.failed_violations(final, clean_text, verdict, removed)
                base = (verdict.model_dump() if verdict is not None
                        else {"verdicts": [], "overall": "fail",
                              "voice_derived": []})
                base["violations"] = rows
                conn.execute(
                    "UPDATE episodes SET status = 'failed', reject_reason = ?,"
                    " script_json = ?, verify_json = ?, updated_at = ? WHERE id = ?",
                    (reason, json.dumps(final, ensure_ascii=False),
                     json.dumps(base, ensure_ascii=False),
                     db.now_iso(), episode_id))
            else:
                conn.execute("UPDATE episodes SET status = 'failed', reject_reason = ?,"
                             " updated_at = ? WHERE id = ?",
                             (reason, db.now_iso(), episode_id))
            return status
    finally:
        conn.close()


@_jobs.stage("metadata")
async def metadata_stage(ctx: dict) -> None:
    max_episodes = int(ctx.get("max_episodes") or 3)
    conn = db.connect(ctx["db_path"])
    try:
        db.migrate(conn)
        ep_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM episodes WHERE status = 'verified' ORDER BY id ASC"
            f" LIMIT {max_episodes}")]
        scripts = {r["id"]: (r["script_json"] or "") for r in conn.execute(
            "SELECT id, script_json FROM episodes WHERE status = 'verified'")}
    finally:
        conn.close()
    import json

    from devbrief.llm.client import CallCap

    try:
        stage_mode = _meta.resolve_metadata_mode()
    except ValueError as e:
        for epid in ep_ids[:max_episodes]:
            conn = db.connect(ctx["db_path"])
            try:
                _meta.fail_metadata(conn, epid, str(e)[:500])
            finally:
                conn.close()
        return
    stage_cap = 8 if stage_mode == "generative" else 6
    for epid in ep_ids[:max_episodes]:
        try:
            script = json.loads(scripts.get(epid) or "{}")
        except ValueError:
            continue
        conn = db.connect(ctx["db_path"])
        try:
            source_text = _source_text_for(ctx["db_path"], epid)
            meta, warns, budget, support = await _meta.produce_auto(
                script, source_text, conn=conn, cap=CallCap(stage_cap),
                mode=stage_mode)
            _meta.save_metadata(conn, epid, meta, warns, budget, support)
        except _meta.MetadataFailed as e:
            _meta.fail_metadata(conn, epid, str(e)[:2000])
        except Exception as e:  # noqa: BLE001
            _meta.fail_metadata(conn, epid, f"{type(e).__name__}: {e}"[:500])
        finally:
            conn.close()


def _source_text_for(db_path: str, episode_id: int) -> str:
    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT day, slug FROM episodes WHERE id = ?",
                           (episode_id,)).fetchone()
        if row is None:
            return ""
        target = Path("out") / (row["day"] or "undated") / (row["slug"] or "") / "fetch.json"
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8")).get("text", "")
            except ValueError:
                return ""
        return ""
    finally:
        conn.close()


@_jobs.stage("render")
async def render_stage(ctx: dict) -> None:
    max_episodes = int(ctx.get("max_episodes") or 3)
    conn = db.connect(ctx["db_path"])
    try:
        ep_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM episodes WHERE status = 'verified' ORDER BY id ASC"
            f" LIMIT {max_episodes}")]
    finally:
        conn.close()
    rendered: list[int] = []
    for epid in ep_ids:
        if await render_episode(ctx["db_path"], epid):
            rendered.append(epid)
    ctx["episode_ids"] = rendered


def render_allowed(row) -> str | None:
    """Render kapisi: None = gecer, yoksa red nedeni.

    Yalnizca verified/rendered ilerler (failed/draft/rejected/low_source
    asla); metadata_failed asla; metadatasiz yalnizca is_demo ile gecer.
    failed bolumun saklanan senaryosu bu kapidan doner.
    """
    try:
        demo = bool(row["is_demo"])
        has_meta = bool(row["metadata_json"])
    except (KeyError, IndexError):
        demo, has_meta = False, False
    if row["status"] == "metadata_failed":
        return "metadata_failed"
    if row["status"] not in ("verified", "rendered"):
        return f"durum:{row['status']}"
    if not has_meta and not demo:
        return "metadatasiz"
    return None


async def render_episode(db_path: str, episode_id: int) -> bool:
    """Tek bolum: TTS -> slayt -> altyazi -> ffmpeg. Basarida True."""
    import devbrief.db as _db

    get_settings.cache_clear()
    voice = get_settings().tts_voice
    conn = _db.connect(db_path)
    try:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None or not row["script_json"]:
            return False
        if render_allowed(row) is not None:
            return False
        try:
            data = json.loads(row["script_json"])
        except ValueError:
            return False
        if data.get("insufficient_source"):
            return False
        scenes = list(data.get("scenes") or [])
        if not scenes:
            return False
        slug, day = row["slug"] or f"ep-{episode_id}", row["day"] or today()
    finally:
        conn.close()
    final_dir = Path("out") / day / slug
    final_dir.mkdir(parents=True, exist_ok=True)
    try:
        audio = await _tts.synth_script(scenes, voice, final_dir / "_audio")
        slide_paths = [
            _slides.render_scene(sc, hook=str(data.get("hook") or ""), index=i,
                                 total=len(scenes),
                                 out=final_dir / "_slides" / f"scene_{i + 1}.png")
            for i, sc in enumerate(scenes)]
        durs = [_compose.probe_duration(a["mp3"]) for a in audio]
        groups: list[dict] = []
        offset = 0.0
        for a, dur in zip(audio, durs, strict=True):
            for g in _subs.group_words(a["words"]):
                groups.append({"start": g["start"] + offset, "end": g["end"] + offset,
                               "text": g["text"]})
            offset += dur
        if _compose.over_limit(sum(durs)):
            return False
        _subs.write_ass(groups, final_dir / "subs.ass")
        _subs.write_srt(groups, final_dir / "subs.srt")
        _slides.render_thumb(str(data.get("hook") or slug), final_dir / "thumb.png")
        _compose.build(scenes, audio, slide_paths, slug=slug, day=day,
                         ass_path=final_dir / "subs.ass", out_root="out",
                         source_url=str(data.get("source_url") or ""))
    except Exception:  # noqa: BLE001 - render hatasi failed sayilmaz, atlanir
        return False
    conn = _db.connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE episodes SET status = 'rendered', video_path = ?,"
                         " thumb_path = ?, updated_at = ? WHERE id = ?",
                         (str(final_dir / "video.mp4"), str(final_dir / "thumb.png"),
                          _db.now_iso(), episode_id))
    finally:
        conn.close()
    return True


def make_stages() -> list:
    return [collect_stage, curate_stage, script_stage, metadata_stage, render_stage]


async def run_all(db_path: str, max_episodes: int = 3) -> list[int]:
    """Tum hat tek cagrida (CLI/debug icin). Donus: render edilen episode id'ler."""
    ctx: dict = {"db_path": str(db_path), "job_id": 0,
                 "set_stage": _noop_stage, "max_episodes": max_episodes, "episode_ids": []}
    for st in make_stages():
        await ctx["set_stage"](getattr(st, "__stage_name__", "?"))
        await st(ctx)
    return list(ctx.get("episode_ids") or [])


async def _noop_stage(stage: str) -> None:
    return None
