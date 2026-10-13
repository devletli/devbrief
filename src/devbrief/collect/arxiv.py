"""arXiv toplayici (API + Atom): `async def collect(cfg) -> list[RawItem]`.

cfg: {"categories": ["cs.SE", ...], "weight": float = 0.5,
      "max_results": int = 30, "max_age_hours": int = 168,
      "delay_s": float = 3.0, "enabled": bool = True}.
Birim SAAT: 168 saat = 7 gun (arastirma kaynagi; gunluk akista tekrarlar
`upsert_item` ile elenir). Kategori basina tek API istegi; istek aralarinda
`delay_s` beklenir (arXiv kullanim kurali: asiri yuklenme yok).
URL: https://export.arxiv.org/api/query?search_query=cat:<kat>&... .
Hata disari sizmaz (FetchError sarmalanir); abs URL https'e normalize edilir,
surum eki (v1, v2) korunur (farkli icerik = farkli kayit).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from devbrief.collect.base import TIMEOUT_S, USER_AGENT, FetchError, RawItem

API_URL = "https://export.arxiv.org/api/query"
_WS_RX = re.compile(r"\s+")


async def collect(
    cfg: dict, client: httpx.AsyncClient | None = None
) -> list[RawItem]:
    """Kategoriler sirali cekilir (arada delay). `enabled: false` ise []."""
    if not isinstance(cfg, dict) or cfg.get("enabled", True) is False:
        return []
    cats = [str(c).strip() for c in (cfg.get("categories") or []) if str(c).strip()]
    if not cats:
        return []
    max_results = max(1, int(cfg.get("max_results", 30)))
    max_age = int(cfg.get("max_age_hours", 168))
    delay = max(0.0, float(cfg.get("delay_s", 3.0)))
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
    assert client is not None
    out: list[RawItem] = []
    try:
        for n, cat in enumerate(cats):
            if n > 0 and delay > 0:
                await asyncio.sleep(delay)
            params = {
                "search_query": f"cat:{cat}",
                "sortBy": "submitted",
                "sortOrder": "descending",
                "max_results": max_results,
            }
            try:
                r = await client.get(API_URL, params=params)
            except Exception as e:  # noqa: BLE001 - ag hatasi sarmalanir
                raise FetchError(f"{type(e).__name__}: {e}") from e
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code}")
            out.extend(parse_arxiv(r.content, category=cat, max_age_hours=max_age))
        return out
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001 - ag hatasi tipine bakilmaksizin sarmalanir
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        if own_client:
            await client.aclose()


def parse_arxiv(
    xml: str | bytes, category: str, max_age_hours: int = 168
) -> list[RawItem]:
    """arXiv Atom yanıtını RawItem listesine çevirir (tazelik filtreli)."""
    feed = feedparser.parse(xml)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    out: list[RawItem] = []
    for e in feed.entries:
        url = _abs_url(e)
        if not url:
            continue
        published = _published_at(e)
        if published is not None and published < cutoff:
            continue
        title = _WS_RX.sub(" ", str(getattr(e, "title", "") or "")).strip()
        abstract = _WS_RX.sub(" ", str(getattr(e, "summary", "") or "")).strip()
        authors = [str(a.get("name", "")).strip() for a in (getattr(e, "authors", None) or [])]
        authors = [a for a in authors if a][:5]
        cats = [str(t.get("term", "")).strip() for t in (getattr(e, "tags", None) or [])]
        cats = [c for c in cats if c]
        out.append(
            RawItem(
                source=f"arxiv:{category}",
                url=url,
                title=title,
                summary=abstract[:2000],
                published_at=published.isoformat() if published else None,
                raw_json=json.dumps(
                    {
                        "arxiv_id": url.rsplit("/", 1)[-1],
                        "categories": cats or [category],
                        "comment": str(getattr(e, "arxiv_comment", "") or "")[:500],
                        "authors": authors,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return out


def _abs_url(entry) -> str:
    """Makale sayfasi (abs) linki; rel=alternate tercih edilir, https'e cevrilir."""
    for link in getattr(entry, "links", None) or []:
        href = str((link or {}).get("href", "") or "").strip()
        rel = str((link or {}).get("rel", "") or "").strip().lower()
        if href and (rel == "alternate" or "/abs/" in href):
            return _https_abs(href)
    for attr in ("link", "id"):
        href = str(getattr(entry, attr, "") or "").strip()
        if href:
            return _https_abs(href)
    return ""


def _https_abs(href: str) -> str:
    if href.startswith("http://arxiv.org/abs/"):
        return "https://" + href[len("http://"):]
    return href


def _published_at(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        ts = getattr(entry, attr, None)
        if ts:
            try:
                return datetime(*ts[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                pass
    return None
