"""REST routes. Shapes are stable and documented in OpenAPI."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dungscraper import __version__
from dungscraper.api.auth import require_api_key
from dungscraper.core.chunk import chunk_markdown, estimate_tokens
from dungscraper.core.extract import extract_structured
from dungscraper.core.jobs import cancel_crawl, job_view, start_crawl
from dungscraper.core.map import map_site
from dungscraper.core.scrape import scrape_url
from dungscraper.core.search import search_web
from dungscraper.errors import DungScraperError
from dungscraper.models import (
    BatchItem,
    BatchScrapeData,
    BatchScrapeRequest,
    BatchScrapeResponse,
    ChunkData,
    ChunkRequest,
    ChunkResponse,
    CrawlRequest,
    CrawlResponse,
    ExtractRequest,
    ExtractResponse,
    MapRequest,
    MapResponse,
    ScrapeRequest,
    ScrapeResponse,
    SearchRequest,
    SearchResponse,
)

bearer = HTTPBearer(auto_error=False, description="API key as Bearer token. Omit when the server has no keys configured.")


async def authorized(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    await require_api_key(request)


router = APIRouter(dependencies=[Depends(authorized)])


def _services(request: Request):
    return request.app.state.services


@router.post(
    "/v1/scrape",
    response_model=ScrapeResponse,
    response_model_exclude_none=True,
    tags=["scrape"],
    summary="Scrape one URL into Markdown",
)
async def scrape(body: ScrapeRequest, request: Request) -> ScrapeResponse:
    services = _services(request)
    data = await scrape_url(
        body,
        settings=services.settings,
        client=services.client,
        cache=services.cache,
        browser=services.browser,
    )
    return ScrapeResponse(success=True, data=data)


@router.post(
    "/v1/batch/scrape",
    response_model=BatchScrapeResponse,
    response_model_exclude_none=True,
    tags=["scrape"],
    summary="Scrape many URLs",
)
async def batch_scrape(body: BatchScrapeRequest, request: Request) -> BatchScrapeResponse:
    services = _services(request)
    semaphore = asyncio.Semaphore(body.concurrency)

    async def one(url: str) -> BatchItem:
        async with semaphore:
            try:
                options = body.scrape_options.model_dump()
                data = await scrape_url(
                    ScrapeRequest(url=url, **options),
                    settings=services.settings,
                    client=services.client,
                    cache=services.cache,
                    browser=services.browser,
                )
                return BatchItem(url=url, data=data)
            except DungScraperError as exc:
                return BatchItem(url=url, error=f"{exc.code}: {exc.message}")

    results = await asyncio.gather(*(one(url) for url in body.urls))
    return BatchScrapeResponse(success=True, data=BatchScrapeData(results=list(results)))


@router.post(
    "/v1/crawl",
    response_model=CrawlResponse,
    response_model_exclude_none=True,
    tags=["crawl"],
    summary="Start a site crawl",
)
async def crawl(body: CrawlRequest, request: Request) -> CrawlResponse:
    job = await start_crawl(body, _services(request))
    return CrawlResponse(success=True, data=job)


@router.get(
    "/v1/crawl/{job_id}",
    response_model=CrawlResponse,
    response_model_exclude_none=True,
    tags=["crawl"],
    summary="Poll a crawl job",
)
async def crawl_status(job_id: str, request: Request) -> CrawlResponse:
    job = await _services(request).jobs.get(job_id)
    if job is None:
        raise DungScraperError("job_not_found", f"No crawl job {job_id}.", status=404)
    return CrawlResponse(success=True, data=job_view(job))


@router.delete(
    "/v1/crawl/{job_id}",
    response_model=CrawlResponse,
    response_model_exclude_none=True,
    tags=["crawl"],
    summary="Cancel a crawl job",
)
async def crawl_cancel(job_id: str, request: Request) -> CrawlResponse:
    job = await cancel_crawl(job_id, _services(request))
    return CrawlResponse(success=True, data=job)


@router.post(
    "/v1/map",
    response_model=MapResponse,
    response_model_exclude_none=True,
    tags=["map"],
    summary="Discover URLs on a site",
)
async def map_urls(body: MapRequest, request: Request) -> MapResponse:
    services = _services(request)
    data = await map_site(
        body,
        settings=services.settings,
        client=services.client,
        browser=services.browser,
    )
    return MapResponse(success=True, data=data)


@router.post(
    "/v1/search",
    response_model=SearchResponse,
    response_model_exclude_none=True,
    tags=["search"],
    summary="Search the web and optionally scrape the hits",
)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    services = _services(request)
    data = await search_web(
        body,
        settings=services.settings,
        client=services.client,
        cache=services.cache,
        browser=services.browser,
    )
    return SearchResponse(success=True, data=data)


@router.post(
    "/v1/extract",
    response_model=ExtractResponse,
    response_model_exclude_none=True,
    tags=["extract"],
    summary="Extract JSON with a schema or a prompt",
)
async def extract(body: ExtractRequest, request: Request) -> ExtractResponse:
    services = _services(request)
    data = await extract_structured(
        body,
        settings=services.settings,
        client=services.client,
        cache=services.cache,
        browser=services.browser,
    )
    return ExtractResponse(success=True, data=data)


@router.post(
    "/v1/chunk",
    response_model=ChunkResponse,
    response_model_exclude_none=True,
    tags=["chunk"],
    summary="Split Markdown into token-aware RAG chunks",
)
async def chunk(body: ChunkRequest) -> ChunkResponse:
    chunks = chunk_markdown(
        body.text,
        max_tokens=body.max_tokens,
        overlap_tokens=body.overlap_tokens,
        metadata=body.metadata,
    )
    return ChunkResponse(
        success=True,
        data=ChunkData(chunks=chunks, total_tokens=sum(item.token_count for item in chunks) or estimate_tokens(body.text)),
    )


def api_version() -> str:
    return __version__
