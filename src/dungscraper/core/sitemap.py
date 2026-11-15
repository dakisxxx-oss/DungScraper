"""Sitemap discovery from robots.txt, /sitemap.xml, and nested indexes."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import httpx

from dungscraper.core.robots import sitemaps_in_robots
from dungscraper.core.urls import origin_of
from dungscraper.log import get_logger

log = get_logger("dungscraper.sitemap")


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page URLs, child sitemap URLs)."""
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")
    xml_text = xml_text.lstrip("\ufeff").strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    tag = root.tag.split("}")[-1].lower()
    locs = []
    for element in root.iter():
        if element.tag.split("}")[-1].lower() == "loc" and element.text:
            locs.append(element.text.strip())
    if tag == "sitemapindex":
        return [], locs
    if tag == "urlset":
        return locs, []
    return locs, []


async def discover_sitemap_urls(
    client: httpx.AsyncClient,
    root_url: str,
    *,
    limit: int,
    robots_text: str = "",
    max_sitemaps: int = 20,
) -> list[str]:
    origin = origin_of(root_url)
    seeds = sitemaps_in_robots(robots_text)
    if not seeds:
        seeds = [urljoin(origin + "/", "sitemap.xml")]
    queue = list(dict.fromkeys(seeds))
    seen_sitemaps: set[str] = set()
    pages: list[str] = []
    while queue and len(pages) < limit and len(seen_sitemaps) < max_sitemaps:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            response = await client.get(sitemap_url, timeout=20)
        except Exception as exc:
            log.info("sitemap fetch failed", extra={"data": {"url": sitemap_url, "error": str(exc)}})
            continue
        if response.status_code >= 400:
            continue
        payload = response.content
        if sitemap_url.endswith(".gz") or payload[:2] == b"\x1f\x8b":
            try:
                payload = gzip.decompress(payload)
            except Exception:
                continue
        text = payload.decode("utf-8", errors="replace")
        found, children = parse_sitemap(text)
        for child in children:
            if child not in seen_sitemaps:
                queue.append(child)
        for page in found:
            if page not in pages:
                pages.append(page)
            if len(pages) >= limit:
                break
    return pages
