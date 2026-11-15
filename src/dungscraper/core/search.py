"""Web search providers. DuckDuckGo HTML needs no key; the others are optional."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from dungscraper.config import Settings
from dungscraper.core.fetch import merge_headers
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import ScrapeRequest, SearchData, SearchRequest, SearchResultItem

log = get_logger("dungscraper.search")


@dataclass
class Hit:
    url: str
    title: str
    snippet: str


def provider_chain(name: str, settings: Settings, *, fallback: bool = True) -> list[str]:
    """Providers to try, in order. DuckDuckGo is always last when fallback is on."""
    available: list[str] = []
    if settings.brave_api_key:
        available.append("brave")
    if settings.serper_api_key:
        available.append("serper")
    if settings.searxng_url:
        available.append("searxng")
    available.append("duckduckgo")
    if name == "auto":
        return available
    if name not in {"duckduckgo", "brave", "serper", "searxng"}:
        raise DungScraperError("search_not_configured", f"Unknown search provider {name}.", status=400)
    if not fallback:
        return [name]
    return [name] + [item for item in available if item != name]


def resolve_provider(name: str, settings: Settings) -> str:
    if name == "brave":
        if not settings.brave_api_key:
            raise DungScraperError(
                "search_not_configured",
                "Brave search needs DUNGSCRAPER_BRAVE_API_KEY, or choose provider duckduckgo.",
                status=400,
            )
        return "brave"
    if name == "serper":
        if not settings.serper_api_key:
            raise DungScraperError(
                "search_not_configured",
                "Serper needs DUNGSCRAPER_SERPER_API_KEY, or choose provider duckduckgo.",
                status=400,
            )
        return "serper"
    if name == "searxng":
        if not settings.searxng_url:
            raise DungScraperError(
                "search_not_configured",
                "SearXNG needs DUNGSCRAPER_SEARXNG_URL, for example http://searxng:8080.",
                status=400,
            )
        return "searxng"
    if name == "duckduckgo":
        return "duckduckgo"
    if settings.brave_api_key:
        return "brave"
    if settings.serper_api_key:
        return "serper"
    if settings.searxng_url:
        return "searxng"
    return "duckduckgo"


def unwrap_duckduckgo(href: str) -> str:
    if not href:
        return ""
    parts = urlsplit(href)
    if parts.path.startswith("/l/") or "uddg" in parts.query:
        values = parse_qs(parts.query).get("uddg") or []
        if values:
            return unquote(values[0])
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return urljoin("https://duckduckgo.com", href)
    return href


def parse_duckduckgo_html(html: str) -> list[Hit]:
    soup = BeautifulSoup(html, "lxml")
    hits: list[Hit] = []
    anchors = soup.select("a.result__a")
    if anchors:
        for anchor in anchors:
            url = unwrap_duckduckgo(anchor.get("href") or "")
            title = anchor.get_text(" ", strip=True)
            parent = anchor.find_parent(class_="result")
            snippet_node = parent.select_one(".result__snippet") if parent else None
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            if url.startswith("http"):
                hits.append(Hit(url=url, title=title, snippet=snippet))
        return hits
    for anchor in soup.select("a.result-link"):
        url = unwrap_duckduckgo(anchor.get("href") or "")
        title = anchor.get_text(" ", strip=True)
        row = anchor.find_parent("tr")
        snippet = ""
        if row is not None:
            nxt = row.find_next_sibling("tr")
            if nxt:
                snippet = nxt.get_text(" ", strip=True)
        if url.startswith("http"):
            hits.append(Hit(url=url, title=title, snippet=snippet))
    return hits


def parse_duckduckgo_lite(html: str) -> list[Hit]:
    soup = BeautifulSoup(html, "lxml")
    hits: list[Hit] = []
    for anchor in soup.select("a"):
        href = anchor.get("href") or ""
        classes = " ".join(anchor.get("class") or [])
        if "result-link" not in classes and not href.startswith("http"):
            continue
        if "result-link" not in classes and "duckduckgo.com" in href:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or not href.startswith("http"):
            continue
        hits.append(Hit(url=href, title=title, snippet=""))
    return hits


async def search_duckduckgo(client: httpx.AsyncClient, query: str, limit: int, settings: Settings) -> list[Hit]:
    headers = merge_headers(settings, {"Referer": "https://html.duckduckgo.com/"})
    response = await client.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers=headers,
        timeout=20,
    )
    hits = parse_duckduckgo_html(response.text)[:limit]
    if hits:
        return hits
    lite = await client.get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        headers=headers,
        timeout=20,
    )
    hits = parse_duckduckgo_lite(lite.text)[:limit]
    if not hits:
        raise DungScraperError(
            "search_failed",
            "DuckDuckGo returned no parseable results. It may be rate-limiting this server. "
            "Set DUNGSCRAPER_SEARXNG_URL, DUNGSCRAPER_BRAVE_API_KEY, or DUNGSCRAPER_SERPER_API_KEY.",
            status=502,
        )
    return hits


async def search_searxng(client: httpx.AsyncClient, query: str, limit: int, base_url: str) -> list[Hit]:
    response = await client.get(
        base_url.rstrip("/") + "/search",
        params={"q": query, "format": "json", "categories": "general"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise DungScraperError("search_failed", f"SearXNG returned HTTP {response.status_code}.", status=502)
    payload = response.json()
    hits: list[Hit] = []
    for item in payload.get("results") or []:
        url = item.get("url") or ""
        if url.startswith("http"):
            hits.append(Hit(url=url, title=item.get("title") or "", snippet=item.get("content") or ""))
        if len(hits) >= limit:
            break
    return hits


async def search_brave(client: httpx.AsyncClient, query: str, limit: int, api_key: str) -> list[Hit]:
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise DungScraperError("search_failed", f"Brave search returned HTTP {response.status_code}.", status=502)
    web = (response.json().get("web") or {}).get("results") or []
    return [
        Hit(url=item.get("url") or "", title=item.get("title") or "", snippet=item.get("description") or "")
        for item in web
        if (item.get("url") or "").startswith("http")
    ][:limit]


async def search_serper(client: httpx.AsyncClient, query: str, limit: int, api_key: str) -> list[Hit]:
    response = await client.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": limit},
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise DungScraperError("search_failed", f"Serper returned HTTP {response.status_code}.", status=502)
    organic = response.json().get("organic") or []
    return [
        Hit(url=item.get("link") or "", title=item.get("title") or "", snippet=item.get("snippet") or "")
        for item in organic
        if (item.get("link") or "").startswith("http")
    ][:limit]


async def _hits_from(provider: str, client: httpx.AsyncClient, query: str, limit: int, settings: Settings) -> list[Hit]:
    if provider == "brave":
        if not settings.brave_api_key:
            raise DungScraperError("search_not_configured", "Brave search needs DUNGSCRAPER_BRAVE_API_KEY.", status=400)
        return await search_brave(client, query, limit, settings.brave_api_key)
    if provider == "serper":
        if not settings.serper_api_key:
            raise DungScraperError("search_not_configured", "Serper needs DUNGSCRAPER_SERPER_API_KEY.", status=400)
        return await search_serper(client, query, limit, settings.serper_api_key)
    if provider == "searxng":
        if not settings.searxng_url:
            raise DungScraperError("search_not_configured", "SearXNG needs DUNGSCRAPER_SEARXNG_URL.", status=400)
        return await search_searxng(client, query, limit, settings.searxng_url)
    return await search_duckduckgo(client, query, limit, settings)


async def search_web(
    request: SearchRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache=None,
    browser=None,
) -> SearchData:
    chain = provider_chain(request.provider, settings, fallback=request.fallback)
    hits: list[Hit] = []
    provider = chain[0]
    notes: list[str] = []
    for index, candidate in enumerate(chain):
        try:
            found = await _hits_from(candidate, client, request.query, request.limit, settings)
        except DungScraperError as exc:
            notes.append(f"{candidate}: {exc.message}")
            if index == 0 and not request.fallback:
                raise
            continue
        if found:
            hits = found
            provider = candidate
            if index:
                notes.append(f"used {candidate} after {chain[0]} failed")
            break
        notes.append(f"{candidate}: no results")
    if not hits:
        detail = " ".join(notes) or "no providers returned results"
        raise DungScraperError(
            "search_failed",
            "Search failed across the provider chain. " + detail,
            status=502,
        )
    results: list[SearchResultItem] = []
    for hit in hits[: request.limit]:
        item = SearchResultItem(url=hit.url, title=hit.title or None, snippet=hit.snippet or None)
        if request.scrape:
            try:
                options = request.scrape_options.model_dump()
                scraped = await scrape_url(
                    ScrapeRequest(url=hit.url, **options),
                    settings=settings,
                    client=client,
                    cache=cache,
                    browser=browser,
                )
                item.markdown = scraped.markdown
                item.metadata = scraped.metadata
            except DungScraperError as exc:
                item.error = f"{exc.code}: {exc.message}"
            except Exception as exc:  # noqa: BLE001
                log.exception("search scrape failed", extra={"data": {"url": hit.url}})
                item.error = str(exc)
        results.append(item)
    return SearchData(query=request.query, provider=provider, results=results, notes=notes)
