import pytest

from dungscraper.config import reset_settings_cache
from dungscraper.local import local_crawl, local_map, local_scrape
from dungscraper.models import CrawlRequest, MapRequest
from site_server import site  # noqa: F401


@pytest.fixture(autouse=True)
def local_env(monkeypatch):
    monkeypatch.setenv("DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS", "true")
    monkeypatch.setenv("DUNGSCRAPER_REDIS_URL", "")
    monkeypatch.setenv("DUNGSCRAPER_CACHE_ENABLED", "false")
    reset_settings_cache()
    yield
    reset_settings_cache()


async def test_local_scrape(site):  # noqa: F811
    data = await local_scrape(site + "/blog/post", "http")
    assert "blog@acme-site.io" in data["markdown"]


async def test_local_map_lists_same_site_links(site):  # noqa: F811
    data = await local_map(MapRequest(url=site + "/", use_sitemap=False))
    urls = {item["url"] for item in data["links"]}
    assert site + "/contact" in urls
    assert site + "/careers" in urls


async def test_local_crawl_waits_and_returns_pages(site):  # noqa: F811
    data = await local_crawl(CrawlRequest(url=site + "/", limit=3, max_depth=1, use_sitemap=False, delay_ms=0))
    assert data["status"] == "completed"
    assert data["completed"] >= 2
    assert any("/contact" in page["url"] for page in data["pages"])
