"""Keyless Reddit: mocked JSON, RSS, HTML, OAuth preference, and rate limits."""

import httpx
import pytest

from dungscraper.adapters import reddit as reddit_mod
from dungscraper.adapters.reddit import (
    RedditGate,
    build_reddit_target,
    reset_reddit_throttle,
)
from dungscraper.config import Settings
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeRequest

LISTING = {
    "kind": "Listing",
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "title": "Hello",
                    "author": "ada",
                    "score": 12,
                    "permalink": "/r/Python/comments/abc/hello/",
                    "subreddit": "Python",
                    "is_self": True,
                    "selftext": "Body text",
                },
            }
        ]
    },
}

POST = [
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "title": "Hello",
                        "author": "ada",
                        "score": 12,
                        "subreddit": "Python",
                        "is_self": True,
                        "selftext": "Body text",
                        "num_comments": 1,
                    },
                }
            ]
        },
    },
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "author": "grace",
                        "score": 3,
                        "body": "A reply",
                        "replies": {
                            "kind": "Listing",
                            "data": {
                                "children": [
                                    {"kind": "t1", "data": {"author": "linus", "score": 1, "body": "Nested", "replies": ""}}
                                ]
                            },
                        },
                    },
                }
            ]
        },
    },
]

RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Python</title>
    <item>
      <title>From RSS</title>
      <link>https://www.reddit.com/r/Python/comments/abc/from_rss/</link>
      <dc:creator>ada</dc:creator>
      <description>A short RSS body.</description>
    </item>
  </channel>
</rss>
"""

HTML = """
<html><body><div class="content"><div class="sitetable">
  <div class="thing link" data-author="ada" data-score="9" data-permalink="/r/Python/comments/abc/html/">
    <p class="title"><a class="title" href="https://example.com/post">From HTML</a></p>
  </div>
</div></div></body></html>
"""

POST_HTML = """
<html><body>
  <div class="thing link self" data-author="ada" data-score="4" data-subreddit="Python">
    <a class="title">HTML post</a>
    <div class="expando"><div class="usertext-body"><div class="md"><p>Post body</p></div></div></div>
  </div>
  <div class="commentarea"><div class="sitetable">
    <div class="thing comment" data-author="grace" data-score="2">
      <div class="entry"><div class="usertext-body"><div class="md"><p>Top comment</p></div></div></div>
      <div class="child"><div class="sitetable">
        <div class="thing comment" data-author="linus" data-score="1">
          <div class="entry"><div class="usertext-body"><div class="md"><p>Nested html</p></div></div></div>
        </div>
      </div></div>
    </div>
  </div></div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _reset_reddit_state(monkeypatch):
    reset_reddit_throttle()

    async def no_browser(*_args) -> bool:
        return False

    monkeypatch.setattr(reddit_mod, "chromium_installed", no_browser)
    yield
    reset_reddit_throttle()


def _settings(**overrides) -> Settings:
    values = dict(
        allow_private_networks=True,
        cache_enabled=False,
        log_json=False,
        log_level="ERROR",
        reddit_client_id="",
        reddit_client_secret="",
        reddit_min_interval_seconds=0,
        reddit_browser_impersonate=False,
    )
    values.update(overrides)
    return Settings(**values)


async def _scrape(url: str, handler, settings: Settings | None = None) -> object:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await scrape_url(
            ScrapeRequest(url=url, respect_robots=True, cache=False),
            settings=settings or _settings(),
            client=client,
        )


def test_build_reddit_target_shapes():
    assert build_reddit_target(subreddit="r/Python", sort="new").startswith("https://www.reddit.com/r/Python/new/")
    top = build_reddit_target(subreddit="Python", sort="top", time_range="week")
    assert "/top/" in top and "t=week" in top
    assert "/user/ada/submitted/" in build_reddit_target(username="u/ada")
    assert "q=web+scraping" in build_reddit_target(query="web scraping", subreddit="Python") or "q=web" in build_reddit_target(
        query="web scraping", subreddit="Python"
    )
    assert build_reddit_target(post_id="t3_abc123").endswith("/comments/abc123/?limit=25") or "/comments/abc123/" in build_reddit_target(
        post_id="t3_abc123"
    )
    with pytest.raises(DungScraperError) as caught:
        build_reddit_target()
    assert caught.value.code == "reddit_query_missing"
    with pytest.raises(DungScraperError) as bad:
        build_reddit_target(subreddit="../etc")
    assert bad.value.code == "reddit_bad_name"
    with pytest.raises(DungScraperError) as foreign:
        build_reddit_target(url="https://example.com/r/Python")
    assert foreign.value.code == "reddit_unsupported"


@pytest.mark.asyncio
async def test_reddit_gate_spaces_calls():
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reddit_mod.asyncio, "sleep", fake_sleep)
    try:
        gate = RedditGate()
        await gate.wait(0.5)
        await gate.wait(0.5)
    finally:
        monkey.undo()
    assert slept and slept[0] == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio
async def test_json_block_falls_through_to_rss():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json"):
            return httpx.Response(403, text="<html>blocked by network security</html>")
        if request.url.path.endswith(".rss"):
            assert request.url.host == "old.reddit.com"
            return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
        return httpx.Response(500, text="html should not run")

    data = await _scrape("https://www.reddit.com/r/Python/new/", handler)
    assert data.fetch_tier == "reddit_rss"
    assert "From RSS" in data.markdown
    assert "u/ada" in data.markdown
    assert "not configured" not in (data.metadata.description or "").lower()


@pytest.mark.asyncio
async def test_html_fallback_parses_old_reddit():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json") or request.url.path.endswith(".rss"):
            return httpx.Response(403, text="blocked")
        assert request.url.host == "old.reddit.com"
        return httpx.Response(200, text=HTML)

    data = await _scrape("https://www.reddit.com/r/Python/", handler)
    assert data.fetch_tier == "reddit_html"
    assert "From HTML" in data.markdown
    assert "u/ada" in data.markdown
    assert "9 points" in data.markdown


@pytest.mark.asyncio
async def test_post_html_keeps_nested_comments():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json") or ".rss" in request.url.path:
            return httpx.Response(403, text="blocked")
        return httpx.Response(200, text=POST_HTML)

    data = await _scrape("https://www.reddit.com/r/Python/comments/abc123/hello/", handler)
    assert "Post body" in data.markdown
    assert "u/grace" in data.markdown
    assert any(line.startswith("  ") and "u/linus" in line for line in data.markdown.splitlines())


@pytest.mark.asyncio
async def test_post_json_includes_comments():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/comments/abc123.json" in request.url.path
        assert request.url.params["depth"] == "4"
        return httpx.Response(200, json=POST)

    data = await _scrape("https://old.reddit.com/r/Python/comments/abc123/hello/", handler)
    assert "Body text" in data.markdown
    assert "1 comments" in data.markdown
    assert "u/grace" in data.markdown
    assert "u/linus" in data.markdown


@pytest.mark.asyncio
async def test_search_and_user_json():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path + "?" + request.url.query.decode())
        if request.url.path.endswith("/search.json"):
            assert "web" in request.url.params["q"]
            assert request.url.params["restrict_sr"] == "on"
        if "/user/ada/submitted.json" in request.url.path:
            payload = {
                "kind": "Listing",
                "data": {
                    "children": [
                        {"kind": "t3", "data": {"title": "User post", "author": "ada", "score": 2, "permalink": "/r/x/comments/z/p/"}}
                    ]
                },
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=LISTING)

    search = await _scrape("https://www.reddit.com/r/Python/search?q=web+scraping", handler)
    assert search.metadata.title.startswith("Search r/Python")
    assert "Hello" in search.markdown
    user = await _scrape("https://www.reddit.com/user/ada/submitted", handler)
    assert "User post" in user.markdown
    assert any("/user/ada/submitted.json" in item for item in seen)


@pytest.mark.asyncio
async def test_private_and_missing_stop_the_chain():
    def private(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"reason": "private", "message": "Forbidden", "error": 403})

    with pytest.raises(DungScraperError) as caught:
        await _scrape("https://www.reddit.com/r/secrets/", private)
    assert caught.value.code == "reddit_unavailable"
    assert "private" in caught.value.message
    assert "not configured" not in caught.value.message.lower()

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found", "error": 404})

    with pytest.raises(DungScraperError) as gone:
        await _scrape("https://www.reddit.com/r/nope/", missing)
    assert gone.value.code == "reddit_unavailable"
    assert "missing" in gone.value.message


@pytest.mark.asyncio
async def test_long_retry_after_stops_reddit_and_uses_the_archive():
    reddit_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arctic-shift.photon-reddit.com":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "abc",
                            "title": "From archive",
                            "author": "ada",
                            "score": 4,
                            "subreddit": "Python",
                            "created_utc": 1700000000,
                            "num_comments": 0,
                            "selftext": "Body",
                            "url": "https://www.reddit.com/r/Python/comments/abc/from_archive/",
                        }
                    ]
                },
            )
        reddit_calls["n"] += 1
        return httpx.Response(429, text="too many requests", headers={"Retry-After": "60"})

    data = await _scrape("https://www.reddit.com/r/Python/", handler)
    assert reddit_calls["n"] == 1
    assert data.fetch_tier == "reddit_arctic_shift"
    assert "From archive" in data.markdown
    assert "Arctic Shift" in data.markdown


@pytest.mark.asyncio
async def test_short_429_retries_once(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(reddit_mod.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith(".json"):
            return httpx.Response(500, text="no")
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="whoa there", headers={"Retry-After": "1"})
        return httpx.Response(200, json=LISTING)

    data = await _scrape("https://www.reddit.com/r/Python/", handler)
    assert "Hello" in data.markdown
    assert calls["n"] == 2
    assert slept and slept[0] == pytest.approx(1, abs=0.05)


@pytest.mark.asyncio
async def test_short_429_on_json_still_tries_rss(monkeypatch):
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(reddit_mod.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json"):
            return httpx.Response(429, text="whoa there", headers={"Retry-After": "1"})
        if request.url.path.endswith(".rss"):
            return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
        return httpx.Response(500, text="html should not run")

    data = await _scrape("https://www.reddit.com/r/Python/", handler)
    assert data.fetch_tier == "reddit_rss"
    assert "From RSS" in data.markdown


@pytest.mark.asyncio
async def test_all_public_methods_fail_clearly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>blocked</html>")

    with pytest.raises(DungScraperError) as caught:
        await _scrape("https://www.reddit.com/r/Python/", handler)
    assert caught.value.code == "reddit_unavailable"
    message = caught.value.message
    assert "JSON" in message and "Arctic Shift" in message
    assert "python -m playwright install chromium" in message
    assert "not configured" not in message.lower()
    assert "No API key is required" in message


@pytest.mark.asyncio
async def test_oauth_is_preferred_when_keys_are_set():
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.url.host == "oauth.reddit.com"
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json=LISTING)

    settings = _settings(reddit_client_id="id", reddit_client_secret="secret", reddit_browser_impersonate=True)
    data = await _scrape("https://www.reddit.com/r/Python/new/", handler, settings)
    assert data.fetch_tier == "reddit_oauth"
    assert "reddit_api" in data.warnings
    assert "old.reddit.com" not in hosts
    assert not any(".json" in host for host in hosts)
    assert "Hello" in data.markdown


@pytest.mark.asyncio
async def test_oauth_failure_falls_back_to_public_json():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(401, text="nope")
        assert request.url.host == "old.reddit.com"
        return httpx.Response(200, json=LISTING)

    settings = _settings(reddit_client_id="id", reddit_client_secret="secret")
    data = await _scrape("https://www.reddit.com/r/Python/", handler, settings)
    assert "reddit_oauth_fallback" in data.warnings
    assert "reddit_json" in data.warnings
    assert "Hello" in data.markdown


@pytest.mark.asyncio
async def test_impersonate_recovers_a_blocked_json_host(monkeypatch):
    async def fake_impersonated(url: str, params: dict) -> httpx.Response:
        assert url.startswith("https://old.reddit.com/")
        assert params["raw_json"] == "1"
        return httpx.Response(200, json=LISTING)

    monkeypatch.setattr(reddit_mod, "_impersonated_get", fake_impersonated)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json"):
            return httpx.Response(403, text="blocked")
        return httpx.Response(500, text="should have impersonated")

    settings = _settings(reddit_browser_impersonate=True)
    data = await _scrape("https://www.reddit.com/r/Python/", handler, settings)
    assert "reddit_impersonate" in data.warnings
    assert "Hello" in data.markdown


@pytest.mark.asyncio
async def test_custom_user_agent_is_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "dungscraper-test/2.0"
        return httpx.Response(200, json=LISTING)

    settings = _settings(reddit_user_agent="dungscraper-test/2.0")
    data = await _scrape("https://www.reddit.com/r/Python/", handler, settings)
    assert "Hello" in data.markdown


@pytest.mark.asyncio
async def test_unsupported_reddit_url_is_explicit():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsupported URLs must not be fetched")

    with pytest.raises(DungScraperError) as caught:
        await _scrape("https://www.reddit.com/r/Python/wiki/index", handler)
    assert caught.value.code == "reddit_unsupported"
    assert "not configured" not in caught.value.message.lower()


@pytest.mark.asyncio
async def test_decode_error_and_login_redirect_fall_through_to_arctic_shift():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arctic-shift.photon-reddit.com":
            assert request.headers["user-agent"].startswith("dungscraper/")
            assert request.url.params["subreddit"] == "ObsidianMD"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "zz",
                            "title": "Archive hello",
                            "author": "ada",
                            "score": 9,
                            "subreddit": "ObsidianMD",
                            "created_utc": 1700000000,
                            "num_comments": 1,
                            "selftext": "Notes",
                            "url": "https://www.reddit.com/r/ObsidianMD/comments/zz/archive_hello/",
                        }
                    ]
                },
            )
        if request.url.params.get("raw_json") == "1" and request.headers.get("accept-encoding") != "identity":
            raise httpx.DecodingError(
                "Error -3 while decompressing data: incorrect header check",
                request=request,
            )
        return httpx.Response(
            302,
            headers={"location": "https://old.reddit.com/login/?reason=lor2"},
        )

    data = await _scrape("https://www.reddit.com/r/ObsidianMD/hot/", handler)
    assert data.fetch_tier == "reddit_arctic_shift"
    assert "Archive hello" in data.markdown
    assert "Arctic Shift" in data.markdown


@pytest.mark.asyncio
async def test_arctic_shift_loads_a_post_and_its_comments():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host.endswith("reddit.com") and not host.startswith("arctic-shift") and request.headers.get("accept-encoding") != "identity":
            return httpx.Response(403, text="<html>blocked</html>")
        if request.url.path == "/api/posts/ids":
            assert request.url.params["ids"] == "abc123"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "abc123",
                            "title": "Hello",
                            "author": "ada",
                            "score": 3,
                            "subreddit": "Python",
                            "created_utc": 1700000000,
                            "num_comments": 1,
                            "selftext": "Body text",
                            "url": "https://www.reddit.com/r/Python/comments/abc123/hello/",
                        }
                    ]
                },
            )
        if request.url.path == "/api/comments/search":
            assert request.url.params["link_id"] == "t3_abc123"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "c1", "body": "A reply", "author": "grace", "score": 2, "parent_id": "t3_abc123"},
                        {"id": "c2", "body": "Nested", "author": "linus", "score": 1, "parent_id": "t1_c1"},
                    ]
                },
            )
        return httpx.Response(404, text="no")

    data = await _scrape("https://www.reddit.com/r/Python/comments/abc123/hello/", handler)
    assert "Body text" in data.markdown
    assert "u/grace" in data.markdown
    assert "u/linus" in data.markdown
    assert data.fetch_tier == "reddit_arctic_shift"


def test_shreddit_listing_marks_the_browser_source():
    from dungscraper.adapters.reddit import markdown_from_shreddit

    markdown, title = markdown_from_shreddit(
        {
            "posts": [
                {
                    "title": "Hello",
                    "author": "ada",
                    "score": "4",
                    "permalink": "/r/Python/comments/abc/hello/",
                    "subreddit": "Python",
                    "body": "",
                }
            ],
            "comments": [],
        },
        {"kind": "subreddit", "subreddit": "Python", "sort": "hot", "t": None, "search": None, "username": None, "section": None},
        ScrapeRequest(url="https://www.reddit.com/r/Python/", cache=False),
    )
    assert title.startswith("r/Python")
    assert "Hello" in markdown
    assert "u/ada" in markdown


@pytest.mark.asyncio
async def test_browser_step_runs_only_when_chromium_is_installed(monkeypatch):
    async def browser_ready(*_args) -> bool:
        return True

    async def fake_read(settings, parsed, request):
        return {
            "posts": [
                {
                    "title": "Rendered",
                    "author": "ada",
                    "score": "1",
                    "permalink": "/r/Python/comments/abc/rendered/",
                    "subreddit": "Python",
                    "body": "",
                }
            ],
            "comments": [],
        }

    monkeypatch.setattr(reddit_mod, "chromium_installed", browser_ready)
    monkeypatch.setattr(reddit_mod, "_read_shreddit", fake_read)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html><script>blocked</script></html>")

    data = await _scrape("https://www.reddit.com/r/Python/", handler)
    assert data.fetch_tier == "reddit_browser"
    assert "Rendered" in data.markdown
    assert "headless Chromium" in data.markdown


def test_user_comment_listing_formats_t1():
    from dungscraper.adapters.reddit import format_listing

    text = format_listing(
        [{"kind": "t1", "data": {"author": "ada", "score": 2, "body": "Noted", "permalink": "/r/Python/comments/x/y/c"}}],
        heading="u/ada",
    )
    assert "comment by **u/ada**" in text
    assert "https://www.reddit.com/r/Python/comments/x/y/c" in text
