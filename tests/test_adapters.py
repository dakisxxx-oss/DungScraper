import json

import httpx
import pytest

from dungscraper.adapters.arxiv import format_atom
from dungscraper.adapters.github import parse_github
from dungscraper.adapters.hackernews import hn_target
from dungscraper.adapters.reddit import REDDIT_SETUP, format_comment, format_listing, format_post, parse_reddit_url
from dungscraper.adapters.wikipedia import wiki_title
from dungscraper.adapters.youtube import (
    caption_tracks_from_watch,
    captions_to_markdown,
    pick_caption_track,
    video_id,
)
from dungscraper.adapters.youtube import YouTubeAdapter
from dungscraper.config import Settings
from dungscraper.core.content import strip_overlays
from dungscraper.core.diff import markdown_diff
from dungscraper.core.extract import extract_structured, heuristic_fields, select_fields
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import ExtractRequest, ScrapeRequest


def test_reddit_url_shapes():
    post = parse_reddit_url("https://old.reddit.com/r/Python/comments/abc123/hello_world/")
    assert post["kind"] == "post" and post["post_id"] == "abc123" and post["subreddit"] == "Python"
    listing = parse_reddit_url("https://www.reddit.com/r/Python/new/")
    assert listing["kind"] == "subreddit" and listing["sort"] == "new"
    search = parse_reddit_url("https://www.reddit.com/search?q=web+scraping")
    assert search["kind"] == "search" and search["search"] == "web scraping"
    top = parse_reddit_url("https://www.reddit.com/r/Python/top.json?t=week")
    assert top["kind"] == "subreddit" and top["sort"] == "top" and top["t"] == "week"
    user = parse_reddit_url("https://old.reddit.com/u/ada/submitted/")
    assert user["kind"] == "user" and user["username"] == "ada" and user["section"] == "submitted"


def test_reddit_markdown_includes_score_author_and_depth():
    post = format_post(
        {
            "title": "Hello",
            "author": "ada",
            "score": 12,
            "subreddit": "Python",
            "is_self": True,
            "selftext": "Body text",
        }
    )
    assert "u/ada" in post and "12 points" in post and "Body text" in post
    comment = {
        "kind": "t1",
        "data": {
            "author": "grace",
            "score": 3,
            "body": "A reply",
            "replies": {
                "kind": "Listing",
                "data": {"children": [{"kind": "t1", "data": {"author": "linus", "score": 1, "body": "Nested", "replies": ""}}]},
            },
        },
    }
    lines = format_comment(comment, 0, 2)
    assert any("u/grace" in line for line in lines)
    assert any("u/linus" in line and line.startswith("  ") for line in lines)
    listing = format_listing(
        [{"kind": "t3", "data": {"title": "Post", "author": "ada", "score": 4, "permalink": "/r/Python/comments/x/post/"}}],
        heading="r/Python",
    )
    assert "https://www.reddit.com/r/Python/comments/x/post/" in listing


def test_reddit_setup_text_does_not_require_keys():
    assert "not configured" not in REDDIT_SETUP.lower()
    assert "no API key" in REDDIT_SETUP


@pytest.mark.asyncio
async def test_reddit_without_credentials_reads_public_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "old.reddit.com"
        assert request.url.path == "/r/Python/hot.json"
        assert "Mozilla" in request.headers["user-agent"]
        return httpx.Response(
            200,
            json={
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
                            },
                        }
                    ]
                },
            },
        )

    settings = Settings(
        reddit_client_id="",
        reddit_client_secret="",
        reddit_min_interval_seconds=0,
        reddit_browser_impersonate=False,
        allow_private_networks=True,
        cache_enabled=False,
        log_json=False,
        log_level="ERROR",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await scrape_url(
            ScrapeRequest(url="https://www.reddit.com/r/Python/", respect_robots=True, cache=False),
            settings=settings,
            client=client,
        )
    assert data.fetch_tier == "reddit_json"
    assert "reddit_json" in data.warnings
    assert "reddit_not_configured" not in data.warnings
    assert "Hello" in (data.markdown or "")
    assert "u/ada" in (data.markdown or "")


def test_other_adapters_parse_public_urls():
    assert wiki_title("https://en.wikipedia.org/wiki/Python_(programming_language)")[1].startswith("Python_")
    parsed = parse_github("https://github.com/microsoft/playwright/blob/main/README.md")
    assert parsed["owner"] == "microsoft" and parsed["path"] == "README.md"
    assert hn_target("https://news.ycombinator.com/")["kind"] == "top"
    assert hn_target("https://news.ycombinator.com/item?id=1")["id"] == 1
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Attention Is All You Need</title><summary>We propose a new network.</summary>
      <author><name>Vaswani</name></author><published>2017-06-12T00:00:00Z</published>
    </entry></feed>"""
    markdown, title = format_atom(xml)
    assert title == "Attention Is All You Need"
    assert "Vaswani" in markdown
    captions = captions_to_markdown("Talk", '<transcript><text start="0">Hello there</text></transcript>')
    assert "Hello there" in captions


def test_overlay_strips_signup_and_keeps_the_article():
    markdown = """# Join Stack Overflow

Sign up with Google

# OR

Email

Password

Sign up

# What does the yield keyword do?

The yield keyword turns a function into a generator. """ + ("word " * 40)
    cleaned = strip_overlays(markdown)
    assert "Password" not in cleaned.split("# What", 1)[0]
    assert cleaned.startswith("# What does the yield keyword do?")
    cnn = """### CNN values your feedback

1. How relevant is this ad to you?

# Markets rise after the opening bell

Stocks moved higher in early trading as investors read the morning notes. """ + ("word " * 30)
    assert strip_overlays(cnn).startswith("# Markets rise")


def test_selector_and_heuristic_extract_need_no_model():
    html = "<html><body><h1>Ada</h1><p class='bio'>First programmer</p></body></html>"
    selected = select_fields(html, {"name": "h1", "bio": "css:.bio", "via": "xpath://p"})
    assert selected["name"] == "Ada"
    assert selected["bio"] == "First programmer"
    assert "programmer" in selected["via"]
    fields = heuristic_fields("# Ada\n\nFirst programmer of the analytical engine and more words here today.")
    assert fields["title"] == "Ada"
    assert fields["summary"]


@pytest.mark.asyncio
async def test_selector_mode_does_not_call_an_llm():
    settings = Settings(llm_api_key="", llm_base_url="https://api.openai.com/v1", log_json=False)
    data = await extract_structured(
        ExtractRequest(
            content="<article><h1>Ada</h1><p class='bio'>Notes</p></article>",
            mode="selector",
            selectors={"name": "h1"},
        ),
        settings=settings,
    )
    assert data.model == "selector"
    assert data.results[0].data["name"] == "Ada"


@pytest.mark.asyncio
async def test_youtube_watch_page_captions_when_the_list_is_empty():
    track = "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=en"
    player = {
        "videoDetails": {"title": "Talk"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"baseUrl": track, "languageCode": "en", "kind": "asr"},
                    {"baseUrl": track + "&kind=manual", "languageCode": "en"},
                ]
            }
        },
    }
    page = "<html><script>var ytInitialPlayerResponse = " + json.dumps(player) + ";</script></html>"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/api/timedtext" and request.url.params.get("type") == "list":
            return httpx.Response(404, text="missing")
        if request.url.path == "/watch":
            return httpx.Response(200, text=page)
        if "kind=manual" in str(request.url):
            xml = "<transcript><text start='0'>Hello there from the watch page captions today</text></transcript>"
            return httpx.Response(200, text=xml)
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await YouTubeAdapter().fetch(
            ScrapeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", cache=False),
            settings=Settings(log_json=False, allow_private_networks=True),
            client=client,
        )
    assert data is not None
    assert "watch page captions" in (data.markdown or "")
    assert data.metadata.title == "Talk"
    assert any("/watch" in url for url in seen)
    assert any("kind=manual" in url for url in seen)


@pytest.mark.asyncio
async def test_youtube_list_endpoint_is_still_preferred():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.params.get("type") == "list":
            return httpx.Response(200, text='<transcript_list><track lang_code="en" lang_original="English"/></transcript_list>')
        return httpx.Response(200, text="<transcript><text>Hello there from the timedtext list today</text></transcript>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await YouTubeAdapter().fetch(
            ScrapeRequest(url="https://youtu.be/dQw4w9WgXcQ", cache=False),
            settings=Settings(log_json=False, allow_private_networks=True),
            client=client,
        )
    assert data is not None
    assert "timedtext list" in (data.markdown or "")
    assert "/watch" not in seen


def test_caption_helpers_prefer_manual_english_and_json3():
    title, tracks = caption_tracks_from_watch(
        '<script>ytInitialPlayerResponse = {"videoDetails":{"title":"Talk"},"captions":{"playerCaptionsTracklistRenderer":{"captionTracks":[{"baseUrl":"https://example.com/a","languageCode":"en","kind":"asr"}]}}};</script>'
    )
    assert title == "Talk"
    assert pick_caption_track(tracks)["kind"] == "asr"
    manual = pick_caption_track(
        [
            {"languageCode": "en", "kind": "asr", "baseUrl": "a"},
            {"languageCode": "en", "baseUrl": "b"},
        ]
    )
    assert manual["baseUrl"] == "b"
    text = captions_to_markdown("Talk", '{"events":[{"segs":[{"utf8":"Hello there from json3 captions now"}]}]}')
    assert "json3 captions" in text


@pytest.mark.asyncio
async def test_scrape_renders_an_rss_url_as_items():
    feed = """<?xml version="1.0"?><rss><channel><title>News</title><item>
      <title>Hello feed</title><link>https://example.com/hello</link>
      <description>A long enough description of the post for the feed reader to keep.</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert request.url.path == "/feed.xml"
        return httpx.Response(200, text=feed, headers={"content-type": "application/rss+xml"})

    settings = Settings(allow_private_networks=True, cache_enabled=False, log_json=False, log_level="ERROR")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await scrape_url(
            ScrapeRequest(url="https://example.com/feed.xml", cache=False, respect_robots=True),
            settings=settings,
            client=client,
        )
    assert data.render_mode == "rss"
    assert data.fetch_tier == "feed"
    assert "feed" in data.warnings
    assert "Hello feed" in (data.markdown or "")
    assert data.metadata.title == "News"


@pytest.mark.asyncio
async def test_read_feed_reports_a_robots_block_and_parses_items():
    from dungscraper.core.feeds import fetch_feed

    feed = """<?xml version="1.0"?><rss><channel><title>News</title><item>
      <title>Hello feed</title><link>https://example.com/hello</link>
      <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">ada</dc:creator>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <description>Notes from the feed.</description>
    </item></channel></rss>"""

    def allowed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=feed, headers={"content-type": "application/rss+xml"})

    settings = Settings(allow_private_networks=True, log_json=False, robots_user_agent="DungScraperBot/0.1")
    transport = httpx.MockTransport(allowed)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await fetch_feed("https://example.com/feed.xml", settings=settings, client=client, limit=5)
    assert data["title"] == "News"
    assert data["items"][0]["title"] == "Hello feed"
    assert data["items"][0]["author"] == "ada"
    assert "2024" in data["items"][0]["published"]

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    transport = httpx.MockTransport(blocked)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DungScraperError) as caught:
            await fetch_feed("https://example.com/feed.xml", settings=settings, client=client)
    assert caught.value.code == "robots_disallowed"


def test_markdown_diff_detects_a_change():
    result = markdown_diff("alpha\n", "alpha\nbeta\n", url="https://example.com/a")
    assert result.changed is True
    assert "+beta" in result.diff
    same = markdown_diff("alpha\n", "alpha\n")
    assert same.changed is False
