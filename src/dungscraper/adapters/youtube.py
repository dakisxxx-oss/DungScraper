"""YouTube public caption tracks. No unofficial player client.

The timedtext list endpoint is tried first. When it is empty, caption track
URLs are read from the watch page's player response. That is the same public
page a browser loads, not an authenticated API.
"""

from __future__ import annotations

import html
import json
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
    heading = title or "YouTube captions"
    stripped = (xml_text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        body = _json3_text(stripped)
        return f"# {heading}\n\n{body}".strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        text = re.sub(r"<[^>]+>", " ", xml_text)
        body = html.unescape(" ".join(text.split()))
    else:
        text_nodes = [node for node in root.iter() if node.tag.split("}")[-1] == "text"]
        nodes = text_nodes or [node for node in root.iter() if node.tag.split("}")[-1] == "p"]
        lines = []
        for node in nodes:
            piece = html.unescape("".join(node.itertext())).replace("\n", " ").strip()
            if piece:
                lines.append(piece)
        body = "\n".join(lines)
    return f"# {heading}\n\n{body}".strip()


def caption_tracks_from_watch(page_html: str) -> tuple[str, list[dict]]:
    """Title and captionTracks from a watch-page player response."""
    blob = _player_blob(page_html or "")
    if not blob:
        return "", []
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return "", []
    if not isinstance(data, dict):
        return "", []
    title = str((data.get("videoDetails") or {}).get("title") or "")
    tracks = (
        ((data.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get("captionTracks")
        or []
    )
    if not isinstance(tracks, list):
        return title, []
    return title, [track for track in tracks if isinstance(track, dict)]


def pick_caption_track(tracks: list[dict]) -> dict | None:
    def language(track: dict) -> str:
        return str(track.get("languageCode") or track.get("lang_code") or "").lower()

    english = [track for track in tracks if language(track).startswith("en")]
    pool = english or list(tracks)
    manual = [track for track in pool if track.get("kind") != "asr"]
    chosen = manual or pool
    return chosen[0] if chosen else None


class YouTubeAdapter:
    name = "youtube"

    def matches(self, host: str, url: str) -> bool:
        return video_id(url) is not None

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        vid = video_id(request.url)
        if vid is None:
            return None
        found = await _captions_via_list(client, vid)
        if found is not None and len(found[2].split()) < 5:
            found = None
        if found is None:
            found = await _captions_via_watch(client, vid, settings)
        if found is None:
            return None
        title, language, markdown = found
        if len(markdown.split()) < 5:
            return None
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, language=language or None),
            render_mode="api",
            status_code=200,
            content_type="application/xml",
            warnings=["youtube_captions"],
        )


async def _captions_via_list(client: httpx.AsyncClient, vid: str) -> tuple[str, str, str] | None:
    try:
        listing = await client.get(
            "https://www.youtube.com/api/timedtext",
            params={"type": "list", "v": vid},
            timeout=15,
        )
    except httpx.HTTPError:
        return None
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
    try:
        caption = await client.get("https://www.youtube.com/api/timedtext", params=params, timeout=20)
    except httpx.HTTPError:
        return None
    if caption.status_code >= 400 or not caption.text.strip():
        return None
    title = track.get("lang_original") or "YouTube captions"
    return title, track.get("lang_code") or "", captions_to_markdown(title, caption.text)


async def _captions_via_watch(
    client: httpx.AsyncClient,
    vid: str,
    settings: Settings,
) -> tuple[str, str, str] | None:
    headers = {
        "User-Agent": settings.fetch_user_agent,
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        page = await client.get(
            "https://www.youtube.com/watch",
            params={"v": vid, "hl": "en"},
            headers=headers,
            timeout=20,
        )
    except httpx.HTTPError:
        return None
    if page.status_code >= 400:
        return None
    title, tracks = caption_tracks_from_watch(page.text)
    track = pick_caption_track(tracks)
    if not track:
        return None
    base = str(track.get("baseUrl") or "").replace("\\u0026", "&")
    if base.startswith("//"):
        base = "https:" + base
    if not base.startswith("http"):
        return None
    try:
        caption = await client.get(base, headers=headers, timeout=20)
    except httpx.HTTPError:
        return None
    if caption.status_code >= 400 or not caption.text.strip():
        return None
    language = str(track.get("languageCode") or "")
    heading = title or "YouTube captions"
    return heading, language, captions_to_markdown(heading, caption.text)


def _player_blob(page_html: str) -> str | None:
    fallback = None
    start = 0
    marker = "ytInitialPlayerResponse"
    while True:
        at = page_html.find(marker, start)
        if at < 0:
            return fallback
        brace = page_html.find("{", at)
        if brace < 0 or brace - at > 80:
            start = at + len(marker)
            continue
        blob = _json_object_at(page_html, brace)
        if not blob:
            start = at + len(marker)
            continue
        if "captionTracks" in blob:
            return blob
        if fallback is None and "videoDetails" in blob:
            fallback = blob
        start = brace + len(blob)


def _json_object_at(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _json3_text(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    events = data.get("events") if isinstance(data, dict) else None
    lines: list[str] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        piece = "".join(
            str(seg.get("utf8") or "")
            for seg in (event.get("segs") or [])
            if isinstance(seg, dict)
        ).replace("\n", " ").strip()
        if piece:
            lines.append(piece)
    return "\n".join(lines)
