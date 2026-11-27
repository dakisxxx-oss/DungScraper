"""Request and response models.

Input accepts both snake_case and the camelCase names agents already use
with Firecrawl (`onlyMainContent`, `maxDepth`, `scrapeOptions`, `waitFor`).
Responses are snake_case.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

FORMATS = {"markdown", "html", "raw_html", "links", "metadata", "screenshot", "chunks"}
RenderMode = Literal["auto", "http", "browser"]
WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]


def _http_url(value: str) -> str:
    from dungscraper.core.urls import validate_http_url
    from dungscraper.errors import DungScraperError

    try:
        return validate_http_url(value)
    except DungScraperError as exc:
        raise ValueError(exc.message) from exc


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["click", "scroll", "type", "wait", "press"]
    selector: str | None = None
    text: str | None = None
    key: str | None = None
    ms: int | None = Field(None, ge=0, le=60_000)
    pixels: int | None = Field(None, ge=0, le=20_000)


class ChunkOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_tokens: int = Field(500, ge=50, le=8000, validation_alias=AliasChoices("max_tokens", "maxTokens"))
    overlap_tokens: int = Field(
        50, ge=0, le=2000, validation_alias=AliasChoices("overlap_tokens", "overlapTokens")
    )

    @model_validator(mode="after")
    def overlap_fits(self) -> ChunkOptions:
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        return self


class ScrapeOptions(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    formats: list[str] = Field(default_factory=lambda: ["markdown", "metadata"])
    render: RenderMode = "auto"
    only_main_content: bool = Field(
        True, validation_alias=AliasChoices("only_main_content", "onlyMainContent")
    )
    wait_for: str | None = Field(None, validation_alias=AliasChoices("wait_for", "wait_for_selector"))
    wait_ms: int | None = Field(None, ge=0, le=60_000, validation_alias=AliasChoices("wait_ms", "waitFor"))
    wait_until: WaitUntil = Field(
        "domcontentloaded", validation_alias=AliasChoices("wait_until", "waitUntil")
    )
    actions: list[BrowserAction] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    proxy: str | None = None
    timeout_ms: int | None = Field(
        None, ge=1000, le=180_000, validation_alias=AliasChoices("timeout_ms", "timeout")
    )
    respect_robots: bool | None = Field(
        None, validation_alias=AliasChoices("respect_robots", "respectRobots")
    )
    cache: bool = True
    screenshot_full_page: bool = Field(
        False, validation_alias=AliasChoices("screenshot_full_page", "screenshotFullPage")
    )
    include_selectors: list[str] = Field(
        default_factory=list,
        max_length=30,
        validation_alias=AliasChoices("include_selectors", "includeSelectors"),
    )
    exclude_selectors: list[str] = Field(
        default_factory=list,
        max_length=30,
        validation_alias=AliasChoices("exclude_selectors", "excludeSelectors"),
    )
    chunk: ChunkOptions | None = None
    max_comment_depth: int = Field(
        3, ge=0, le=8, validation_alias=AliasChoices("max_comment_depth", "maxCommentDepth")
    )

    @field_validator("formats", mode="before")
    @classmethod
    def firecrawl_format_names(cls, value):
        aliases = {"rawHtml": "raw_html", "screenshot@fullPage": "screenshot", "summary": "markdown"}
        if isinstance(value, list):
            return [aliases.get(item, item) if isinstance(item, str) else item for item in value]
        return value

    @field_validator("formats")
    @classmethod
    def known_formats(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in FORMATS]
        if unknown:
            allowed = ", ".join(sorted(FORMATS))
            raise ValueError(f"Unknown formats {unknown}. Allowed: {allowed}")
        return value


class ScrapeRequest(ScrapeOptions):
    url: str

    @field_validator("url")
    @classmethod
    def absolute_http_url(cls, value: str) -> str:
        return _http_url(value)


class PageMetadata(BaseModel):
    title: str | None = None
    description: str | None = None
    language: str | None = None
    canonical: str | None = None
    author: str | None = None
    published: str | None = None
    favicon: str | None = None
    keywords: list[str] = Field(default_factory=list)
    og: dict[str, str] = Field(default_factory=dict)
    # Firecrawl-compatible names, so clients that read metadata.sourceURL keep working.
    sourceURL: str | None = None
    statusCode: int | None = None


class LinkItem(BaseModel):
    url: str
    text: str = ""


class Chunk(BaseModel):
    index: int
    text: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScrapeData(BaseModel):
    url: str
    markdown: str | None = None
    html: str | None = None
    raw_html: str | None = None
    links: list[LinkItem] | None = None
    metadata: PageMetadata | None = None
    screenshot: str | None = None
    chunks: list[Chunk] | None = None
    status_code: int | None = None
    content_type: str | None = None
    render_mode: str
    timings_ms: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    block_reason: str | None = None
    fetch_tier: str | None = None
    structured: dict[str, Any] | None = None
    crawl_score: float | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ScrapeResponse(BaseModel):
    success: bool = True
    data: ScrapeData | None = None
    error: ErrorBody | None = None


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    limit: int = Field(25, ge=1, le=1000)
    max_depth: int = Field(2, ge=0, le=10, validation_alias=AliasChoices("max_depth", "maxDepth"))
    include_paths: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("include_paths", "includePaths", "include_patterns", "includePatterns"),
    )
    exclude_paths: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("exclude_paths", "excludePaths", "exclude_patterns", "excludePatterns"),
    )
    strategy: Literal["bfs", "dfs", "best_first"] = "bfs"
    keywords: list[str] = Field(default_factory=list)
    scorers: list[Literal["keyword", "depth", "freshness", "authority"]] = Field(
        default_factory=lambda: ["keyword", "depth", "freshness", "authority"]
    )
    scorer_weights: dict[str, float] = Field(
        default_factory=dict, validation_alias=AliasChoices("scorer_weights", "scorerWeights")
    )
    min_score: float = Field(0.0, ge=0.0, le=1.0, validation_alias=AliasChoices("min_score", "minScore"))
    content_types: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("content_types", "contentTypes")
    )
    same_domain: bool = Field(True, validation_alias=AliasChoices("same_domain", "sameDomain"))
    allow_subdomains: bool = Field(
        False, validation_alias=AliasChoices("allow_subdomains", "allowSubdomains")
    )
    respect_robots: bool | None = Field(
        None, validation_alias=AliasChoices("respect_robots", "respectRobots")
    )
    use_sitemap: bool = Field(True, validation_alias=AliasChoices("use_sitemap", "useSitemap"))
    concurrency: int = Field(4, ge=1, le=20)
    delay_ms: int = Field(250, ge=0, le=60_000, validation_alias=AliasChoices("delay_ms", "delay"))
    dedupe: bool = True
    webhook: str | None = None
    wait: bool = False
    timeout_seconds: float = Field(
        120, ge=1, le=3600, validation_alias=AliasChoices("timeout_seconds", "timeoutSeconds")
    )
    scrape_options: ScrapeOptions = Field(
        default_factory=ScrapeOptions, validation_alias=AliasChoices("scrape_options", "scrapeOptions")
    )

    @field_validator("keywords", "content_types", "scorers", mode="before")
    @classmethod
    def split_text(cls, value):
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("url", "webhook")
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _http_url(value)


class CrawlJobData(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    url: str
    limit: int
    completed: int = 0
    failed: int = 0
    skipped_duplicates: int = 0
    skipped_robots: int = 0
    skipped_content_type: int = 0
    strategy: str = "bfs"
    pages: list[ScrapeData] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str
    error: ErrorBody | None = None


class CrawlResponse(BaseModel):
    success: bool = True
    data: CrawlJobData | None = None
    error: ErrorBody | None = None


class MapRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    search: str | None = None
    limit: int = Field(200, ge=1, le=5000)
    include_paths: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("include_paths", "includePaths")
    )
    exclude_paths: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("exclude_paths", "excludePaths")
    )
    allow_subdomains: bool = Field(
        False, validation_alias=AliasChoices("allow_subdomains", "allowSubdomains")
    )
    use_sitemap: bool = Field(True, validation_alias=AliasChoices("use_sitemap", "useSitemap"))
    discover_links: bool = Field(True, validation_alias=AliasChoices("discover_links", "discoverLinks"))
    respect_robots: bool | None = Field(
        None, validation_alias=AliasChoices("respect_robots", "respectRobots")
    )
    render: Literal["auto", "http", "browser"] = "auto"

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _http_url(value)


class MapLink(BaseModel):
    url: str
    title: str | None = None
    source: Literal["sitemap", "link", "browser"]


class MapData(BaseModel):
    url: str
    links: list[MapLink]
    sitemap_count: int = 0
    link_count: int = 0
    rendered_with: str = "http"


class MapResponse(BaseModel):
    success: bool = True
    data: MapData | None = None
    error: ErrorBody | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(5, ge=1, le=10)
    provider: Literal["auto", "duckduckgo", "searxng", "brave", "serper"] = "auto"
    scrape: bool = True
    fallback: bool = True
    scrape_options: ScrapeOptions = Field(
        default_factory=lambda: ScrapeOptions(formats=["markdown", "metadata"]),
        validation_alias=AliasChoices("scrape_options", "scrapeOptions"),
    )


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    question: str = Field(min_length=3, max_length=500)
    urls: list[str] = Field(default_factory=list, max_length=20)
    search: bool = True
    provider: Literal["auto", "duckduckgo", "searxng", "brave", "serper"] = "auto"
    search_limit: int = Field(6, ge=1, le=10, validation_alias=AliasChoices("search_limit", "searchLimit"))
    max_pages: int = Field(8, ge=1, le=40, validation_alias=AliasChoices("max_pages", "maxPages"))
    max_seconds: float = Field(60, ge=3, le=600, validation_alias=AliasChoices("max_seconds", "maxSeconds"))
    follow_links: bool = Field(True, validation_alias=AliasChoices("follow_links", "followLinks"))
    max_depth: int = Field(1, ge=0, le=3, validation_alias=AliasChoices("max_depth", "maxDepth"))
    max_passages: int = Field(10, ge=1, le=40, validation_alias=AliasChoices("max_passages", "maxPassages"))
    concurrency: int = Field(3, ge=1, le=8)
    synthesize: bool = False

    @field_validator("urls")
    @classmethod
    def check_urls(cls, value: list[str]) -> list[str]:
        return [_http_url(item) for item in value]


class SearchResultItem(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    markdown: str | None = None
    metadata: PageMetadata | None = None
    error: str | None = None


class SearchData(BaseModel):
    query: str
    provider: str
    results: list[SearchResultItem]
    notes: list[str] = Field(default_factory=list)


class BatchScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    urls: list[str] = Field(min_length=1, max_length=200)
    scrape_options: ScrapeOptions = Field(
        default_factory=lambda: ScrapeOptions(formats=["markdown", "metadata"]),
        validation_alias=AliasChoices("scrape_options", "scrapeOptions"),
    )
    concurrency: int = Field(4, ge=1, le=32)
    per_host_concurrency: int = Field(
        2, ge=1, le=16, validation_alias=AliasChoices("per_host_concurrency", "perHostConcurrency")
    )
    delay_ms: int = Field(0, ge=0, le=60_000, validation_alias=AliasChoices("delay_ms", "delay"))

    @field_validator("urls")
    @classmethod
    def check_urls(cls, value: list[str]) -> list[str]:
        return [_http_url(item) for item in value]


class BatchItem(BaseModel):
    url: str
    data: ScrapeData | None = None
    error: str | None = None


class BatchScrapeData(BaseModel):
    results: list[BatchItem]


class BatchScrapeResponse(BaseModel):
    success: bool = True
    data: BatchScrapeData | None = None
    error: ErrorBody | None = None


class SearchResponse(BaseModel):
    success: bool = True
    data: SearchData | None = None
    error: ErrorBody | None = None


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str | None = None
    urls: list[str] = Field(default_factory=list)
    content: str | None = None
    prompt: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    mode: Literal["auto", "llm", "selector"] = "auto"
    selectors: dict[str, str] = Field(default_factory=dict)
    model: str | None = None
    max_chars: int = Field(24_000, ge=500, le=200_000, validation_alias=AliasChoices("max_chars", "maxChars"))
    scrape_options: ScrapeOptions = Field(
        default_factory=lambda: ScrapeOptions(formats=["markdown", "metadata"]),
        validation_alias=AliasChoices("scrape_options", "scrapeOptions"),
    )

    @field_validator("url")
    @classmethod
    def check_single_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _http_url(value)

    @field_validator("urls")
    @classmethod
    def check_urls(cls, value: list[str]) -> list[str]:
        return [_http_url(item) for item in value]

    @model_validator(mode="after")
    def has_source_and_instruction(self) -> ExtractRequest:
        urls = list(self.urls)
        if self.url and self.url not in urls:
            urls.append(self.url)
        self.urls = urls
        if not urls and not (self.content and self.content.strip()):
            raise ValueError("Provide url, urls, or content")
        has_instruction = bool(self.prompt and self.prompt.strip()) or bool(self.schema_)
        if self.mode == "selector":
            return self
        if not has_instruction and not self.selectors:
            raise ValueError("Provide a prompt, a JSON schema, or selectors")
        return self


class ExtractItem(BaseModel):
    url: str | None = None
    data: Any = None
    error: str | None = None


class ExtractData(BaseModel):
    results: list[ExtractItem]
    model: str


class ExtractResponse(BaseModel):
    success: bool = True
    data: ExtractData | None = None
    error: ErrorBody | None = None


class ChunkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)
    max_tokens: int = Field(500, ge=50, le=8000)
    overlap_tokens: int = Field(50, ge=0, le=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def overlap_fits(self) -> ChunkRequest:
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        return self


class ChunkData(BaseModel):
    chunks: list[Chunk]
    total_tokens: int


class ChunkResponse(BaseModel):
    success: bool = True
    data: ChunkData | None = None
    error: ErrorBody | None = None


class DiffRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    baseline: str | None = None
    current: str | None = None

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _http_url(value)

    @model_validator(mode="after")
    def has_text(self) -> DiffRequest:
        if not self.url and self.current is None:
            raise ValueError("Provide url or current markdown")
        return self


class DiffData(BaseModel):
    url: str | None = None
    changed: bool
    diff: str
    baseline_chars: int
    current_chars: int


class DiffResponse(BaseModel):
    success: bool = True
    data: DiffData | None = None
    error: ErrorBody | None = None


class ContactsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    depth: int = Field(1, ge=0, le=3)
    limit: int = Field(8, ge=1, le=30)
    render: RenderMode = "auto"
    respect_robots: bool | None = Field(
        None, validation_alias=AliasChoices("respect_robots", "respectRobots")
    )
    delay_ms: int = Field(250, ge=0, le=60_000, validation_alias=AliasChoices("delay_ms", "delay"))
    timeout_ms: int | None = Field(
        None, ge=1000, le=180_000, validation_alias=AliasChoices("timeout_ms", "timeout")
    )
    check_mx: bool = Field(
        False,
        validation_alias=AliasChoices("check_mx", "checkMx"),
        description="Look up whether each email domain has a mail server (adds a DNS lookup per domain).",
    )

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _http_url(value)


class EmailLocation(BaseModel):
    url: str
    source: Literal["mailto", "text", "obfuscated", "jsonld", "cloudflare"]
    context: str = ""


class ContactEmail(BaseModel):
    email: str
    kind: str = "other"
    same_domain: bool = False
    mx: bool | None = None
    found_at: list[EmailLocation] = Field(default_factory=list)


class CareerLink(BaseModel):
    url: str
    text: str = ""
    found_at: str = ""


class ContactPage(BaseModel):
    url: str
    status_code: int | None = None
    emails: int = 0
    career_links: int = 0


class ContactsData(BaseModel):
    url: str
    domain: str
    emails: list[ContactEmail] = Field(default_factory=list)
    career_links: list[CareerLink] = Field(default_factory=list)
    pages: list[ContactPage] = Field(default_factory=list)
    pages_scanned: int = 0
    skipped_robots: int = 0
    ignored_addresses: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ContactsResponse(BaseModel):
    success: bool = True
    data: ContactsData | None = None
    error: ErrorBody | None = None


class JobsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    limit: int = Field(100, ge=1, le=500)
    render: RenderMode = "auto"
    respect_robots: bool | None = Field(
        None, validation_alias=AliasChoices("respect_robots", "respectRobots")
    )
    timeout_ms: int | None = Field(
        None, ge=1000, le=180_000, validation_alias=AliasChoices("timeout_ms", "timeout")
    )

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _http_url(value)


class JobLink(BaseModel):
    url: str
    title: str = ""
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    apply_email: str | None = None


class JobsData(BaseModel):
    url: str
    jobs: list[JobLink] = Field(default_factory=list)
    total_links: int = 0
    status_code: int | None = None
    render_mode: str = "http"
    notes: list[str] = Field(default_factory=list)


class JobsResponse(BaseModel):
    success: bool = True
    data: JobsData | None = None
    error: ErrorBody | None = None


class CssExtractRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    schema_: dict[str, Any] = Field(alias="schema")
    render: RenderMode = "auto"
    wait_for: str | None = Field(None, validation_alias=AliasChoices("wait_for", "waitFor"))
    cache: bool = True

    @field_validator("url")
    @classmethod
    def absolute_url(cls, value: str) -> str:
        return _http_url(value)


class FocusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    query: str = ""
    max_tokens: int = Field(1500, ge=100, le=20_000, validation_alias=AliasChoices("max_tokens", "maxTokens"))
    render: RenderMode = "auto"
    only_main_content: bool = Field(
        True, validation_alias=AliasChoices("only_main_content", "onlyMainContent")
    )

    @field_validator("url")
    @classmethod
    def absolute_url(cls, value: str) -> str:
        return _http_url(value)
