"""Bounded site crawl: sitemap seeds, link following, robots, dedupe, rate limit."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.core.cache import Cache, NullCache
from dungscraper.core.dedupe import Fingerprint, is_near_duplicate
from dungscraper.core.fetch import BrowserManager
from dungscraper.core.limiter import DomainRateLimiter, polite_interval
from dungscraper.core.robots import RobotsGate
from dungscraper.core.scrape import scrape_url
from dungscraper.core.sitemap import discover_sitemap_urls
from dungscraper.core.urls import dedupe_key, extension_skipped, host_key, path_matches, same_site
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import CrawlRequest, ScrapeData, ScrapeRequest

log = get_logger("dungscraper.crawl")

Progress = Callable[[ScrapeData, int], Awaitable[None]]
Cancel = Callable[[], Awaitable[bool]]


def link_allowed(url: str, root: str, request: CrawlRequest) -> bool:
    if extension_skipped(url):
        return False
    if request.same_domain and not same_site(url, root, allow_subdomains=request.allow_subdomains):
        return False
    path = urlsplit(url).path or "/"
    if request.exclude_paths and any(path_matches(path, pattern) for pattern in request.exclude_paths):
        return False
    if request.include_paths and not any(path_matches(path, pattern) for pattern in request.include_paths):
        return False
    return True


def _stored(data: ScrapeData, formats: list[str]) -> ScrapeData:
    copy = data.model_copy(deep=True)
    keep = set(formats)
    if "markdown" not in keep:
        copy.markdown = None
    if "html" not in keep:
        copy.html = None
    if "raw_html" not in keep:
        copy.raw_html = None
    if "links" not in keep:
        copy.links = None
    if "metadata" not in keep:
        copy.metadata = None
    if "screenshot" not in keep:
        copy.screenshot = None
    if "chunks" not in keep:
        copy.chunks = None
    return copy


async def crawl_site(
    request: CrawlRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache: Cache | None = None,
    browser: BrowserManager | None = None,
    limiter: DomainRateLimiter | None = None,
    on_page: Progress | None = None,
    should_cancel: Cancel | None = None,
    checkpoint: dict | None = None,
    on_checkpoint=None,
) -> dict:
    cache = cache or NullCache()
    limiter = limiter or DomainRateLimiter()
    respect = settings.respect_robots_default if request.respect_robots is None else request.respect_robots
    gate = RobotsGate(client, settings.robots_user_agent, respect=respect, fail_open=settings.robots_fail_open)
    root = request.url
    resumed = bool(checkpoint and checkpoint.get("frontier"))
    if resumed:
        seen = set(checkpoint.get("seen") or [])
        queue = [(item[0], int(item[1])) for item in checkpoint.get("frontier") or []]
        skipped_duplicates = int(checkpoint.get("skipped_duplicates") or 0)
        skipped_robots = int(checkpoint.get("skipped_robots") or 0)
        fingerprints = [Fingerprint(**item) for item in checkpoint.get("fingerprints") or []]
    else:
        seen = {dedupe_key(root)}
        queue = [(root, 0)]
        skipped_duplicates = 0
        skipped_robots = 0
        fingerprints = []
    if not resumed and request.use_sitemap and request.max_depth >= 1:
        robots_text = await gate.raw(root) if respect else ""
        try:
            sitemap_urls = await discover_sitemap_urls(client, root, limit=request.limit * 3, robots_text=robots_text)
        except Exception as exc:
            log.info("sitemap discovery failed", extra={"data": {"error": str(exc)}})
            sitemap_urls = []
        for url in sitemap_urls:
            key = dedupe_key(url)
            if key in seen or not link_allowed(url, root, request):
                continue
            seen.add(key)
            queue.append((url, 1))
            if len(queue) >= request.limit * 3:
                break

    pages: list[ScrapeData] = []
    errors: list[dict] = []
    semaphore = asyncio.Semaphore(max(1, request.concurrency))
    formats = list(request.scrape_options.formats)

    async def one(url: str, depth: int):
        async with semaphore:
            if should_cancel and await should_cancel():
                return None
            delay = await gate.crawl_delay(url)
            await limiter.wait(host_key(url), polite_interval(request.delay_ms / 1000, delay))
            options = request.scrape_options.model_dump()
            options["formats"] = list(dict.fromkeys([*formats, "markdown", "links"]))
            options["respect_robots"] = respect
            scrape_request = ScrapeRequest(url=url, **options)
            try:
                data = await scrape_url(
                    scrape_request,
                    settings=settings,
                    client=client,
                    cache=cache,
                    browser=browser,
                    robots=gate,
                )
                return url, depth, data, None
            except DungScraperError as exc:
                return url, depth, None, exc
            except Exception as exc:  # noqa: BLE001
                log.exception("crawl page failed", extra={"data": {"url": url}})
                return url, depth, None, DungScraperError("fetch_failed", str(exc), status=502)

    while queue and len(pages) < request.limit:
        if should_cancel and await should_cancel():
            break
        batch: list[tuple[str, int]] = []
        while queue and len(batch) < request.concurrency and len(pages) + len(batch) < request.limit:
            batch.append(queue.pop(0))
        results = await asyncio.gather(*(one(url, depth) for url, depth in batch))
        for item in results:
            if item is None:
                continue
            url, depth, data, error = item
            if error is not None:
                if error.code == "robots_disallowed":
                    skipped_robots += 1
                else:
                    errors.append({"url": url, "code": error.code, "message": error.message})
                continue
            assert data is not None
            duplicate, fp = is_near_duplicate(data.markdown or "", fingerprints) if request.dedupe else (False, None)
            if duplicate:
                skipped_duplicates += 1
                continue
            if fp is not None:
                fingerprints.append(fp)
            stored = _stored(data, formats)
            pages.append(stored)
            if on_page:
                await on_page(stored, len(pages))
            if on_checkpoint:
                await on_checkpoint(
                    {
                        "seen": list(seen),
                        "frontier": [[queued, depth_] for queued, depth_ in queue],
                        "skipped_duplicates": skipped_duplicates,
                        "skipped_robots": skipped_robots,
                        "fingerprints": [
                            {"simhash": fp.simhash, "exact": fp.exact, "words": fp.words} for fp in fingerprints
                        ],
                    }
                )
            if len(pages) >= request.limit:
                break
            if depth < request.max_depth:
                for link in data.links or []:
                    key = dedupe_key(link.url)
                    if key in seen or not link_allowed(link.url, root, request):
                        continue
                    seen.add(key)
                    queue.append((link.url, depth + 1))
                    if len(queue) >= request.limit * 5:
                        break
    return {
        "pages": pages,
        "errors": errors,
        "skipped_duplicates": skipped_duplicates,
        "skipped_robots": skipped_robots,
        "completed": len(pages),
        "failed": len(errors),
    }
