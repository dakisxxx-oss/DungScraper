"""Concurrent batch scraping with a global limit, a per-host limit and per-host spacing."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.core.limiter import DomainRateLimiter
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import BatchItem, ScrapeOptions, ScrapeRequest

log = get_logger("dungscraper.batch")


async def batch_scrape_urls(
    urls: list[str],
    options: ScrapeOptions,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache=None,
    browser=None,
    concurrency: int = 4,
    per_host_concurrency: int = 2,
    delay_ms: int = 0,
    limiter: DomainRateLimiter | None = None,
) -> list[BatchItem]:
    """Scrape every URL. Results keep the input order. A failure is reported per URL, never for the batch.

    `concurrency` caps requests in flight overall, `per_host_concurrency` caps them per host, and
    `delay_ms` is the minimum gap between two request starts on the same host.
    """
    limiter = limiter or DomainRateLimiter()
    overall = asyncio.Semaphore(max(1, concurrency))
    per_host: dict[str, asyncio.Semaphore] = {}
    payload = options.model_dump()

    async def one(url: str) -> BatchItem:
        host = (urlsplit(url).hostname or "").lower()
        gate = per_host.setdefault(host, asyncio.Semaphore(max(1, per_host_concurrency)))
        # Take the per-host slot first so a slow host cannot hold the overall slots while it waits.
        async with gate:
            async with overall:
                await limiter.wait(host, delay_ms / 1000)
                started = time.perf_counter()
                try:
                    data = await scrape_url(
                        ScrapeRequest(url=url, **payload),
                        settings=settings,
                        client=client,
                        cache=cache,
                        browser=browser,
                    )
                    return BatchItem(url=url, data=data)
                except DungScraperError as exc:
                    return BatchItem(url=url, error=f"{exc.code}: {exc.message}")
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "batch item failed", extra={"data": {"url": url, "ms": int((time.perf_counter() - started) * 1000)}}
                    )
                    return BatchItem(url=url, error=f"fetch_failed: {exc}")

    return list(await asyncio.gather(*(one(url) for url in urls)))
