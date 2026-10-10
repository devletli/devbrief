"""Is kuyrugu: tek isci, jobs tablosu, 409 kilidi, callback.

Asamalar enjekte edilebilir (`stages`): uretim `pipeline.run_all` adimlarini,
testler sahte fonksiyonlari verir. Asama imzasi: `async def stage(ctx)`,
`ctx` icinde `db_path`, `job_id`, `set_stage`, `max_episodes` vardir.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import devbrief.db as db

log = logging.getLogger("devbrief.jobs")

StageFn = Callable[[dict], Awaitable[None]]


def acquire(db_path: str | Path) -> int | None:
    """Kilit bos ise job acar (id), doluysa None (API 409 verir)."""
    conn = db.connect(db_path)
    try:
        db.migrate(conn)
        return db.try_acquire_job(conn)
    finally:
        conn.close()


def get(db_path: str | Path, job_id: int) -> dict | None:
    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_stage(db_path: str | Path, job_id: int, stage: str) -> None:
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE jobs SET stage = ?, updated_at = ? WHERE id = ?",
                         (stage, db.now_iso(), job_id))
    finally:
        conn.close()


def finish(db_path: str | Path, job_id: int, status: str, error: str = "",
           episode_ids: list[int] | None = None) -> None:
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, episode_ids = ?, updated_at = ?"
                " WHERE id = ?",
                (status, error[:2000], json.dumps(episode_ids or []), db.now_iso(), job_id),
            )
    finally:
        conn.close()


def recover(db_path: str | Path) -> int:
    """Yeniden baslatmada yarim kalan `running` isleri failed isaretle."""
    conn = db.connect(db_path)
    try:
        db.migrate(conn)
        with conn:
            cur = conn.execute("UPDATE jobs SET status = 'failed', error = 'yeniden baslatma',"
                               " updated_at = ? WHERE status = 'running'",
                               (db.now_iso(),))
            return cur.rowcount or 0
    finally:
        conn.close()


async def run_job(db_path: str | Path, job_id: int, stages: list[StageFn],
                  callback_url: str = "", max_episodes: int = 3) -> None:
    """Isi kostur; bitince callback at (basari da hata da bildirilir)."""
    from devbrief.api import callback as _cb

    async def _set(stage: str) -> None:
        await asyncio.to_thread(set_stage, db_path, job_id, stage)

    ctx = {"db_path": str(db_path), "job_id": job_id, "set_stage": _set,
           "max_episodes": max_episodes, "episode_ids": []}
    status, error = "done", ""
    try:
        for i, stage in enumerate(stages):
            await _set(getattr(stage, "__stage_name__", f"stage_{i}"))
            await stage(ctx)
        await _set("")
    except Exception as e:  # noqa: BLE001 - hata callback'e tasinir
        status, error = "failed", f"{type(e).__name__}: {e}"
        log.exception("job failed id=%d", job_id)
    await asyncio.to_thread(finish, db_path, job_id, status, error,
                            list(ctx.get("episode_ids") or []))
    if callback_url:
        from devbrief.config import get_settings

        secret = get_settings().n8n_callback_secret
        ok = await _cb.post_result(callback_url, secret,
                                   {"job_id": job_id, "status": status,
                                    "error": error,
                                    "episode_ids": list(ctx.get("episode_ids") or [])})
        if not ok:
            conn = db.connect(db_path)
            try:
                with conn:
                    conn.execute("UPDATE jobs SET callback_failed = 1 WHERE id = ?", (job_id,))
            finally:
                conn.close()


def stage(name: str):
    """Asama fonksiyonuna ad takar (jobs tablosundaki `stage` icin)."""
    def wrap(fn: StageFn) -> StageFn:
        fn.__stage_name__ = name  # type: ignore[attr-defined]
        return fn
    return wrap
