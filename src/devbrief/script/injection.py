"""Enjeksiyon savunmasi (hat duzeyi, uretim kodu).

Kaynak metin cumle cumle taranir; `config/injection_patterns.yaml`
kalibina uyan cumleler writer girdisinden CIKARILIR. Cikarilanlar episode
kaydina yazilir (`injection_suspected`, `injection_removed`).
"""

from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

PATTERNS_PATH = "config/injection_patterns.yaml"
_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


@lru_cache(maxsize=4)
def load_patterns(path: str = PATTERNS_PATH) -> list:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    out = []
    for p in data.get("patterns") or []:
        try:
            out.append(re.compile(str(p), re.IGNORECASE))
        except re.error:
            continue
    return out


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s.strip()]


def scan(text: str, path: str = PATTERNS_PATH) -> tuple[str, list[str]]:
    """(temiz_metin, cikarilan_cumleler). Eslesme yoksa metin aynen doner."""
    patterns = load_patterns(path)
    kept, removed = [], []
    for s in split_sentences(text):
        (removed if any(rx.search(s) for rx in patterns) else kept).append(s)
    if not removed:
        return text, []
    return " ".join(kept), removed


def record_flag(conn: sqlite3.Connection, episode_id: int, removed: list[str]) -> None:
    """Episode kaydina suphe bayragi + cikarilan cumleler."""
    import devbrief.db as db

    with conn:
        conn.execute(
            "UPDATE episodes SET injection_suspected = 1, injection_removed = ?,"
            " updated_at = ? WHERE id = ?",
            (json.dumps(removed, ensure_ascii=False)[:4000], db.now_iso(), episode_id),
        )
