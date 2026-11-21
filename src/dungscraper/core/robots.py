"""robots.txt fetch and allow/deny checks. Failure can be open or closed."""

from __future__ import annotations

from urllib.parse import urljoin

import httpx
from protego import Protego

from dungscraper.core.urls import origin_of
from dungscraper.log import get_logger

log = get_logger("dungscraper.robots")


def sitemaps_in_robots(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                found.append(url)
    return found


class RobotsGate:
    def __init__(self, client: httpx.AsyncClient, user_agent: str, *, respect: bool, fail_open: bool) -> None:
        self.client = client
        self.user_agent = user_agent
        self.respect = respect
        self.fail_open = fail_open
        self._parsed: dict[str, Protego | None] = {}
        self._raw: dict[str, str] = {}

    async def raw(self, url: str) -> str:
        origin = origin_of(url)
        if origin not in self._raw:
            await self._load(origin)
        return self._raw.get(origin, "")

    async def allowed(self, url: str) -> bool:
        if not self.respect:
            return True
        origin = origin_of(url)
        if origin not in self._parsed:
            await self._load(origin)
        parser = self._parsed.get(origin)
        if parser is None:
            return self.fail_open
        try:
            return bool(parser.can_fetch(url, self.user_agent))
        except Exception:
            log.warning("robots check failed", extra={"data": {"url": url}})
            return self.fail_open

    async def crawl_delay(self, url: str) -> float | None:
        """Crawl-delay for this user agent, capped at 10 seconds. None when unset."""
        if not self.respect:
            return None
        origin = origin_of(url)
        if origin not in self._parsed:
            await self._load(origin)
        parser = self._parsed.get(origin)
        if parser is None or not hasattr(parser, "crawl_delay"):
            return None
        try:
            value = parser.crawl_delay(self.user_agent)
        except Exception:
            log.warning("crawl-delay check failed", extra={"data": {"url": url}})
            return None
        if value is None:
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        return min(seconds, 10.0)

    async def _load(self, origin: str) -> None:
        robots_url = urljoin(origin + "/", "robots.txt")
        try:
            response = await self.client.get(robots_url, timeout=10)
        except Exception as exc:
            log.info("robots fetch failed", extra={"data": {"url": robots_url, "error": str(exc)}})
            self._raw[origin] = ""
            self._parsed[origin] = None
            return
        if response.status_code >= 400:
            self._raw[origin] = ""
            self._parsed[origin] = Protego.parse("") if response.status_code == 404 else None
            return
        text = response.text
        self._raw[origin] = text
        try:
            self._parsed[origin] = Protego.parse(text)
        except Exception:
            self._parsed[origin] = None
