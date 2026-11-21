"""Wikipedia article HTML from the public REST API."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.core.content import html_to_content
from dungscraper.models import ScrapeData, ScrapeRequest


PROJECT_URL = "+https://github.com/dakisxxx-oss/DungScraper"


def wiki_title(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host.endswith("wikipedia.org"):
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2 and segments[0] == "wiki":
        language = host.split(".")[0]
        return language, unquote(segments[1])
    return None


class WikipediaAdapter:
    name = "wikipedia"

    def matches(self, host: str, url: str) -> bool:
        return host.endswith("wikipedia.org") and "/wiki/" in urlsplit(url).path

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        parsed = wiki_title(request.url)
        if parsed is None:
            return None
        language, title = parsed
        api = f"https://{language}.wikipedia.org/api/rest_v1/page/html/{quote(title, safe='')}"
        # Wikimedia blocks browser-like user agents and bare bot names. It wants a contact URL.
        response = await client.get(
            api,
            headers={"Accept": "text/html", "User-Agent": f"{settings.robots_user_agent} ({PROJECT_URL})"},
            timeout=20,
        )
        if response.status_code >= 400 or not response.text:
            return None
        extracted = html_to_content(response.text, request.url, only_main_content=request.only_main_content)
        return ScrapeData(
            url=request.url,
            markdown=extracted.markdown,
            html=extracted.html if "html" in request.formats else None,
            links=extracted.links if "links" in request.formats else None,
            metadata=extracted.metadata,
            render_mode="api",
            status_code=200,
            content_type="text/html",
            warnings=["wikipedia_api"],
        )
