"""Official feeds used when a page is a challenge instead of an article."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from dungscraper.core.fetch import RawFetch


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


def feed_to_markdown(xml_text: str, *, wanted_url: str | None = None) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        title = _child_text(node, "title")
        link = _link(node)
        body = _child_text(node, "description") or _child_text(node, "summary") or _child_text(node, "content")
        items.append((title, link, _plain(body)))
    if wanted_url:
        slug = urlsplit(wanted_url).path.rstrip("/").split("/")[-1]
        matched = [item for item in items if slug and slug in (item[1] or "")]
        if matched:
            items = matched
    lines = ["# Feed", ""]
    for title, link, body in items[:20]:
        lines.append(f"## {title or link or 'Untitled'}")
        if link:
            lines.append("")
            lines.append(link)
        if body:
            lines.append("")
            lines.append(body)
        lines.append("")
    return "\n".join(lines).strip()


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
