"""arXiv abstract pages via the public Atom API."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest

ATOM = "{http://www.w3.org/2005/Atom}"


def arxiv_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"arxiv.org", "export.arxiv.org"}:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2 and segments[0] in {"abs", "pdf"}:
        return segments[1].removesuffix(".pdf")
    return None


def format_atom(xml_text: str) -> tuple[str, str] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    entry = root.find(f"{ATOM}entry")
    if entry is None:
        return None
    title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
    summary = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
    authors = [
        " ".join((node.findtext(f"{ATOM}name") or "").split())
        for node in entry.findall(f"{ATOM}author")
    ]
    published = (entry.findtext(f"{ATOM}published") or "")[:10]
    lines = [f"# {title}", "", f"Authors: {', '.join(authors)}"]
    if published:
        lines.append(f"Published: {published}")
    lines.extend(["", summary])
    return "\n".join(lines).strip(), title


class ArxivAdapter:
    name = "arxiv"

    def matches(self, host: str, url: str) -> bool:
        return arxiv_id(url) is not None

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        paper_id = arxiv_id(request.url)
        if paper_id is None or urlsplit(request.url).path.startswith("/pdf/"):
            return None
        response = await client.get(
            "https://export.arxiv.org/api/query",
            params={"id_list": paper_id},
            timeout=20,
        )
        if response.status_code >= 400:
            return None
        formatted = format_atom(response.text)
        if formatted is None:
            return None
        markdown, title = formatted
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, description="arXiv via the public API"),
            render_mode="api",
            status_code=200,
            content_type="application/atom+xml",
            warnings=["arxiv_api"],
        )
