"""MCP tools for agents. Stdio, or streamable HTTP mounted on the API."""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from dungscraper import __version__
from dungscraper.config import get_settings
from dungscraper.core.chunk import chunk_markdown
from dungscraper.core.extract import extract_structured
from dungscraper.core.jobs import job_view, start_crawl
from dungscraper.core.map import map_site
from dungscraper.core.scrape import scrape_url
from dungscraper.core.search import search_web
from dungscraper.errors import DungScraperError
from dungscraper.models import (
    ChunkRequest,
    CrawlRequest,
    ExtractRequest,
    MapRequest,
    ScrapeRequest,
    SearchRequest,
)
from dungscraper.services import current, startup

INSTRUCTIONS = (
    "DungScraper reads the web for you. Use scrape for one URL, map_site to list URLs, "
    "crawl to read many pages on one site, search_web to search and read the top hits, "
    "extract to pull JSON, and chunk_text to split Markdown for retrieval. "
    "scrape tries HTTP first and uses a browser when the page needs JavaScript. "
    "robots.txt is respected unless respect_robots is false."
)


def build_mcp() -> MCPServer:
    mcp = MCPServer(
        "DungScraper",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @mcp.tool(description=(
        "Fetch one URL and return LLM-ready Markdown. Use this for a single page. "
        "render=auto uses a fast HTTP fetch and falls back to a headless browser when the page is script-rendered. "
        "render=browser forces Chromium (also use it for screenshots, clicks, and waits). "
        "formats is a comma-separated list: markdown, html, raw_html, links, metadata, screenshot, chunks. "
        "actions_json is a JSON list of {type: click|scroll|type|wait|press, selector, text, key, ms, pixels}. "
        "Set respect_robots false only for a site you are allowed to access."
    ))
    async def scrape(
        url: str,
        render: str = "auto",
        formats: str = "markdown,metadata",
        only_main_content: bool = True,
        wait_for_selector: str = "",
        extra_wait_ms: int = 0,
        headers_json: str = "",
        cookies_json: str = "",
        proxy: str = "",
        actions_json: str = "",
        respect_robots: bool = True,
    ) -> str:
        payload = {
            "url": url,
            "render": render,
            "formats": [part.strip() for part in formats.split(",") if part.strip()],
            "only_main_content": only_main_content,
            "wait_for": wait_for_selector or None,
            "wait_ms": extra_wait_ms or None,
            "headers": _json_object(headers_json, "headers_json"),
            "cookies": _json_object(cookies_json, "cookies_json"),
            "proxy": proxy or None,
            "actions": _json_list(actions_json, "actions_json"),
            "respect_robots": respect_robots,
        }
        return await _dispatch("POST", "/v1/scrape", payload, None)

    @mcp.tool(description=(
        "Crawl one site and return Markdown for each page. Bounded by limit and max_depth. "
        "Uses sitemap.xml and robots.txt, skips near-duplicate pages, and spaces requests per domain. "
        "wait=true blocks until the crawl finishes (best for small limits). wait=false returns a job id; "
        "poll it with crawl_status. include_paths and exclude_paths are comma-separated globs or re:regex."
    ))
    async def crawl(
        url: str,
        limit: int = 10,
        max_depth: int = 2,
        wait: bool = True,
        include_paths: str = "",
        exclude_paths: str = "",
        respect_robots: bool = True,
    ) -> str:
        payload = {
            "url": url,
            "limit": limit,
            "max_depth": max_depth,
            "wait": wait,
            "include_paths": _csv(include_paths),
            "exclude_paths": _csv(exclude_paths),
            "respect_robots": respect_robots,
            "scrape_options": {"formats": ["markdown", "metadata"]},
        }
        return await _dispatch("POST", "/v1/crawl", payload, None)

    @mcp.tool(description="Poll a crawl started with crawl(wait=false). Returns status, pages, and errors.")
    async def crawl_status(job_id: str) -> str:
        async def local(services):
            job = await services.jobs.get(job_id)
            if job is None:
                raise DungScraperError("job_not_found", f"No crawl job {job_id}.", status=404)
            return job_view(job).model_dump(mode="json")

        return await _dispatch("GET", f"/v1/crawl/{job_id}", None, local)

    @mcp.tool(
        name="map_site",
        description=(
            "Quickly list URLs on a site from sitemap.xml and links on the entry page. "
            "Does not render JavaScript. Optional search keeps URLs or titles that contain the string."
        ),
    )
    async def map_site_tool(
        url: str,
        search: str = "",
        limit: int = 100,
        allow_subdomains: bool = False,
    ) -> str:
        payload = {
            "url": url,
            "search": search or None,
            "limit": limit,
            "allow_subdomains": allow_subdomains,
        }

        async def local(services):
            data = await map_site(MapRequest.model_validate(payload), settings=services.settings, client=services.client)
            return data.model_dump(mode="json")

        return await _dispatch("POST", "/v1/map", payload, local)

    @mcp.tool(
        name="search_web",
        description=(
            "Search the web, then scrape the top results into Markdown. "
            "provider=auto uses Brave, Serper, or SearXNG when those env keys are set, otherwise keyless DuckDuckGo HTML. "
            "DuckDuckGo may rate-limit datacenter IPs; set DUNGSCRAPER_SEARXNG_URL for a reliable self-hosted index."
        ),
    )
    async def search_web_tool(
        query: str,
        limit: int = 5,
        provider: str = "auto",
        scrape_results: bool = True,
    ) -> str:
        payload = {"query": query, "limit": limit, "provider": provider, "scrape": scrape_results}

        async def local(services):
            data = await search_web(
                SearchRequest.model_validate(payload),
                settings=services.settings,
                client=services.client,
                cache=services.cache,
                browser=services.browser,
            )
            return data.model_dump(mode="json")

        return await _dispatch("POST", "/v1/search", payload, local)

    @mcp.tool(description=(
        "Extract JSON from a URL or from Markdown you already have. "
        "Pass a natural-language prompt and/or json_schema (a JSON Schema as a string). "
        "Requires an OpenAI-compatible endpoint: DUNGSCRAPER_LLM_BASE_URL, DUNGSCRAPER_LLM_API_KEY, DUNGSCRAPER_LLM_MODEL. "
        "Ollama works if you set the base URL to http://127.0.0.1:11434/v1."
    ))
    async def extract(
        url: str = "",
        prompt: str = "",
        json_schema: str = "",
        content: str = "",
    ) -> str:
        schema = None
        if json_schema.strip():
            try:
                schema = json.loads(json_schema)
            except json.JSONDecodeError as exc:
                raise ToolError(f"json_schema is not valid JSON: {exc}") from exc
        payload = {
            "url": url or None,
            "prompt": prompt or None,
            "schema": schema,
            "content": content or None,
        }

        async def local(services):
            data = await extract_structured(
                ExtractRequest.model_validate(payload),
                settings=services.settings,
                client=services.client,
                cache=services.cache,
                browser=services.browser,
            )
            return data.model_dump(mode="json")

        return await _dispatch("POST", "/v1/extract", payload, local)

    @mcp.tool(description=(
        "Split Markdown into retrieval chunks. Chunks follow headings, carry token estimates "
        "(about 4 characters per token), and overlap so sentences are not cut off from their context."
    ))
    async def chunk_text(
        text: str,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
        url: str = "",
        title: str = "",
    ) -> str:
        try:
            body = ChunkRequest(
                text=text,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                metadata={"url": url, "title": title},
            )
        except Exception as exc:
            raise ToolError(str(exc)) from exc
        chunks = chunk_markdown(
            body.text,
            max_tokens=body.max_tokens,
            overlap_tokens=body.overlap_tokens,
            metadata=body.metadata,
        )
        return _dump({"chunks": [chunk.model_dump(mode="json") for chunk in chunks]})

    return mcp


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _json_object(value: str, name: str) -> dict:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolError(f"{name} must be a JSON object")
    return {str(key): str(item) for key, item in parsed.items()}


def _json_list(value: str, name: str) -> list:
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ToolError(f"{name} must be a JSON list")
    return parsed


def _dump(data) -> str:
    return json.dumps(data, indent=2, default=str)


async def _dispatch(method: str, path: str, payload: dict | None, local) -> str:
    try:
        if os.environ.get("DUNGSCRAPER_BASE_URL") and current() is None:
            data = await _remote(method, path, payload)
            return _dump(data)
        services = current()
        if services is None:
            services = await startup(get_settings())
        if path == "/v1/scrape":
            data = await scrape_url(
                ScrapeRequest.model_validate(payload),
                settings=services.settings,
                client=services.client,
                cache=services.cache,
                browser=services.browser,
            )
            return _dump(data.model_dump(mode="json", exclude_none=True))
        if path == "/v1/crawl":
            job = await start_crawl(CrawlRequest.model_validate(payload), services)
            return _dump(job.model_dump(mode="json"))
        if local is None:
            raise ToolError(f"No handler for {path}")
        result = await local(services)
        return _dump(result)
    except ToolError:
        raise
    except DungScraperError as exc:
        raise ToolError(f"{exc.code}: {exc.message}") from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


async def _remote(method: str, path: str, payload: dict | None):
    base = os.environ["DUNGSCRAPER_BASE_URL"].rstrip("/")
    headers = {"Accept": "application/json"}
    key = os.environ.get("DUNGSCRAPER_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.request(method, base + path, json=payload, headers=headers)
    try:
        body = response.json()
    except Exception as exc:
        raise ToolError(f"DungScraper API returned a non-JSON response ({response.status_code}).") from exc
    if response.status_code >= 400 or body.get("success") is False:
        error = body.get("error") or {}
        raise ToolError(f"{error.get('code', 'http_error')}: {error.get('message', response.text[:300])}")
    return body.get("data")
