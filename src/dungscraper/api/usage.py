"""Per-API-key rate limits and usage counters, in memory.

Counters live in this process. They reset when the server restarts and are not shared between several
workers or containers. Limits are off unless DUNGSCRAPER_RATE_LIMIT_PER_MINUTE or ..._PER_DAY is set.
A key can have its own per-minute limit with DUNGSCRAPER_KEY_LIMITS="keyA=120,keyB=30".
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from dungscraper.config import Settings
from dungscraper.errors import DungScraperError

ANONYMOUS = "anonymous"


def key_id(key: str | None) -> str:
    """Short stable id for a key. Safe to show: the key itself is never returned or logged."""
    if not key:
        return ANONYMOUS
    return "k_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def anon_id(ip: str | None) -> str:
    """Stable per-IP id for anonymous requests. The raw IP is never returned."""
    if not ip:
        return ANONYMOUS
    return "ip_" + hashlib.sha256(ip.encode("utf-8")).hexdigest()[:12]


class UsageTracker:
    def __init__(
        self,
        per_minute: int = 0,
        per_day: int = 0,
        overrides: dict[str, int] | None = None,
        clock: Callable[[], float] = time.time,
        anon_per_minute: int = 0,
        anon_per_day: int = 0,
    ) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self.anon_per_minute = anon_per_minute
        self.anon_per_day = anon_per_day
        self.overrides = overrides or {}
        self._clock = clock
        self.started_at = datetime.fromtimestamp(clock(), timezone.utc).isoformat()
        self._window: dict[str, deque[float]] = defaultdict(deque)
        self._total: Counter[str] = Counter()
        self._rejected: Counter[str] = Counter()
        self._daily: dict[str, Counter[str]] = defaultdict(Counter)
        self._paths: dict[str, Counter[str]] = defaultdict(Counter)

    @classmethod
    def from_settings(cls, settings: Settings) -> "UsageTracker":
        overrides: dict[str, int] = {}
        for part in settings.key_limits.split(","):
            name, _, value = part.partition("=")
            if name.strip() and value.strip().isdigit():
                overrides[key_id(name.strip())] = int(value.strip())
        anon_minute = settings.anon_rate_limit_per_minute if settings.allow_anonymous else 0
        anon_day = settings.anon_rate_limit_per_day if settings.allow_anonymous else 0
        return cls(
            settings.rate_limit_per_minute,
            settings.rate_limit_per_day,
            overrides,
            anon_per_minute=anon_minute,
            anon_per_day=anon_day,
        )

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")

    def check(self, key: str | None, path: str = "", client_ip: str | None = None) -> dict:
        """Count one request for this key or anonymous IP, or raise a 429 when it is over a limit."""
        if key:
            ident = key_id(key)
            limit = self.overrides.get(ident, self.per_minute)
            day_limit = self.per_day
        elif self.anon_per_minute or self.anon_per_day:
            ident = anon_id(client_ip)
            limit = self.anon_per_minute
            day_limit = self.anon_per_day
        else:
            ident = key_id(None)
            limit = 0
            day_limit = 0
        now = self._clock()
        window = self._window[ident]
        while window and window[0] <= now - 60:
            window.popleft()
        day = self._day()
        if limit and len(window) >= limit:
            retry = max(1, int(window[0] + 60 - now) + 1)
            self._rejected[ident] += 1
            raise DungScraperError(
                "rate_limited",
                f"Rate limit of {limit} requests per minute reached. Retry in {retry} s.",
                status=429,
                details={"limit_per_minute": limit, "retry_after_seconds": retry},
                headers={"Retry-After": str(retry), "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
            )
        if day_limit and self._daily[ident][day] >= day_limit:
            self._rejected[ident] += 1
            raise DungScraperError(
                "quota_exceeded",
                f"Daily quota of {day_limit} requests reached. It resets at 00:00 UTC.",
                status=429,
                details={"limit_per_day": day_limit},
                headers={"Retry-After": "3600", "X-RateLimit-Limit-Day": str(day_limit)},
            )
        window.append(now)
        self._total[ident] += 1
        self._daily[ident][day] += 1
        self._paths[ident][path or "?"] += 1
        headers: dict[str, str] = {}
        if limit:
            headers["X-RateLimit-Limit"] = str(limit)
            headers["X-RateLimit-Remaining"] = str(max(0, limit - len(window)))
        if day_limit:
            headers["X-RateLimit-Limit-Day"] = str(day_limit)
            headers["X-RateLimit-Remaining-Day"] = str(max(0, day_limit - self._daily[ident][day]))
        return {"key_id": ident, "headers": headers}

    def report(self, ident: str) -> dict:
        now = self._clock()
        window = self._window.get(ident, deque())
        recent = sum(1 for stamp in window if stamp > now - 60)
        if ident.startswith("ip_") or ident == ANONYMOUS:
            limit = self.anon_per_minute
            day_limit = self.anon_per_day
        else:
            limit = self.overrides.get(ident, self.per_minute)
            day_limit = self.per_day
        day = self._day()
        today = self._daily[ident][day] if ident in self._daily else 0
        return {
            "key_id": ident,
            "requests_total": self._total[ident],
            "requests_today": today,
            "requests_last_minute": recent,
            "rejected_total": self._rejected[ident],
            "limit_per_minute": limit or None,
            "remaining_this_minute": max(0, limit - recent) if limit else None,
            "limit_per_day": day_limit or None,
            "remaining_today": max(0, day_limit - today) if day_limit else None,
            "by_path": dict(self._paths[ident].most_common(20)),
            "counting_since": self.started_at,
            "note": "Counters are in memory and reset when the server restarts.",
        }
