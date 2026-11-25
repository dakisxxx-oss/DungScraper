"""Wait-then-reload interstitials and system Chrome/Edge fallback (0.3.2)."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dungscraper.config import Settings
from dungscraper.core import fetch as fetch_mod
from dungscraper.core.fetch import (
    BrowserManager,
    _self_reloads,
    _visible_words,
    browser_available,
    is_interstitial,
    launch_chromium,
)
from dungscraper.core.scrape import scrape_url
from dungscraper.models import ScrapeRequest

HEX_SHELL = (
    "<html><head><title>Preparing Answers</title><style>body{color:red}</style></head><body>"
    "<main><h1>Preparing <span id='w'>Answers</span></h1><p>Refresh in <b id='t'>5</b></p></main>"
    "<script>eval(decodeURIComponent(escape('\\x28\\x66\\x75')))</script></body></html>"
)

REAL_PAGE = (
    "<html><head><title>Classes</title></head><body><main><h1>Races and Classes</h1>"
    + "".join(f"<p><a href='/class/{i}'>Class number {i} with some words</a></p>" for i in range(30))
    + "</main></body></html>"
)

GATE_SHELL = (
    "<html><head><title>Checking</title></head><body><p>Preparing, please wait</p>"
    "<script>setTimeout(function(){document.cookie='gate=ok; path=/';location.reload();},1200);</script>"
    "</body></html>"
)


class _Gate(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        body = REAL_PAGE if "gate=ok" in (self.headers.get("Cookie") or "") else GATE_SHELL
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def gate_server():
    server = HTTPServer(("127.0.0.1", 0), _Gate)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_hex_shell_is_interstitial_and_reloads():
    assert is_interstitial(HEX_SHELL)
    assert _self_reloads(HEX_SHELL)
    assert _visible_words(HEX_SHELL) < 10


def test_real_page_is_not_interstitial():
    assert not is_interstitial(REAL_PAGE)
    assert not _self_reloads(REAL_PAGE)


def test_meta_refresh_counts_as_reloading():
    assert _self_reloads('<meta http-equiv="refresh" content="3">')


class _FakeChromium:
    def __init__(self, fail_for: set[str | None]):
        self.fail_for = fail_for
        self.calls: list[dict] = []

    async def launch(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("channel") in self.fail_for and "executable_path" not in kwargs:
            raise RuntimeError("BrowserType.launch: Executable doesn't exist at /x\nplaywright install")
        return object()


class _FakePlaywright:
    def __init__(self, fail_for):
        self.chromium = _FakeChromium(fail_for)


@pytest.mark.asyncio
async def test_launch_falls_back_to_system_chrome_then_edge():
    settings = Settings()
    pw = _FakePlaywright({None})
    await launch_chromium(pw, settings, args=[])
    assert [c.get("channel") for c in pw.chromium.calls] == [None, "chrome"]

    pw = _FakePlaywright({None, "chrome"})
    await launch_chromium(pw, settings, args=[])
    assert [c.get("channel") for c in pw.chromium.calls] == [None, "chrome", "msedge"]


@pytest.mark.asyncio
async def test_launch_raises_original_error_when_nothing_installed():
    pw = _FakePlaywright({None, "chrome", "msedge"})
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        await launch_chromium(pw, Settings(), args=[])


@pytest.mark.asyncio
async def test_launch_respects_pinned_channel_and_executable():
    pw = _FakePlaywright(set())
    await launch_chromium(pw, Settings(browser_channel="msedge"), args=[])
    assert pw.chromium.calls[-1]["channel"] == "msedge"
    await launch_chromium(pw, Settings(browser_executable="C:/x/chrome.exe"), args=[])
    assert pw.chromium.calls[-1]["executable_path"] == "C:/x/chrome.exe"
    assert "channel" not in pw.chromium.calls[-1]


@pytest.mark.asyncio
async def test_other_launch_errors_are_not_masked(monkeypatch):
    class Boom:
        async def launch(self, **kwargs):
            raise RuntimeError("sandbox exploded")

    class PW:
        chromium = Boom()

    with pytest.raises(RuntimeError, match="sandbox exploded"):
        await launch_chromium(PW(), Settings(), args=[])


class _FakePage:
    """Page whose DOM grows after a few polls, with one mid-navigation failure."""

    def __init__(self, shells: int):
        self.polls = 0
        self.shells = shells

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)

    async def content(self):
        self.polls += 1
        if self.polls == 2:
            raise RuntimeError("Execution context was destroyed")
        return HEX_SHELL if self.polls <= self.shells else REAL_PAGE

    async def wait_for_load_state(self, *_a, **_k):
        return None


@pytest.mark.asyncio
async def test_wait_out_interstitial_returns_real_page():
    manager = BrowserManager(Settings(interstitial_wait_seconds=5))
    html = await manager._wait_out_interstitial(_FakePage(shells=3), HEX_SHELL, 200)
    assert "Races and Classes" in html


@pytest.mark.asyncio
async def test_wait_out_interstitial_gives_up():
    manager = BrowserManager(Settings(interstitial_wait_seconds=1))
    html = await manager._wait_out_interstitial(_FakePage(shells=10_000), HEX_SHELL, 200)
    assert "Preparing" in html


@pytest.mark.asyncio
async def test_scrape_waits_through_cookie_gate(gate_server):
    if not await browser_available(Settings()):
        pytest.skip("no Chromium, Chrome or Edge available")
    settings = Settings(
        cache_enabled=False,
        allow_private_networks=True,
        log_json=False,
        log_level="WARNING",
        browser_timeout_seconds=30,
        interstitial_wait_seconds=10,
    )
    browser = BrowserManager(settings)
    try:
        data = await scrape_url(
            ScrapeRequest(url=gate_server, formats=["markdown"]),
            settings=settings,
            browser=browser,
        )
    finally:
        await browser.shutdown()
    assert data.markdown and "Races and Classes" in data.markdown
    assert data.fetch_tier == "playwright"
