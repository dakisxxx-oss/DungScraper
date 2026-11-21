"""RSS and Atom parsing, plus the feed used when a page is a challenge."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from dungscraper.config import Settings
from dungscraper.core.fetch import RawFetch
from dungscraper.core.robots import RobotsGate
from dungscraper.errors import DungScraperError
from dungscraper.security import assert_public_url

_FEED_MARKUP = re.compile(r"<(rss|rdf:rdf|feed)(\s|>)", re.IGNORECASE)


@dataclass
class FeedItem:
    title: str
    link: str
    author: str = ""
    published: str = ""
    summary: str = ""


def medium_feed_url(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"medium.com"} and not host.endswith(".medium.com"):
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return "https://medium.com/feed"
    if segments[0] == "tag" and len(segments) > 1:
        return f"https://medium.com/feed/tag/{segments[1]}"
    if segments[0].startswith("@"):
        return f"https://medium.com/feed/{segments[0]}"
    return f"https://medium.com/feed/{segments[0]}"


def discover_feed(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rels = rel if isinstance(rel, list) else [rel]
        if "alternate" not in [item.lower() for item in rels]:
            continue
        kind = (link.get("type") or "").lower()
        href = link.get("href") or ""
        if href and any(token in kind for token in ("rss", "atom", "xml")):
            return urljoin(base_url, href)
    return None


def looks_like_feed(text: str, content_type: str = "", url: str = "") -> bool:
    sample = (text or "").lstrip("\ufeff").lstrip()[:1500]
    lowered = sample.lower()
    if not lowered:
        return False
    if "<html" in lowered[:600] or lowered.startswith("<!doctype html"):
        return False
    if not _FEED_MARKUP.search(sample):
        return False
    ctype = (content_type or "").lower()
    if any(token in ctype for token in ("rss", "atom", "xml", "text/plain")) or not ctype:
        return True
    path = urlsplit(url).path.lower() if url else ""
    if path.endswith((".rss", ".atom", ".xml")):
        return True
    return "<item" in lowered or "<entry" in lowered


def parse_feed(xml_text: str) -> tuple[str, list[FeedItem]]:
    raw = (xml_text or "").lstrip("\ufeff").strip()
    if not raw:
        return "", []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "", []
    container = root
    if _local(root) == "rss":
        container = next((child for child in list(root) if _local(child) == "channel"), root)
    title = _child_text(container, "title")
    items: list[FeedItem] = []
    for node in list(container):
        if _local(node) not in {"item", "entry"}:
            continue
        item_title = _child_text(node, "title")
        link = _link(node)
        summary = _summary(node)
        if not (item_title or link or summary):
            continue
        items.append(
            FeedItem(
                title=item_title,
                link=link,
                author=_author(node),
                published=_published(node),
                summary=summary,
            )
        )
        if len(items) >= 100:
            break
    return title, items


def items_to_markdown(title: str, items: list[FeedItem], *, limit: int = 20) -> str:
    lines = [f"# {title or 'Feed'}", ""]
    for item in items[:limit]:
        lines.append(f"## {item.title or item.link or 'Untitled'}")
        byline = " · ".join(part for part in (item.author, item.published) if part)
        if byline:
            lines.extend(["", byline])
        if item.link:
            lines.extend(["", item.link])
        if item.summary:
            summary = item.summary if len(item.summary) <= 1500 else item.summary[:1497] + "..."
            lines.extend(["", summary])
        lines.append("")
    return "\n".join(lines).strip()


def feed_to_markdown(xml_text: str, *, wanted_url: str | None = None) -> str:
    title, items = parse_feed(xml_text)
    if wanted_url:
        slug = urlsplit(wanted_url).path.rstrip("/").split("/")[-1]
        matched = [item for item in items if slug and slug in (item.link or "")]
        if matched:
            items = matched
    if not items:
        return ""
    return items_to_markdown(title or "Feed", items)


async def fetch_feed(
    url: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    limit: int = 20,
    respect_robots: bool | None = None,
) -> dict:
    """Fetch one RSS or Atom URL and return structured items."""
    assert_public_url(url, allow_private=settings.allow_private_networks)
    respect = settings.respect_robots_default if respect_robots is None else respect_robots
    if respect:
        gate = RobotsGate(
            client,
            settings.robots_user_agent,
            respect=True,
            fail_open=settings.robots_fail_open,
        )
        if not await gate.allowed(url):
            raise DungScraperError(
                "robots_disallowed",
                f"{url} is disallowed by robots.txt for {settings.robots_user_agent}. "
                "Pass respect_robots=false only for a feed you are allowed to read.",
                status=403,
            )
    bounded = max(1, min(int(limit), 50))
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        "User-Agent": settings.fetch_user_agent,
    }
    try:
        response = await client.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        raise DungScraperError("feed_unavailable", f"Could not fetch the feed: {exc}", status=504) from exc
    if response.status_code >= 400:
        raise DungScraperError(
            "feed_unavailable",
            f"The feed returned HTTP {response.status_code}.",
            status=502,
        )
    title, items = parse_feed(response.text)
    if not items:
        raise DungScraperError(
            "feed_unreadable",
            "The URL did not contain an RSS or Atom feed.",
            status=422,
        )
    kept = items[:bounded]
    return {
        "title": title or "Feed",
        "url": str(response.url),
        "items": [
            {
                "title": item.title,
                "link": item.link,
                "author": item.author,
                "published": item.published,
                "summary": item.summary if len(item.summary) <= 1000 else item.summary[:997] + "...",
            }
            for item in kept
        ],
    }


def feed_fetch_result(url: str, xml_text: str, *, source: str) -> RawFetch | None:
    markdown = feed_to_markdown(xml_text, wanted_url=url)
    if len(markdown.split()) < 20:
        return None
    return RawFetch(
        url=url,
        status_code=200,
        headers={},
        content=markdown.encode(),
        text=markdown,
        content_type="text/plain",
        render_mode="rss",
        tier="rss",
        warnings=[f"feed:{source}"],
    )


def _local(node: ET.Element) -> str:
    return node.tag.split("}")[-1].lower()


def _author(node: ET.Element) -> str:
    creator = _child_text(node, "creator")
    if creator:
        return creator
    for child in list(node):
        if _local(child) == "author":
            name = _child_text(child, "name")
            return name or "".join(child.itertext()).strip()
    return ""


def _published(node: ET.Element) -> str:
    for name in ("published", "updated", "pubDate", "date"):
        value = _child_text(node, name)
        if value:
            return value
    return ""


def _summary(node: ET.Element) -> str:
    for name in ("description", "summary", "content"):
        value = _child_text(node, name)
        if value:
            return _plain(value)
    return ""


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if child.tag.split("}")[-1] == name:
            return "".join(child.itertext()).strip()
    return ""


def _link(node: ET.Element) -> str:
    text = _child_text(node, "link")
    if text.startswith("http"):
        return text
    for child in list(node):
        if child.tag.split("}")[-1] == "link":
            href = child.attrib.get("href") or ""
            if href.startswith("http"):
                return href
    return text


def _plain(value: str) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)
