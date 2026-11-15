"""Response cache. Redis when configured, otherwise a small in-process LRU."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Protocol

from dungscraper.log import get_logger

log = get_logger("dungscraper.cache")


class Cache(Protocol):
    async def get(self, key: str) -> dict | None: ...

    async def set(self, key: str, value: dict, ttl: int) -> None: ...


class MemoryCache:
    def __init__(self, max_items: int = 256) -> None:
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    async def get(self, key: str) -> dict | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.time():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    async def set(self, key: str, value: dict, ttl: int) -> None:
        if ttl <= 0:
            return
        self._items[key] = (time.time() + ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)


class RedisCache:
    def __init__(self, redis, prefix: str = "dungscraper:cache:") -> None:
        self.redis = redis
        self.prefix = prefix

    async def get(self, key: str) -> dict | None:
        try:
            raw = await self.redis.get(self.prefix + key)
        except Exception as exc:
            log.warning("cache read failed", extra={"data": {"error": str(exc)}})
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        if ttl <= 0:
            return
        try:
            await self.redis.set(self.prefix + key, json.dumps(value), ex=ttl)
        except Exception as exc:
            log.warning("cache write failed", extra={"data": {"error": str(exc)}})


class NullCache:
    async def get(self, key: str) -> dict | None:
        return None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        return None
