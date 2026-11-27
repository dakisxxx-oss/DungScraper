"""Environment configuration.

Every setting is `DUNGSCRAPER_*`. Empty API keys disable authentication so a
laptop can run without a secret; set keys before exposing the port.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DUNGSCRAPER_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8741
    api_keys: str = ""
    api_key: str = ""
    redis_url: str = ""
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    cache_max_memory_items: int = 256
    respect_robots_default: bool = True
    robots_user_agent: str = "DungScraperBot/0.1"
    robots_fail_open: bool = True
    fetch_user_agent: str = DEFAULT_FETCH_UA
    max_concurrency: int = 8
    request_timeout_seconds: float = 30
    browser_timeout_seconds: float = 45
    browser_concurrency: int = 3
    browser_channel: str = ""
    browser_executable: str = ""
    interstitial_wait_seconds: float = 15
    data_dir: str = ""
    allow_private_networks: bool = False
    proxy_url: str = ""
    proxy_urls: str = ""
    proxies: str = ""
    fetch_tiers: str = ""
    flaresolverr_url: str = ""
    solve_captcha: bool = False
    mcp_max_chars: int = 12_000
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    searxng_url: str = ""
    brave_api_key: str = ""
    serper_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""
    reddit_user_agent: str = "dungscraper/0.1 (script app)"
    reddit_min_interval_seconds: float = 1.25
    reddit_browser_impersonate: bool = True
    github_token: str = ""
    log_level: str = "INFO"
    log_json: bool = True
    max_response_bytes: int = 10_000_000
    crawl_mode: Literal["auto", "inline", "queue"] = "auto"
    cors_origins: str = "*"
    http_retries: int = 2
    job_ttl_seconds: int = 86_400

    @property
    def parsed_api_keys(self) -> set[str]:
        keys = {part.strip() for part in self.api_keys.split(",") if part.strip()}
        if self.api_key.strip():
            keys.add(self.api_key.strip())
        return keys

    @property
    def reddit_configured(self) -> bool:
        return bool(self.reddit_client_id.strip() and self.reddit_client_secret.strip())

    def resolved_crawl_mode(self) -> str:
        if self.crawl_mode == "auto":
            return "queue" if self.redis_url else "inline"
        return self.crawl_mode

    @property
    def proxy_list(self) -> list[str]:
        found: list[str] = []
        for part in (self.proxy_url, *self.proxy_urls.split(","), *self.proxies.split(",")):
            value = part.strip()
            if value and value not in found:
                found.append(value)
        return found

    def tier_order(self) -> list[str]:
        default = ["http", "curl", "patchright", "camoufox", "flaresolverr", "seleniumbase"]
        if not self.fetch_tiers.strip():
            return default
        chosen = [part.strip().lower() for part in self.fetch_tiers.split(",") if part.strip()]
        return chosen or default


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
