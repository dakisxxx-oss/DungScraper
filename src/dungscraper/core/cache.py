"""Response cache. Redis when configured, otherwise a small in-process LRU."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
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


class DiskCache:
    """JSON files on disk with a TTL. Survives restarts; no server needed.

    Entries are named by the SHA-256 of the key. Old files are pruned once the folder holds more than max_files.
    """

    def __init__(self, directory: str | Path, max_files: int = 5000) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self._writes = 0

    def _path(self, key: str) -> Path:
        return self.directory / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    async def get(self, key: str) -> dict | None:
        path = self._path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if float(payload.get("expires", 0)) < time.time():
            try:
                path.unlink()
            except OSError:
                pass
            return None
        value = payload.get("value")
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        if ttl <= 0:
            return
        path = self._path(key)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps({"expires": time.time() + ttl, "value": value}), encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            log.warning("disk cache write failed", extra={"data": {"error": str(exc)}})
            return
        self._writes += 1
        if self._writes % 50 == 0:
            self._prune()

    def _prune(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        excess = len(files) - self.max_files
        for old in files[: max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass


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
