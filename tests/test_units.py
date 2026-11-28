import json
import socket
from pathlib import Path

import httpx
import pytest

from dungscraper.core.cache import MemoryCache
from dungscraper.core.chunk import chunk_markdown, estimate_tokens
from dungscraper.core.content import html_to_content, looks_js_heavy
from dungscraper.core.dedupe import is_near_duplicate
from dungscraper.core.documents import docx_to_markdown, pdf_to_markdown
from dungscraper.core.extract import parse_json_loose
from dungscraper.core.robots import RobotsGate, sitemaps_in_robots
from dungscraper.core.search import parse_duckduckgo_html, resolve_provider, search_brave
from dungscraper.core.sitemap import parse_sitemap
from dungscraper.core.urls import dedupe_key, path_matches, same_site
from dungscraper.errors import DungScraperError
from dungscraper.security import assert_public_url

FIXTURES = Path(__file__).parent / "fixtures"


def test_path_and_site_rules():
    assert path_matches("/blog/post", "/blog/**")
    assert path_matches("/blog/post", "/blog")
    assert not path_matches("/news", "/blog")
    assert path_matches("/docs/page", "re:^/docs")
    assert same_site("https://www.example.com/a", "https://example.com/", allow_subdomains=False)
    assert same_site("https://docs.example.com/a", "https://example.com/", allow_subdomains=True)
    assert not same_site("https://docs.example.com/a", "https://example.com/", allow_subdomains=False)
    assert dedupe_key("https://www.Example.com/a/?utm_source=x&b=1") == dedupe_key("https://example.com/a?b=1")


def test_article_markdown_drops_boilerplate():
    html = (FIXTURES / "article.html").read_text()
    extracted = html_to_content(html, "https://example.com/guide")
    markdown = extracted.markdown
    assert "How DungScraper Reads a Page" in markdown
    assert "Main paragraph about extraction" in markdown
    assert "hello-dungscraper" in markdown
    assert "alpha" in markdown and "beta" in markdown
    assert "https://example.com/docs" in markdown
    assert "pic.png" in markdown
    lowered = markdown.lower()
    assert "accept all cookies" not in lowered
    assert "buy now" not in lowered
    assert "subscribe to our newsletter" not in lowered
    assert extracted.metadata.title == "OG Title"
    assert extracted.metadata.description == "OG Desc"
    assert extracted.metadata.language == "en"
    assert extracted.metadata.canonical == "https://example.com/guide"
    assert extracted.metadata.og["image"] == "https://example.com/og.png"
    assert any(link.url == "https://example.com/docs" for link in extracted.links)


def test_js_heavy_detector():
    thin = "<html><body><div id='app'>Loading</div><script>start()</script></body></html>"
    assert looks_js_heavy(thin)
    example = (
        "<html><body><p>This domain is for use in documentation examples without needing permission.</p>"
        "<script src='/s.js'></script></body></html>"
    )
    assert not looks_js_heavy(example)
    shell = (
        "<html><body><h1>Quotes</h1><a href='/login'>Login</a>"
        "<script>var data = " + ("{quote:1};" * 80) + "</script></body></html>"
    )
    assert looks_js_heavy(shell)
    fat = "<html><body><p>" + ("word " * 200) + "</p><script>analytics()</script></body></html>"
    assert not looks_js_heavy(fat)


def test_chunks_cover_text_and_carry_metadata():
    paragraphs = [f"Paragraph {index} " + ("word " * 80) for index in range(8)]
    text = "# Field guide\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_markdown(text, max_tokens=200, overlap_tokens=20, metadata={"url": "https://example.com/a"})
    assert len(chunks) > 1
    assert chunks[0].metadata["url"] == "https://example.com/a"
    assert chunks[0].index == 0
    assert all(chunk.token_count == estimate_tokens(chunk.text) for chunk in chunks)
    blob = "\n".join(chunk.text for chunk in chunks)
    assert "Paragraph 0" in blob and "Paragraph 7" in blob
    assert "Field guide" in chunks[0].text


def test_near_duplicates():
    base = " ".join(f"token{i}" for i in range(80))
    changed = base.replace("token10", "token10b").replace("token11", "token11b")
    different = " ".join(f"other{i}" for i in range(80))
    seen = []
    duplicate, fp = is_near_duplicate(base, seen)
    assert not duplicate
    seen.append(fp)
    duplicate, _ = is_near_duplicate(changed, seen)
    assert duplicate
    duplicate, _ = is_near_duplicate(different, seen)
    assert not duplicate


def test_robots_and_sitemap_parse():
    text = "User-agent: *\nDisallow: /private\nSitemap: https://example.com/sitemap.xml\n"
    assert sitemaps_in_robots(text) == ["https://example.com/sitemap.xml"]
    pages, children = parse_sitemap(
        """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc></url>
          <url><loc>https://example.com/b</loc></url>
        </urlset>"""
    )
    assert pages == ["https://example.com/a", "https://example.com/b"]
    assert children == []
    pages, children = parse_sitemap(
        """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/s1.xml</loc></sitemap>
           </sitemapindex>"""
    )
    assert pages == []
    assert children == ["https://example.com/s1.xml"]


@pytest.mark.asyncio
async def test_decoding_error_retries_with_identity_encoding():
    from dungscraper.core.fetch import request_with_retries

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers.get("accept-encoding") != "identity":
            raise httpx.DecodingError(
                "Error -3 while decompressing data: incorrect header check",
                request=request,
            )
        return httpx.Response(200, text="plain body")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await request_with_retries(client, "GET", "https://example.com/a", retries=1)
    assert response.text == "plain body"
    assert calls["n"] == 2


def test_polite_interval_honors_crawl_delay_with_a_cap():
    from dungscraper.core.limiter import polite_interval

    assert polite_interval(0.25, None) == 0.25
    assert polite_interval(2, 1) == 2
    assert polite_interval(0.25, 12) == 10


@pytest.mark.asyncio
async def test_robots_gate_reads_crawl_delay():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nCrawl-delay: 12\nDisallow: /private\n")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gate = RobotsGate(client, "DungScraperBot/0.1", respect=True, fail_open=False)
        assert await gate.crawl_delay("https://example.com/docs") == 10
        closed = RobotsGate(client, "DungScraperBot/0.1", respect=False, fail_open=False)
        assert await closed.crawl_delay("https://example.com/docs") is None


@pytest.mark.asyncio
async def test_robots_gate_disallows_private_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gate = RobotsGate(client, "DungScraperBot/0.1", respect=True, fail_open=False)
        assert await gate.allowed("https://example.com/docs")
        assert not await gate.allowed("https://example.com/private/secret")


def test_public_url_guard(monkeypatch):
    def fake_getaddrinfo(host, *_args, **_kwargs):
        address = "8.8.8.8" if host == "public.test" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert_public_url("https://public.test/page", allow_private=False)
    with pytest.raises(DungScraperError) as blocked:
        assert_public_url("http://internal.test/", allow_private=False)
    assert blocked.value.code == "blocked_url"
    with pytest.raises(DungScraperError):
        assert_public_url("http://127.0.0.1/latest", allow_private=False)
    assert_public_url("http://127.0.0.1/latest", allow_private=True)
    with pytest.raises(DungScraperError):
        assert_public_url("file:///etc/passwd", allow_private=True)


@pytest.mark.asyncio
async def test_memory_cache_expires():
    cache = MemoryCache()
    await cache.set("a", {"ok": 1}, ttl=60)
    assert await cache.get("a") == {"ok": 1}
    await cache.set("b", {"ok": 2}, ttl=0)
    assert await cache.get("b") is None


def test_duckduckgo_parser_unwraps_redirects():
    hits = parse_duckduckgo_html((FIXTURES / "ddg.html").read_text())
    assert [hit.url for hit in hits] == ["https://example.com/guide", "https://en.wikipedia.org/wiki/Web_scraping"]
    assert hits[0].title == "Example Guide"
    assert "snippet" in hits[0].snippet


def test_provider_resolution(settings):
    assert resolve_provider("auto", settings) == "duckduckgo"
    settings.brave_api_key = "token"
    assert resolve_provider("auto", settings) == "brave"
    with pytest.raises(DungScraperError) as exc:
        resolve_provider("serper", settings)
    assert exc.value.code == "search_not_configured"


@pytest.mark.asyncio
async def test_brave_adapter_reads_web_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Subscription-Token"] == "token"
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "Ada", "url": "https://example.com", "description": "bio"}]}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hits = await search_brave(client, "ada", 3, "token")
    assert hits[0].title == "Ada"
    assert hits[0].url == "https://example.com"


def test_loose_json_parser():
    assert parse_json_loose('```json\n{"name": "Ada"}\n```') == {"name": "Ada"}
    assert parse_json_loose('sure {"name": "Ada"} thanks') == {"name": "Ada"}


def test_pdf_and_docx_roundtrip():
    import pymupdf
    from docx import Document

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "DungScraper PDF sample")
    markdown, _meta = pdf_to_markdown(pdf.tobytes())
    pdf.close()
    assert "DungScraper PDF sample" in markdown

    document = Document()
    document.add_heading("DungScraper Doc", level=1)
    document.add_paragraph("A paragraph from a word processor.")
    buffer = __import__("io").BytesIO()
    document.save(buffer)
    doc_markdown, _messages = docx_to_markdown(buffer.getvalue())
    assert "DungScraper Doc" in doc_markdown
    assert "word processor" in doc_markdown


def test_extract_repairs_invalid_json(monkeypatch, settings):
    from dungscraper.core import extract as extract_mod
    from dungscraper.models import ExtractRequest

    calls = {"n": 0}

    async def fake_complete(messages, *, settings, model, client=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return json.dumps({"name": "Ada"})

    monkeypatch.setattr(extract_mod, "llm_complete", fake_complete)
    settings.llm_base_url = "http://llm.test/v1"
    request = ExtractRequest(content="Ada Lovelace wrote the notes.", prompt="name", schema={"type": "object"})

    import asyncio

    data = asyncio.run(
        extract_mod.extract_structured(request, settings=settings)
    )
    assert data.results[0].data == {"name": "Ada"}
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_redis_dequeue_returns_job_id():
    from unittest.mock import AsyncMock

    from dungscraper.core.jobs import RedisJobStore

    redis = AsyncMock()
    redis.brpop.return_value = ("dungscraper:jobs", "abc123")
    store = RedisJobStore(redis, ttl=60)
    assert await store.dequeue(5) == "abc123"
    redis.brpop.assert_awaited_once_with("dungscraper:jobs", timeout=5)


@pytest.mark.asyncio
async def test_redis_dequeue_empty_brpop_returns_none():
    from unittest.mock import AsyncMock

    from dungscraper.core.jobs import RedisJobStore

    redis = AsyncMock()
    redis.brpop.return_value = None
    store = RedisJobStore(redis, ttl=60)
    assert await store.dequeue(5) is None


@pytest.mark.asyncio
async def test_redis_dequeue_socket_timeout_returns_none():
    from unittest.mock import AsyncMock

    from dungscraper.core.jobs import RedisJobStore

    redis = AsyncMock()
    redis.brpop.side_effect = TimeoutError("Timeout reading from redis:6379")
    store = RedisJobStore(redis, ttl=60)
    assert await store.dequeue(5) is None
