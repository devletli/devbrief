"""SQLite baglanti + migrasyon + kucuk repository. ORM yok.

Kurallar:
- /run kilidi `jobs` tablosunda tutulur (bellekte degil): ayni anda tek `running`.
- LLM butce icin cagridan once atomik slot ayrilir: `reserve_llm_slot` (ok=-1 pending),
  sonuc belli olunca `finish_llm_slot` ile guncellenir. Boylece paralel cagrilar
  butceyi asamaz (tek isci + IMMEDIATE transaction ile birlikte).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB = Path("data/devbrief.db")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        try:
            version = int(f.stem.split("_")[0])
        except ValueError:
            continue
        if version in applied:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, now_iso()),
        )
    conn.commit()
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def count_llm_calls_today(conn: sqlite3.Connection) -> int:
    day = now_iso()[:10]
    row = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= 0", (day,)
    ).fetchone()
    return int(row[0])


def reserve_llm_slot(conn: sqlite3.Connection, task: str, model: str, budget: int) -> int:
    """Atomik slot: IMMEDIATE ile say + insert tek transactionda yapilir."""
    with conn:  # BEGIN IMMEDIATE -> COMMIT/ROLLBACK
        conn.execute("SELECT COUNT(*) FROM llm_calls")
        day = now_iso()[:10]
        n = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= -1",
            (day,),
        ).fetchone()[0]
        if int(n) >= budget:
            raise BudgetExceeded(f"gunluk butce asildi ({budget})")
        cur = conn.execute(
            "INSERT INTO llm_calls (ts, task, model, ok, ms, tokens) "
            "VALUES (?, ?, ?, -1, 0, 0)",
            (now_iso(), task, model),
        )
        return int(cur.lastrowid)


def finish_llm_slot(
    conn: sqlite3.Connection, slot_id: int, ok: bool, ms: int, tokens: int = 0
) -> None:
    with conn:
        conn.execute(
            "UPDATE llm_calls SET ok = ?, ms = ?, tokens = ? WHERE id = ?",
            (1 if ok else 0, ms, tokens, slot_id),
        )


def try_acquire_job(conn: sqlite3.Connection) -> int | None:
    """Ayni anda tek `running` job. Basariliysa job id, doluysa None doner."""
    with conn:
        row = conn.execute("SELECT id FROM jobs WHERE status = 'running' LIMIT 1").fetchone()
        if row:
            return None
        # Sartmis `running` varsa DB yeniden baslamistir -> failed isaretle (Faz 5'te kullanilir).
        cur = conn.execute(
            "INSERT INTO jobs (status, stage, created_at, updated_at) "
            "VALUES ('running', '', ?, ?)",
            (now_iso(), now_iso()),
        )
        return int(cur.lastrowid)


def finish_job(conn: sqlite3.Connection, job_id: int, status: str, error: str = "") -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, now_iso(), job_id),
        )


class BudgetExceeded(RuntimeError):
    pass


# --- Faz 1 repository: items / classifications / episodes ---------------------

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}


def canonical_url(url: str) -> str:
    """URL normalizasyon: host kucuk, fragment yok, utm yok, sondaki `/` yok."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in _TRACKING_PARAMS]
    path = p.path.rstrip("/") or ""
    return urlunparse((p.scheme.lower(), host, path, "", urlencode(q), ""))


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def upsert_item(
    conn: sqlite3.Connection,
    *,
    source: str,
    url: str,
    title: str = "",
    summary: str = "",
    published_at: str | None = None,
    raw_json: str = "{}",
    prescore: float | None = None,
) -> int:
    """Ayni URL tekrar yazilmaz (idempotent). Item id doner."""
    canon = canonical_url(url)
    chash = content_hash(canon, title, summary or "")
    now = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO items (source, url, canonical_url, title, summary, published_at,"
            " fetched_at, content_hash, raw_json, prescore, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')"
            " ON CONFLICT(url) DO UPDATE SET"
            " title=excluded.title, summary=excluded.summary,"
            " prescore=COALESCE(excluded.prescore, items.prescore)",
            (source, url, canon, title, summary, published_at, now, chash, raw_json, prescore),
        )
        row = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()
        return int(row["id"])


def get_new_items(conn: sqlite3.Connection, limit: int = 40) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM items WHERE status = 'new'"
            " ORDER BY COALESCE(prescore, 0) DESC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    )


def save_classification(
    conn: sqlite3.Connection,
    item_id: int,
    category: str,
    importance: int,
    affects_devs: bool,
    reason: str,
    model: str,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO classifications"
            " (item_id, category, importance, affects_devs, reason, model, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(item_id) DO UPDATE SET"
            " category=excluded.category, importance=excluded.importance,"
            " affects_devs=excluded.affects_devs, reason=excluded.reason,"
            " model=excluded.model",
            (item_id, category, importance, 1 if affects_devs else 0, reason, model, now_iso()),
        )


def set_episode_status(
    conn: sqlite3.Connection, episode_id: int, status: str, reason: str = ""
) -> None:
    with conn:
        conn.execute(
            "UPDATE episodes SET status = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, now_iso(), episode_id),
        )


def update_prescore(conn: sqlite3.Connection, item_id: int, prescore: float) -> None:
    with conn:
        conn.execute("UPDATE items SET prescore = ? WHERE id = ?", (prescore, item_id))


def log_source_run(
    conn: sqlite3.Connection, source: str, ok: bool, n_items: int, error: str = ""
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO source_runs (ts, source, ok, n_items, error)"
            " VALUES (?, ?, ?, ?, ?)",
            (now_iso(), source, 1 if ok else 0, n_items, error[:2000]),
        )
