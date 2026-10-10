"""Toplu siniflandirma: ilk 40 girdi, 20'serli batch, classify_batch (tier fast).

Tum LLM cagrilari llm.client uzerinden; sonuclar classifications tablosuna.
"""

from __future__ import annotations

import sqlite3

import devbrief.db as db
from devbrief.curate.schemas import ClassifyBatchOut
from devbrief.llm.client import complete_json

BATCH = 20


def batch_text(rows: list[sqlite3.Row]) -> str:
    lines = []
    for r in rows:
        summary = (r["summary"] or "")[:300].replace("\n", " ")
        lines.append(f"[{r['id']}] ({r['source']}) {r['title'] or ''} :: {summary}")
    return "\n".join(lines)


async def classify_batch(rows: list[sqlite3.Row], conn=None, client=None,
                         rate_limit_per_min: float | None = None,
                         tier: str = "smart", max_tokens: int | None = None) -> ClassifyBatchOut:
    merged: list = []
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        out = await complete_json(
            "classify", "classify_batch_v1", {"items": batch_text(chunk)},
            ClassifyBatchOut, tier=tier, temperature=0.1, max_tokens=max_tokens,
            conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
        )
        merged.extend(out.results)
    return ClassifyBatchOut(results=merged)


async def run(conn: sqlite3.Connection, limit: int = 40, dry_run: bool = False,
              client=None, rate_limit_per_min: float | None = None,
              tier: str = "smart", max_tokens: int | None = None) -> ClassifyBatchOut:
    """Ilk `limit` girdiyi siniflandir; dry_run degilse DB'ye yaz.

    Varsayilan tier smart: kapi FAST adayi cikarmadi (JSON uretemiyorlar).
    FAST adayi bulununca geri alinir.
    """
    rows = db.get_new_items(conn, limit=limit)
    out = await classify_batch(rows, conn=conn, client=client,
                               rate_limit_per_min=rate_limit_per_min,
                               tier=tier, max_tokens=max_tokens)
    if not dry_run:
        known = {r["id"] for r in rows}
        for c in out.results:
            if c.id not in known:
                continue  # halusinasyon id: yazma
            db.save_classification(conn, c.id, c.category, c.importance,
                                   c.affects_devs, c.reason, tier)
    return out
