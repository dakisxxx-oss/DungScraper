"""Run the service functions in this process, without a DungScraper server.

The CLI uses this for `--local`. Redis is turned off and crawls run inline, so
nothing outside this process is needed except the sites being read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from dungscraper.config import Settings, get_settings
from dungscraper.services import Services, shutdown, startup

T = TypeVar("T")


def local_settings() -> Settings:
    return get_settings().model_copy(update={"redis_url": "", "crawl_mode": "inline"})


async def with_services(work: Callable[[Services], Awaitable[T]]) -> T:
    services = await startup(local_settings())
    try:
        return await work(services)
    finally:
        await shutdown(services)


def _dump(model: Any) -> dict:
    return model.model_dump(mode="json", exclude_none=True)


async def local_scrape(url: str, render: str = "auto") -> dict:
    from dungscraper.core.scrape import scrape_url
    from dungscraper.models import ScrapeRequest

    async def work(services: Services) -> dict:
        data = await scrape_url(
            ScrapeRequest(url=url, render=render),
            settings=services.settings,
            client=services.client,
            cache=services.cache,
            browser=services.browser,
        )
        return data.model_dump(mode="json")

    return await with_services(work)


async def local_search(request) -> dict:
    from dungscraper.core.search import search_web

    async def work(services: Services) -> dict:
        data = await search_web(
            request,
            settings=services.settings,
            client=services.client,
            cache=services.cache,
            browser=services.browser,
        )
        return _dump(data)

    return await with_services(work)


async def local_map(request) -> dict:
    from dungscraper.core.map import map_site

    async def work(services: Services) -> dict:
        data = await map_site(
            request,
            settings=services.settings,
            client=services.client,
            browser=services.browser,
        )
        return _dump(data)

    return await with_services(work)


async def local_crawl(request) -> dict:
    from dungscraper.core.jobs import start_crawl

    async def work(services: Services) -> dict:
        # A local run has nothing to poll later, so it always waits for the result.
        job = await start_crawl(request.model_copy(update={"wait": True}), services)
        return _dump(job)

    return await with_services(work)


async def local_contacts(request) -> dict:
    from dungscraper.core.contacts import find_contacts

    async def work(services: Services) -> dict:
        data = await find_contacts(
            request,
            settings=services.settings,
            client=services.client,
            cache=services.cache,
            browser=services.browser,
            limiter=services.limiter,
        )
        return _dump(data)

    return await with_services(work)


async def local_jobs(request) -> dict:
    from dungscraper.core.joblinks import find_job_links

    async def work(services: Services) -> dict:
        data = await find_job_links(
            request,
            settings=services.settings,
            client=services.client,
            cache=services.cache,
            browser=services.browser,
        )
        return _dump(data)

    return await with_services(work)


async def local_watch(url: str, *, pages: int = 1, save: bool = True, reset: bool = False, render: str = "auto") -> dict:
    from dungscraper.core.watch import watch_page

    async def work(services: Services) -> dict:
        return await watch_page(
            url,
            settings=services.settings,
            client=services.client,
            browser=services.browser,
            pages=pages,
            save=save,
            reset=reset,
            render=render,
        )

    return await with_services(work)
