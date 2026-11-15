import pytest

from dungscraper.config import Settings
from dungscraper.core.fetch import BrowserManager, make_client
from dungscraper.core.scrape import scrape_url
from dungscraper.models import ScrapeRequest

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(
        cache_enabled=False,
        log_json=False,
        log_level="WARNING",
        respect_robots_default=True,
        allow_private_networks=False,
        request_timeout_seconds=40,
        browser_timeout_seconds=45,
        http_retries=1,
    )


@pytest.mark.asyncio
async def test_example_and_wikipedia_and_pdf_and_js_page():
    settings = _settings()
    browser = BrowserManager(settings)
    async with make_client(settings) as client:
        try:
            example = await scrape_url(
                ScrapeRequest(url="https://example.com", formats=["markdown", "metadata", "links"]),
                settings=settings,
                client=client,
                browser=browser,
            )
            assert example.render_mode == "http"
            assert example.metadata and "Example Domain" in (example.metadata.title or "")
            assert example.markdown and "Example Domain" in example.markdown
            assert "documentation examples" in example.markdown

            wiki = await scrape_url(
                ScrapeRequest(
                    url="https://en.wikipedia.org/wiki/Web_scraping",
                    formats=["markdown", "metadata"],
                    timeout_ms=60000,
                ),
                settings=settings,
                client=client,
                browser=browser,
            )
            assert wiki.markdown
            assert "scrap" in wiki.markdown.lower()
            assert wiki.markdown.count("\n#") + wiki.markdown.count("\n##") >= 1
            assert "wikipedia" not in (wiki.markdown[:80].lower()) or "Web scraping" in wiki.markdown

            pdf = await scrape_url(
                ScrapeRequest(
                    url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                    formats=["markdown", "metadata"],
                    # This host challenges a Chrome user agent and serves the file to a plain one.
                    headers={"User-Agent": "DungScraperBot/0.1"},
                ),
                settings=settings,
                client=client,
                browser=browser,
            )
            assert pdf.markdown
            assert "Dummy" in pdf.markdown or "PDF" in pdf.markdown

            quotes = await scrape_url(
                ScrapeRequest(
                    url="https://quotes.toscrape.com/js/",
                    formats=["markdown"],
                    render="browser",
                    respect_robots=True,
                    timeout_ms=45000,
                ),
                settings=settings,
                client=client,
                browser=browser,
            )
            assert quotes.render_mode == "browser"
            assert quotes.markdown
            assert "quote" in quotes.markdown.lower() or "“" in quotes.markdown or '"' in quotes.markdown
        finally:
            await browser.shutdown()
