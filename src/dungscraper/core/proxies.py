"""Bring-your-own proxy pool: parsing, per-domain stickiness, health and backoff.

DungScraper bundles no proxy service. Give it a list (DUNGSCRAPER_PROXIES, DUNGSCRAPER_PROXY_URLS,
DUNGSCRAPER_PROXY_URL or a file in DUNGSCRAPER_PROXY_FILE) and it rotates across them.
A proxy that fails or gets 403/429 on a domain is put on a cool-down for that domain, with the
cool-down doubling on repeated failures, and the next healthy proxy is used.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlsplit

SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}
BLOCK_STATUSES = {403, 429}


def normalize_proxy(value: str) -> str | None:
    """Return a proxy URL with a scheme, or None when the text is not a usable proxy."""
    text = (value or "").strip()
    if not text or text.startswith("#"):
        return None
    if "://" not in text:
        # A bare entry must look like host:port, so stray words in a list are not taken for hosts.
        if not re.fullmatch(r"(?:[^@\s/]+@)?[^@\s/:]+:\d{1,5}", text):
            return None
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in SCHEMES or not parts.hostname:
        return None
    return text


def parse_proxy_list(*chunks: str, file: str = "") -> list[str]:
    """Split comma, space and newline separated proxies, add a scheme to bare host:port, drop duplicates."""
    texts = [chunk for chunk in chunks if chunk]
    if file.strip():
        path = Path(file.strip()).expanduser()
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    found: list[str] = []
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            line = re.split(r"\s#", line, maxsplit=1)[0]
            for part in re.split(r"[,\s]+", line):
                proxy = normalize_proxy(part)
                if proxy and proxy not in found:
                    found.append(proxy)
    return found


def redact(proxy: str | None) -> str:
    """Proxy address without its password, safe for logs and responses."""
    if not proxy:
        return "direct"
    parts = urlsplit(proxy)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}"


class ProxyPool:
    """Round-robin over proxies with a sticky proxy per domain and a cool-down for failing ones."""

    def __init__(
        self,
        proxies: Iterable[str],
        *,
        base_cooldown: float = 30.0,
        max_cooldown: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.proxies = [proxy for proxy in dict.fromkeys(proxies) if proxy]
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self._clock = clock
        self._sticky: dict[str, str] = {}
        self._index = 0
        self._failures: dict[tuple[str, str], int] = {}
        self._cooldown_until: dict[tuple[str, str], float] = {}
        self._stats: dict[str, dict[str, int]] = {proxy: {"ok": 0, "failed": 0} for proxy in self.proxies}

    def __bool__(self) -> bool:
        return bool(self.proxies)

    def _healthy(self, proxy: str, domain: str) -> bool:
        return self._cooldown_until.get((proxy, domain), 0.0) <= self._clock()

    def pick(self, domain: str, exclude: Iterable[str] = ()) -> str | None:
        """The proxy to use for this domain now, or None when the pool is empty or every proxy is excluded."""
        if not self.proxies:
            return None
        domain = (domain or "").lower()
        skip = set(exclude)
        candidates = [proxy for proxy in self.proxies if proxy not in skip]
        if not candidates:
            return None
        sticky = self._sticky.get(domain)
        if sticky in candidates and self._healthy(sticky, domain):
            return sticky
        count = len(self.proxies)
        for step in range(count):
            proxy = self.proxies[(self._index + step) % count]
            if proxy in candidates and self._healthy(proxy, domain):
                self._index = (self._index + step + 1) % count
                self._sticky[domain] = proxy
                return proxy
        # Everything is cooling down: use the one that recovers first rather than stall.
        proxy = min(candidates, key=lambda item: self._cooldown_until.get((item, domain), 0.0))
        self._sticky[domain] = proxy
        return proxy

    def report_success(self, proxy: str | None, domain: str) -> None:
        if not proxy or proxy not in self._stats:
            return
        key = (proxy, (domain or "").lower())
        self._failures.pop(key, None)
        self._cooldown_until.pop(key, None)
        self._stats[proxy]["ok"] += 1

    def report_failure(self, proxy: str | None, domain: str) -> float:
        """Record a transport error or 403/429. Returns the cool-down in seconds."""
        if not proxy or proxy not in self._stats:
            return 0.0
        domain = (domain or "").lower()
        key = (proxy, domain)
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        cooldown = min(self.base_cooldown * (2 ** (failures - 1)), self.max_cooldown)
        self._cooldown_until[key] = self._clock() + cooldown
        if self._sticky.get(domain) == proxy:
            self._sticky.pop(domain, None)
        self._stats[proxy]["failed"] += 1
        return cooldown

    def snapshot(self) -> list[dict]:
        now = self._clock()
        rows = []
        for proxy in self.proxies:
            cooling = sorted(
                domain for (item, domain), until in self._cooldown_until.items() if item == proxy and until > now
            )
            rows.append({"proxy": redact(proxy), **self._stats[proxy], "cooling_down_for": cooling})
        return rows


_pools: dict[tuple[str, ...], ProxyPool] = {}


def pool_for(proxies: Iterable[str]) -> ProxyPool:
    """One shared pool per distinct proxy list, so health survives across requests."""
    key = tuple(proxies)
    pool = _pools.get(key)
    if pool is None:
        pool = ProxyPool(key)
        _pools[key] = pool
    return pool
