"""Haftalik derleme: yayinlanan bolumlerden metin bulteni (Markdown).

Girdi yalnizca DB'dir (LLM yok, ag yok): son N gunde `published` olan
bolumler seriye gore gruplanir; her madde kanca + etki + kaynak linki tasir.
/// Duzeltmesi olan bolumun maddesine "Duzeltme:" satiri eklenir (seffaflik).
"""

from __future__ import annotations

import json
from datetime import date, timedelta


def _source_url(script_json: str | None) -> str:
    try:
        return str((json.loads(script_json or "{}")).get("source_url") or "")
    except ValueError:
        return ""


def _hook(script_json: str | None, fallback: str) -> str:
    try:
        hook = str((json.loads(script_json or "{}")).get("hook") or "").strip()
    except ValueError:
        hook = ""
    return hook or fallback


def _impact(script_json: str | None) -> str:
    try:
        return str((json.loads(script_json or "{}")).get("impact") or "").strip()
    except ValueError:
        return ""


def collect_week(conn, days: int = 7) -> list[dict]:
    """Penceredeki yayinlanmis bolumler (yeni once)."""
    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    rows = conn.execute(
        "SELECT id, slug, title, day, series, script_json FROM episodes"
        " WHERE status = 'published' AND day >= ? ORDER BY day DESC, id",
        (since,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        corr = [c["note"] for c in conn.execute(
            "SELECT note FROM corrections WHERE episode_id = ? ORDER BY id",
            (d["id"],)).fetchall()]
        out.append({
            "id": d["id"], "slug": d["slug"], "title": d["title"] or "",
            "day": d["day"] or "", "series": (d.get("series") or "").strip(),
            "hook": _hook(d.get("script_json"), d["title"] or d["slug"]),
            "impact": _impact(d.get("script_json")),
            "source_url": _source_url(d.get("script_json")),
            "corrections": [str(c) for c in corr if str(c).strip()],
        })
    return out


def render_markdown(items: list[dict], days: int = 7) -> str:
    """Bulten metni: seri basliklari + maddeler + istatistik."""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    lines = [f"# DevBrief Haftalik Derleme ({since}..{today})", "",
             f"- bolum: {len(items)}"]
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["series"] or "genel", []).append(it)
    for series in sorted(groups):
        lines += ["", f"## {series}"]
        for it in groups[series]:
            head = it["hook"] or it["title"] or it["slug"]
            lines.append(f"- **{head}**")
            if it["impact"]:
                lines.append(f"  {it['impact']}")
            if it["source_url"]:
                lines.append(f"  Kaynak: {it['source_url']}")
            for c in it["corrections"]:
                lines.append(f"  Düzeltme: {c}")
    lines += ["", "_Yapay zekâ destekli hazırlanmıştır._"]
    return "\n".join(lines) + "\n"
