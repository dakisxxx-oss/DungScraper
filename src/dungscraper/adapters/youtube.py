"""YouTube public caption tracks. No unofficial player client."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest


def video_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        segment = parts.path.strip("/")
        return segment or None
    if host not in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        return None
    if parts.path == "/watch":
        return (parse_qs(parts.query).get("v") or [None])[0]
    if parts.path.startswith("/shorts/") or parts.path.startswith("/embed/"):
        return parts.path.split("/")[2] if len(parts.path.split("/")) > 2 else None
    return None


def captions_to_markdown(title: str, xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        text = re.sub(r"<[^>]+>", " ", xml_text)
        body = html.unescape(" ".join(text.split()))
    else:
        lines = []
        for node in root.iter("text"):
            piece = html.unescape("".join(node.itertext())).replace("\n", " ").strip()
            if piece:
                lines.append(piece)
        body = "\n".join(lines)
    heading = title or "YouTube captions"
    return f"# {heading}\n\n{body}".strip()


class YouTubeAdapter:
    name = "youtube"

    def matches(self, host: str, url: str) -> bool:
        return video_id(url) is not None

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        vid = video_id(request.url)
        if vid is None:
            return None
        listing = await client.get(
            "https://www.youtube.com/api/timedtext",
            params={"type": "list", "v": vid},
            timeout=15,
        )
        if listing.status_code >= 400 or "<track" not in listing.text:
            return None
        try:
            root = ET.fromstring(listing.text)
        except ET.ParseError:
            return None
        track = None
        for node in root.iter("track"):
            if (node.attrib.get("lang_code") or "").startswith("en"):
                track = node.attrib
                break
        if track is None:
            for node in root.iter("track"):
                track = node.attrib
                break
        if not track:
            return None
        params = {"v": vid, "lang": track.get("lang_code", "en")}
        if track.get("name"):
            params["name"] = track["name"]
        caption = await client.get("https://www.youtube.com/api/timedtext", params=params, timeout=20)
        if caption.status_code >= 400 or not caption.text.strip():
            return None
        title = track.get("lang_original") or "YouTube captions"
        markdown = captions_to_markdown(title, caption.text)
        if len(markdown.split()) < 5:
            return None
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, language=track.get("lang_code")),
            render_mode="api",
            status_code=200,
            content_type="application/xml",
            warnings=["youtube_captions"],
        )
