"""One URL in, LLM-ready content out."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx

from dungscraper.adapters import adapter_for, fetch_via_adapter
from dungscraper.config import Settings, get_settings
from dungscraper.core.cache import Cache, NullCache
from dungscraper.core.chunk import chunk_markdown
from dungscraper.core.content import challenge_vendor, html_to_content, is_challenge, should_escalate
from dungscraper.core.escalate import escalate_challenge
from dungscraper.core.documents import docx_to_markdown, pdf_to_markdown
from dungscraper.core.feeds import items_to_markdown, looks_like_feed, parse_feed
from dungscraper.core.fetch import BrowserManager, RawFetch, http_fetch, make_client, sticky_proxies, sniff_kind
from dungscraper.core.robots import RobotsGate
from dungscraper.core.urls import clean_request_url, safe_url
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import FORMATS, ChunkOptions, LinkItem, PageMetadata, ScrapeData, ScrapeRequest
from dungscraper.security import assert_public_url

log = get_logger("dungscraper.scrape")

_browser: BrowserManager | None = None
_browser_settings_id: int | None = None


def get_browser(settings: Settings) -> BrowserManager:
    global _browser, _browser_settings_id
    if _browser is None or _browser_settings_id != id(settings):
        _browser = BrowserManager(settings)
        _browser_settings_id = id(settings)
    return _browser


async def shutdown_browser() -> None:
    global _browser
    if _browser is not None:
        await _browser.shutdown()
        _browser = None


def cache_key(request: ScrapeRequest) -> str:
    payload = request.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _apply_formats(data: ScrapeData, formats: list[str]) -> ScrapeData:
    keep = set(formats)
    if "markdown" not in keep:
        data.markdown = None
    if "html" not in keep:
        data.html = None
    if "raw_html" not in keep:
        data.raw_html = None
    if "links" not in keep:
        data.links = None
    if "metadata" not in keep:
        data.metadata = None
    if "screenshot" not in keep:
        data.screenshot = None
    if "chunks" not in keep:
        data.chunks = None
    return data


def _from_documents(kind: str, fetched: RawFetch, request: ScrapeRequest) -> ScrapeData:
    if kind == "pdf":
        markdown, meta = pdf_to_markdown(fetched.content)
        title = meta.get("title")
    elif kind == "docx":
        markdown, _meta = docx_to_markdown(fetched.content)
        title = None
    else:
        markdown = fetched.text or fetched.content.decode("utf-8", "replace")
        title = None
    metadata = PageMetadata(title=title) if "metadata" in request.formats or title else PageMetadata(title=title)
    chunks = None
    if "chunks" in request.formats:
        options = request.chunk or ChunkOptions()
        chunks = chunk_markdown(
            markdown,
            max_tokens=options.max_tokens,
            overlap_tokens=options.overlap_tokens,
            metadata={"url": fetched.url, "title": title or ""},
        )
    return ScrapeData(
        url=fetched.url,
        markdown=markdown,
        raw_html=None,
        html=None,
        links=[],
        metadata=metadata,
        chunks=chunks,
        status_code=fetched.status_code,
        content_type=fetched.content_type,
        render_mode=fetched.render_mode,
        warnings=list(fetched.warnings) + ([f"parsed_{kind}"] if kind in {"pdf", "docx"} else []),
    )


async def _maybe_browser(request: ScrapeRequest, settings: Settings, browser: BrowserManager) -> RawFetch:
    return await browser.fetch(
        clean_request_url(request.url),
        headers=request.headers,
        cookies=request.cookies,
        proxy=request.proxy or settings.proxy_url or None,
        timeout_ms=request.timeout_ms,
        wait_until=request.wait_until,
        wait_for=request.wait_for,
        wait_ms=request.wait_ms,
        actions=request.actions,
        screenshot="screenshot" in request.formats,
        screenshot_full_page=request.screenshot_full_page,
    )


def _needs_browser(request: ScrapeRequest) -> bool:
    path = request.url.lower().split("?", 1)[0]
    if path.endswith((".pdf", ".docx")):
        return False
    if request.render == "browser":
        return True
    if request.render == "http":
        return False
    if request.actions or request.wait_for or request.wait_ms or "screenshot" in request.formats:
        return True
    return False


async def scrape_url(
    request: ScrapeRequest,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    cache: Cache | None = None,
    browser: BrowserManager | None = None,
    robots: RobotsGate | None = None,
) -> ScrapeData:
    settings = settings or get_settings()
    started = time.perf_counter()
    assert_public_url(request.url, allow_private=settings.allow_private_networks)
    target = clean_request_url(request.url)
    request = request.model_copy(update={"url": target})
    path = urlsplit(target).path.lower()
    if path.endswith((".pdf", ".docx")) and not any(key.lower() == "user-agent" for key in request.headers):
        # File hosts often challenge a browser user agent and serve the bytes to a plain one.
        request = request.model_copy(
            update={
                "headers": {
                    **request.headers,
                    "User-Agent": settings.robots_user_agent,
                    "Accept": "*/*",
                }
            }
        )

    cache = cache if cache is not None else NullCache()
    key = cache_key(request)
    if request.cache and settings.cache_enabled:
        cached = await cache.get(key)
        if cached:
            data = ScrapeData.model_validate(cached)
            data.warnings = [*data.warnings, "cache_hit"]
            data.timings_ms = {"total_ms": int((time.perf_counter() - started) * 1000)}
            return data

    proxy = request.proxy or sticky_proxies(settings).for_domain(urlsplit(target).hostname or "")
    own_client = client is None or bool(request.proxy)
    if client is None or request.proxy:
        client = make_client(settings, proxy=proxy)
    respect = settings.respect_robots_default if request.respect_robots is None else request.respect_robots
    gate = robots or RobotsGate(
        client,
        settings.robots_user_agent,
        respect=respect,
        fail_open=settings.robots_fail_open,
    )
    try:
        adapted = await _from_adapter(request, settings=settings, client=client)
        if adapted is not None:
            adapted.timings_ms = {"total_ms": int((time.perf_counter() - started) * 1000)}
            adapted = _apply_formats(adapted, request.formats or ["markdown"])
            _note_block(adapted)
            if request.cache and settings.cache_enabled:
                await cache.set(key, adapted.model_dump(mode="json"), settings.cache_ttl_seconds)
            return adapted
        if respect and not await gate.allowed(target):
            raise DungScraperError(
                "robots_disallowed",
                f"{safe_url(target)} is disallowed by robots.txt for {settings.robots_user_agent}. "
                "Pass respect_robots=false only for a site you are allowed to access.",
                status=403,
            )
        fetch_started = time.perf_counter()
        browser_mgr = browser or get_browser(settings)
        if _needs_browser(request):
            fetched = await _maybe_browser(request, settings, browser_mgr)
        else:
            fetched = await http_fetch(
                client,
                target,
                settings=settings,
                headers=request.headers,
                cookies=request.cookies,
                timeout=(request.timeout_ms / 1000) if request.timeout_ms else None,
            )
            kind = sniff_kind(fetched.content, fetched.content_type, fetched.url)
            feed_body = fetched.text or ""
            reason = None
            if kind == "html" and not looks_like_feed(feed_body, fetched.content_type or "", fetched.url):
                reason = should_escalate(feed_body, fetched.status_code)
            if reason == "js_shell" and request.render == "auto":
                fetched.warnings.append("upgraded_to_browser:js_shell")
                browser_fetch = await _maybe_browser(request, settings, browser_mgr)
                browser_fetch.warnings = [*fetched.warnings, *browser_fetch.warnings]
                browser_fetch.tier = "playwright"
                fetched = browser_fetch
            elif reason == "challenge" and request.render == "auto":
                fetched.warnings.append("challenge_detected")
                fetched = await escalate_challenge(
                    fetched,
                    request=request,
                    settings=settings,
                    client=client,
                    browser=browser_mgr,
                    proxy=proxy,
                )
        fetch_ms = int((time.perf_counter() - fetch_started) * 1000)
        kind = sniff_kind(fetched.content, fetched.content_type, fetched.url)
        if fetched.status_code >= 400 and not (fetched.text or fetched.content):
            raise DungScraperError(
                "http_error",
                f"{safe_url(fetched.url)} returned HTTP {fetched.status_code} with an empty body.",
                status=502,
                details={"status_code": fetched.status_code},
            )
        extract_started = time.perf_counter()
        data = _from_feed(fetched, request)
        if data is not None:
            pass
        elif kind in {"pdf", "docx", "text"} and kind != "html":
            if kind == "binary":
                raise DungScraperError(
                    "unsupported_content",
                    f"Unsupported content type {fetched.content_type or 'unknown'} at {safe_url(fetched.url)}.",
                    status=415,
                )
            data = _from_documents(kind, fetched, request)
        elif kind == "binary":
            raise DungScraperError(
                "unsupported_content",
                f"Unsupported content type {fetched.content_type or 'unknown'} at {safe_url(fetched.url)}.",
                status=415,
            )
        else:
            extracted = html_to_content(
                fetched.text or fetched.content.decode("utf-8", "replace"),
                fetched.url,
                only_main_content=request.only_main_content,
                exclude_selectors=request.exclude_selectors,
                include_selectors=request.include_selectors,
            )
            chunks = None
            if "chunks" in request.formats:
                options = request.chunk or ChunkOptions()
                chunks = chunk_markdown(
                    extracted.markdown,
                    max_tokens=options.max_tokens,
                    overlap_tokens=options.overlap_tokens,
                    metadata={"url": fetched.url, "title": extracted.metadata.title or ""},
                )
            screenshot = None
            if fetched.screenshot:
                screenshot = base64.b64encode(fetched.screenshot).decode("ascii")
            warnings = list(fetched.warnings)
            if request.render == "http" and "screenshot" in request.formats:
                warnings.append("screenshot_requires_browser")
            data = ScrapeData(
                url=fetched.url,
                markdown=extracted.markdown,
                html=extracted.html,
                raw_html=fetched.text if "raw_html" in request.formats else None,
                links=extracted.links,
                metadata=extracted.metadata,
                screenshot=screenshot,
                chunks=chunks,
                status_code=fetched.status_code,
                content_type=fetched.content_type,
                render_mode=fetched.render_mode,
                warnings=warnings,
            )
        extract_ms = int((time.perf_counter() - extract_started) * 1000)
        data.timings_ms = {
            "total_ms": int((time.perf_counter() - started) * 1000),
            "fetch_ms": fetch_ms,
            "extract_ms": extract_ms,
        }
        data = _apply_formats(data, request.formats or ["markdown"])
        if not data.fetch_tier:
            data.fetch_tier = getattr(fetched, "tier", None) or data.fetch_tier
        _note_block(data)
        if request.cache and settings.cache_enabled and "screenshot" not in request.formats:
            encoded = data.model_dump(mode="json")
            blob = json.dumps(encoded)
            if len(blob) < 1_500_000:
                await cache.set(key, encoded, settings.cache_ttl_seconds)
        log.info(
            "scrape",
            extra={
                "data": {
                    "url": safe_url(data.url),
                    "status": data.status_code,
                    "render_mode": data.render_mode,
                    "ms": data.timings_ms.get("total_ms"),
                }
            },
        )
        return data
    finally:
        if own_client:
            await client.aclose()


def _from_feed(fetched: RawFetch, request: ScrapeRequest) -> ScrapeData | None:
    text = fetched.text or ""
    if not text and fetched.content:
        text = fetched.content.decode("utf-8", "replace")
    if not looks_like_feed(text, fetched.content_type or "", fetched.url):
        return None
    title, items = parse_feed(text)
    if not items:
        return None
    markdown = items_to_markdown(title or "Feed", items)
    chunks = None
    if "chunks" in request.formats:
        options = request.chunk or ChunkOptions()
        chunks = chunk_markdown(
            markdown,
            max_tokens=options.max_tokens,
            overlap_tokens=options.overlap_tokens,
            metadata={"url": fetched.url, "title": title or ""},
        )
    return ScrapeData(
        url=fetched.url,
        markdown=markdown,
        html=None,
        raw_html=text if "raw_html" in request.formats else None,
        links=[LinkItem(url=item.link, text=item.title) for item in items if item.link],
        metadata=PageMetadata(title=title or "Feed", description="RSS or Atom feed"),
        chunks=chunks,
        status_code=fetched.status_code,
        content_type=fetched.content_type,
        render_mode="rss",
        warnings=[*fetched.warnings, "feed"],
        fetch_tier="feed",
    )


def known_formats() -> set[str]:
    return set(FORMATS)


def _note_block(data: ScrapeData) -> None:
    if data.block_reason:
        return
    vendor = challenge_vendor(data.markdown or "", data.status_code or 200)
    if vendor or "challenge_unresolved" in data.warnings:
        data.block_reason = vendor or "challenge"


async def _from_adapter(request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
    adapter = adapter_for(request.url)
    if adapter is None:
        return None
    interactive = bool(request.actions or request.wait_for or "screenshot" in request.formats or request.render == "browser")
    if interactive and getattr(adapter, "name", "") != "reddit":
        return None
    try:
        adapted = await fetch_via_adapter(request, settings=settings, client=client)
        if adapted is not None:
            adapted.fetch_tier = adapted.fetch_tier or "api"
        return adapted
    except DungScraperError as exc:
        if str(getattr(adapter, "name", "")) == "reddit" or exc.code.startswith("reddit_"):
            raise
        log.info("adapter failed", extra={"data": {"adapter": getattr(adapter, "name", ""), "error": exc.message}})
        return None
