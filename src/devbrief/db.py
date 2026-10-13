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
    conn: sqlite3.Connection, slot_id: int, ok: bool, ms: int, tokens: int = 0,
    status: str | None = None, finish_reason: str | None = None,
    repair_attempt: int = 0, is_paid: bool = False,
    reasoning_tokens: int = 0, response_format: str = "",
    cost: float | None = None,
) -> None:
    with conn:
        try:
            conn.execute(
                "UPDATE llm_calls SET ok = ?, ms = ?, tokens = ?, status = ?,"
                " finish_reason = ?, repair_attempt = ?, is_paid = ?,"
                " reasoning_tokens = ?, response_format = ?, cost = ? WHERE id = ?",
                (1 if ok else 0, ms, tokens, status, finish_reason, repair_attempt,
                 1 if is_paid else 0, reasoning_tokens, response_format, cost, slot_id),
            )
        except sqlite3.OperationalError:
            # 011 oncesi sema (cost kolonu yok): maliyetsiz yaz.
            conn.execute(
                "UPDATE llm_calls SET ok = ?, ms = ?, tokens = ?, status = ?,"
                " finish_reason = ?, repair_attempt = ?, is_paid = ?,"
                " reasoning_tokens = ?, response_format = ? WHERE id = ?",
                (1 if ok else 0, ms, tokens, status, finish_reason, repair_attempt,
                 1 if is_paid else 0, reasoning_tokens, response_format, slot_id),
            )


def llm_stats_today(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rapor icin: bugun model+saat bazinda sayilar ve durum dagilimi."""
    day = now_iso()[:10]
    try:
        return list(
            conn.execute(
                "SELECT model, substr(ts, 12, 2) AS hour,"
                " COUNT(*) AS n, SUM(ok) AS ok_n, status, MAX(is_paid) AS paid,"
                " response_format AS fmt, SUM(reasoning_tokens) AS rtok,"
                " SUM(cost) AS cost_total,"
                " SUM(CASE WHEN cost IS NOT NULL THEN 1 ELSE 0 END) AS cost_n"
                " FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= 0"
                " GROUP BY model, hour, status, fmt ORDER BY hour, model",
                (day,),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        # 011 oncesi sema: maliyetsiz eski sorgu.
        return list(
            conn.execute(
                "SELECT model, substr(ts, 12, 2) AS hour,"
                " COUNT(*) AS n, SUM(ok) AS ok_n, status, MAX(is_paid) AS paid,"
                " response_format AS fmt, SUM(reasoning_tokens) AS rtok"
                " FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= 0"
                " GROUP BY model, hour, status, fmt ORDER BY hour, model",
                (day,),
            ).fetchall()
        )


def llm_cost_by_model_today(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Model bazinda toplam maliyet + istek basina ortalama (rapor icin).

    cost kolonu yoksa/bilgi yoksa bos liste doner; cagiran "bilinmiyor" yazar.
    """
    day = now_iso()[:10]
    try:
        return list(
            conn.execute(
                "SELECT model, COUNT(*) AS n, SUM(cost) AS cost_total,"
                " SUM(CASE WHEN cost IS NOT NULL THEN 1 ELSE 0 END) AS cost_n"
                " FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= 0"
                " GROUP BY model ORDER BY model",
                (day,),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return []


def llm_stats_by_task_today(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rapor icin: bugun gorev+durum bazinda sayilar.

    Gorev `task` kolonunun `:` oncesi bazidir (verify_script:format_retry
    -> verify_script); durum `status` kolonudur (bos ise `?`).
    """
    day = now_iso()[:10]
    return list(
        conn.execute(
            "SELECT substr(task, 1, instr(task || ':', ':') - 1) AS task,"
            " COALESCE(NULLIF(status, ''), '?') AS status,"
            " COUNT(*) AS n, SUM(ok) AS ok_n"
            " FROM llm_calls WHERE substr(ts, 1, 10) = ? AND ok >= 0"
            " GROUP BY task, status ORDER BY task, status",
            (day,),
        ).fetchall()
    )


def metrics_weekly(conn: sqlite3.Connection, days: int = 7) -> list[sqlite3.Row]:
    """Son N gun metrikleri: bolum x platform (izlenme/begeni toplami, son tutma).

    Pencere disi olcumler toplama girmez; tutma (`retention`) yayinin bilinen
    en son degeridir. Olcumsuz yayin 0 ile listelenir (bekleyen ayri sorguda).
    """
    from datetime import date, timedelta

    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    return list(
        conn.execute(
            "SELECT e.id AS episode_id, e.slug AS slug, COALESCE(e.title, '') AS title,"
            " COALESCE(e.ab_group, '') AS ab_group,"
            " p.platform AS platform, COALESCE(SUM(m.views), 0) AS views,"
            " COALESCE(SUM(m.likes), 0) AS likes,"
            " (SELECT m2.avg_view_pct FROM metrics m2"
            "  WHERE m2.publication_id = p.id ORDER BY m2.day DESC LIMIT 1) AS retention"
            " FROM episodes e JOIN publications p ON p.episode_id = e.id"
            " LEFT JOIN metrics m ON m.publication_id = p.id AND m.day >= ?"
            " WHERE e.status = 'published'"
            " GROUP BY e.id, p.platform ORDER BY views DESC, e.id",
            (since,),
        ).fetchall()
    )


def metrics_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Yayinlanmis ama hic olcumu olmayan bolumler (olcum bekleniyor)."""
    return list(
        conn.execute(
            "SELECT e.id AS episode_id, e.slug AS slug FROM episodes e"
            " WHERE e.status = 'published' AND NOT EXISTS (SELECT 1 FROM metrics m"
            " JOIN publications p ON p.id = m.publication_id"
            " WHERE p.episode_id = e.id) ORDER BY e.id"
        ).fetchall()
    )


def llm_stats_7d(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Son 7 gun: gun+model bazinda toplam ve ucretli sayisi."""
    return list(
        conn.execute(
            "SELECT substr(ts, 1, 10) AS day, model,"
            " COUNT(*) AS n, SUM(ok) AS ok_n, SUM(is_paid) AS paid_n"
            " FROM llm_calls WHERE date(ts) >= date('now', '-6 days') AND ok >= 0"
            " GROUP BY day, model ORDER BY day, model"
        ).fetchall()
    )


def record_eval_gate(conn: sqlite3.Connection, task: str, passed: bool,
                     recall: float | None = None, fp_rate: float | None = None) -> None:
    """Eval kapisi sonucunu kaydet (upsert). Uretken mod kilidi bunu okur."""
    with conn:
        conn.execute(
            "INSERT INTO eval_gates (task, passed, recall, fp_rate, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(task) DO UPDATE SET passed=excluded.passed,"
            " recall=excluded.recall, fp_rate=excluded.fp_rate,"
            " recorded_at=excluded.recorded_at",
            (task, 1 if passed else 0, recall, fp_rate, now_iso()),
        )


def get_eval_gate(conn: sqlite3.Connection | None, task: str) -> dict | None:
    """Son kapi sonucu (yoksa/geçiş öncesi şemadaysa None)."""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT passed, recall, fp_rate, recorded_at FROM eval_gates WHERE task = ?",
            (task,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - 010 oncesi sema: kayit yok sayilir
        return None
    if row is None:
        return None
    return {"passed": bool(row["passed"]), "recall": row["recall"],
            "fp_rate": row["fp_rate"], "recorded_at": row["recorded_at"]}


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


SUGGESTION_STATUSES = ("new", "reviewed", "dismissed")


def add_suggestion(conn: sqlite3.Connection, title: str, url: str = "",
                   note: str = "") -> int:
    """Topluluk onerisi kaydet (durum: new). Donus: id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO suggestions (title, url, note, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'new', ?, ?)",
            (title, url, note, now_iso(), now_iso()),
        )
        return int(cur.lastrowid)


def list_suggestions(conn: sqlite3.Connection, status: str | None = None,
                     ) -> list[sqlite3.Row]:
    """Oneriler (yeni once). Gecersiz durum filtresi yok sayilir."""
    if status in SUGGESTION_STATUSES:
        return list(conn.execute(
            "SELECT * FROM suggestions WHERE status = ? ORDER BY id DESC LIMIT 100",
            (status,)).fetchall())
    return list(conn.execute(
        "SELECT * FROM suggestions ORDER BY id DESC LIMIT 100").fetchall())


def set_suggestion_status(conn: sqlite3.Connection, sid: int, status: str) -> bool:
    """Oneri durumu (reviewed/dismissed). Donus: satir bulundu mu."""
    if status not in ("reviewed", "dismissed"):
        return False
    with conn:
        cur = conn.execute(
            "UPDATE suggestions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), sid),
        )
        return cur.rowcount > 0


def add_correction(conn: sqlite3.Connection, episode_id: int, note: str) -> int:
    """Bolume duzeltme notu ekle. Donus: id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO corrections (episode_id, note, created_at)"
            " VALUES (?, ?, ?)",
            (episode_id, note, now_iso()),
        )
        return int(cur.lastrowid)


def list_corrections(conn: sqlite3.Connection, episode_id: int) -> list[sqlite3.Row]:
    """Bolumun duzeltme notlari (eskiden yeniye)."""
    return list(conn.execute(
        "SELECT * FROM corrections WHERE episode_id = ? ORDER BY id",
        (episode_id,)).fetchall())


def set_episode_series(conn: sqlite3.Connection, episode_id: int,
                       series: str) -> bool:
    """Seri etiketi ata (bos metin temizler). Donus: satir bulundu mu."""
    with conn:
        cur = conn.execute(
            "UPDATE episodes SET series = ?, updated_at = ? WHERE id = ?",
            (series.strip()[:60] or None, now_iso(), episode_id),
        )
        return cur.rowcount > 0


def series_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Seri -> bolum sayisi (etiketsizler 'genel' sayilmaz, ayri listelenir)."""
    return list(conn.execute(
        "SELECT COALESCE(NULLIF(series, ''), '(etiketsiz)') AS series,"
        " COUNT(*) AS n FROM episodes WHERE status IN ('approved', 'published')"
        " GROUP BY series ORDER BY n DESC, series").fetchall())


def update_prescore(conn: sqlite3.Connection, item_id: int, prescore: float) -> None:
    with conn:
        conn.execute("UPDATE items SET prescore = ? WHERE id = ?", (prescore, item_id))


def create_cluster(conn: sqlite3.Connection, day: str, topic: str, score: float) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO clusters (day, topic, score, selected) VALUES (?, ?, ?, 0)",
            (day, topic, score),
        )
        return int(cur.lastrowid)


def add_cluster_item(conn: sqlite3.Connection, cluster_id: int, item_id: int) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO cluster_items (cluster_id, item_id) VALUES (?, ?)",
            (cluster_id, item_id),
        )


def set_cluster_selected(conn: sqlite3.Connection, cluster_id: int, selected: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE clusters SET selected = ? WHERE id = ?", (1 if selected else 0, cluster_id)
        )


def get_day_clusters(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT c.*, GROUP_CONCAT(ci.item_id) AS item_ids,"
            " AVG(cl.importance) AS avg_importance"
            " FROM clusters c"
            " LEFT JOIN cluster_items ci ON ci.cluster_id = c.id"
            " LEFT JOIN classifications cl ON cl.item_id = ci.item_id"
            " WHERE c.day = ? GROUP BY c.id ORDER BY c.score DESC",
            (day,),
        ).fetchall()
    )


def log_source_run(
    conn: sqlite3.Connection, source: str, ok: bool, n_items: int, error: str = ""
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO source_runs (ts, source, ok, n_items, error)"
            " VALUES (?, ?, ?, ?, ?)",
            (now_iso(), source, 1 if ok else 0, n_items, error[:2000]),
        )
