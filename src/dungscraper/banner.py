"""The visible "DungScraper was used" line on every MCP tool result.

Set DUNGSCRAPER_BANNER=0 (or false/off/no) to turn it off.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import time

from dungscraper import __version__

_OFF = {"0", "false", "off", "no", ""}


def banner_enabled() -> bool:
    value = os.environ.get("DUNGSCRAPER_BANNER")
    return True if value is None else value.strip().lower() not in _OFF


def format_banner(tool: str, tier: str, ms: int) -> str:
    return f"🦴 DungScraper v{__version__} · {tool} · {tier or 'local'} · {ms} ms"


def _tier_of(result: str) -> str:
    """Best-effort fetch tier from a JSON tool result."""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return ""
    if isinstance(data, dict):
        tier = data.get("fetch_tier") or data.get("render_mode")
        if tier:
            return str(tier)
        results = data.get("results")
        if isinstance(results, list) and results:
            return f"{len(results)} pages"
    return ""


def with_banner(tool: str, result: str, elapsed_ms: int) -> str:
    if not banner_enabled() or not isinstance(result, str):
        return result
    return f"{format_banner(tool, _tier_of(result), elapsed_ms)}\n{result}"


def bannered(name: str | None = None):
    """Decorator for async MCP tool functions. Keeps the signature so schemas stay the same."""

    def decorate(func):
        label = name or func.__name__.removesuffix("_tool")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            started = time.perf_counter()
            result = await func(*args, **kwargs)
            return with_banner(label, result, int((time.perf_counter() - started) * 1000))

        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        return wrapper

    return decorate
