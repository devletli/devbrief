"""Guvenli yedek: sqlite backup API + integrity_check + 7 gunluk rotasyon.

Dosya kopyasi DEGIL: `Connection.backup()` tutarli snapshot alir (yazma
sirasinda bile). integrity_check "ok" degilse yedek silinir, hata doner.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIR = Path("backups")
KEEP_DAYS = 7


def backup_name(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"devbrief-{now.strftime('%Y%m%d-%H%M')}.db"


def take_backup(db_path: str | Path = "data/devbrief.db",
                backup_dir: str | Path = BACKUP_DIR) -> Path:
    """Yedek al + dogrula + rotasyon. Donus: yedek yolu. Hatada RuntimeError."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / backup_name()
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if not check_integrity(dest):
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"yedek bozuk, silindi: {dest}")
    rotate(out_dir)
    return dest


def check_integrity(db_path: str | Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            return len(rows) == 1 and str(rows[0][0]).lower() == "ok"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - acilamayan dosya = bozuk
        return False


def rotate(backup_dir: str | Path = BACKUP_DIR, keep_days: int = KEEP_DAYS) -> list[str]:
    """7 gunden eski devbrief-*.db dosyalari sil; silinen adlari dondur."""
    import time

    out_dir = Path(backup_dir)
    cutoff = time.time() - keep_days * 86400
    removed = []
    for f in sorted(out_dir.glob("devbrief-*.db")):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed.append(f.name)
    return removed


def latest(backup_dir: str | Path = BACKUP_DIR) -> Path | None:
    files = sorted(Path(backup_dir).glob("devbrief-*.db"))
    return files[-1] if files else None


def restore(backup_file: str | Path, db_path: str | Path = "data/devbrief.db") -> Path:
    """Calisan DB'yi .bak'a alip yedekten geri yukle. Donus: .bak yolu."""
    import shutil

    backup_file = Path(backup_file)
    if not backup_file.is_file() or not check_integrity(backup_file):
        raise RuntimeError(f"geri yuklenecek dosya bozuk/yok: {backup_file}")
    db_path = Path(db_path)
    bak = db_path.with_suffix(db_path.suffix + ".bak")
    if db_path.exists():
        shutil.copyfile(db_path, bak)
    src = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(db_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # Eski WAL yan dosyalari kalirsa tutarsizlik olur; temizle.
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    if not check_integrity(db_path):
        raise RuntimeError("geri yukleme sonrasi dogrulama basarisiz")
    return bak
