"""GitHub releases toplayici (Atom): `async def collect(cfg) -> list[RawItem]`.

cfg: {"repo": "owner/name", "weight": float = 0.8, "max_age_hours": int = 720}.
Birim SAAT: 720 saat = 30 gun. Gunluk akista genis bir penceredir; ilk dolumda
eski surumler DB'ye girer, tazelik elemesi collect sonrasi prefilter'da yapilir.
URL: https://github.com/<repo>/releases.atom. GITHUB_TOKEN varsa Authorization
eklenir (rate limit icin). Hata disari sizmaz (FetchError sarmalanir).
"""

from __future__ import annotations

import httpx

from devbrief.collect.base import TIMEOUT_S, USER_AGENT, FetchError, RawItem
from devbrief.collect.rss import parse_feed


async def collect(
    cfg: dict, client: httpx.AsyncClient | None = None, token: str | None = None
) -> list[RawItem]:
    repo = str(cfg["repo"])
    max_age = int(cfg.get("max_age_hours", 720))
    url = f"https://github.com/{repo}/releases.atom"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True)
    assert client is not None
    try:
        r = await client.get(url, headers=headers)
        if r.status_code == 304:
            return []
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        items = parse_feed(r.text, source=f"github:{repo}", max_age_hours=max_age)
        return items
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        if own_client:
            await client.aclose()
