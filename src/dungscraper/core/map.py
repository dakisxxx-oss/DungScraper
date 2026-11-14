"""Fast URL discovery: sitemaps plus links on the entry page. No browser."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.core.content import extract_links
from dungscraper.core.fetch import http_fetch
from dungscraper.core.robots import RobotsGate
from dungscraper.core.sitemap import discover_sitemap_urls
from dungscraper.core.urls import dedupe_key, extension_skipped, path_matches, same_site
from dungscraper.errors import DungScraperError
from dungscraper.models import MapData, MapLink, MapRequest
from dungscraper.security import assert_public_url


def _allowed(url: str, root: str, request: MapRequest) -> bool:
    if extension_skipped(url):
        return False
    if not same_site(url, root, allow_subdomains=request.allow_subdomains):
        return False
    path = urlsplit(url).path or "/"
    if request.exclude_paths and any(path_matches(path, pattern) for pattern in request.exclude_paths):
        return False
    if request.include_paths and not any(path_matches(path, pattern) for pattern in request.include_paths):
        return False
    return True


def _matches(url: str, title: str | None, search: str | None) -> bool:
    if not search:
        return True
    needle = search.lower()
    return needle in url.lower() or needle in (title or "").lower()


async def map_site(
    request: MapRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> MapData:
    assert_public_url(request.url, allow_private=settings.allow_private_networks)
    respect = settings.respect_robots_default if request.respect_robots is None else request.respect_robots
    gate = RobotsGate(client, settings.robots_user_agent, respect=respect, fail_open=settings.robots_fail_open)
    if respect and not await gate.allowed(request.url):
        raise DungScraperError(
            "robots_disallowed",
            f"{request.url} is disallowed by robots.txt.",
            status=403,
        )
    links: list[MapLink] = []
    seen: set[str] = set()

    def add(url: str, title: str | None, source: str) -> None:
        if len(links) >= request.limit:
            return
        if not _allowed(url, request.url, request):
            return
        if not _matches(url, title, request.search):
            return
        key = dedupe_key(url)
        if key in seen:
            return
        seen.add(key)
        links.append(MapLink(url=url, title=title or None, source=source))  # type: ignore[arg-type]

    sitemap_count = 0
    if request.use_sitemap:
        robots_text = await gate.raw(request.url) if respect else ""
        for url in await discover_sitemap_urls(
            client, request.url, limit=request.limit, robots_text=robots_text
        ):
            if respect and not await gate.allowed(url):
                continue
            before = len(links)
            add(url, None, "sitemap")
            sitemap_count += len(links) - before

    link_count = 0
    if request.discover_links and len(links) < request.limit:
        fetched = await http_fetch(client, request.url, settings=settings)
        if fetched.text:
            for item in extract_links(fetched.text, fetched.url):
                if respect and not await gate.allowed(item.url):
                    continue
                before = len(links)
                add(item.url, item.text or None, "link")
                link_count += len(links) - before
                if len(links) >= request.limit:
                    break
    return MapData(url=request.url, links=links, sitemap_count=sitemap_count, link_count=link_count)
