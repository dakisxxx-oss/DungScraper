"""Per-domain spacing so a crawl does not stampede one host."""

from __future__ import annotations

import asyncio
import time


MAX_CRAWL_DELAY_S = 10.0


def polite_interval(requested_s: float, crawl_delay_s: float | None) -> float:
    """Use the larger of the caller's gap and robots.txt Crawl-delay, capped at 10s."""
    if crawl_delay_s is None or crawl_delay_s <= 0:
        return requested_s
    return max(requested_s, min(float(crawl_delay_s), MAX_CRAWL_DELAY_S))


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
