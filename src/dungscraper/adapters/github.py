"""GitHub repository and file reads via the public REST API."""

from __future__ import annotations

import base64
from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest


def parse_github(url: str) -> dict | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "github.com":
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if owner in {"topics", "features", "settings", "marketplace"}:
        return None
    path = None
    ref = None
    if len(segments) >= 5 and segments[2] in {"blob", "tree"}:
        ref = segments[3]
        path = "/".join(segments[4:])
    return {"owner": owner, "repo": repo, "ref": ref, "path": path}


class GitHubAdapter:
    name = "github"

    def matches(self, host: str, url: str) -> bool:
        return host == "github.com" and parse_github(url) is not None

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData | None:
        parsed = parse_github(request.url)
        if parsed is None:
            return None
        headers = {"Accept": "application/vnd.github+json", "User-Agent": settings.robots_user_agent}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        owner, repo = parsed["owner"], parsed["repo"]
        repo_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=20)
        if repo_response.status_code >= 400:
            return None
        info = repo_response.json()
        if parsed["path"]:
            content_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{parsed['path']}"
            params = {"ref": parsed["ref"]} if parsed["ref"] else None
            file_response = await client.get(content_url, headers=headers, params=params, timeout=20)
            if file_response.status_code >= 400:
                return None
            payload = file_response.json()
            encoded = payload.get("content") or ""
            try:
                text = base64.b64decode(encoded).decode("utf-8", "replace")
            except Exception:
                return None
            language = parsed["path"].rsplit(".", 1)[-1] if "." in parsed["path"] else ""
            markdown = f"# {owner}/{repo}/{parsed['path']}\n\n```{language}\n{text.rstrip()}\n```"
            title = parsed["path"]
        else:
            readme = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers={**headers, "Accept": "application/vnd.github.raw"},
                timeout=20,
            )
            body = readme.text if readme.status_code < 400 else ""
            description = info.get("description") or ""
            markdown = f"# {info.get('full_name') or owner + '/' + repo}\n\n{description}\n\n{body}".strip()
            title = info.get("full_name")
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, description=info.get("description")),
            render_mode="api",
            status_code=200,
            content_type="application/json",
            warnings=["github_api"],
        )
