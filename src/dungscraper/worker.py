"""Redis-backed crawl worker."""

from __future__ import annotations

import asyncio

from dungscraper.config import get_settings
from dungscraper.core.jobs import RedisJobStore, execute_crawl_job
from dungscraper.log import get_logger
from dungscraper.services import shutdown, startup

log = get_logger("dungscraper.worker")


async def run_worker() -> None:
    settings = get_settings()
    services = await startup(settings)
    if not isinstance(services.jobs, RedisJobStore):
        await shutdown(services)
        raise SystemExit("dungscraper worker requires DUNGSCRAPER_REDIS_URL and a reachable Redis.")
    log.info("worker listening")
    try:
        while True:
            job_id = await services.jobs.dequeue(5)
            if not job_id:
                continue
            await execute_crawl_job(job_id, services)
    finally:
        await shutdown(services)


def main() -> None:
    asyncio.run(run_worker())
