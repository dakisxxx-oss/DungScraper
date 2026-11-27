"""Crawl jobs: memory for one process, Redis when a worker is running."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import httpx

from dungscraper.config import Settings
from dungscraper.core.crawl import crawl_site
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import CrawlJobData, CrawlRequest, ErrorBody
from dungscraper.security import assert_public_url

log = get_logger("dungscraper.jobs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: dict) -> None:
        async with self._lock:
            self._jobs[job["id"]] = job

    async def get(self, job_id: str) -> dict | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    async def save(self, job: dict) -> None:
        async with self._lock:
            self._jobs[job["id"]] = json.loads(json.dumps(job))

    async def enqueue(self, job_id: str) -> None:
        return None

    async def dequeue(self, timeout: int = 5) -> str | None:
        return None


class RedisJobStore:
    def __init__(self, redis, ttl: int) -> None:
        self.redis = redis
        self.ttl = ttl
        self.queue = "dungscraper:jobs"

    def _key(self, job_id: str) -> str:
        return f"dungscraper:job:{job_id}"

    async def create(self, job: dict) -> None:
        await self.save(job)

    async def get(self, job_id: str) -> dict | None:
        raw = await self.redis.get(self._key(job_id))
        if not raw:
            return None
        return json.loads(raw)

    async def save(self, job: dict) -> None:
        await self.redis.set(self._key(job["id"]), json.dumps(job), ex=self.ttl)

    async def enqueue(self, job_id: str) -> None:
        await self.redis.lpush(self.queue, job_id)

    async def dequeue(self, timeout: int = 5) -> str | None:
        item = await self.redis.brpop(self.queue, timeout=timeout)
        if not item:
            return None
        return item[1]


def new_job(request: CrawlRequest) -> dict:
    now = _now()
    return {
        "id": uuid.uuid4().hex,
        "type": "crawl",
        "status": "queued",
        "url": request.url,
        "limit": request.limit,
        "completed": 0,
        "failed": 0,
        "skipped_duplicates": 0,
        "skipped_robots": 0,
        "pages": [],
        "errors": [],
        "created_at": now,
        "updated_at": now,
        "error": None,
        "webhook": request.webhook,
        "request": request.model_dump(mode="json"),
    }


def job_view(job: dict) -> CrawlJobData:
    return CrawlJobData(
        id=job["id"],
        status=job["status"],
        url=job["url"],
        limit=job["limit"],
        completed=job.get("completed", 0),
        failed=job.get("failed", 0),
        skipped_duplicates=job.get("skipped_duplicates", 0),
        skipped_robots=job.get("skipped_robots", 0),
        skipped_content_type=job.get("skipped_content_type", 0),
        strategy=(job.get("request") or {}).get("strategy", "bfs"),
        pages=job.get("pages") or [],
        errors=job.get("errors") or [],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        error=job.get("error"),
    )


async def post_webhook(client: httpx.AsyncClient, url: str, payload: dict, *, allow_private: bool) -> None:
    assert_public_url(url, allow_private=allow_private)
    delay = 0.5
    last: str = ""
    for _attempt in range(3):
        try:
            response = await client.post(url, json=payload, timeout=15)
            if response.status_code < 500:
                return
            last = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        await asyncio.sleep(delay)
        delay *= 2
    log.warning("webhook failed", extra={"data": {"url": url, "error": last}})


async def execute_crawl_job(job_id: str, services, *, resume: bool = False) -> None:
    job = await services.jobs.get(job_id)
    if job is None or job["status"] == "cancelled":
        return
    if job["status"] == "completed" and not resume:
        return
    if job["status"] == "completed":
        return
    prior_pages = list(job.get("pages") or [])
    prior_checkpoint = job.get("checkpoint") if prior_pages or resume or job["status"] == "running" else None
    job["status"] = "running"
    job["updated_at"] = _now()
    await services.jobs.save(job)
    request = CrawlRequest.model_validate(job["request"])

    async def on_page(page, count: int) -> None:
        current = await services.jobs.get(job_id)
        if current is None:
            return
        current["pages"] = (current.get("pages") or []) + [page.model_dump(mode="json")]
        current["completed"] = count
        current["updated_at"] = _now()
        current["status"] = "running"
        await services.jobs.save(current)

    async def should_cancel() -> bool:
        current = await services.jobs.get(job_id)
        return current is None or current["status"] == "cancelled"

    async def on_checkpoint(checkpoint: dict) -> None:
        current = await services.jobs.get(job_id)
        if current is None:
            return
        current["checkpoint"] = checkpoint
        current["updated_at"] = _now()
        await services.jobs.save(current)

    try:
        result = await crawl_site(
            request,
            settings=services.settings,
            client=services.client,
            cache=services.cache,
            browser=services.browser,
            limiter=services.limiter,
            on_page=on_page,
            should_cancel=should_cancel,
            checkpoint=prior_checkpoint,
            on_checkpoint=on_checkpoint,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("crawl job failed", extra={"data": {"id": job_id}})
        failed = await services.jobs.get(job_id) or job
        failed["status"] = "failed"
        failed["updated_at"] = _now()
        failed["error"] = ErrorBody(code="crawl_failed", message=str(exc)).model_dump()
        await services.jobs.save(failed)
        return

    current = await services.jobs.get(job_id) or job
    if current["status"] == "cancelled":
        return
    current["status"] = "completed"
    current["pages"] = prior_pages + [page.model_dump(mode="json") for page in result["pages"]]
    current["resumed"] = bool(prior_checkpoint)
    current["errors"] = result["errors"]
    current["completed"] = len(prior_pages) + result["completed"]
    current["failed"] = result["failed"]
    current["skipped_duplicates"] = result["skipped_duplicates"]
    current["skipped_robots"] = result["skipped_robots"]
    current["skipped_content_type"] = result.get("skipped_content_type", 0)
    current["updated_at"] = _now()
    await services.jobs.save(current)
    if current.get("webhook"):
        payload = {"id": current["id"], "type": "crawl", "status": current["status"], "data": job_view(current).model_dump(mode="json")}
        try:
            await post_webhook(
                services.client,
                current["webhook"],
                payload,
                allow_private=services.settings.allow_private_networks,
            )
        except DungScraperError as exc:
            log.warning("webhook blocked", extra={"data": {"error": exc.message}})


async def start_crawl(request: CrawlRequest, services) -> CrawlJobData:
    mode = services.settings.resolved_crawl_mode()
    if mode == "queue" and not isinstance(services.jobs, RedisJobStore):
        raise DungScraperError(
            "config_error",
            "DUNGSCRAPER_CRAWL_MODE=queue requires a reachable DUNGSCRAPER_REDIS_URL.",
            status=500,
        )
    job = new_job(request)
    await services.jobs.create(job)
    if mode == "queue":
        await services.jobs.enqueue(job["id"])
        if not request.wait:
            return job_view(job)
        deadline = asyncio.get_event_loop().time() + request.timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.4)
            current = await services.jobs.get(job["id"])
            if current and current["status"] in {"completed", "failed", "cancelled"}:
                return job_view(current)
        current = await services.jobs.get(job["id"])
        return job_view(current or job)

    task = services.spawn(execute_crawl_job(job["id"], services))
    if not request.wait:
        return job_view(job)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=request.timeout_seconds)
    except TimeoutError:
        current = await services.jobs.get(job["id"])
        return job_view(current or job)
    current = await services.jobs.get(job["id"])
    return job_view(current or job)


async def resume_crawl(job_id: str, services) -> CrawlJobData:
    job = await services.jobs.get(job_id)
    if job is None:
        raise DungScraperError("job_not_found", f"No crawl job {job_id}.", status=404)
    if job["status"] == "completed":
        return job_view(job)
    if not job.get("checkpoint"):
        raise DungScraperError(
            "nothing_to_resume",
            "This crawl has no saved frontier. Start a new crawl instead.",
            status=400,
        )
    job["status"] = "running"
    job["updated_at"] = _now()
    await services.jobs.save(job)
    mode = services.settings.resolved_crawl_mode()
    if mode == "queue":
        await services.jobs.enqueue(job_id)
        return job_view(job)
    services.spawn(execute_crawl_job(job_id, services, resume=True))
    return job_view(job)


async def cancel_crawl(job_id: str, services) -> CrawlJobData:
    job = await services.jobs.get(job_id)
    if job is None:
        raise DungScraperError("job_not_found", f"No crawl job {job_id}.", status=404)
    if job["status"] in {"queued", "running"}:
        job["status"] = "cancelled"
        job["updated_at"] = _now()
        await services.jobs.save(job)
    return job_view(job)
