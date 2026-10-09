"""Makale cekme (LLM'siz): trafilatura -> kirpma -> hash -> fallback.

- Birincil: trafilatura ile ana metin, 6000 karakter kirpma.
- Dusme: cekilemezse/bos/cok kisa ise RSS ozeti; `low_source=True`.
- HTML karakter normalizasyonu: entity cozme + bosluk sadelestirme.
- Ag'a yalnizca bu modul cikar (testlerde mock'lanir).
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

MAX_CHARS = 6000
MIN_CHARS = 300  # altindakiler ozet sayilir, low_source'a duser

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """HTML entity coz + bosluklari tekle + kirp."""
    return _WS.sub(" ", html.unescape(text or "")).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass
class FetchResult:
    url: str
    text: str  # kirpilmis, normalize kaynak metin
    source_hash: str  # text'in sha256'si (idempotency: ayni hash = tekrar cekme)
    low_source: bool  # True ise yalnizca RSS ozeti var
    reason: str = ""  # dusme/uyari aciklamasi


def fetch_article(url: str, rss_summary: str = "", timeout: int = 20) -> FetchResult:
    """URL'den ana metni cek. Basarisizsa ozete dus (low_source=True)."""
    downloaded = None
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
    except Exception:  # noqa: BLE001 - ag kutuphanesi hatasi dusme sayilir
        downloaded = None
    text = ""
    if downloaded:
        try:
            import trafilatura

            text = normalize_text(
                trafilatura.extract(downloaded, include_comments=False) or ""
            )
        except Exception:  # noqa: BLE001
            text = ""
    if len(text) >= MIN_CHARS:
        cropped = text[:MAX_CHARS]
        return FetchResult(url=url, text=cropped, source_hash=text_hash(cropped),
                           low_source=False)
    fallback = normalize_text(rss_summary)[:MAX_CHARS]
    reason = "indirilemedi" if not text else f"cok kisa ({len(text)} karakter)"
    if not fallback:
        return FetchResult(url=url, text="", source_hash=text_hash(""),
                           low_source=True, reason=f"{reason}, ozet de yok")
    return FetchResult(url=url, text=fallback, source_hash=text_hash(fallback),
                       low_source=True, reason=reason)
