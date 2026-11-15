"""Per-domain spacing so a crawl does not stampede one host."""

from __future__ import annotations

import asyncio
import time


class DomainRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next: dict[str, float] = {}

    async def wait(self, domain: str, min_interval_s: float) -> None:
        if min_interval_s <= 0 or not domain:
            return
        async with self._lock:
            now = time.monotonic()
            earliest = self._next.get(domain, now)
            scheduled = max(now, earliest)
            self._next[domain] = scheduled + min_interval_s
            delay = scheduled - now
        if delay > 0:
            await asyncio.sleep(delay)
