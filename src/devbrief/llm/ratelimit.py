"""Dakikada N istek: istek aralarini acar (token bucket'in basiti).

Paylasilan sinirlayici: ayni `key` ile cagrilan tum asenkron gorevler
siralanir. Modulsuz testler icin `Bucket(6000)` gibi yuksek deger verilir.
"""

from __future__ import annotations

import asyncio
import time


class Bucket:
    def __init__(self, per_minute: float):
        self.interval = 60.0 / max(1.0, per_minute)
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if self._next > now:
            await asyncio.sleep(self._next - now)
            now = time.monotonic()
        self._next = max(now, self._next) + self.interval


_limiters: dict[str, Bucket] = {}


def shared(key: str, per_minute: float) -> Bucket:
    """Ayni key hep ayni sinirlayiciyi dondurur (surec ici)."""
    if key not in _limiters:
        _limiters[key] = Bucket(per_minute)
    return _limiters[key]
