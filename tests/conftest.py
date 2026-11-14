import os

os.environ.setdefault("DUNGSCRAPER_LOG_JSON", "false")
os.environ.setdefault("DUNGSCRAPER_LOG_LEVEL", "WARNING")
os.environ.setdefault("DUNGSCRAPER_API_KEYS", "")
os.environ.setdefault("DUNGSCRAPER_REDIS_URL", "")
os.environ.pop("DUNGSCRAPER_BASE_URL", None)

import pytest

from dungscraper.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_keys="",
        redis_url="",
        cache_enabled=False,
        log_json=False,
        log_level="WARNING",
        respect_robots_default=True,
        allow_private_networks=True,
        crawl_mode="inline",
        request_timeout_seconds=20,
        browser_timeout_seconds=25,
    )
