"""HN toplayici (Algolia API): `async def collect(cfg) -> list[RawItem]`.

cfg: {"min_points": int = 150, "max_age_hours": int = 30}.
Birim SAAT: 30 saat ~= son 1 gun + pay.
Algolia `search` + numericFilters kullanir: story, points ve created_at_i.
Harici URL yoksa HN tartisma sayfasi kullanilir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from devbrief.collect.base import TIMEOUT_S, USER_AGENT, FetchError, RawItem

API_URL = "https://hn.algolia.com/api/v1/search"


async def collect(cfg: dict, client: httpx.AsyncClient | None = None) -> list[RawItem]:
    min_points = int(cfg.get("min_points", 150))
    max_age = int(cfg.get("max_age_hours", 30))
    cutoff = int((datetime.now(UTC) - timedelta(hours=max_age)).timestamp())
    params = {
        "tags": "story",
        "numericFilters": f"points>{min_points},created_at_i>{cutoff}",
        "hitsPerPage": 100,
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
    assert client is not None
    try:
        r = await client.get(API_URL, params=params)
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        return parse_hits(r.json(), min_points=min_points, cutoff=cutoff)
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        if own_client:
            await client.aclose()


def parse_hits(payload: dict, min_points: int = 0, cutoff: int = 0) -> list[RawItem]:
    out: list[RawItem] = []
    for h in payload.get("hits", []):
        try:
            points = int(h.get("points") or 0)
        except (TypeError, ValueError):
            points = 0
        if points < min_points:
            continue
        try:
            created = int(h.get("created_at_i") or 0)
        except (TypeError, ValueError):
            created = 0
        if cutoff and created < cutoff:
            continue
        url = str(h.get("url") or "")
        obj_id = str(h.get("objectID", ""))
        if not url and obj_id:
            url = f"https://news.ycombinator.com/item?id={obj_id}"
        if not url:
            continue
        title = str(h.get("title") or "")
        comments = h.get("num_comments", 0)
        published = (
            datetime.fromtimestamp(created, UTC).isoformat() if created else None
        )
        out.append(
            RawItem(
                source="hn",
                url=url,
                title=title,
                summary=f"{points} points, {comments} comments: {title}"[:2000],
                published_at=published,
                raw_json=json.dumps(
                    {
                        "objectID": obj_id,
                        "points": points,
                        "num_comments": comments,
                        "author": h.get("author"),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return out
