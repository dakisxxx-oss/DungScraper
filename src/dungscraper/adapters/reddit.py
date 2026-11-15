"""Reddit via the official OAuth API. HTML scraping is not attempted."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.errors import DungScraperError
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest

REDDIT_SETUP = (
    "Reddit is not configured, and DungScraper will not scrape reddit.com HTML. "
    "Reddit's robots.txt disallows that fetch. Use the official API instead: create a "
    "script app at https://www.reddit.com/prefs/apps, then set DUNGSCRAPER_REDDIT_CLIENT_ID, "
    "DUNGSCRAPER_REDDIT_CLIENT_SECRET, and DUNGSCRAPER_REDDIT_USER_AGENT "
    "(a unique string such as 'dungscraper/0.1 by yourname'). Script apps also need "
    "DUNGSCRAPER_REDDIT_USERNAME and DUNGSCRAPER_REDDIT_PASSWORD for the password grant. "
    "If those are omitted, DungScraper tries the client_credentials grant."
)

_token: dict[str, float | str] = {}


def parse_reddit_url(url: str) -> dict:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    query = parse_qs(parts.query)
    kind = "unknown"
    subreddit = None
    post_id = None
    search = (query.get("q") or [None])[0]
    if segments[:1] == ["r"] and len(segments) >= 2:
        subreddit = segments[1]
        if len(segments) >= 4 and segments[2] == "comments":
            kind = "post"
            post_id = segments[3]
        elif len(segments) >= 3 and segments[2] == "search":
            kind = "search"
        elif len(segments) == 2 or (len(segments) >= 3 and segments[2] in {"hot", "new", "top", "rising"}):
            kind = "subreddit"
    elif segments[:1] == ["comments"] and len(segments) >= 2:
        kind = "post"
        post_id = segments[1]
    elif segments[:1] == ["search"] or search:
        kind = "search"
    return {"kind": kind, "subreddit": subreddit, "post_id": post_id, "search": search, "sort": _sort(segments)}


def _sort(segments: list[str]) -> str:
    if len(segments) >= 3 and segments[0] == "r" and segments[2] in {"hot", "new", "top", "rising"}:
        return segments[2]
    return "hot"


def format_post(data: dict) -> str:
    title = data.get("title") or "(untitled)"
    author = data.get("author") or "[deleted]"
    score = data.get("score", 0)
    body = (data.get("selftext") or "").strip()
    link = data.get("url") or ""
    lines = [
        f"# {title}",
        "",
        f"u/{author} · {score} points · r/{data.get('subreddit') or '?'}",
    ]
    if link and not data.get("is_self"):
        lines.extend(["", link])
    if body:
        lines.extend(["", body])
    return "\n".join(lines).strip()


def format_comment(node: dict, depth: int, max_depth: int) -> list[str]:
    if depth > max_depth or not isinstance(node, dict):
        return []
    data = node.get("data") or {}
    if node.get("kind") != "t1":
        return []
    author = data.get("author") or "[deleted]"
    score = data.get("score", 0)
    body = (data.get("body") or "").replace("\n", " ").strip()
    indent = "  " * depth
    lines = [f"{indent}- **u/{author}** ({score}): {body}"]
    replies = data.get("replies")
    if isinstance(replies, dict):
        for child in (replies.get("data") or {}).get("children") or []:
            lines.extend(format_comment(child, depth + 1, max_depth))
    return lines


def format_listing(children: list[dict], *, heading: str) -> str:
    lines = [f"# {heading}", ""]
    for child in children:
        data = (child or {}).get("data") or {}
        if child.get("kind") != "t3":
            continue
        title = data.get("title") or "(untitled)"
        author = data.get("author") or "[deleted]"
        score = data.get("score", 0)
        permalink = data.get("permalink") or ""
        link = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
        lines.append(f"- [{title}]({link}) · u/{author} · {score} points")
    return "\n".join(lines).strip()


class RedditAdapter:
    name = "reddit"

    def matches(self, host: str, url: str) -> bool:
        return host in {"reddit.com", "old.reddit.com", "np.reddit.com", "i.reddit.com"} or host.endswith(".reddit.com")

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData:
        if not settings.reddit_configured:
            raise DungScraperError("reddit_not_configured", REDDIT_SETUP, status=400)
        parsed = parse_reddit_url(request.url)
        if parsed["kind"] == "unknown":
            raise DungScraperError(
                "reddit_unsupported",
                "This Reddit URL is not a post, subreddit, or search. DungScraper will not fall back to HTML.",
                status=400,
            )
        token = await _access_token(client, settings)
        headers = {"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent}
        if parsed["kind"] == "post":
            response = await client.get(
                f"https://oauth.reddit.com/comments/{parsed['post_id']}",
                headers=headers,
                params={"limit": 100, "depth": request.max_comment_depth + 1, "raw_json": 1},
                timeout=20,
            )
            _raise_for_status(response)
            payload = response.json()
            post = (((payload[0] or {}).get("data") or {}).get("children") or [{}])[0].get("data") or {}
            comments = ((payload[1] or {}).get("data") or {}).get("children") or []
            lines = [format_post(post), "", "## Comments"]
            for child in comments:
                lines.extend(format_comment(child, 0, request.max_comment_depth))
            markdown = "\n".join(lines).strip()
            title = post.get("title")
        elif parsed["kind"] == "search":
            params = {"q": parsed["search"] or "", "limit": 25, "raw_json": 1, "restrict_sr": bool(parsed["subreddit"])}
            path = f"/r/{parsed['subreddit']}/search" if parsed["subreddit"] else "/search"
            response = await client.get("https://oauth.reddit.com" + path, headers=headers, params=params, timeout=20)
            _raise_for_status(response)
            children = ((response.json().get("data") or {}).get("children")) or []
            heading = f"Search r/{parsed['subreddit']}: {parsed['search']}" if parsed["subreddit"] else f"Search: {parsed['search']}"
            markdown = format_listing(children, heading=heading)
            title = heading
        else:
            sort = parsed["sort"]
            response = await client.get(
                f"https://oauth.reddit.com/r/{parsed['subreddit']}/{sort}",
                headers=headers,
                params={"limit": 25, "raw_json": 1},
                timeout=20,
            )
            _raise_for_status(response)
            children = ((response.json().get("data") or {}).get("children")) or []
            title = f"r/{parsed['subreddit']}"
            markdown = format_listing(children, heading=title)
        return ScrapeData(
            url=request.url,
            markdown=markdown,
            metadata=PageMetadata(title=title, description="Reddit via the official OAuth API"),
            render_mode="api",
            status_code=200,
            content_type="application/json",
            warnings=["reddit_api"],
        )


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    raise DungScraperError(
        "reddit_api_error",
        f"Reddit's API returned HTTP {response.status_code}. HTML scraping was not attempted.",
        status=502,
    )


async def _access_token(client: httpx.AsyncClient, settings: Settings) -> str:
    cached = _token.get("value")
    expires = float(_token.get("expires") or 0)
    if isinstance(cached, str) and expires > time.time() + 30:
        return cached
    headers = {"User-Agent": settings.reddit_user_agent}
    auth = (settings.reddit_client_id, settings.reddit_client_secret)
    if settings.reddit_username and settings.reddit_password:
        data = {
            "grant_type": "password",
            "username": settings.reddit_username,
            "password": settings.reddit_password,
        }
    else:
        data = {"grant_type": "client_credentials"}
    response = await client.post(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        auth=auth,
        headers=headers,
        timeout=20,
    )
    if response.status_code >= 400:
        raise DungScraperError(
            "reddit_auth_failed",
            "Reddit rejected the OAuth client. Script apps need DUNGSCRAPER_REDDIT_USERNAME and "
            "DUNGSCRAPER_REDDIT_PASSWORD as well as the client id and secret. "
            f"HTTP {response.status_code}.",
            status=401,
        )
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise DungScraperError("reddit_auth_failed", "Reddit did not return an access token.", status=401)
    _token["value"] = token
    _token["expires"] = time.time() + float(payload.get("expires_in") or 3600)
    return token
