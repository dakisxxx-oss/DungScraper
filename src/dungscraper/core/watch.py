"""Remember what a page looked like and say what changed next time.

For a listing page the snapshot is the list of ads, so the report names new ads,
removed ads and price changes. For any other page it is a Markdown diff.
Snapshots live as small JSON files under DUNGSCRAPER_DATA_DIR (default ~/.dungscraper).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from dungscraper.config import Settings
from dungscraper.core.diff import markdown_diff
from dungscraper.core.fetch import BrowserManager
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeRequest

MAX_PAGES = 10
PAGE_DELAY_SECONDS = 1.5


def data_dir(settings: Settings) -> Path:
    base = settings.data_dir.strip() or os.path.join(os.path.expanduser("~"), ".dungscraper")
    return Path(base) / "watch"


def snapshot_path(settings: Settings, url: str, pages: int) -> Path:
    digest = hashlib.sha256(f"{url}|{pages}".encode()).hexdigest()[:16]
    return data_dir(settings) / f"{digest}.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "title", "price", "currency", "price_text", "location", "url")
    return {key: item.get(key) for key in keys}


def compare_listings(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> dict[str, list]:
    added = [new[key] for key in new if key not in old]
    removed = [old[key] for key in old if key not in new]
    changed = []
    for key, item in new.items():
        before = old.get(key)
        if before is None:
            continue
        if before.get("price") != item.get("price") and (before.get("price") is not None or item.get("price") is not None):
            changed.append({**item, "old_price": before.get("price"), "new_price": item.get("price")})
    return {"added": added, "removed": removed, "price_changed": changed}


def render_report(result: dict[str, Any]) -> str:
    lines = [f"# Watch: {result['url']}", ""]
    if result["first_run"]:
        lines.append(f"First snapshot saved ({result['count']} ads). Run again later to see changes.")
    elif result["kind"] == "listing":
        lines.append(
            f"{len(result['added'])} new, {len(result['removed'])} gone, "
            f"{len(result['price_changed'])} price changes, {result['count']} ads now."
        )
    else:
        lines.append("Page changed." if result["changed"] else "No change.")
    for label, key in (("New", "added"), ("Gone", "removed")):
        for item in result.get(key, []):
            price = item.get("price_text") or "-"
            lines.append(f"- {label}: [{item.get('title')}]({item.get('url')}) {price} {item.get('location') or ''}".rstrip())
    for item in result.get("price_changed", []):
        lines.append(
            f"- Price: [{item.get('title')}]({item.get('url')}) {item['old_price']} -> {item['new_price']} {item.get('currency') or ''}".rstrip()
        )
    if result["kind"] != "listing" and result.get("diff"):
        lines += ["", "```diff", result["diff"][:6000], "```"]
    return "\n".join(lines)


async def watch_page(
    url: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    browser: BrowserManager | None = None,
    pages: int = 1,
    save: bool = True,
    reset: bool = False,
    render: str = "auto",
) -> dict[str, Any]:
    pages = max(1, min(int(pages), MAX_PAGES))
    path = snapshot_path(settings, url, pages)
    previous = None if reset else _load(path)

    collected: dict[str, dict[str, Any]] = {}
    markdown = ""
    kind = "page"
    next_url: str | None = url
    seen_urls: set[str] = set()
    fetched_pages = 0
    final_url = url
    while next_url and fetched_pages < pages and next_url not in seen_urls:
        seen_urls.add(next_url)
        if fetched_pages:
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        data = await scrape_url(
            ScrapeRequest(url=next_url, render=render, formats=["markdown"], cache=False),
            settings=settings,
            client=client,
            browser=browser,
        )
        fetched_pages += 1
        final_url = data.url
        structured = data.structured or {}
        if structured.get("type") == "listing":
            kind = "listing"
            for item in structured.get("listings", []):
                key = str(item.get("id") or item.get("url"))
                collected.setdefault(key, _brief(item))
            next_url = structured.get("next_page")
        else:
            markdown = data.markdown or ""
            next_url = None
    if kind == "listing" and not collected:
        raise DungScraperError("watch_empty", f"No ads were found on {url}.", status=422)

    result: dict[str, Any] = {
        "url": url,
        "final_url": final_url,
        "kind": kind,
        "pages_read": fetched_pages,
        "first_run": previous is None,
        "taken_at": int(time.time()),
        "previous_taken_at": (previous or {}).get("taken_at"),
        "saved": False,
    }
    if kind == "listing":
        old_items = (previous or {}).get("items", {}) if previous and previous.get("kind") == "listing" else {}
        diff = compare_listings(old_items, collected)
        if previous is None or previous.get("kind") != "listing":
            diff = {"added": [], "removed": [], "price_changed": []}
            result["first_run"] = True
        result.update(diff)
        result["count"] = len(collected)
        snapshot = {"url": url, "kind": kind, "taken_at": result["taken_at"], "items": collected}
    else:
        old_md = (previous or {}).get("markdown", "") if previous else ""
        change = markdown_diff(old_md, markdown, url=url)
        result.update({"changed": change.changed and previous is not None, "diff": change.diff if previous else "", "count": 1})
        snapshot = {"url": url, "kind": kind, "taken_at": result["taken_at"], "markdown": markdown}
    if save:
        _save(path, snapshot)
        result["saved"] = True
        result["snapshot"] = str(path)
    result["report"] = render_report(result)
    return result
