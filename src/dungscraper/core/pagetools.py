"""Page-level tools built on scrape: CSS extraction and query-focused reading."""

from __future__ import annotations

from typing import Any

import httpx

from dungscraper.config import Settings
from dungscraper.core.css_extract import extract_with_schema, normalize_schema
from dungscraper.core.fetch import BrowserManager
from dungscraper.core.focus import focus_markdown
from dungscraper.core.scrape import scrape_url
from dungscraper.models import CssExtractRequest, FocusRequest, ScrapeRequest


async def extract_css(
    body: CssExtractRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache=None,
    browser: BrowserManager | None = None,
) -> dict[str, Any]:
    normalize_schema(body.schema_)  # fail before any network call
    data = await scrape_url(
        ScrapeRequest(
            url=body.url,
            render=body.render,
            wait_for=body.wait_for,
            formats=["raw_html"],
            only_main_content=False,
            cache=body.cache,
        ),
        settings=settings,
        client=client,
        cache=cache,
        browser=browser,
    )
    result = extract_with_schema(data.raw_html or "", body.schema_, base_url=data.url)
    return {"url": data.url, "fetch_tier": data.fetch_tier, "status_code": data.status_code, **result}


async def focus_page(
    body: FocusRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache=None,
    browser: BrowserManager | None = None,
) -> dict[str, Any]:
    data = await scrape_url(
        ScrapeRequest(
            url=body.url,
            render=body.render,
            formats=["markdown", "metadata"],
            only_main_content=body.only_main_content,
        ),
        settings=settings,
        client=client,
        cache=cache,
        browser=browser,
    )
    focused = focus_markdown(data.markdown or "", body.query, max_tokens=body.max_tokens)
    return {
        "url": data.url,
        "title": data.metadata.title if data.metadata else None,
        "query": body.query,
        "fetch_tier": data.fetch_tier,
        **focused,
    }
