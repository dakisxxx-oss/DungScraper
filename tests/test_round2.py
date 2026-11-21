import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dungscraper.api.app import create_app
from dungscraper.config import Settings
from dungscraper.core.clip import clip_text
from dungscraper.core.content import is_challenge, merge_structures, should_escalate
from dungscraper.core.extract import extract_structured
from dungscraper.core.fetch import ProxyRotator
from dungscraper.core.search import Hit, provider_chain, search_web
from dungscraper.errors import DungScraperError
from dungscraper.install_claude import claude_config_path, install_claude_config, launch_command
from dungscraper.models import ScrapeData, SearchRequest


def test_challenge_and_js_escalation():
    assert is_challenge("<html><title>Just a moment...</title><body>Checking</body></html>", 403)
    assert should_escalate("<html><title>Just a moment...</title><body>Checking</body></html>", 403) == "challenge"
    shell = (
        "<html><body><h1>Quotes</h1><a href='/login'>Login</a>"
        "<script>var data = " + ("{quote:1};" * 80) + "</script></body></html>"
    )
    assert should_escalate(shell, 200) == "js_shell"
    paragraph = "<html><body><p>" + ("This is a real sentence about documentation. " * 5) + "</p></body></html>"
    assert should_escalate(paragraph, 200) is None
    assert is_challenge("<html><body>HTTP 429</body></html>", 429)


def test_merge_keeps_code_and_table_the_winner_dropped():
    primary = "# Article\n\nThe body of the piece stays here and is long enough."
    other = "# Article\n\n```python\nprint('kept')\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    merged = merge_structures(primary, [other])
    assert "print('kept')" in merged
    assert "| 1 | 2 |" in merged
    assert "The body of the piece" in merged


def test_proxy_rotation_cycles():
    rotator = ProxyRotator(["http://a:1", "http://b:1"])
    assert [rotator.next() for _ in range(3)] == ["http://a:1", "http://b:1", "http://a:1"]
    assert ProxyRotator([]).next() is None


def test_provider_chain_puts_duckduckgo_last_and_falls_back():
    settings = Settings(brave_api_key="", serper_api_key="", searxng_url="", log_json=False)
    assert provider_chain("auto", settings) == ["duckduckgo"]
    settings.brave_api_key = "token"
    assert provider_chain("auto", settings) == ["brave", "duckduckgo"]
    assert provider_chain("duckduckgo", settings)[0] == "duckduckgo"
    assert "brave" in provider_chain("duckduckgo", settings)
    assert provider_chain("duckduckgo", settings, fallback=False) == ["duckduckgo"]


@pytest.mark.asyncio
async def test_search_falls_back_when_duckduckgo_fails(monkeypatch):
    from dungscraper.core import search as search_mod

    settings = Settings(brave_api_key="token", log_json=False, log_level="ERROR")

    async def blocked(*_args, **_kwargs):
        raise DungScraperError("search_failed", "DuckDuckGo rate limited this server.", status=502)

    async def brave(*_args, **_kwargs):
        return [Hit(url="https://example.com/guide", title="Guide", snippet="A guide")]

    monkeypatch.setattr(search_mod, "search_duckduckgo", blocked)
    monkeypatch.setattr(search_mod, "search_brave", brave)
    data = await search_web(
        SearchRequest(query="crawler", provider="duckduckgo", scrape=False, limit=1),
        settings=settings,
        client=None,  # type: ignore[arg-type]
    )
    assert data.provider == "brave"
    assert data.results[0].url == "https://example.com/guide"
    assert any("rate limited" in note for note in data.notes)


@pytest.mark.asyncio
async def test_extract_without_llm_is_explicit():
    settings = Settings(llm_base_url="https://api.openai.com/v1", llm_api_key="", log_json=False)
    from dungscraper.models import ExtractRequest

    with pytest.raises(DungScraperError) as caught:
        await extract_structured(
            ExtractRequest(content="Ada Lovelace wrote the notes.", prompt="Who?"),
            settings=settings,
        )
    assert caught.value.code == "llm_not_configured"
    assert "DUNGSCRAPER_LLM_API_KEY" in caught.value.message
    assert "DUNGSCRAPER_LLM_BASE_URL" in caught.value.message


@pytest.mark.asyncio
async def test_extract_schema_over_several_urls(monkeypatch):
    from dungscraper.core import extract as extract_mod
    from dungscraper.models import ExtractRequest

    async def fake_scrape(request, **_kwargs):
        return ScrapeData(url=request.url, markdown=f"Title for {request.url}", render_mode="http", status_code=200)

    async def fake_llm(messages, **_kwargs):
        blob = messages[-1]["content"]
        return json.dumps({"title": "ok", "seen": "example.com/a" in blob or "example.com/b" in blob})

    monkeypatch.setattr(extract_mod, "scrape_url", fake_scrape)
    monkeypatch.setattr(extract_mod, "llm_complete", fake_llm)
    settings = Settings(llm_base_url="http://llm.test/v1", llm_api_key="", log_json=False)
    data = await extract_structured(
        ExtractRequest(
            urls=["https://example.com/a", "https://example.com/b"],
            prompt="Extract the title.",
            schema={"type": "object", "required": ["title"]},
        ),
        settings=settings,
    )
    assert len(data.results) == 2
    assert [item.error for item in data.results] == [None, None]
    assert all(item.data["title"] == "ok" for item in data.results)


def test_clip_paginates():
    text, info = clip_text("abcdef", max_chars=3, offset=0)
    assert text.startswith("abc")
    assert info["next_offset"] == 3
    assert info["truncated"] is True
    rest, info = clip_text("abcdef", max_chars=3, offset=3)
    assert rest == "def"
    assert info["truncated"] is False


def test_clipped_markdown_keeps_a_heading_outline():
    from dungscraper.core.clip import clip_payload

    original = "# Title\n\npara\n\n## Later\n\nmore"
    payload = {"markdown": original}
    clip_payload(payload, max_chars=5, offset=0)
    assert payload["outline"][0] == {"level": 1, "heading": "Title", "offset": 0}
    assert payload["outline"][1]["heading"] == "Later"
    assert payload["outline"][1]["offset"] == original.index("## Later")
    assert payload["truncation"]["truncated"] is True


def test_claude_config_paths_and_backup(tmp_path: Path):
    assert claude_config_path(platform="darwin", home=tmp_path).name == "claude_desktop_config.json"
    assert "Application Support" in str(claude_config_path(platform="darwin", home=tmp_path))
    classic = claude_config_path(
        platform="win32",
        appdata=str(tmp_path / "Roaming"),
        localappdata=str(tmp_path / "Local"),
    )
    assert classic == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    package = tmp_path / "Local" / "Packages" / "Claude_store" / "LocalCache" / "Roaming" / "Claude"
    package.mkdir(parents=True)
    (package / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    other = tmp_path / "Local" / "Packages" / "Claude_other"
    other.mkdir()
    msix = claude_config_path(
        platform="win32",
        appdata=str(tmp_path / "Roaming"),
        localappdata=str(tmp_path / "Local"),
    )
    assert msix == package / "claude_desktop_config.json"
    assert claude_config_path(platform="linux", home=tmp_path).parts[-3:] == (".config", "Claude", "claude_desktop_config.json")

    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")
    backup = install_claude_config(path, command=["/usr/bin/dungscraper", "mcp"])
    assert backup is not None and backup.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["other"]["command"] == "other"
    assert saved["mcpServers"]["dungscraper"]["args"] == ["mcp"]
    assert saved["mcpServers"]["dungscraper"]["command"] == "/usr/bin/dungscraper"
    command = launch_command("python")
    assert command[-3:] == ["-m", "dungscraper", "mcp"]


def test_batch_endpoint_reports_each_url(monkeypatch):
    async def fake_scrape(request, **_kwargs):
        if request.url.endswith("/missing"):
            raise DungScraperError("fetch_failed", "nope", status=502)
        return ScrapeData(url=request.url, markdown="# Hi", render_mode="http", status_code=200)

    monkeypatch.setattr("dungscraper.api.routes.scrape_url", fake_scrape)
    settings = Settings(api_keys="", redis_url="", log_json=False, log_level="ERROR", allow_private_networks=True)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com/a", "https://example.com/missing"], "concurrency": 2},
        )
    assert response.status_code == 200, response.text
    results = response.json()["data"]["results"]
    assert results[0]["data"]["markdown"] == "# Hi"
    assert "fetch_failed" in results[1]["error"]
