import pytest

from dungscraper.config import Settings
from dungscraper.core.content import challenge_vendor, is_challenge
from dungscraper.core.escalate import JAR, Clearance, escalate_challenge, ladder_for
from dungscraper.core.feeds import discover_feed, feed_to_markdown, medium_feed_url
from dungscraper.core.fetch import RawFetch, StickyProxies
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeRequest


def _fetch(text: str, status: int = 200, tier: str = "http") -> RawFetch:
    return RawFetch(
        url="https://example.com/page",
        status_code=status,
        headers={},
        content=text.encode(),
        text=text,
        content_type="text/html",
        render_mode="http",
        tier=tier,
    )


def test_vendors_are_named_only_on_challenge_pages():
    assert challenge_vendor("<html>Just a moment... cf-chl</html>", 403) == "cloudflare"
    assert challenge_vendor("<html><script src='https://ct.captcha-delivery.com/c.js'></script>datadome</html>", 403) == "datadome"
    assert challenge_vendor("<html>px-captcha perimeterx</html>", 403) == "perimeterx"
    assert challenge_vendor("<p>blocked by akamai _abck</p>", 403) == "akamai"
    article = "<html><body><p>" + ("This is a real article about Akamai. " * 30) + "</p></body></html>"
    assert is_challenge(article, 200) is False
    assert challenge_vendor(article, 200) is None


def test_medium_and_generic_feeds():
    assert medium_feed_url("https://medium.com/@ada/some-story-abc") == "https://medium.com/feed/@ada"
    assert medium_feed_url("https://medium.com/tag/python") == "https://medium.com/feed/tag/python"
    assert medium_feed_url("https://medium.com/some-publication/a-post") == "https://medium.com/feed/some-publication"
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    assert discover_feed(html, "https://example.com/post") == "https://example.com/feed.xml"
    markdown = feed_to_markdown(
        """<rss><channel><item><title>Hello</title><link>https://medium.com/@ada/hello</link>
        <description>A long enough description of the post for the feed fallback to count.</description></item></channel></rss>""",
        wanted_url="https://medium.com/@ada/hello",
    )
    assert "Hello" in markdown
    assert "long enough" in markdown


def test_sticky_proxy_reuses_a_domain():
    pool = StickyProxies(["http://a:1", "http://b:1"])
    assert pool.for_domain("example.com") == "http://a:1"
    assert pool.for_domain("example.com") == "http://a:1"
    assert pool.for_domain("other.test") == "http://b:1"


def test_ladder_skips_optional_tiers_unless_configured():
    settings = Settings(flaresolverr_url="", solve_captcha=False, fetch_tiers="", log_json=False)
    assert ladder_for(settings, "auto")[:2] == ["rss", "curl"]
    assert "flaresolverr" not in ladder_for(settings, "auto")
    assert "seleniumbase" not in ladder_for(settings, "auto")
    settings.flaresolverr_url = "http://127.0.0.1:8191/v1"
    settings.solve_captcha = True
    order = ladder_for(settings, "auto")
    assert order[-2:] == ["flaresolverr", "seleniumbase"]
    assert ladder_for(settings, "http") == []


@pytest.mark.asyncio
async def test_escalation_stops_at_the_first_clean_tier():
    calls = []

    async def curl(_current):
        calls.append("curl")
        return _fetch("<html><p>" + ("word " * 40) + "</p></html>", 200, "curl")

    async def patchright(_current):
        calls.append("patchright")
        return _fetch("<html>should not run</html>", 200, "patchright")

    settings = Settings(log_json=False, log_level="ERROR", flaresolverr_url="", solve_captcha=False)
    start = _fetch("<html>Just a moment...</html>", 403)
    result = await escalate_challenge(
        start,
        request=ScrapeRequest(url="https://example.com/page", render="auto", cache=False),
        settings=settings,
        client=None,  # type: ignore[arg-type]
        browser=None,  # type: ignore[arg-type]
        proxy=None,
        runners={"rss": curl, "curl": curl, "patchright": patchright},
    )
    assert calls == ["curl"]
    assert result.tier == "rss"
    assert is_challenge(result.text, result.status_code) is False


@pytest.mark.asyncio
async def test_missing_tier_is_reported_and_skipped():
    async def curl(_current):
        raise DungScraperError("tier_unavailable", "need curl_cffi", status=501)

    async def camoufox(_current):
        from dungscraper.core.escalate import TierUnavailable

        raise TierUnavailable("camoufox", "camoufox")

    settings = Settings(log_json=False, log_level="ERROR")
    start = _fetch("<html>Just a moment...</html>", 403)
    result = await escalate_challenge(
        start,
        request=ScrapeRequest(url="https://news.example/post", render="auto", cache=False),
        settings=settings,
        client=None,  # type: ignore[arg-type]
        browser=None,
        proxy=None,
        runners={"curl": curl, "camoufox": camoufox},
    )
    assert result.tier == "http"
    assert any(item.startswith("tier_unavailable:camoufox") for item in result.warnings)
    assert any("curl_failed" in item for item in result.warnings)


def test_clearance_cookie_jar_is_per_domain():
    JAR.put("https://www.example.com/a", {"cf_clearance": "abc"}, "TestAgent")
    stored = JAR.get("https://example.com/b")
    assert stored == Clearance(cookies={"cf_clearance": "abc"}, user_agent="TestAgent")
    assert JAR.get("https://other.test/") is None
