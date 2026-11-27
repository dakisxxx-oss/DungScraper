"""0.6.0: proxy pool, batch limits, disk cache, deep-crawl strategies, research, self-host auth and limits."""

import asyncio
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from site_server import _serve
from typer.testing import CliRunner

from dungscraper.api.app import create_app
from dungscraper.api.usage import UsageTracker, key_id
from dungscraper.cli import app as cli_app
from dungscraper.config import Settings, reset_settings_cache
from dungscraper.core import batch as batch_module
from dungscraper.core import research as research_module
from dungscraper.core.cache import DiskCache
from dungscraper.core.crawl import content_type_allowed, crawl_site, link_allowed, pop_next
from dungscraper.core.crawl_scoring import (
    authority_score,
    depth_score,
    freshness_score,
    keyword_score,
    page_relevance,
    score_link,
)
from dungscraper.core.fetch import RawFetch, make_client
from dungscraper.core.proxies import ProxyPool, normalize_proxy, parse_proxy_list, redact
from dungscraper.core.research import research, synthesis_backend
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import CrawlRequest, ResearchRequest, ScrapeData, ScrapeOptions, ScrapeRequest
from dungscraper.services import shutdown, startup


def settings(**overrides) -> Settings:
    base = dict(
        api_keys="",
        redis_url="",
        cache_enabled=False,
        log_json=False,
        log_level="ERROR",
        allow_private_networks=True,
        respect_robots_default=False,
        robots_fail_open=True,
        crawl_mode="inline",
        http_retries=0,
    )
    base.update(overrides)
    return Settings(**base)


def article(title: str, body: str, links: str = "") -> tuple[str, str]:
    return (
        "text/html",
        f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{body}</p><p>{links}</p></article></body></html>",
    )


# --- proxy pool ---------------------------------------------------------------


def test_parse_proxies_from_lists_files_and_bare_hosts(tmp_path):
    file = tmp_path / "proxies.txt"
    file.write_text("# my proxies\nsocks5://u:p@10.0.0.3:1080\n10.0.0.4:3128  # bare\n\nnot a proxy ://\n", encoding="utf-8")
    found = parse_proxy_list("http://10.0.0.1:8080, https://10.0.0.2:8443", "http://10.0.0.1:8080", file=str(file))
    assert found == [
        "http://10.0.0.1:8080",
        "https://10.0.0.2:8443",
        "socks5://u:p@10.0.0.3:1080",
        "http://10.0.0.4:3128",
    ]
    assert normalize_proxy("ftp://x:1") is None
    assert redact("socks5://u:secret@10.0.0.3:1080") == "socks5://10.0.0.3:1080"
    s = Settings(proxy_url="http://a:1", proxy_urls="http://b:2", proxies="http://c:3\nhttp://d:4", proxy_file=str(file))
    assert s.proxy_list[:4] == ["http://a:1", "http://b:2", "http://c:3", "http://d:4"]
    assert "http://10.0.0.4:3128" in s.proxy_list


def test_proxy_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("DUNGSCRAPER_PROXIES", "http://p1:1,http://p2:2")
    assert Settings().proxy_list == ["http://p1:1", "http://p2:2"]


def test_pool_is_sticky_per_domain_and_rotates_across_domains():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    assert pool.pick("one.test") == "http://a:1"
    assert pool.pick("one.test") == "http://a:1"
    assert pool.pick("two.test") == "http://b:2"
    assert pool.pick("three.test") == "http://c:3"
    assert pool.pick("four.test") == "http://a:1"
    assert ProxyPool([]).pick("x") is None


def test_pool_backoff_doubles_and_recovers_per_domain():
    now = [1000.0]
    pool = ProxyPool(["http://a:1", "http://b:2"], base_cooldown=30, max_cooldown=100, clock=lambda: now[0])
    assert pool.pick("site.test") == "http://a:1"
    assert pool.report_failure("http://a:1", "site.test") == 30
    assert pool.pick("site.test") == "http://b:2"  # a cools down for this domain only
    assert pool.report_failure("http://a:1", "site.test") == 60
    assert pool.report_failure("http://a:1", "site.test") == 100  # capped
    now[0] += 101
    pool.report_failure("http://b:2", "site.test")
    assert pool.pick("fresh.test") in {"http://a:1", "http://b:2"}
    assert pool.pick("site.test") == "http://a:1"  # a recovered, b is cooling
    pool.report_success("http://a:1", "site.test")
    assert pool.snapshot()[0]["ok"] == 1
    assert pool.snapshot()[1]["cooling_down_for"] == ["site.test"]


def test_pool_excludes_tried_and_never_stalls_when_all_cool():
    now = [0.0]
    pool = ProxyPool(["http://a:1", "http://b:2"], base_cooldown=30, clock=lambda: now[0])
    pool.report_failure("http://a:1", "x.test")
    now[0] += 5
    pool.report_failure("http://b:2", "x.test")
    assert pool.pick("x.test") == "http://a:1"  # both cooling: the one that recovers first
    assert pool.pick("x.test", exclude=["http://a:1"]) == "http://b:2"
    assert pool.pick("x.test", exclude=["http://a:1", "http://b:2"]) is None


class ForwardProxy:
    """A tiny HTTP forward proxy. mode is 'ok', 'block' (403) or 'limit' (429)."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if outer.mode in {"block", "limit"}:
                    self.send_response(403 if outer.mode == "block" else 429)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(self.path, timeout=10) as upstream:
                    body = upstream.read()
                    self.send_response(upstream.status)
                    self.send_header("Content-Type", upstream.headers.get("Content-Type", "text/html"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def origin():
    pages = {"/page": article("Origin page", "The origin page says hello through a proxy.")}
    server, base, _ = _serve(pages)
    yield base
    server.shutdown()


async def test_http_retries_next_proxy_on_403_and_remembers_health(origin):
    blocked, good = ForwardProxy("block"), ForwardProxy("ok")
    try:
        s = settings(proxies=f"{blocked.url},{good.url}")
        data = await scrape_url(ScrapeRequest(url=origin + "/page", formats=["markdown"]), settings=s)
        assert "hello through a proxy" in data.markdown
        assert any(w.startswith("proxy_blocked_403") for w in data.warnings)
        assert any(w.startswith("proxy_rotated:http") for w in data.warnings)
        assert len(blocked.hits) == 1 and len(good.hits) == 1
        # The next request for the same host goes straight to the proxy that worked.
        again = await scrape_url(ScrapeRequest(url=origin + "/page", formats=["markdown"]), settings=s)
        assert "hello through a proxy" in again.markdown
        assert len(blocked.hits) == 1 and len(good.hits) == 2
        assert not any(w.startswith("proxy_") for w in again.warnings)
    finally:
        blocked.close()
        good.close()


async def test_http_retries_next_proxy_on_429_and_on_dead_proxy(origin):
    limited, good = ForwardProxy("limit"), ForwardProxy("ok")
    dead = f"http://127.0.0.1:{free_port()}"
    try:
        s = settings(proxies=f"{dead},{limited.url},{good.url}")
        data = await scrape_url(ScrapeRequest(url=origin + "/page", formats=["markdown"]), settings=s)
        assert "hello through a proxy" in data.markdown
        assert any(w.startswith("proxy_error:") for w in data.warnings)
        assert any(w.startswith("proxy_blocked_429") for w in data.warnings)
        assert len(limited.hits) == 1 and len(good.hits) == 1
    finally:
        limited.close()
        good.close()


async def test_all_proxies_blocked_returns_the_last_response_not_a_hang(origin):
    a, b = ForwardProxy("block"), ForwardProxy("block")
    try:
        s = settings(proxies=f"{a.url},{b.url}")
        with pytest.raises(DungScraperError) as caught:
            await scrape_url(ScrapeRequest(url=origin + "/page", formats=["markdown"]), settings=s)
        assert caught.value.code == "http_error"  # 403 with an empty body, after trying both
        assert len(a.hits) == 1 and len(b.hits) == 1
    finally:
        a.close()
        b.close()


async def test_explicit_request_proxy_bypasses_the_pool(origin):
    pooled, explicit = ForwardProxy("block"), ForwardProxy("ok")
    try:
        data = await scrape_url(
            ScrapeRequest(url=origin + "/page", formats=["markdown"], proxy=explicit.url),
            settings=settings(proxies=pooled.url),
        )
        assert "hello through a proxy" in data.markdown
        assert pooled.hits == [] and len(explicit.hits) == 1
    finally:
        pooled.close()
        explicit.close()


class FakeBrowser:
    """Stands in for Playwright: 403 for the first proxy, a real page for the second."""

    def __init__(self, bad: str):
        self.bad = bad
        self.proxies: list[str | None] = []

    async def fetch(self, url, **kwargs):
        proxy = kwargs.get("proxy")
        self.proxies.append(proxy)
        blocked = proxy == self.bad
        html = "<html><body>blocked</body></html>" if blocked else "<html><body><article><h1>Rendered</h1><p>Rendered through the second proxy.</p></article></body></html>"
        return RawFetch(
            url=url,
            status_code=403 if blocked else 200,
            headers={"content-type": "text/html"},
            content=html.encode(),
            text=html,
            content_type="text/html",
            render_mode="browser",
        )


async def test_browser_path_rotates_proxies_too():
    s = settings(proxies="http://10.255.0.1:8080,http://10.255.0.2:8080")
    browser = FakeBrowser(bad="http://10.255.0.1:8080")
    data = await scrape_url(
        ScrapeRequest(url="http://127.0.0.1:9/rotating-browser", render="browser", formats=["markdown"]),
        settings=s,
        browser=browser,
    )
    assert "Rendered through the second proxy" in data.markdown
    assert browser.proxies == ["http://10.255.0.1:8080", "http://10.255.0.2:8080"]
    assert any(w.startswith("proxy_blocked_403") for w in data.warnings)


def test_playwright_proxy_settings_split_credentials():
    from dungscraper.core.fetch import _playwright_proxy

    assert _playwright_proxy("socks5://user:pw@10.0.0.9:1080") == {
        "server": "socks5://10.0.0.9:1080",
        "username": "user",
        "password": "pw",
    }


def socks5_server(hits: list[str]):
    """A minimal no-auth SOCKS5 CONNECT server, enough to prove httpx can use socks5:// proxies."""
    import socketserver

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            conn = self.request
            conn.recv(262)
            conn.sendall(b"\x05\x00")
            head = conn.recv(4)
            if head[3] == 1:
                host = socket.inet_ntoa(conn.recv(4))
            elif head[3] == 3:
                host = conn.recv(conn.recv(1)[0]).decode()
            else:
                return
            port = int.from_bytes(conn.recv(2), "big")
            hits.append(f"{host}:{port}")
            remote = socket.create_connection((host, port), timeout=10)
            conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + (0).to_bytes(2, "big"))

            def pipe(a, b):
                try:
                    while chunk := a.recv(65536):
                        b.sendall(chunk)
                except OSError:
                    pass
                finally:
                    try:
                        b.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

            threading.Thread(target=pipe, args=(remote, conn), daemon=True).start()
            pipe(conn, remote)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def test_socks5_proxy_works_with_httpx(origin):
    hits: list[str] = []
    server = socks5_server(hits)
    try:
        proxy = f"socks5://127.0.0.1:{server.server_address[1]}"
        data = await scrape_url(
            ScrapeRequest(url=origin + "/page", formats=["markdown"]), settings=settings(proxies=proxy)
        )
        assert "hello through a proxy" in data.markdown
        assert hits and hits[0].startswith("127.0.0.1:")
    finally:
        server.shutdown()


# --- batch --------------------------------------------------------------------


async def test_batch_respects_overall_and_per_host_limits_and_spacing(monkeypatch):
    state = {"active": 0, "peak": 0, "host_active": {}, "host_peak": {}, "starts": {}}

    async def fake_scrape(request, **_kwargs):
        host = request.url.split("/")[2]
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        state["host_active"][host] = state["host_active"].get(host, 0) + 1
        state["host_peak"][host] = max(state["host_peak"].get(host, 0), state["host_active"][host])
        state["starts"].setdefault(host, []).append(time.monotonic())
        await asyncio.sleep(0.05)
        state["active"] -= 1
        state["host_active"][host] -= 1
        if request.url.endswith("/bad"):
            raise DungScraperError("fetch_failed", "nope", status=502)
        return ScrapeData(url=request.url, markdown="ok", render_mode="http")

    monkeypatch.setattr(batch_module, "scrape_url", fake_scrape)
    urls = [f"http://a.test/{i}" for i in range(6)] + [f"http://b.test/{i}" for i in range(4)] + ["http://c.test/bad"]
    results = await batch_module.batch_scrape_urls(
        urls,
        ScrapeOptions(),
        settings=settings(),
        client=None,
        concurrency=3,
        per_host_concurrency=1,
        delay_ms=60,
    )
    assert [item.url for item in results] == urls  # input order kept
    assert results[-1].error.startswith("fetch_failed")
    assert all(item.data for item in results[:-1])
    assert state["peak"] <= 3
    assert max(state["host_peak"].values()) == 1
    gaps = [b - a for host in ("a.test", "b.test") for a, b in zip(state["starts"][host], state["starts"][host][1:])]
    assert min(gaps) >= 0.03  # delay is 60 ms; Windows timers are coarse (~15 ms), so allow slack


def test_batch_endpoint_accepts_limits_and_up_to_200_urls(monkeypatch):
    async def fake_scrape(request, **_kwargs):
        return ScrapeData(url=request.url, markdown="ok", render_mode="http")

    monkeypatch.setattr(batch_module, "scrape_url", fake_scrape)
    with TestClient(create_app(settings())) as client:
        urls = [f"http://h{i % 5}.test/{i}" for i in range(120)]
        response = client.post(
            "/v1/batch/scrape", json={"urls": urls, "concurrency": 16, "perHostConcurrency": 2, "delay": 0}
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["data"]["results"]) == 120
        assert client.post("/v1/batch/scrape", json={"urls": urls, "perHostConcurrency": 99}).status_code == 422


# --- disk cache ---------------------------------------------------------------


async def test_disk_cache_persists_expires_and_prunes(tmp_path):
    cache = DiskCache(tmp_path / "cache", max_files=3)
    await cache.set("k1", {"a": 1}, ttl=60)
    assert await DiskCache(tmp_path / "cache").get("k1") == {"a": 1}  # a new instance sees it: survives restarts
    assert await cache.get("missing") is None
    await cache.set("short", {"a": 2}, ttl=1)
    await asyncio.sleep(1.1)
    assert await cache.get("short") is None
    await cache.set("zero", {"a": 3}, ttl=0)
    assert await cache.get("zero") is None
    for index in range(60):
        await cache.set(f"bulk{index}", {"i": index}, ttl=60)
    assert len(list((tmp_path / "cache").glob("*.json"))) <= 3 + 50  # pruned every 50 writes


async def test_services_use_disk_cache_and_scrape_hits_it(tmp_path, origin):
    s = settings(cache_enabled=True, cache_dir=str(tmp_path / "dc"))
    services = await startup(s)
    try:
        assert isinstance(services.cache, DiskCache)
        request = ScrapeRequest(url=origin + "/page", formats=["markdown"])
        first = await scrape_url(request, settings=s, client=services.client, cache=services.cache)
        second = await scrape_url(request, settings=s, client=services.client, cache=services.cache)
        assert "cache_hit" not in first.warnings and "cache_hit" in second.warnings
        assert list((tmp_path / "dc").glob("*.json"))
    finally:
        await shutdown(services)


# --- deep-crawl scoring -------------------------------------------------------


def test_scorers():
    assert keyword_score("https://x.test/pricing", "Plans", ["pricing"]) == pytest.approx(0.8)
    assert keyword_score("https://x.test/a", "Pricing plans", ["pricing"]) == 1.0
    assert keyword_score("https://x.test/a", "About", ["pricing"]) == 0.0
    assert keyword_score("https://x.test/a", "About", []) == 0.0
    assert depth_score(0) == 1.0 and depth_score(3) == 0.25
    assert freshness_score("https://x.test/blog/2026/11/post") > freshness_score("https://x.test/blog/2019/old")
    assert freshness_score("https://x.test/about") == 0.3
    assert authority_score("https://x.test/a", "https://x.test") > authority_score("https://blog.x.test/a", "https://x.test")
    assert authority_score("https://blog.x.test/a", "https://x.test") > authority_score("https://other.test/a", "https://x.test")
    assert authority_score("https://x.test/a/b/c/d/e", "https://x.test") < authority_score("https://x.test/a", "https://x.test")
    assert page_relevance("# Pricing\nplans and pricing", ["pricing", "refund"]) == 0.5


def test_score_link_uses_keywords_weights_and_parent_relevance():
    root = "https://x.test/"
    on_topic = score_link("https://x.test/pricing", "Pricing", 1, root, keywords=["pricing"])
    off_topic = score_link("https://x.test/about", "About us", 1, root, keywords=["pricing"])
    assert on_topic > off_topic
    # Without keywords the keyword scorer drops out and shallow same-site links win.
    shallow = score_link("https://x.test/a", "", 1, root)
    deep = score_link("https://x.test/a/b/c/d/e", "", 4, root)
    assert shallow > deep
    only_depth = score_link("https://x.test/pricing", "Pricing", 1, root, keywords=["pricing"], scorers=["depth"])
    assert only_depth == pytest.approx(0.5)
    weighted = score_link("https://x.test/pricing", "Pricing", 3, root, keywords=["pricing"], scorers=["keyword", "depth"], weights={"keyword": 0.0})
    assert weighted == pytest.approx(0.25)
    assert score_link("https://x.test/a", "", 1, root, keywords=["pricing"], parent_relevance=1.0) > score_link(
        "https://x.test/a", "", 1, root, keywords=["pricing"], parent_relevance=0.0
    )


def test_pop_next_orders_by_strategy():
    queue = [("a", 1, 0.2), ("b", 1, 0.9), ("c", 1, 0.9), ("d", 1, 0.5)]
    assert pop_next(list(queue), "bfs")[0] == "a"
    assert pop_next(list(queue), "dfs")[0] == "d"
    assert pop_next(list(queue), "best_first")[0] == "b"  # ties go to the older link


def test_link_filters_accept_paths_regex_and_full_urls():
    request = CrawlRequest(url="https://x.test/", include_patterns=["/docs/**", "https://x.test/blog/*"], exclude_patterns=["re:/draft"])
    assert link_allowed("https://x.test/docs/a", "https://x.test/", request)
    assert link_allowed("https://x.test/blog/post", "https://x.test/", request)
    assert not link_allowed("https://x.test/pricing", "https://x.test/", request)
    assert not link_allowed("https://x.test/docs/draft-1", "https://x.test/", request)
    assert content_type_allowed("text/html; charset=utf-8", ["text/html"])
    assert not content_type_allowed("application/pdf", ["text/html"])
    assert content_type_allowed(None, [])
    assert CrawlRequest(url="https://x.test/", keywords="a, b", content_types="text/html").keywords == ["a", "b"]


HUB_LINKS = (
    '<a href="/about">About us</a> <a href="/careers">Careers</a> <a href="/blog/2019/old-news">Old news</a> '
    '<a href="/docs/a/b/c/d/e">Deep docs</a> <a href="/pricing">Pricing</a> '
    '<a href="/blog/2026/11/pricing-update">Pricing update</a> <a href="/data">Data file</a>'
)


@pytest.fixture
def hub():
    pages = {"/": article("Home", "Welcome to the home page of the example company.", HUB_LINKS)}
    for path, title in [
        ("/about", "About"),
        ("/careers", "Careers"),
        ("/blog/2019/old-news", "Old news"),
        ("/docs/a/b/c/d/e", "Deep docs"),
        ("/pricing", "Pricing"),
        ("/blog/2026/11/pricing-update", "Pricing update"),
    ]:
        pages[path] = article(title, f"{title} page with some text about the company and its products.")
    pages["/data"] = ("text/plain", "plain data file " * 20)
    server, base, _ = _serve(pages)
    yield base
    server.shutdown()


async def run_crawl(base: str, **options) -> dict:
    s = settings()
    async with make_client(s) as client:
        request = CrawlRequest(url=base + "/", use_sitemap=False, concurrency=1, delay_ms=0, respect_robots=False, **options)
        return await crawl_site(request, settings=s, client=client)


def paths(result: dict, base: str) -> list[str]:
    return [page.url.removeprefix(base) or "/" for page in result["pages"]]


async def test_best_first_follows_keyword_links_before_link_order(hub):
    best = await run_crawl(hub, limit=3, max_depth=1, strategy="best_first", keywords=["pricing"])
    assert paths(best, hub)[0] == "/"
    assert set(paths(best, hub)[1:]) == {"/pricing", "/blog/2026/11/pricing-update"}
    assert all(page.crawl_score is not None for page in best["pages"])
    assert best["pages"][1].crawl_score >= best["pages"][2].crawl_score
    assert best["strategy"] == "best_first"
    bfs = await run_crawl(hub, limit=3, max_depth=1, strategy="bfs", keywords=["pricing"])
    assert paths(bfs, hub)[1:] == ["/about", "/careers"]  # page order, no scoring


async def test_best_first_prefers_fresh_shallow_pages_without_keywords(hub):
    result = await run_crawl(hub, limit=4, max_depth=1, strategy="best_first", scorers=["freshness", "depth", "authority"])
    got = paths(result, hub)
    assert "/blog/2026/11/pricing-update" in got and "/blog/2019/old-news" not in got
    assert "/docs/a/b/c/d/e" not in got


@pytest.fixture
def tree():
    pages = {
        "/": article("Root", "Root page of a small tree.", '<a href="/a">A</a> <a href="/b">B</a>'),
        "/a": article("A", "Branch A has two children.", '<a href="/a1">A1</a> <a href="/a2">A2</a>'),
        "/b": article("B", "Branch B has no children at all."),
        "/a1": article("A1", "Leaf A1 content for the tree."),
        "/a2": article("A2", "Second leaf with entirely different words about harbours."),
    }
    server, base, _ = _serve(pages)
    yield base
    server.shutdown()


async def test_dfs_goes_deep_before_wide_and_bfs_goes_wide(tree):
    dfs = await run_crawl(tree, limit=4, max_depth=2, strategy="dfs")
    bfs = await run_crawl(tree, limit=4, max_depth=2, strategy="bfs")
    assert paths(dfs, tree) == ["/", "/a", "/a1", "/a2"]
    assert paths(bfs, tree) == ["/", "/a", "/b", "/a1"]


async def test_crawl_filters_content_type_min_score_and_patterns(hub):
    html_only = await run_crawl(hub, limit=20, max_depth=1, content_types=["text/html"])
    assert "/data" not in paths(html_only, hub) and html_only["skipped_content_type"] == 1
    everything = await run_crawl(hub, limit=20, max_depth=1)
    assert "/data" in paths(everything, hub)
    strict = await run_crawl(hub, limit=20, max_depth=1, strategy="best_first", keywords=["pricing"], min_score=0.6)
    assert set(paths(strict, hub)) == {"/", "/pricing", "/blog/2026/11/pricing-update"}
    only_blog = await run_crawl(hub, limit=20, max_depth=1, include_patterns=["/blog/**"])
    assert set(paths(only_blog, hub)) == {"/", "/blog/2019/old-news", "/blog/2026/11/pricing-update"}
    no_blog = await run_crawl(hub, limit=20, max_depth=1, exclude_patterns=["/blog/**", "/data"])
    assert not any(item.startswith("/blog") for item in paths(no_blog, hub))


def test_crawl_api_accepts_strategy_and_reports_it(hub):
    with TestClient(create_app(settings())) as client:
        response = client.post(
            "/v1/crawl",
            json={
                "url": hub + "/",
                "limit": 3,
                "maxDepth": 1,
                "strategy": "best_first",
                "keywords": ["pricing"],
                "concurrency": 1,
                "delay": 0,
                "wait": True,
                "useSitemap": False,
                "respectRobots": False,
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["strategy"] == "best_first"
        urls = [page["url"].removeprefix(hub) for page in data["pages"]]
        assert set(urls[1:]) == {"/pricing", "/blog/2026/11/pricing-update"}
        assert all("crawl_score" in page for page in data["pages"])
        assert client.post("/v1/crawl", json={"url": hub + "/", "strategy": "random"}).status_code == 422


# --- research -----------------------------------------------------------------

QUESTION = "What is the boiling point of unobtainium?"


@pytest.fixture
def web():
    """A fake web plus a fake SearXNG that returns three hits. The answer page is only reachable by following a link."""
    pages: dict = {}
    server, base, _ = _serve(pages)
    pages["/a"] = article(
        "Unobtainium overview",
        "Unobtainium is a fictional mineral used in stories about mining. Scientists studying unobtainium report many properties.",
        '<a href="/data/boiling">Unobtainium boiling point data</a> <a href="/contact">Contact</a>',
    )
    pages["/b"] = article("Pasta recipes", "Boil the pasta in salted water for ten minutes. Add sauce and serve warm.")
    pages["/c"] = article("Metals news", "A news roundup about metals markets. Unobtainium was mentioned once in passing.")
    pages["/data/boiling"] = article(
        "Unobtainium boiling point data",
        "The boiling point of unobtainium is 4,812 kelvin at 1 atm, measured by the Orbital Materials Lab.",
    )
    pages["/contact"] = article("Contact", "Write to the editors about anything at all.")
    results = [
        {"url": base + "/a", "title": "Unobtainium overview", "content": "A fictional mineral"},
        {"url": base + "/b", "title": "Pasta recipes", "content": "Boil pasta"},
        {"url": base + "/c", "title": "Metals news", "content": "Metals markets"},
    ]
    pages["/search"] = ("application/json", json.dumps({"results": results}))
    yield base
    server.shutdown()


def research_settings(base: str, **overrides) -> Settings:
    return settings(searxng_url=base, **overrides)


async def run_research(base: str, request: ResearchRequest, **overrides) -> dict:
    s = research_settings(base, **overrides)
    async with make_client(s) as client:
        return await research(request, settings=s, client=client)


async def test_research_follows_links_to_find_an_answer_search_did_not_return(web):
    result = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", max_pages=5))
    top = result["passages"][0]
    assert "4,812 kelvin" in top["text"]
    assert top["url"].endswith("/data/boiling")
    source = next(item for item in result["sources"] if item["url"].endswith("/data/boiling"))
    assert source["found_via"].startswith("link:") and source["depth"] == 1
    assert result["stats"]["followed_links"] >= 1 and result["stats"]["provider"] == "searxng"
    assert f"[{top['source']}]" in result["brief"] and "4,812 kelvin" in result["brief"]
    assert "## Sources" in result["brief"] and "/data/boiling" in result["brief"]
    assert "answer" not in result
    ranks = [p["rank"] for p in result["passages"]]
    assert ranks == sorted(ranks) and [p["score"] for p in result["passages"]] == sorted(
        (p["score"] for p in result["passages"]), reverse=True
    )


async def test_research_without_following_misses_the_answer_and_respects_budgets(web):
    request = ResearchRequest(question=QUESTION, provider="searxng", max_pages=5, follow_links=False)
    result = await run_research(web, request)
    assert not any("4,812" in p["text"] for p in result["passages"])
    assert result["stats"]["pages_fetched"] == 3 and result["stats"]["stopped_by"] == "exhausted"
    capped = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", max_pages=1))
    assert capped["stats"]["pages_fetched"] == 1 and capped["stats"]["stopped_by"] == "page_budget"
    depth0 = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", max_depth=0))
    assert not any("4,812" in p["text"] for p in depth0["passages"])


async def test_research_seed_urls_work_without_search_and_failures_are_noted(web):
    request = ResearchRequest(
        question=QUESTION, search=False, urls=[web + "/a", web + "/missing"], max_pages=5, follow_links=True
    )
    result = await run_research(web, request)
    assert any("4,812 kelvin" in p["text"] for p in result["passages"])
    assert result["stats"]["pages_failed"] == 1 and any("/missing" in note for note in result["notes"])
    with pytest.raises(DungScraperError):
        await run_research(web, ResearchRequest(question=QUESTION, search=False))


async def test_research_time_budget_stops_the_loop(web, monkeypatch):
    ticks = iter(range(0, 10_000, 2))
    monkeypatch.setattr(research_module.time, "monotonic", lambda: float(next(ticks)))
    result = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", max_seconds=3, max_pages=8))
    assert result["stats"]["stopped_by"] == "time_budget"


async def test_research_no_matching_passages_says_so(web):
    result = await run_research(web, ResearchRequest(question="zebra migration patterns in antarctica", provider="searxng", max_pages=3))
    assert result["passages"] == [] and "No passages matched" in result["brief"]


class ModelServer:
    """Records POSTs and answers like an OpenAI-compatible or Anthropic API."""

    def __init__(self, status: int = 200):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                if status != 200:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.endswith("/messages"):
                    payload = {"content": [{"type": "text", "text": "Anthropic says 4,812 K [1]"}]}
                else:
                    payload = {"choices": [{"message": {"content": "OpenAI-compatible says 4,812 K [1]"}}]}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


@pytest.fixture(autouse=True)
def clean_model_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "DUNGSCRAPER_RESEARCH_MODEL"):
        monkeypatch.delenv(name, raising=False)


async def test_research_synthesis_uses_dungscraper_llm_settings(web):
    model = ModelServer()
    try:
        result = await run_research(
            web,
            ResearchRequest(question=QUESTION, provider="searxng", synthesize=True),
            llm_base_url=model.url,
            llm_api_key="secret-1",
            llm_model="tiny",
        )
        assert result["answer"].startswith("OpenAI-compatible says") and result["answer_model"] == "tiny"
        sent = model.requests[0]
        assert sent["path"] == "/chat/completions" and sent["headers"]["Authorization"] == "Bearer secret-1"
        prompt = sent["body"]["messages"][1]["content"]
        assert QUESTION in prompt and "4,812 kelvin" in prompt
    finally:
        model.close()


async def test_research_synthesis_with_openai_and_anthropic_env_keys(web, monkeypatch):
    model = ModelServer()
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("OPENAI_BASE_URL", model.url)
        first = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", synthesize=True))
        assert first["answer"].startswith("OpenAI-compatible") and model.requests[-1]["headers"]["Authorization"] == "Bearer sk-openai"
        monkeypatch.delenv("OPENAI_API_KEY")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
        monkeypatch.setenv("DUNGSCRAPER_RESEARCH_MODEL", "claude-test")
        second = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", synthesize=True))
        assert second["answer"].startswith("Anthropic says") and second["answer_model"] == "claude-test"
        sent = model.requests[-1]
        assert sent["path"] == "/v1/messages" and sent["headers"]["x-api-key"] == "sk-ant" and sent["body"]["model"] == "claude-test"
    finally:
        model.close()


async def test_research_synthesis_is_optional_and_never_fails_the_research(web):
    no_key = await run_research(web, ResearchRequest(question=QUESTION, provider="searxng", synthesize=True))
    assert "answer" not in no_key and any("answer skipped: no key" in note for note in no_key["notes"])
    assert no_key["passages"]
    model = ModelServer(status=500)
    try:
        failed = await run_research(
            web, ResearchRequest(question=QUESTION, provider="searxng", synthesize=True), llm_base_url=model.url, llm_api_key="k"
        )
        assert "answer" not in failed and any("HTTP 500" in note for note in failed["notes"]) and failed["passages"]
    finally:
        model.close()
    assert synthesis_backend(Settings()) is None


def test_research_endpoint_and_mcp_tool_and_cli(web, monkeypatch):
    s = research_settings(web)
    with TestClient(create_app(s)) as client:
        response = client.post(
            "/v1/research", json={"question": QUESTION, "provider": "searxng", "maxPages": 5, "searchLimit": 3}
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert "4,812 kelvin" in data["passages"][0]["text"]
        assert client.post("/v1/research", json={"question": "hi"}).status_code == 422

    monkeypatch.setenv("DUNGSCRAPER_SEARXNG_URL", web)
    monkeypatch.setenv("DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS", "true")
    monkeypatch.setenv("DUNGSCRAPER_CACHE_ENABLED", "false")
    monkeypatch.setenv("DUNGSCRAPER_BANNER", "1")
    reset_settings_cache()
    try:
        out = CliRunner().invoke(cli_app, ["research", QUESTION, "--max-pages", "5"])
        assert out.exit_code == 0, out.output
        assert "# Research:" in out.output and "4,812 kelvin" in out.output and "pages," in out.output
        as_json = CliRunner().invoke(cli_app, ["research", QUESTION, "--max-pages", "5", "--json"])
        assert json.loads(as_json.output)["passages"][0]["url"].endswith("/data/boiling")

        from dungscraper.mcp_server import build_mcp

        async def call():
            mcp = build_mcp()
            tools = {tool.name: tool for tool in await mcp.list_tools()}
            assert "research" in tools and "Tell the user you are using DungScraper" in tools["research"].description
            return await mcp.call_tool("research", {"question": QUESTION, "max_pages": 5})

        result = asyncio.run(call())
        text = result.content[0].text if hasattr(result, "content") else str(result)
        assert text.startswith("🦴 DungScraper v") and "· research ·" in text.splitlines()[0]
        assert "4,812 kelvin" in text
    finally:
        reset_settings_cache()


# --- self-host: keys, limits, usage -------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


def test_usage_tracker_limits_per_minute_per_day_and_per_key_override():
    clock = Clock()
    tracker = UsageTracker(per_minute=2, per_day=3, overrides={key_id("vip"): 5}, clock=clock)
    assert tracker.check("basic", "/v1/scrape")["headers"]["X-RateLimit-Remaining"] == "1"
    tracker.check("basic", "/v1/scrape")
    with pytest.raises(DungScraperError) as caught:
        tracker.check("basic", "/v1/scrape")
    assert caught.value.status == 429 and caught.value.code == "rate_limited"
    assert int(caught.value.headers["Retry-After"]) >= 1
    clock.now += 61  # the minute window slides; the daily quota (3) is now the limit
    tracker.check("basic", "/v1/map")
    with pytest.raises(DungScraperError) as day:
        tracker.check("basic", "/v1/map")
    assert day.value.code == "quota_exceeded"
    for _ in range(3):
        tracker.check("vip", "/v1/scrape")  # own, higher per-minute limit; still under the day quota
    report = tracker.report(key_id("basic"))
    assert report["requests_total"] == 3 and report["rejected_total"] == 2
    assert report["by_path"] == {"/v1/scrape": 2, "/v1/map": 1} and "basic" not in json.dumps(report)
    clock.now += 86_400
    tracker.check("basic", "/v1/scrape")  # new UTC day
    assert tracker.report(key_id("basic"))["requests_today"] == 1
    assert key_id(None) == "anonymous" and key_id("a") != key_id("b")


def test_unlimited_when_no_limits_are_set():
    tracker = UsageTracker()
    for _ in range(500):
        assert tracker.check("k", "/x")["headers"] == {}


def test_api_keys_rate_limit_headers_and_usage_endpoint():
    s = settings(api_keys="alpha-key,beta-key", rate_limit_per_minute=3, key_limits="beta-key=1")
    with TestClient(create_app(s)) as client:
        assert client.get("/health").json()["auth"] is True  # health needs no key
        assert client.get("/v1/usage").status_code == 401
        assert client.get("/v1/usage", headers={"Authorization": "Bearer nope"}).status_code == 401
        alpha = {"Authorization": "Bearer alpha-key"}
        first = client.get("/v1/usage", headers=alpha)
        assert first.status_code == 200
        assert first.headers["X-RateLimit-Limit"] == "3" and first.headers["X-RateLimit-Remaining"] == "2"
        report = first.json()["data"]
        assert report["key_id"] == key_id("alpha-key") and report["requests_total"] == 1 and "alpha-key" not in first.text
        client.get("/v1/usage", headers={"X-API-Key": "alpha-key"})  # X-API-Key works too
        client.get("/v1/usage", headers=alpha)
        blocked = client.get("/v1/usage", headers=alpha)
        assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["Retry-After"]) >= 1
        beta = {"Authorization": "Bearer beta-key"}
        assert client.get("/v1/usage", headers=beta).status_code == 200  # own bucket
        assert client.get("/v1/usage", headers=beta).status_code == 429  # own, lower limit


def test_open_server_has_no_limits_and_usage_is_anonymous():
    with TestClient(create_app(settings())) as client:
        data = client.get("/v1/usage").json()["data"]
        assert data["key_id"] == "anonymous" and data["limit_per_minute"] is None
        assert client.get("/health").json()["auth"] is False


def test_mcp_endpoint_checks_keys_and_rate_limits():
    s = settings(api_keys="mcp-key", rate_limit_per_minute=1)
    with TestClient(create_app(s)) as client:
        assert client.post("/mcp", json={}).status_code == 401
        headers = {"Authorization": "Bearer mcp-key", "Accept": "application/json, text/event-stream"}
        first = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
        assert first.status_code not in {401, 429}
        second = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"}, headers=headers)
        assert second.status_code == 429 and "Retry-After" in second.headers


def test_serve_refuses_to_listen_publicly_without_keys(monkeypatch):
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k))
    monkeypatch.delenv("DUNGSCRAPER_API_KEYS", raising=False)
    reset_settings_cache()
    try:
        refused = CliRunner().invoke(cli_app, ["serve", "--host", "0.0.0.0"])
        assert refused.exit_code == 2 and "DUNGSCRAPER_API_KEYS" in refused.output and started == []
        local = CliRunner().invoke(cli_app, ["serve"])
        assert local.exit_code == 0 and started[-1]["host"] == "127.0.0.1"
        monkeypatch.setenv("DUNGSCRAPER_API_KEYS", "k1")
        reset_settings_cache()
        assert CliRunner().invoke(cli_app, ["serve", "--host", "0.0.0.0"]).exit_code == 0
        assert started[-1]["host"] == "0.0.0.0"
        monkeypatch.delenv("DUNGSCRAPER_API_KEYS")
        monkeypatch.setenv("DUNGSCRAPER_ALLOW_OPEN_SERVER", "true")
        reset_settings_cache()
        assert CliRunner().invoke(cli_app, ["serve", "--host", "0.0.0.0"]).exit_code == 0
    finally:
        reset_settings_cache()


def test_doctor_reports_proxies_cache_and_keys(monkeypatch):
    monkeypatch.setenv("DUNGSCRAPER_PROXIES", "http://u:pw@10.0.0.1:8080,socks5://10.0.0.2:1080")
    monkeypatch.setenv("DUNGSCRAPER_CACHE_DIR", "/tmp/dscache")
    reset_settings_cache()
    try:
        out = CliRunner().invoke(cli_app, ["doctor"]).output
        assert "2 in the pool" in out and "http://10.0.0.1:8080" in out and "pw" not in out
        assert "disk at /tmp/dscache" in out and "API keys for serve" in out
    finally:
        reset_settings_cache()


def test_deploy_files_are_consistent():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    docker = (root / "Dockerfile").read_text()
    assert "HEALTHCHECK" in docker and "/health" in docker and "${PORT:-" in docker and "VOLUME" in docker
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    env = compose["services"]["dungscraper"]["environment"]
    assert env["DUNGSCRAPER_API_KEYS"].startswith("${DUNGSCRAPER_API_KEYS:?")  # compose refuses to start without a key
    assert "healthcheck" in compose["services"]["dungscraper"] and "dungscraper-worker" in compose["services"]
    assert "dungscraper-data" in compose["volumes"]
    render = yaml.safe_load((root / "render.yaml").read_text())["services"][0]
    assert render["runtime"] == "docker" and render["healthCheckPath"] == "/health"
    assert {"key": "DUNGSCRAPER_API_KEYS", "generateValue": True} in render["envVars"]
    railway = json.loads((root / "railway.json").read_text())
    assert railway["build"]["builder"] == "DOCKERFILE" and railway["deploy"]["healthcheckPath"] == "/health"
    fly = (root / "fly.toml").read_text()
    assert "internal_port = 8741" in fly and 'path = "/health"' in fly
    assert (root / ".env.example").read_text().count("DUNGSCRAPER_API_KEYS") >= 1
    assert not (root / ".github" / "FUNDING.yml").exists()  # sponsors are not active yet
