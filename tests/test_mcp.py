import pytest

from dungscraper.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_mcp_tools_are_registered():
    server = build_mcp()
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert {"scrape", "batch_scrape", "crawl", "crawl_status", "map_site", "search_web", "extract", "chunk_text"} <= names
    scrape = next(tool for tool in tools if tool.name == "scrape")
    description = (scrape.description or "").lower()
    assert "markdown" in description
    assert "browser" in description
