import pytest

from dungscraper.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_mcp_tools_are_registered():
    server = build_mcp()
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert {"scrape", "batch_scrape", "crawl", "crawl_status", "map_site", "search_web", "extract", "chunk_text", "reddit", "read_feed"} <= names
    scrape = next(tool for tool in tools if tool.name == "scrape")
    description = (scrape.description or "").lower()
    assert "markdown" in description
    assert "browser" in description
    assert "outline" in description
    assert "no api key" in description
    reddit = next(tool for tool in tools if tool.name == "reddit")
    reddit_description = (reddit.description or "").lower()
    assert "no api key" in reddit_description
    assert "not configured" not in reddit_description
    feed = next(tool for tool in tools if tool.name == "read_feed")
    assert "rss" in (feed.description or "").lower()
