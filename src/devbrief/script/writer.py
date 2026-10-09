"""Senaryo yazari: write_script (tier smart) + previous_errors ile yeniden deneme.

Girdi metni fetch asamasindan gelir (kirpilmis, <=6000). Cikti her zaman
dogrulanmis Script'tir; sema disi ciktiya onarim hakki llm.client'indir.
"""

from __future__ import annotations

import sqlite3

from devbrief.llm.client import complete_json
from devbrief.script.schemas import Script

STYLE_GUIDE = (
    "Turkce, sade; teknik terimler cevrilmez (breaking change, release kalabilir). "
    "Ilk 3 saniyede kanca, sonra Ne degisti -> Sana etkisi -> Ne yapmalisin. "
    "Abarti yok, emin olunmayan 'kaynaga gore' denir."
)


async def write_script(
    source_text: str,
    source_url: str,
    previous_errors: str = "",
    conn: sqlite3.Connection | None = None,
    client=None,
    rate_limit_per_min: float | None = None,
    max_tokens: int = 4000,
    cap=None,
) -> Script:
    """Kaynak metinden senaryo uret. Hataliysa AllModelsFailed yukari sizsin."""
    return await complete_json(
        "write_script", "write_script_v1",
        {"source_text": source_text, "source_url": source_url,
         "style_guide": STYLE_GUIDE, "previous_errors": previous_errors or "yok"},
        Script, tier="smart", temperature=0.6, max_tokens=max_tokens,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min, cap=cap,
    )
