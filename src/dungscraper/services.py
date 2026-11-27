"""Process-wide HTTP client, cache, browser, and job store."""

from __future__ import annotations

import asyncio

import redis.asyncio as redis_async

from dungscraper.config import Settings
from dungscraper.core.cache import DiskCache, MemoryCache, NullCache, RedisCache
from dungscraper.core.fetch import BrowserManager, make_client
from dungscraper.core.jobs import MemoryJobStore, RedisJobStore
from dungscraper.api.usage import UsageTracker
from dungscraper.core.limiter import DomainRateLimiter
from dungscraper.log import get_logger, setup_logging

log = get_logger("dungscraper.services")

_current: "Services | None" = None


class Services:
    def __init__(self, settings: Settings, client, cache, jobs, browser, redis=None) -> None:
        self.settings = settings
        self.client = client
        self.cache = cache
        self.jobs = jobs
        self.browser = browser
        self.redis = redis
        self.limiter = DomainRateLimiter()
        self.usage = UsageTracker.from_settings(settings)
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task


def current() -> Services | None:
    return _current


async def startup(settings: Settings) -> Services:
    global _current
    setup_logging(settings)
    client = make_client(settings, proxy=(settings.proxy_list or [None])[0])
    redis = None
    if settings.redis_url:
        redis = redis_async.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception as exc:
            if settings.crawl_mode == "queue":
                raise
            log.warning("redis unavailable, using memory", extra={"data": {"error": str(exc)}})
            await redis.aclose()
            redis = None
    if redis is not None:
        cache = RedisCache(redis) if settings.cache_enabled else NullCache()
        jobs = RedisJobStore(redis, settings.job_ttl_seconds)
    else:
        if not settings.cache_enabled:
            cache = NullCache()
        elif settings.cache_dir.strip():
            cache = DiskCache(settings.cache_dir, settings.cache_max_files)
        else:
            cache = MemoryCache(settings.cache_max_memory_items)
        jobs = MemoryJobStore()
    services = Services(
        settings=settings,
        client=client,
        cache=cache,
        jobs=jobs,
        browser=BrowserManager(settings),
        redis=redis,
    )
    _current = services
    log.info(
        "ready",
        extra={
            "data": {
                "crawl_mode": settings.resolved_crawl_mode(),
                "redis": bool(redis),
                "auth": bool(settings.parsed_api_keys),
            }
        },
    )
    return services


async def shutdown(services: Services | None = None) -> None:
    global _current
    services = services or _current
    if services is None:
        return
    for task in list(services.tasks):
        task.cancel()
    if services.tasks:
        await asyncio.gather(*services.tasks, return_exceptions=True)
    await services.browser.shutdown()
    await services.client.aclose()
    if services.redis is not None:
        await services.redis.aclose()
    if _current is services:
        _current = None
