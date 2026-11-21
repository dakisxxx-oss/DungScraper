"""API-backed site adapters.

Each adapter reads a public API or a documented page format instead of the
generic HTML fetch. Optional adapters return None when they cannot handle a
URL, and the normal fetch continues. Reddit always handles its own URLs:
the official API when credentials are set, otherwise public JSON, then RSS,
then old.reddit HTML. It does not fall through to the generic fetcher.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from dungscraper.adapters.arxiv import ArxivAdapter
from dungscraper.adapters.github import GitHubAdapter
from dungscraper.adapters.hackernews import HackerNewsAdapter
from dungscraper.adapters.reddit import REDDIT_SETUP, RedditAdapter
from dungscraper.adapters.wikipedia import WikipediaAdapter
from dungscraper.adapters.youtube import YouTubeAdapter
from dungscraper.config import Settings
from dungscraper.models import ScrapeData, ScrapeRequest

ADAPTERS = (
    RedditAdapter(),
    YouTubeAdapter(),
    WikipediaAdapter(),
    GitHubAdapter(),
    ArxivAdapter(),
    HackerNewsAdapter(),
)


def adapter_for(url: str):
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for adapter in ADAPTERS:
        if adapter.matches(host, url):
            return adapter
    return None


async def fetch_via_adapter(
    request: ScrapeRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> ScrapeData | None:
    adapter = adapter_for(request.url)
    if adapter is None:
        return None
    return await adapter.fetch(request, settings=settings, client=client)


__all__ = ["ADAPTERS", "REDDIT_SETUP", "adapter_for", "fetch_via_adapter"]
