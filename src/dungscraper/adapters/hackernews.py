"""Hacker News via the public Firebase API."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest

API = "https://hacker-news.firebaseio.com/v0"


def hn_target(url: str) -> dict | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"news.ycombinator.com", "news.ycombinator.org"}:
        return None
    item_id = (parse_qs(parts.query).get("id") or [None])[0]
    if parts.path.startswith("/item") and item_id and item_id.isdigit():
        return {"kind": "item", "id": int(item_id)}
    if parts.path in {"", "/"} or parts.path.startswith("/news"):
        return {"kind": "top", "id": None}
    return None


def format_item(item: dict, comments: list[str]) -> str:
    title = item.get("title") or item.get("text") or "Hacker News"
    by = item.get("by") or "?"
    score = item.get("score", 0)
    link = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}"
    lines = [f"# {title}", "", f"{score} points · {by}", "", link]
    text = (item.get("text") or "").strip()
    if text and item.get("title"):
        lines.extend(["", text])
    if comments:
        lines.extend(["", "## Comments", *comments])
    return "\n".join(lines).strip()


def format_comment(item: dict, depth: int) -> str:
    indent = "  " * depth
    author = item.get("by") or "[deleted]"
    text = " ".join((item.get("text") or "").split())
    return f"{indent}- **{author}**: {text}"


class HackerNewsAdapter:
    name = "hackernews"

    def matches(self, host: str, url: str) -> bool:
        return hn_target(url) is not None

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        target = hn_target(request.url)
        if target is None:
            return None
        if target["kind"] == "top":
            ids_response = await client.get(f"{API}/topstories.json", timeout=20)
            if ids_response.status_code >= 400:
                return None
            lines = ["# Hacker News", ""]
            for item_id in (ids_response.json() or [])[:30]:
                item_response = await client.get(f"{API}/item/{item_id}.json", timeout=15)
                if item_response.status_code >= 400:
                    continue
                item = item_response.json() or {}
                title = item.get("title") or "(untitled)"
                link = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
                lines.append(f"- [{title}]({link}) · {item.get('score', 0)} points · {item.get('by') or '?'}")
            markdown = "\n".join(lines)
            title = "Hacker News"
        else:
            item_response = await client.get(f"{API}/item/{target['id']}.json", timeout=20)
            if item_response.status_code >= 400:
                return None
            item = item_response.json() or {}
            comments = await _comments(client, item.get("kids") or [], 0, request.max_comment_depth)
            markdown = format_item(item, comments)
            title = item.get("title") or "Hacker News"
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, description="Hacker News via the Firebase API"),
            render_mode="api",
            status_code=200,
            content_type="application/json",
            warnings=["hackernews_api"],
        )


async def _comments(client: httpx.AsyncClient, kids: list, depth: int, max_depth: int) -> list[str]:
    if depth > max_depth:
        return []
    lines: list[str] = []
    for kid in kids[:20]:
        response = await client.get(f"{API}/item/{kid}.json", timeout=15)
        if response.status_code >= 400:
            continue
        item = response.json() or {}
        if item.get("text"):
            lines.append(format_comment(item, depth))
        lines.extend(await _comments(client, item.get("kids") or [], depth + 1, max_depth))
    return lines
