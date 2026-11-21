"""Reddit without requiring API keys.

Order:

1. Official OAuth, only when client id and secret are set. A working token
   stays on this path.
2. Public JSON (``old.reddit.com``, then ``www.reddit.com``, then
   ``np.reddit.com``). Same fields as the API: score, author, selftext,
   nested comments. One shared gap between requests, one retry on HTTP 429,
   and a Chrome-impersonated retry when the datacenter IP is blocked.
3. RSS/Atom for the same resource.
4. ``old.reddit.com`` HTML, parsed from the ``thing`` markup.

Third-party mirrors (redlib, pullpush, and similar) are not used. They
disappear often enough that a failure there is worse than a clear error.

A 429 with a long ``Retry-After`` stops the chain instead of walking every
host. Block pages (403) try the next host and the next format.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup

from dungscraper.config import Settings
from dungscraper.core.feeds import FeedItem, parse_feed
from dungscraper.errors import DungScraperError
from dungscraper.models import PageMetadata, ScrapeData, ScrapeRequest

REDDIT_SETUP = (
    "Reddit is readable with no API key. DungScraper requests public JSON, then RSS, "
    "then old.reddit HTML. Set DUNGSCRAPER_REDDIT_CLIENT_ID and DUNGSCRAPER_REDDIT_CLIENT_SECRET "
    "to use the official API when you have a client. Script apps also need "
    "DUNGSCRAPER_REDDIT_USERNAME and DUNGSCRAPER_REDDIT_PASSWORD."
)

DEFAULT_SCRIPT_UA = "dungscraper/0.1 (script app)"
PUBLIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
JSON_HOSTS = ("https://old.reddit.com", "https://www.reddit.com", "https://np.reddit.com")
MAX_BACKOFF_S = 8.0
_SORTS = {"hot", "new", "top", "rising"}
_TIMES = {"hour", "day", "week", "month", "year", "all"}
_TOKEN: dict[str, float | str] = {}


class RedditGate:
    """One in-process gap for every Reddit request, plus a penalty after HTTP 429."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next = 0.0

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def wait(self, interval: float) -> None:
        async with self._guard():
            now = time.monotonic()
            scheduled = max(now, self._next)
            step = interval if interval > 0 else 0.0
            self._next = scheduled + step
            delay = scheduled - now
        if delay > 0:
            await asyncio.sleep(delay)

    async def penalize(self, seconds: float) -> None:
        if seconds <= 0:
            return
        async with self._guard():
            self._next = max(self._next, time.monotonic() + seconds)

    def reset(self) -> None:
        self._next = 0.0
        self._lock = None
        self._loop = None


GATE = RedditGate()


class _TransportFailure(Exception):
    pass


class _BadPayload(Exception):
    pass


def reset_reddit_throttle() -> None:
    GATE.reset()
    _TOKEN.clear()


def public_user_agent(settings: Settings) -> str:
    custom = (settings.reddit_user_agent or "").strip()
    if custom and custom != DEFAULT_SCRIPT_UA:
        return custom
    return PUBLIC_UA


def parse_reddit_url(url: str) -> dict:
    parts = urlsplit(url)
    segments = [_strip_suffix(segment) for segment in parts.path.split("/") if segment]
    segments = [segment for segment in segments if segment]
    query = parse_qs(parts.query)
    search = (query.get("q") or [None])[0]
    t = (query.get("t") or [None])[0]
    if t not in _TIMES:
        t = None
    limit = (query.get("limit") or [None])[0]
    kind = "unknown"
    subreddit = None
    post_id = None
    username = None
    section = None
    sort = "hot"
    if segments[:1] == ["r"] and len(segments) >= 2:
        subreddit = segments[1]
        if len(segments) >= 4 and segments[2] == "comments":
            kind = "post"
            post_id = segments[3]
        elif len(segments) >= 3 and segments[2] == "search":
            kind = "search"
        elif len(segments) == 2 or (len(segments) >= 3 and segments[2] in _SORTS):
            kind = "subreddit"
            if len(segments) >= 3 and segments[2] in _SORTS:
                sort = segments[2]
    elif segments[:1] == ["comments"] and len(segments) >= 2:
        kind = "post"
        post_id = segments[1]
    elif segments and segments[0] in {"user", "u"} and len(segments) >= 2:
        kind = "user"
        username = segments[1]
        if len(segments) >= 3 and segments[2] in {"submitted", "comments", "overview"}:
            section = segments[2]
        else:
            section = "overview"
    elif segments[:1] == ["search"] or search:
        kind = "search"
    query_sort = (query.get("sort") or [None])[0]
    if kind == "subreddit" and query_sort in _SORTS:
        sort = query_sort
    return {
        "kind": kind,
        "subreddit": subreddit,
        "post_id": post_id,
        "search": search,
        "sort": sort,
        "username": username,
        "section": section,
        "t": t,
        "limit": limit,
    }


def build_reddit_target(
    *,
    url: str = "",
    subreddit: str = "",
    sort: str = "hot",
    time_range: str = "",
    post_id: str = "",
    query: str = "",
    username: str = "",
    limit: int = 25,
) -> str:
    """Build a reddit.com URL for the MCP tool. Pass-through URLs must be Reddit."""
    if url.strip():
        target = url.strip()
        host = (urlsplit(target).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host != "reddit.com" and not host.endswith(".reddit.com"):
            raise DungScraperError(
                "reddit_unsupported",
                "The reddit tool only reads reddit.com URLs. Use scrape for other sites.",
                status=400,
            )
        return target
    try:
        bounded = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        bounded = 25
    sort_name = (sort or "hot").strip().lower()
    if sort_name not in _SORTS:
        raise DungScraperError(
            "reddit_bad_sort",
            "sort must be hot, new, top, or rising.",
            status=400,
        )
    window = (time_range or "").strip().lower()
    if window and window not in _TIMES:
        raise DungScraperError(
            "reddit_bad_time",
            "time_range must be hour, day, week, month, year, or all.",
            status=400,
        )
    if post_id.strip():
        token = post_id.strip().strip("/")
        if token.startswith("http://") or token.startswith("https://"):
            return build_reddit_target(url=token, limit=bounded)
        if token.startswith("t3_"):
            token = token[3:]
        if not token.isalnum():
            raise DungScraperError("reddit_bad_name", "post_id must be a Reddit base36 id.", status=400)
        return f"https://www.reddit.com/comments/{token}/?limit={bounded}"
    if query.strip():
        if len(query) > 300:
            raise DungScraperError("reddit_bad_name", "Search query is too long.", status=400)
        encoded = urlencode({"q": query.strip(), "limit": bounded})
        if subreddit.strip():
            name = _clean_subreddit(subreddit)
            return f"https://www.reddit.com/r/{name}/search?{encoded}&restrict_sr=on"
        return f"https://www.reddit.com/search?{encoded}"
    if username.strip():
        name = _clean_username(username)
        return f"https://www.reddit.com/user/{name}/submitted/?limit={bounded}"
    if subreddit.strip():
        name = _clean_subreddit(subreddit)
        base = f"https://www.reddit.com/r/{name}/{sort_name}/?limit={bounded}"
        if sort_name == "top" and window:
            return base + f"&t={window}"
        return base
    raise DungScraperError(
        "reddit_query_missing",
        "Pass a Reddit url, subreddit, post_id, query, or username.",
        status=400,
    )


def format_post(data: dict) -> str:
    title = data.get("title") or "(untitled)"
    author = data.get("author") or "[deleted]"
    score = data.get("score", 0)
    body = (data.get("selftext") or "").strip()
    link = data.get("url") or ""
    meta = f"u/{author} · {score} points · r/{data.get('subreddit') or '?'}"
    when = _utc_day(data.get("created_utc"))
    if when:
        meta += f" · {when}"
    comments = data.get("num_comments")
    if isinstance(comments, int):
        meta += f" · {comments} comments"
    lines = [f"# {title}", "", meta]
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
    count = 0
    for child in children:
        data = (child or {}).get("data") or {}
        kind = (child or {}).get("kind")
        if kind == "t3":
            title = _md_link_text(data.get("title") or "(untitled)")
            author = data.get("author") or "[deleted]"
            score = data.get("score", 0)
            permalink = data.get("permalink") or ""
            link = f"https://www.reddit.com{permalink}" if str(permalink).startswith("/") else permalink
            lines.append(f"- [{title}]({link}) · u/{author} · {score} points")
            count += 1
        elif kind == "t1":
            body = " ".join((data.get("body") or "").split())
            if len(body) > 280:
                body = body[:277] + "..."
            author = data.get("author") or "[deleted]"
            permalink = data.get("permalink") or ""
            link = f"https://www.reddit.com{permalink}" if str(permalink).startswith("/") else permalink
            lines.append(f"- comment by **u/{author}** ({data.get('score', 0)}): {body}")
            if link:
                lines.append(f"  {link}")
            count += 1
    if count == 0:
        lines.append("_No posts._")
    return "\n".join(lines).strip()


class RedditAdapter:
    name = "reddit"

    def matches(self, host: str, url: str) -> bool:
        return host in {"reddit.com", "old.reddit.com", "np.reddit.com", "i.reddit.com"} or host.endswith(".reddit.com")

    async def fetch(self, request: ScrapeRequest, *, settings: Settings, client: httpx.AsyncClient) -> ScrapeData:
        parsed = parse_reddit_url(request.url)
        if parsed["kind"] == "unknown":
            raise DungScraperError(
                "reddit_unsupported",
                "This Reddit URL is not a subreddit listing, post, search, or user page.",
                status=400,
            )
        oauth_error: DungScraperError | None = None
        if settings.reddit_configured:
            try:
                return await _fetch_oauth(client, settings, request, parsed)
            except DungScraperError as exc:
                oauth_error = exc
        try:
            data = await _fetch_public(client, settings, request, parsed)
        except DungScraperError as exc:
            if oauth_error is None:
                raise
            raise DungScraperError(
                exc.code,
                f"Official Reddit API failed ({oauth_error.code}: {oauth_error.message}) "
                f"and the public fallback also failed. {exc.message}",
                status=exc.status,
            ) from exc
        if oauth_error is not None and "reddit_oauth_fallback" not in data.warnings:
            data.warnings.insert(0, "reddit_oauth_fallback")
        return data


def _strip_suffix(segment: str) -> str:
    lowered = segment.lower()
    for suffix in (".json", ".rss", ".xml"):
        if lowered.endswith(suffix):
            return segment[: -len(suffix)]
    return segment


def _clean_subreddit(value: str) -> str:
    name = value.strip().strip("/")
    for prefix in ("r/", "/r/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
            break
    if not name or len(name) > 80 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+" for char in name):
        raise DungScraperError(
            "reddit_bad_name",
            "Subreddit names use letters, digits, underscores, or + for a multireddit.",
            status=400,
        )
    return name


def _clean_username(value: str) -> str:
    name = value.strip().strip("/")
    for prefix in ("u/", "/u/", "user/", "/user/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
            break
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if not name or len(name) > 30 or any(char not in allowed for char in name):
        raise DungScraperError(
            "reddit_bad_name",
            "Usernames use letters, digits, underscores, or hyphens.",
            status=400,
        )
    return name


def _md_link_text(title: str) -> str:
    return title.replace("[", "(").replace("]", ")")


def _utc_day(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(value))
    except (OverflowError, OSError, ValueError):
        return ""


def _heading(parsed: dict) -> str:
    kind = parsed["kind"]
    if kind == "search":
        query = parsed.get("search") or ""
        if parsed.get("subreddit"):
            return f"Search r/{parsed['subreddit']}: {query}".strip()
        return f"Search: {query}".strip()
    if kind == "user":
        name = parsed.get("username") or "?"
        section = parsed.get("section") or "overview"
        if section == "overview":
            return f"u/{name}"
        return f"u/{name} · {section}"
    sort = parsed.get("sort") or "hot"
    title = f"r/{parsed.get('subreddit') or '?'}"
    if sort != "hot":
        title += f" · {sort}"
    if sort == "top" and parsed.get("t"):
        title += f" ({parsed['t']})"
    return title


def _resource_path(parsed: dict) -> str:
    kind = parsed["kind"]
    if kind == "post":
        post_id = parsed["post_id"]
        if parsed.get("subreddit"):
            return f"/r/{parsed['subreddit']}/comments/{post_id}"
        return f"/comments/{post_id}"
    if kind == "search":
        if parsed.get("subreddit"):
            return f"/r/{parsed['subreddit']}/search"
        return "/search"
    if kind == "user":
        section = parsed.get("section") or "overview"
        if section == "overview":
            return f"/user/{parsed['username']}"
        return f"/user/{parsed['username']}/{section}"
    return f"/r/{parsed['subreddit']}/{parsed.get('sort') or 'hot'}"


def _query_params(parsed: dict, request: ScrapeRequest, *, json_mode: bool) -> dict[str, str]:
    params: dict[str, str] = {}
    if json_mode:
        params["raw_json"] = "1"
    raw_limit = parsed.get("limit") or (100 if parsed["kind"] == "post" else 25)
    try:
        limit = max(1, min(int(raw_limit), 100))
    except (TypeError, ValueError):
        limit = 25
    params["limit"] = str(limit)
    if parsed["kind"] == "post":
        params["depth"] = str(min(int(request.max_comment_depth) + 1, 10))
    if parsed["kind"] == "search":
        params["q"] = parsed.get("search") or ""
        if parsed.get("subreddit"):
            params["restrict_sr"] = "on"
    if parsed["kind"] == "subreddit" and parsed.get("sort") == "top" and parsed.get("t"):
        params["t"] = str(parsed["t"])
    return params


def _public_headers(settings: Settings, accept: str) -> dict[str, str]:
    return {
        "User-Agent": public_user_agent(settings),
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }


async def _fetch_public(
    client: httpx.AsyncClient,
    settings: Settings,
    request: ScrapeRequest,
    parsed: dict,
) -> ScrapeData:
    path = _resource_path(parsed)
    json_params = _query_params(parsed, request, json_mode=True)
    html_params = _query_params(parsed, request, json_mode=False)
    attempts: list[str] = []
    impersonated = False
    json_headers = _public_headers(settings, "application/json,text/javascript;q=0.9,*/*;q=0.1")
    for host in JSON_HOSTS:
        url = host + path + ".json"
        try:
            response = await _request(client, url, headers=json_headers, params=json_params, settings=settings)
        except _TransportFailure as exc:
            attempts.append(f"{host} json: {exc}")
            continue
        rendered = _take_json(response, parsed, request, attempts, host)
        if rendered == "stop":
            continue
        if rendered is not None:
            return rendered
        if settings.reddit_browser_impersonate and not impersonated and not _rate_limited(response):
            impersonated = True
            await GATE.wait(settings.reddit_min_interval_seconds)
            alternate = await _impersonated_get(url, json_params)
            if alternate is None:
                attempts.append("impersonate: unavailable")
            else:
                rendered = _take_json(alternate, parsed, request, attempts, f"impersonate {host}")
                if rendered is not None and rendered != "stop":
                    if "reddit_impersonate" not in rendered.warnings:
                        rendered.warnings.append("reddit_impersonate")
                    return rendered
    rss_headers = _public_headers(
        settings,
        "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
    )
    for host in JSON_HOSTS:
        rss_path = path + ".rss" if parsed["kind"] == "search" else path + "/.rss"
        url = host + rss_path
        try:
            response = await _request(client, url, headers=rss_headers, params=html_params, settings=settings)
        except _TransportFailure as exc:
            attempts.append(f"{host} rss: {exc}")
            continue
        if _stops(response):
            _raise_access_or_rate(response, parsed)
        if response.status_code == 200:
            markdown, title = _render_feed(response.text, parsed)
            if markdown:
                return _package(
                    request,
                    markdown,
                    title,
                    "Reddit via public RSS. Scores and nested replies may be missing.",
                    "rss",
                    "application/rss+xml",
                    ["reddit_rss"],
                    "reddit_rss",
                )
            attempts.append(f"{host} rss: empty feed")
            continue
        attempts.append(f"{host} rss: HTTP {response.status_code}")
    html_headers = _public_headers(settings, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    html_url = "https://old.reddit.com" + path
    try:
        response = await _request(client, html_url, headers=html_headers, params=html_params, settings=settings)
    except _TransportFailure as exc:
        attempts.append(f"old.reddit.com html: {exc}")
    else:
        if _stops(response):
            _raise_access_or_rate(response, parsed)
        if response.status_code == 200:
            try:
                markdown, title = _render_html(response.text, parsed, request)
            except _BadPayload as exc:
                attempts.append(f"old.reddit.com html: {exc}")
            else:
                return _package(
                    request,
                    markdown,
                    title,
                    "Reddit via old.reddit.com HTML.",
                    "http",
                    "text/html",
                    ["reddit_html"],
                    "reddit_html",
                )
        else:
            attempts.append(f"old.reddit.com html: HTTP {response.status_code}")
    raise _unavailable(attempts)


def _take_json(response: httpx.Response, parsed: dict, request: ScrapeRequest, attempts: list[str], label: str):
    if _stops(response):
        _raise_access_or_rate(response, parsed)
    if response.status_code == 200 and _looks_json(response):
        try:
            markdown, title = _render_json(response.json(), parsed, request)
        except _BadPayload:
            attempts.append(f"{label} json: unexpected payload")
            return "stop"
        return _package(
            request,
            markdown,
            title,
            "Reddit via public JSON. No API key.",
            "api",
            "application/json",
            ["reddit_json"],
            "reddit_json",
        )
    attempts.append(f"{label} json: HTTP {response.status_code}")
    return None


def _stops(response: httpx.Response) -> bool:
    """Private/missing pages and a long Retry-After stop the chain. A short 429 does not."""
    if _access_reason(response):
        return True
    return _rate_limited(response) and _retry_after_seconds(response) > MAX_BACKOFF_S


def _raise_access_or_rate(response: httpx.Response, parsed: dict) -> None:
    reason = _access_reason(response)
    if reason:
        raise _access_error(reason, parsed)
    if _rate_limited(response):
        raise _rate_error(response)


async def _request(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str],
    settings: Settings,
) -> httpx.Response:
    response: httpx.Response | None = None
    last_error: Exception | None = None
    for attempt in range(2):
        await GATE.wait(settings.reddit_min_interval_seconds)
        try:
            response = await client.get(url, headers=headers, params=params, timeout=20)
        except httpx.HTTPError as exc:
            last_error = exc
            break
        if _rate_limited(response) and attempt == 0:
            wait = _retry_after_seconds(response)
            if wait > MAX_BACKOFF_S:
                raise _rate_error(response)
            await GATE.penalize(wait)
            continue
        return response
    if response is not None:
        return response
    raise _TransportFailure(str(last_error or "connection failed"))


def _retry_after_seconds(response: httpx.Response) -> float:
    """Seconds to wait. A missing or zero value still pauses briefly."""
    raw = str(response.headers.get("retry-after") or "").strip()
    seconds: float | None = None
    if raw.isdigit():
        seconds = float(raw)
    elif raw:
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(raw)
            if parsed is not None:
                seconds = parsed.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = None
    if seconds is None:
        return 2.0
    if seconds < 1:
        return 1.0
    return seconds


def _rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code == 200 and _looks_json(response):
        return False
    sample = (response.text or "")[:500].lower()
    return "too many requests" in sample or "whoa there" in sample


def _looks_json(response: httpx.Response) -> bool:
    text = (response.text or "").lstrip()
    return text.startswith("{") or text.startswith("[")


def _access_reason(response: httpx.Response) -> str | None:
    if not _looks_json(response):
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    blob = f"{payload.get('reason') or ''} {payload.get('message') or ''}".lower()
    if "private" in blob:
        return "private"
    if "quarantin" in blob:
        return "quarantined"
    if "banned" in blob:
        return "banned"
    if response.status_code == 404 or payload.get("error") == 404 or "not found" in blob:
        return "missing"
    return None


def _access_error(reason: str, parsed: dict) -> DungScraperError:
    if reason == "missing":
        target = parsed.get("subreddit") or parsed.get("username") or parsed.get("post_id") or "that page"
        return DungScraperError(
            "reddit_not_found",
            f"Reddit has no listing for {target} (HTTP 404).",
            status=404,
        )
    if reason == "quarantined":
        return DungScraperError(
            "reddit_quarantined",
            "This subreddit is quarantined. Reddit did not return its posts.",
            status=403,
        )
    if reason == "banned":
        return DungScraperError("reddit_banned", "This subreddit is banned.", status=403)
    return DungScraperError(
        "reddit_private",
        "This subreddit is private. Public Reddit does not return its posts.",
        status=403,
    )


def _rate_error(response: httpx.Response) -> DungScraperError:
    raw = str(response.headers.get("retry-after") or "").strip()
    asked = f" Retry-After is {raw}." if raw else ""
    return DungScraperError(
        "reddit_rate_limited",
        "Reddit rate-limited this request (HTTP 429)."
        f"{asked} Wait and try again. No API key is required.",
        status=429,
    )


def _unavailable(attempts: list[str]) -> DungScraperError:
    if attempts and all("429" in item for item in attempts):
        return DungScraperError(
            "reddit_rate_limited",
            "Reddit rate-limited every public request (HTTP 429). Wait and try again. No API key is required.",
            status=429,
            details={"attempts": attempts[-8:]},
        )
    tail = "; ".join(attempts[-6:]) or "no response"
    blocked = any("403" in item or "blocked" in item.lower() for item in attempts)
    note = ""
    if blocked:
        note = " Datacenter and cloud IPs are often blocked; a home connection usually works."
    return DungScraperError(
        "reddit_unavailable",
        "Reddit did not return a readable page. Tried public JSON on old.reddit.com, "
        "www.reddit.com, and np.reddit.com, then RSS, then old.reddit HTML. "
        f"Last attempts: {tail}.{note} No API key is required.",
        status=502,
        details={"attempts": attempts[-8:]},
    )


def _package(
    request: ScrapeRequest,
    markdown: str,
    title: str | None,
    description: str,
    render_mode: str,
    content_type: str,
    warnings: list[str],
    fetch_tier: str,
) -> ScrapeData:
    return ScrapeData(
        url=request.url,
        markdown=markdown,
        metadata=PageMetadata(title=title, description=description),
        render_mode=render_mode,
        status_code=200,
        content_type=content_type,
        warnings=list(warnings),
        fetch_tier=fetch_tier,
    )


def _render_json(payload: object, parsed: dict, request: ScrapeRequest) -> tuple[str, str]:
    if parsed["kind"] == "post":
        post, comments = _split_post(payload)
        lines = [format_post(post), "", "## Comments"]
        wrote = False
        for child in comments:
            rendered = format_comment(child, 0, request.max_comment_depth)
            if rendered:
                wrote = True
                lines.extend(rendered)
        if not wrote:
            lines.append("_No comments._")
        title = str(post.get("title") or "Reddit")
        return "\n".join(lines).strip(), title
    if not isinstance(payload, dict):
        raise _BadPayload()
    children = (payload.get("data") or {}).get("children") if isinstance(payload.get("data"), dict) else None
    if not isinstance(children, list):
        raise _BadPayload()
    heading = _heading(parsed)
    return format_listing(children, heading=heading), heading


def _split_post(payload: object) -> tuple[dict, list]:
    if isinstance(payload, list):
        post_listing = payload[0] if payload else {}
        comment_listing = payload[1] if len(payload) > 1 else {}
    elif isinstance(payload, dict):
        post_listing = payload
        comment_listing = {}
    else:
        raise _BadPayload()
    if not isinstance(post_listing, dict):
        raise _BadPayload()
    children = ((post_listing.get("data") or {}).get("children")) or []
    post = {}
    if children and isinstance(children[0], dict):
        post = children[0].get("data") or {}
    if not isinstance(post, dict) or not post:
        raise _BadPayload()
    comments: list = []
    if isinstance(comment_listing, dict):
        comments = ((comment_listing.get("data") or {}).get("children")) or []
    if not isinstance(comments, list):
        comments = []
    return post, comments


def _render_feed(xml_text: str, parsed: dict) -> tuple[str, str]:
    title, items = parse_feed(xml_text)
    if not items:
        return "", ""
    heading = title or _heading(parsed)
    if parsed["kind"] == "post":
        lines = [f"# {heading}", "", "## Comments"]
        for item in items:
            author = _feed_author(item)
            body = item.summary or item.title
            lines.append(f"- **u/{author}**: {body}")
        return "\n".join(lines).strip(), heading
    lines = [f"# {heading}", ""]
    for item in items:
        author = _feed_author(item)
        label = _md_link_text(item.title or item.link or "(untitled)")
        link = item.link or ""
        if link:
            lines.append(f"- [{label}]({link}) · u/{author}")
        else:
            lines.append(f"- {label} · u/{author}")
        if item.summary and item.summary != item.title:
            snippet = item.summary if len(item.summary) <= 400 else item.summary[:397] + "..."
            lines.append(f"  {snippet}")
    return "\n".join(lines).strip(), heading


def _feed_author(item: FeedItem) -> str:
    author = (item.author or "").strip()
    for prefix in ("/u/", "u/"):
        if author.lower().startswith(prefix):
            author = author[len(prefix) :]
            break
    return author or "[deleted]"


def _render_html(html: str, parsed: dict, request: ScrapeRequest) -> tuple[str, str]:
    soup = BeautifulSoup(html or "", "lxml")
    if soup.select_one("div.thing") is None:
        sample = soup.get_text(" ", strip=True)[:400].lower()
        if any(token in sample for token in ("blocked", "whoa there", "too many requests", "just a moment")):
            raise _BadPayload("block page")
        raise _BadPayload("no reddit entries")
    if parsed["kind"] == "post":
        return _html_post(soup, parsed, request)
    heading = _heading(parsed)
    children: list[dict] = []
    scope = soup.select_one("div.content") or soup
    table = scope.select_one("div.sitetable")
    things = []
    if table is not None:
        things = [
            child
            for child in table.find_all("div", recursive=False)
            if "thing" in (child.get("class") or [])
        ]
    if not things:
        things = soup.select("div.thing")
    for thing in things:
        classes = thing.get("class") or []
        if "promoted" in classes:
            continue
        if "comment" in classes:
            entry = thing.find("div", class_="entry", recursive=False)
            md = entry.select_one("div.usertext-body .md") if entry else None
            body = md.get_text(" ", strip=True) if md else ""
            children.append(
                {
                    "kind": "t1",
                    "data": {
                        "author": thing.get("data-author") or "[deleted]",
                        "score": _html_score(thing),
                        "body": body,
                        "permalink": thing.get("data-permalink") or "",
                    },
                }
            )
        elif "link" in classes:
            children.append({"kind": "t3", "data": _html_link_data(thing, parsed)})
    if not children:
        raise _BadPayload("no posts")
    return format_listing(children, heading=heading), heading


def _html_post(soup: BeautifulSoup, parsed: dict, request: ScrapeRequest) -> tuple[str, str]:
    link = soup.select_one("div.thing.link")
    data = _html_link_data(link, parsed) if link is not None else {
        "title": soup.title.get_text(strip=True) if soup.title else "Reddit",
        "author": "[deleted]",
        "score": 0,
        "subreddit": parsed.get("subreddit"),
        "is_self": True,
        "selftext": "",
    }
    area = soup.select_one("div.commentarea")
    lines = [format_post(data), "", "## Comments"]
    wrote = False
    for node in _html_comment_nodes(area):
        rendered = format_comment(node, 0, request.max_comment_depth)
        if rendered:
            wrote = True
            lines.extend(rendered)
    if not wrote:
        lines.append("_No comments._")
    title = str(data.get("title") or "Reddit")
    return "\n".join(lines).strip(), title


def _html_link_data(thing, parsed: dict) -> dict:
    title_node = thing.select_one("a.title") if thing is not None else None
    title = title_node.get_text(strip=True) if title_node else "(untitled)"
    classes = thing.get("class") or []
    is_self = "self" in classes
    href = (title_node.get("href") if title_node else "") or ""
    expando = thing.select_one("div.expando div.md") or thing.select_one("div.entry div.usertext-body div.md")
    body = expando.get_text("\n", strip=True) if expando else ""
    return {
        "title": title,
        "author": thing.get("data-author") or "[deleted]",
        "score": _html_score(thing),
        "subreddit": thing.get("data-subreddit") or parsed.get("subreddit"),
        "permalink": thing.get("data-permalink") or "",
        "is_self": is_self,
        "selftext": body if is_self else "",
        "url": "" if is_self else href,
    }


def _html_score(thing) -> int:
    try:
        return int(thing.get("data-score"))
    except (TypeError, ValueError):
        return 0


def _html_comment_nodes(container) -> list[dict]:
    if container is None:
        return []
    return [_html_comment_node(thing) for thing in _top_comments(container)]


def _top_comments(container) -> list:
    found = []
    for child in container.find_all("div", recursive=False):
        classes = child.get("class") or []
        if "thing" in classes and "comment" in classes:
            found.append(child)
        elif "sitetable" in classes or "child" in classes:
            found.extend(_top_comments(child))
    return found


def _html_comment_node(thing) -> dict:
    entry = thing.find("div", class_="entry", recursive=False)
    md = entry.select_one("div.usertext-body .md") if entry else None
    body = md.get_text(" ", strip=True) if md else ""
    child = thing.find("div", class_="child", recursive=False)
    replies: dict | str = ""
    if child is not None:
        nested = [_html_comment_node(sub) for sub in _top_comments(child)]
        if nested:
            replies = {"kind": "Listing", "data": {"children": nested}}
    return {
        "kind": "t1",
        "data": {
            "author": thing.get("data-author") or "[deleted]",
            "score": _html_score(thing),
            "body": body,
            "replies": replies,
        },
    }


async def _impersonated_get(url: str, params: dict[str, str]) -> httpx.Response | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    full = url + ("?" + urlencode(params) if params else "")
    headers = {
        "Accept": "application/json,text/javascript;q=0.9,*/*;q=0.1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        try:
            session = AsyncSession(impersonate="chrome131")
        except Exception:
            session = AsyncSession(impersonate="chrome")
        async with session:
            response = await session.get(full, headers=headers, timeout=20, allow_redirects=True)
    except Exception:
        return None
    content = getattr(response, "content", None) or b""
    header_items = dict(getattr(response, "headers", {}) or {})
    return httpx.Response(int(response.status_code), content=content, headers=header_items)


async def _fetch_oauth(
    client: httpx.AsyncClient,
    settings: Settings,
    request: ScrapeRequest,
    parsed: dict,
) -> ScrapeData:
    token = await _access_token(client, settings)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent}
    await GATE.wait(settings.reddit_min_interval_seconds)
    try:
        response = await _oauth_get(client, headers, request, parsed)
    except httpx.HTTPError as exc:
        raise DungScraperError("reddit_api_error", f"Reddit's API request failed: {exc}", status=502) from exc
    if response.status_code >= 400:
        raise DungScraperError(
            "reddit_api_error",
            f"Reddit's API returned HTTP {response.status_code}.",
            status=502,
        )
    try:
        markdown, title = _render_json(response.json(), parsed, request)
    except _BadPayload as exc:
        raise DungScraperError("reddit_api_error", "Reddit's API returned an unexpected payload.", status=502) from exc
    return _package(
        request,
        markdown,
        title,
        "Reddit via the official OAuth API",
        "api",
        "application/json",
        ["reddit_api"],
        "reddit_oauth",
    )


async def _oauth_get(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    request: ScrapeRequest,
    parsed: dict,
) -> httpx.Response:
    if parsed["kind"] == "post":
        return await client.get(
            f"https://oauth.reddit.com/comments/{parsed['post_id']}",
            headers=headers,
            params={"limit": 100, "depth": request.max_comment_depth + 1, "raw_json": 1},
            timeout=20,
        )
    if parsed["kind"] == "search":
        params = {
            "q": parsed["search"] or "",
            "limit": 25,
            "raw_json": 1,
            "restrict_sr": bool(parsed["subreddit"]),
        }
        path = f"/r/{parsed['subreddit']}/search" if parsed["subreddit"] else "/search"
        return await client.get("https://oauth.reddit.com" + path, headers=headers, params=params, timeout=20)
    if parsed["kind"] == "user":
        section = parsed.get("section") or "overview"
        suffix = "" if section == "overview" else f"/{section}"
        return await client.get(
            f"https://oauth.reddit.com/user/{parsed['username']}{suffix}",
            headers=headers,
            params={"limit": 25, "raw_json": 1},
            timeout=20,
        )
    params = {"limit": 25, "raw_json": 1}
    if parsed.get("t"):
        params["t"] = parsed["t"]
    return await client.get(
        f"https://oauth.reddit.com/r/{parsed['subreddit']}/{parsed['sort']}",
        headers=headers,
        params=params,
        timeout=20,
    )


async def _access_token(client: httpx.AsyncClient, settings: Settings) -> str:
    cached = _TOKEN.get("value")
    expires = float(_TOKEN.get("expires") or 0)
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
    try:
        response = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data=data,
            auth=auth,
            headers=headers,
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise DungScraperError("reddit_auth_failed", f"Reddit token request failed: {exc}", status=502) from exc
    if response.status_code >= 400:
        raise DungScraperError(
            "reddit_auth_failed",
            "Reddit rejected the OAuth client. Script apps need DUNGSCRAPER_REDDIT_USERNAME and "
            "DUNGSCRAPER_REDDIT_PASSWORD as well as the client id and secret. "
            f"HTTP {response.status_code}.",
            status=401,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise DungScraperError("reddit_auth_failed", "Reddit did not return an access token.", status=401) from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise DungScraperError("reddit_auth_failed", "Reddit did not return an access token.", status=401)
    _TOKEN["value"] = token
    _TOKEN["expires"] = time.time() + float(payload.get("expires_in") or 3600)
    return str(token)
