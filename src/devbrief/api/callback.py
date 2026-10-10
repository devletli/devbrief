"""Is bitis bildirimi: n8n webhook'una POST, 3 deneme + ustel bekleme."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("devbrief.callback")

MAX_TRIES = 3


async def post_result(callback_url: str, secret: str, payload: dict,
                      tries: int = MAX_TRIES) -> bool:
    """Basarida True; 3 deneme de olmazsa False (jobs.callback_failed=1 olur)."""
    import httpx

    headers = {"X-Callback-Secret": secret} if secret else {}
    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in range(tries):
            try:
                r = await client.post(callback_url, json=payload, headers=headers)
                if r.status_code < 400:
                    return True
                log.warning("callback %d attempt=%d", r.status_code, attempt + 1)
            except Exception as e:  # noqa: BLE001
                log.warning("callback err=%s attempt=%d", type(e).__name__, attempt + 1)
            await asyncio.sleep(min(2.0**attempt, 30.0))
    return False
