"""RSS toplayici: `async def collect(cfg) -> list[RawItem]`.

Idempotent: ayni URL `upsert_item` ile tek satir olur (DB katmani).
Kaynak hatasi disari sizmaz; CLI `source_runs` satiri yazar.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from devbrief.collect.base import TIMEOUT_S, USER_AGENT, FetchError, RawItem


async def collect(
    cfg: dict,
    client: httpx.AsyncClient | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> list[RawItem]:
    """cfg: {"name": str, "url": str, "max_age_hours": int = 30}.

    Birim SAAT: 30 saat ~= son 1 gun + pay. Tazelik elemesi prefilter'dadir."""
    name = str(cfg.get("name", "rss"))
    url = str(cfg["url"])
    max_age = int(cfg.get("max_age_hours", 30))
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
    assert client is not None
    try:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        r = await client.get(url, headers=headers)
        if r.status_code == 304:
            return []
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        # Bayt verilir: kodlamayi feedparser XML bildiriminden cozer.
        # response.text HTTP basligindaki yanlis charset ile cozulebilir (mojibake).
        return parse_feed(r.content, source=name, max_age_hours=max_age)
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001 - ag hatasi tipine bakilmaksizin sarmalanir
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        if own_client:
            await client.aclose()


def parse_feed(xml: str | bytes, source: str, max_age_hours: int = 30) -> list[RawItem]:
    feed = feedparser.parse(xml)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    out: list[RawItem] = []
    for e in feed.entries:
        link = str(getattr(e, "link", "") or "").strip()
        if not link:
            continue
        published = _published_at(e)
        if published is not None and published < cutoff:
            continue
        summary = str(getattr(e, "summary", "") or getattr(e, "description", "") or "")
        out.append(
            RawItem(
                source=source,
                url=link,
                title=str(getattr(e, "title", "") or ""),
                summary=summary[:2000],
                published_at=published.isoformat() if published else None,
                raw_json=json.dumps({"id": getattr(e, "id", link)}, ensure_ascii=False),
            )
        )
    return out


def _published_at(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        ts = getattr(entry, attr, None)
        if ts:
            try:
                return datetime(*ts[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                pass
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = parsedate_to_datetime(str(val))
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                pass
    return None
