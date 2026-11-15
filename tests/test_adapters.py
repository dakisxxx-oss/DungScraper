import pytest

from dungscraper.adapters.arxiv import format_atom
from dungscraper.adapters.github import parse_github
from dungscraper.adapters.hackernews import hn_target
from dungscraper.adapters.reddit import REDDIT_SETUP, format_comment, format_listing, format_post, parse_reddit_url
from dungscraper.adapters.wikipedia import wiki_title
from dungscraper.adapters.youtube import captions_to_markdown, video_id
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


@pytest.mark.asyncio
async def test_reddit_without_credentials_explains_the_app():
    settings = Settings(
        reddit_client_id="",
        reddit_client_secret="",
        log_json=False,
        log_level="ERROR",
        respect_robots_default=True,
        cache_enabled=False,
    )
    with pytest.raises(DungScraperError) as caught:
        await scrape_url(
            ScrapeRequest(url="https://www.reddit.com/r/Python/", respect_robots=True, cache=False),
            settings=settings,
        )
    assert caught.value.code == "reddit_not_configured"
    assert "robots.txt" in caught.value.message
    assert "DUNGSCRAPER_REDDIT_CLIENT_ID" in caught.value.message
    assert REDDIT_SETUP.split()[0] in caught.value.message


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


def test_markdown_diff_detects_a_change():
    result = markdown_diff("alpha\n", "alpha\nbeta\n", url="https://example.com/a")
    assert result.changed is True
    assert "+beta" in result.diff
    same = markdown_diff("alpha\n", "alpha\n")
    assert same.changed is False
