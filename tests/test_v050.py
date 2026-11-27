"""0.5.0: banner, CSS/XPath extraction, focused reading, reader endpoint, Firecrawl-style names."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dungscraper import __version__
from dungscraper.api.app import create_app
from dungscraper.banner import banner_enabled, format_banner, with_banner
from dungscraper.config import Settings
from dungscraper.core.css_extract import extract_with_schema, normalize_schema
from dungscraper.core.focus import bm25_rank, focus_markdown, split_sections
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeData, ScrapeRequest
from tests.test_api import _serve  # noqa: F401  (reuses the tiny local HTTP server)

SHOP = """<html><head><title>Shop</title></head><body>
<div class="card" data-id="7"><h2> Blue  Lamp </h2><a href="/lamp">more</a><span class="price">EUR 12,50</span>
<i class="tag">home</i><i class="tag">light</i><ul><li><b>w</b>10</li><li><b>h</b>20</li></ul></div>
<div class="card" data-id="8"><h2>Red Chair</h2><span class="price">EUR 40</span></div></body></html>"""

LONG = "# Guide\n\nIntro for beginners.\n\n## Pricing\n\nThe pro plan costs 20 euros per month and includes API access.\n\n" + (
    "## Install\n\nRun pip install foo and then start the server. " + "More install detail here. " * 300 + "\n\n"
) + "## Contact\n\nWrite to us anytime."


# --- banner -------------------------------------------------------------------

def test_banner_text_and_switch(monkeypatch):
    line = format_banner("scrape", "playwright", 1234)
    assert line == f"🦴 DungScraper v{__version__} · scrape · playwright · 1234 ms"
    monkeypatch.delenv("DUNGSCRAPER_BANNER", raising=False)
    assert banner_enabled()
    out = with_banner("scrape", json.dumps({"fetch_tier": "http"}), 15)
    assert out.splitlines()[0] == f"🦴 DungScraper v{__version__} · scrape · http · 15 ms"
    assert out.splitlines()[1].startswith("{")
    for off in ("0", "false", "off", "NO", ""):
        monkeypatch.setenv("DUNGSCRAPER_BANNER", off)
        assert not banner_enabled()
        assert with_banner("scrape", "x", 1) == "x"


@pytest.mark.asyncio
async def test_every_mcp_tool_is_bannered_and_described(monkeypatch):
    from dungscraper.mcp_server import build_mcp

    server = build_mcp()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"read_focused", "extract_fields", "watch_page_tool"} <= names
    for tool in tools:
        assert "Tell the user you are using DungScraper" in (tool.description or ""), tool.name
    assert "🦴 DungScraper" in server.instructions and "using DungScraper" in server.instructions
    # Schemas survive the wrapper.
    scrape = next(t for t in tools if t.name == "scrape")
    assert "url" in scrape.input_schema["properties"] and "max_chars" in scrape.input_schema["properties"]


@pytest.mark.asyncio
async def test_tool_result_starts_with_banner(monkeypatch):
    from dungscraper.mcp_server import build_mcp

    monkeypatch.delenv("DUNGSCRAPER_BANNER", raising=False)
    server = build_mcp()
    result = await server.call_tool("chunk_text", {"text": "# T\n\nhello world"})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert text.startswith("🦴 DungScraper v")
    assert "chunk_text" in text.splitlines()[0]
    monkeypatch.setenv("DUNGSCRAPER_BANNER", "0")
    result = await server.call_tool("chunk_text", {"text": "# T\n\nhello world"})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert not text.startswith("🦴")


# --- CSS / XPath extraction ------------------------------------------------------

def test_css_items_with_all_field_types():
    schema = {
        "baseSelector": ".card",
        "fields": [
            {"name": "title", "selector": "h2"},
            {"name": "url", "selector": "a", "type": "attribute", "attribute": "href"},
            {"name": "price", "selector": ".price", "type": "regex", "pattern": r"([\d,]+)"},
            {"name": "tags", "selector": ".tag", "type": "list"},
            {"name": "id", "selector": "xpath:./@data-id"},
            {"name": "specs", "selector": "li", "type": "nested", "fields": [
                {"name": "k", "selector": "b"}, {"name": "raw", "selector": "xpath:.", "type": "text"}]},
            {"name": "missing", "selector": ".nope", "default": "n/a"},
            {"name": "loud", "selector": "h2", "transform": "uppercase"},
        ],
    }
    out = extract_with_schema(SHOP, schema, base_url="https://shop.test/")
    assert out["count"] == 2
    lamp, chair = out["items"]
    assert lamp["title"] == "Blue Lamp"
    assert lamp["url"] == "https://shop.test/lamp"
    assert lamp["price"] == "12,50"
    assert lamp["tags"] == ["home", "light"]
    assert lamp["id"] == "7"
    assert [s["k"] for s in lamp["specs"]] == ["w", "h"]
    assert lamp["missing"] == "n/a" and lamp["loud"] == "BLUE LAMP"
    assert chair["tags"] == [] and chair["url"] is None and chair["id"] == "8"


def test_css_short_form_and_page_level_xpath():
    out = extract_with_schema(SHOP, {"first": "h2", "link": "a@href", "ids": {"selector": ".card", "type": "list", "attribute": "data-id"}}, base_url="https://shop.test")
    assert out["data"]["first"] == "Blue Lamp"
    assert out["data"]["link"] == "https://shop.test/lamp"
    assert out["data"]["ids"] == ["7", "8"]
    xp = extract_with_schema(SHOP, {"fields": [{"name": "n", "selector": "xpath://div[@class='card'][2]/h2"}]})
    assert xp["data"]["n"] == "Red Chair"


def test_css_schema_errors():
    for bad in ({}, {"fields": []}, {"fields": [{"selector": "h1"}]}, {"x": 5}):
        with pytest.raises(DungScraperError):
            extract_with_schema(SHOP, bad)
    with pytest.raises(DungScraperError, match="CSS"):
        extract_with_schema(SHOP, {"x": "div[["})
    with pytest.raises(DungScraperError, match="XPath"):
        extract_with_schema(SHOP, {"x": "xpath:///[bad"})
    assert normalize_schema({"title": "h1"})["fields"][0]["name"] == "title"


# --- focused reading ---------------------------------------------------------------

def test_focus_keeps_the_answer_and_drops_the_rest():
    result = focus_markdown(LONG, "how much does the pro plan cost", max_tokens=200)
    assert "20 euros per month" in result["markdown"]
    assert "More install detail" not in result["markdown"]
    assert result["tokens"] <= 260 and result["total_tokens"] > 1000
    assert result["trimmed"] and result["matched"]
    assert result["sections"][0]["heading"].endswith("Pricing")


def test_focus_other_question_finds_other_section():
    result = focus_markdown(LONG, "install foo start the server", max_tokens=400)
    assert "pip install foo" in result["markdown"]
    assert "20 euros" not in result["markdown"]


def test_focus_without_query_and_short_pages():
    short = "# T\n\nsmall page"
    assert focus_markdown(short, "", max_tokens=500)["markdown"] == short
    fitted = focus_markdown(LONG, "", max_tokens=150)
    assert fitted["markdown"].startswith("## Pricing") or fitted["markdown"].startswith("# Guide") or "Intro" in fitted["markdown"]
    assert fitted["tokens"] <= 200 and fitted["trimmed"]


def test_split_sections_and_ranking():
    sections = split_sections(LONG)
    assert [s["heading"] for s in sections][:2] == ["Guide", "Guide > Pricing"]
    scores = bm25_rank(sections, "pro plan price")
    assert max(range(len(scores)), key=scores.__getitem__) == 1
    assert bm25_rank(sections, "") == [0.0] * len(sections)


# --- API ----------------------------------------------------------------------------

@pytest.fixture
def served():
    pages = {
        "/shop": ("text/html", SHOP),
        "/guide": ("text/html", "<html><head><title>Guide</title></head><body><article>" + LONG.replace("# Guide", "<h1>Guide</h1>").replace("## Pricing", "<h2>Pricing</h2>").replace("## Install", "<h2>Install</h2>").replace("## Contact", "<h2>Contact</h2>") + "</article></body></html>"),
        "/robots.txt": ("text/plain", "User-agent: *\nAllow: /\n"),
    }
    server, base, _posts = _serve(pages)
    yield base
    server.shutdown()


@pytest.fixture
def api_settings() -> Settings:
    return Settings(api_keys="", redis_url="", cache_enabled=False, log_json=False, log_level="WARNING",
                    allow_private_networks=True, crawl_mode="inline", robots_fail_open=False)


def test_extract_css_endpoint(api_settings, served):
    with TestClient(create_app(api_settings)) as client:
        response = client.post("/v1/extract/css", json={"url": served + "/shop", "schema": {"baseSelector": ".card", "fields": [
            {"name": "title", "selector": "h2"}, {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"}]}})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["count"] == 2 and data["items"][0]["title"] == "Blue Lamp"
        assert data["items"][0]["link"] == served + "/lamp"
        bad = client.post("/v1/extract/css", json={"url": served + "/shop", "schema": {}})
        assert bad.status_code in (400, 422)


def test_focus_endpoint_and_reader(api_settings, served):
    with TestClient(create_app(api_settings)) as client:
        focused = client.post("/v1/focus", json={"url": served + "/guide", "query": "pro plan cost", "max_tokens": 200})
        assert focused.status_code == 200, focused.text
        data = focused.json()["data"]
        assert "20 euros" in data["markdown"] and data["tokens"] < data["total_tokens"]
        plain = client.get("/r/" + served + "/guide")
        assert plain.status_code == 200
        assert plain.headers["content-type"].startswith("text/markdown")
        assert plain.headers["x-powered-by"] == f"DungScraper/{__version__}"
        assert "Pricing" in plain.text
        small = client.get("/r/" + served + "/guide", params={"query": "pro plan cost", "max_tokens": 200})
        assert "20 euros" in small.text and "More install detail" not in small.text
        # A browser or proxy may collapse https:// to https:/ in the path.
        collapsed = client.get("/r/" + served.replace("://", ":/") + "/guide")
        assert collapsed.status_code == 200 and "Pricing" in collapsed.text


def test_firecrawl_style_names(api_settings, served):
    req = ScrapeRequest.model_validate({"url": served + "/shop", "formats": ["markdown", "rawHtml"], "onlyMainContent": False})
    assert req.formats == ["markdown", "raw_html"]
    with TestClient(create_app(api_settings)) as client:
        response = client.post("/v1/scrape", json={"url": served + "/shop", "formats": ["markdown", "rawHtml"], "onlyMainContent": False})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        meta = body["data"]["metadata"]
        assert meta["sourceURL"] == served + "/shop" and meta["statusCode"] == 200
        assert "<div" in body["data"]["raw_html"]


def test_scrape_model_roundtrip():
    assert ScrapeData(url="https://x.test", render_mode="http").structured is None


def test_doctor_command(monkeypatch):
    from typer.testing import CliRunner

    from dungscraper.cli import app

    async def fake(_settings):
        return False

    monkeypatch.setattr("dungscraper.core.fetch.browser_available", fake)
    out = CliRunner().invoke(app, ["doctor"])
    assert out.exit_code == 0, out.output
    assert f"DungScraper: {__version__}" in out.output
    assert "WARN Browser" in out.output
