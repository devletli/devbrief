"""Gunluk secim: kumele (kural tabanli) -> select_daily (tier smart).

`merged_cluster_ids`: parafraz kümeler LLM tarafindan birlestirilir;
birlesen kume secilmez (secili bayragi picks+backup'ta kalir).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import devbrief.db as db
from devbrief.curate import dedupe
from devbrief.curate.schemas import SelectDailyOut
from devbrief.llm.client import complete_json


def make_clusters(rows: list[sqlite3.Row],
                  jaccard_thr: float = 0.5, difflib_thr: float = 0.8,
                  max_size: int = 6) -> list[dict]:
    """Satirlari baslik benzerligiyle kumeler. Donus: [{topic, score, members}]."""
    items = [
        {"id": r["id"], "title": r["title"] or "", "canonical_url": r["canonical_url"] or "",
         "prescore": float(r["prescore"] or 0.0)}
        for r in rows
    ]
    infos = []
    for members in dedupe.cluster(items, jaccard_thr=jaccard_thr,
                                  difflib_thr=difflib_thr, max_size=max_size):
        rep = dedupe.representative(members)
        infos.append({
            "topic": str(rep.get("title") or "")[:200],
            "score": max(float(m.get("prescore", 0.0)) for m in members),
            "members": members,
        })
    return infos


def clear_day_clusters(conn: sqlite3.Connection, day: str) -> int:
    """Gunun bolumsuz kumelerini sil (bolumu olanlar korunur, oksuz kalmaz).

    Ayni gun tekrar curate kosumu yoksa kume uretmez; onceki secimler
    (secili bayrak dahil) korunur. Donus: silinen kume sayisi.
    """
    with conn:
        conn.execute(
            "DELETE FROM cluster_items WHERE cluster_id IN (SELECT id FROM clusters"
            " WHERE day = ? AND id NOT IN (SELECT DISTINCT cluster_id FROM episodes"
            " WHERE cluster_id IS NOT NULL))",
            (day,),
        )
        cur = conn.execute(
            "DELETE FROM clusters WHERE day = ? AND id NOT IN"
            " (SELECT DISTINCT cluster_id FROM episodes WHERE cluster_id IS NOT NULL)",
            (day,),
        )
        return cur.rowcount or 0


def persist_clusters(conn: sqlite3.Connection, day: str, infos: list[dict]) -> list[dict]:
    """Kumeleri DB'ye yaz, `id` (db) eklenmis kopya dondur.

    Ayni gun ikinci yazim once bolumsuzleri temizler: tekrar kosum
    ikiz kume uretmez (butce yakmaz, secim bulanmaz).
    """
    clear_day_clusters(conn, day)
    out = []
    for info in infos:
        cid = db.create_cluster(conn, day, info["topic"], info["score"])
        for m in info["members"]:
            db.add_cluster_item(conn, cid, int(m["id"]))
        out.append({**info, "id": cid})
    return out


def cluster_text(infos: list[dict], classes: dict[int, dict]) -> str:
    lines = []
    for idx, info in enumerate(infos, 1):
        cid = info.get("id", idx)
        titles = "; ".join(str(m.get("title") or "")[:120] for m in info["members"][:6])
        imps = [classes.get(int(m["id"]), {}).get("importance", 0) for m in info["members"]]
        cats = sorted({classes.get(int(m["id"]), {}).get("category", "?") for m in info["members"]})
        avg = (sum(imps) / len(imps)) if imps else 0
        lines.append(
            f"K{cid}: {info['topic']} | ogeler: {titles} | "
            f"ort.onem: {avg:.1f} | kat: {','.join(cats)}"
        )
    return "\n".join(lines)


async def select_day(infos: list[dict], classes: dict[int, dict], conn=None, client=None,
                     rate_limit_per_min: float | None = None,
                     max_tokens: int | None = None) -> SelectDailyOut:
    out = await complete_json(
        "select", "select_daily_v1", {"clusters": cluster_text(infos, classes)},
        SelectDailyOut, tier="smart", temperature=0.2, max_tokens=max_tokens,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
    )
    known = {int(info.get("id", idx)) for idx, info in enumerate(infos, 1)}
    for pick in (*out.picks, out.backup):
        pick.merged_cluster_ids = [c for c in pick.merged_cluster_ids if c in known]
    return out


def apply_selection(conn: sqlite3.Connection, out: SelectDailyOut) -> None:
    for pick in (*out.picks, out.backup):
        db.set_cluster_selected(conn, pick.cluster_id, True)


def today() -> str:
    return datetime.now(UTC).date().isoformat()
