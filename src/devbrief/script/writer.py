"""Senaryo yazari: write_script (tier smart) + previous_errors ile yeniden deneme.

Girdi metni fetch asamasindan gelir (kirpilmis, <=6000). Cikti her zaman
dogrulanmis Script'tir; sema disi ciktiya onarim hakki llm.client'indir.
"""

from __future__ import annotations

import sqlite3

from devbrief.llm.client import ShortNoSource, complete_json
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
    max_tokens: int | None = None,
    cap=None,
    removed: list[str] | tuple = (),
) -> Script:
    """Kaynak metinden senaryo uret. Hataliysa AllModelsFailed yukari sizsin."""
    from devbrief.script.verifier import number_source_sentences

    if removed:
        injection_note = (
            f"Cikarilmis {len(removed)} supheli cumle var; bunlarin icerigini "
            "iddia olarak KULLANMA.")
    else:
        injection_note = ""
    numbered, _ = number_source_sentences(source_text)
    try:
        return await complete_json(
            "write_script", "write_script_v1",
            {"source_text": f"<KAYNAK>\n{numbered}\n</KAYNAK>", "source_url": source_url,
             "style_guide": STYLE_GUIDE, "previous_errors": previous_errors or "yok",
             "injection_note": injection_note},
            Script, tier="smart", temperature=0.6, max_tokens=max_tokens,
            conn=conn, client=client, rate_limit_per_min=rate_limit_per_min, cap=cap,
        )
    except ShortNoSource:
        # Genisletme kaynagi yok: onarim turu harcamadan low_source.
        return Script.model_validate({
            "hook": "kaynak yetersiz", "source_url": source_url or "https://x",
            "scenes": [{"id": 1,
                        "sentences": [{"text": "Kaynakta genisletme icin olgu yok.",
                                       "src": [0]}],
                        "on_screen": "kaynak yetersiz", "visual": "title",
                        "code": None, "evidence": ["kaynak yetersiz"]}],
            "impact": "Kaynak yetersiz.", "action": "Kaynaga bak.",
            "claims": [], "duration_estimate_s": 40,
            "insufficient_source": True, "reason": "kaynak yetersiz"})
