import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from dungscraper.api.app import create_app
from dungscraper.config import Settings
from dungscraper.core.scrape import shutdown_browser
from dungscraper.models import ScrapeData


LONG = "Documentation page for dungscraper. " + " ".join(f"detail{i}" for i in range(40))


def _serve(pages: dict[str, tuple[str, str]]):
    posts: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in pages:
                self.send_response(404)
                self.end_headers()
                return
            ctype, body = pages[path]
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            posts.append(self.rfile.read(length))
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}", posts


@pytest.fixture
def api_settings() -> Settings:
    return Settings(
        api_keys="",
        redis_url="",
        cache_enabled=True,
        cache_ttl_seconds=60,
        log_json=False,
        log_level="WARNING",
        allow_private_networks=True,
        respect_robots_default=True,
        crawl_mode="inline",
        robots_fail_open=False,
    )


def test_health_and_auth_and_scrape_shape(monkeypatch, api_settings):
    async def fake_scrape(request, **kwargs):
        assert request.only_main_content is False
        return ScrapeData(url=request.url, markdown="# Hi", render_mode="http", status_code=200)

    monkeypatch.setattr("dungscraper.api.routes.scrape_url", fake_scrape)
    secured = api_settings.model_copy(update={"api_keys": "secret"})
    with TestClient(create_app(secured)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        denied = client.post("/v1/scrape", json={"url": "https://example.com", "onlyMainContent": False})
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"
        allowed = client.post(
            "/v1/scrape",
            headers={"Authorization": "Bearer secret"},
            json={"url": "https://example.com", "onlyMainContent": False},
        )
        assert allowed.status_code == 200
        assert allowed.json()["data"]["markdown"] == "# Hi"
        invalid = client.post(
            "/v1/scrape",
            headers={"Authorization": "Bearer secret"},
            json={"url": "not a url"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_request"


def test_chunk_endpoint(api_settings):
    with TestClient(create_app(api_settings)) as client:
        response = client.post(
            "/v1/chunk",
            json={"text": "# Title\n\n" + ("alpha " * 400), "max_tokens": 100, "overlap_tokens": 10},
        )
    assert response.status_code == 200
    chunks = response.json()["data"]["chunks"]
    assert len(chunks) > 1
    assert chunks[0]["metadata"]["heading"] == "Title"


def test_crawl_map_webhook_and_robots(api_settings):
    server, base, posts = _serve({})
    pages = {
        "/": (
            "text/html",
            f"""<html><body><article><h1>Home</h1><p>Home page unique dungscraper home.</p>
            <a href="{base}/docs">Docs</a>
            <a href="{base}/blog/post">Blog</a>
            <a href="{base}/private">Private</a>
            <a href="{base}/skip-me">Skip</a>
            <a href="{base}/dup">Dup</a>
            <a href="{base}/photo.jpg">Photo</a>
            </article></body></html>""",
        ),
        "/docs": ("text/html", f"<html><body><article><h1>Docs</h1><p>{LONG}</p></article></body></html>"),
        "/dup": ("text/html", f"<html><body><article><h1>Docs</h1><p>{LONG}</p></article></body></html>"),
        "/blog/post": ("text/html", "<html><body><article><h1>Blog</h1><p>Blog post about crawling the web.</p></article></body></html>"),
        "/from-sitemap": ("text/html", "<html><body><article><h1>Listed</h1><p>Sitemap only page.</p></article></body></html>"),
        "/private": ("text/html", "<html><body>secret private</body></html>"),
        "/skip-me": ("text/html", "<html><body>should be excluded</body></html>"),
        "/robots.txt": ("text/plain", f"User-agent: *\nDisallow: /private\nSitemap: {base}/sitemap.xml\n"),
        "/sitemap.xml": (
            "application/xml",
            f"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>{base}/docs</loc></url>
            <url><loc>{base}/from-sitemap</loc></url>
            </urlset>""",
        ),
    }
    # The handler closes over the original dict. Replace contents in place.
    # Recreate so the handler sees the populated mapping.
    server.shutdown()
    server, base, posts = _serve(pages)
    # Pages were built with the previous base. Rebuild against the live base.
    server.shutdown()
    # Build pages after we know the port by serving a placeholder then... do it cleanly:
    holder: dict = {}

    class Late(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            item = holder.get(path)
            if item is None:
                self.send_response(404)
                self.end_headers()
                return
            ctype, body = item
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            posts.append(self.rfile.read(length))
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    live = ThreadingHTTPServer(("127.0.0.1", 0), Late)
    thread = threading.Thread(target=live.serve_forever, daemon=True)
    thread.start()
    host, port = live.server_address
    base = f"http://{host}:{port}"
    posts.clear()
    holder.update(
        {
            "/": (
                "text/html",
                f"""<html><body><article><h1>Home</h1><p>Home page unique dungscraper home.</p>
                <a href="{base}/docs">Docs</a>
                <a href="{base}/blog/post">Blog</a>
                <a href="{base}/private">Private</a>
                <a href="{base}/skip-me">Skip</a>
                <a href="{base}/dup">Dup</a>
                <a href="{base}/photo.jpg">Photo</a>
                </article></body></html>""",
            ),
            "/docs": ("text/html", f"<html><body><article><h1>Docs</h1><p>{LONG}</p></article></body></html>"),
            "/dup": ("text/html", f"<html><body><article><h1>Docs</h1><p>{LONG}</p></article></body></html>"),
            "/blog/post": (
                "text/html",
                "<html><body><article><h1>Blog</h1><p>Blog post about crawling the web.</p></article></body></html>",
            ),
            "/from-sitemap": (
                "text/html",
                "<html><body><article><h1>Listed</h1><p>Sitemap only page with its own words.</p></article></body></html>",
            ),
            "/private": ("text/html", "<html><body>secret private</body></html>"),
            "/skip-me": ("text/html", "<html><body>should be excluded</body></html>"),
            "/robots.txt": ("text/plain", f"User-agent: *\nDisallow: /private\nSitemap: {base}/sitemap.xml\n"),
            "/sitemap.xml": (
                "application/xml",
                f"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <url><loc>{base}/docs</loc></url>
                <url><loc>{base}/from-sitemap</loc></url>
                </urlset>""",
            ),
            "/js": (
                "text/html",
                "<html><body><div id='app'>Loading</div><script>document.getElementById('app').innerHTML="
                "'<article><h1>Rendered</h1><p>The secret token is dungscraper-js-ok.</p></article>';</script></body></html>",
            ),
        }
    )
    try:
        with TestClient(create_app(api_settings)) as client:
            mapped = client.post("/v1/map", json={"url": base + "/", "limit": 50})
            assert mapped.status_code == 200, mapped.text
            urls = {item["url"] for item in mapped.json()["data"]["links"]}
            assert f"{base}/docs" in urls
            assert f"{base}/from-sitemap" in urls
            filtered = client.post("/v1/map", json={"url": base + "/", "search": "blog", "limit": 50})
            filtered_urls = [item["url"] for item in filtered.json()["data"]["links"]]
            assert filtered_urls == [f"{base}/blog/post"]

            crawled = client.post(
                "/v1/crawl",
                json={
                    "url": base + "/",
                    "limit": 10,
                    "max_depth": 2,
                    "delay_ms": 0,
                    "wait": True,
                    "timeout_seconds": 30,
                    "webhook": base + "/hook",
                    "exclude_paths": ["/skip-me"],
                    "respect_robots": True,
                    "use_sitemap": True,
                    "dedupe": True,
                    "concurrency": 4,
                },
            )
            assert crawled.status_code == 200, crawled.text
            body = crawled.json()["data"]
            assert body["status"] == "completed"
            found = {page["url"].rstrip("/") for page in body["pages"]}
            assert f"{base}/docs" in found
            assert f"{base}/from-sitemap" in found
            assert f"{base}/blog/post" in found
            assert all("/private" not in url for url in found)
            assert all("/skip-me" not in url for url in found)
            assert all(not url.endswith(".jpg") for url in found)
            assert body["skipped_duplicates"] >= 1
            assert body["skipped_robots"] >= 1
            assert posts, "webhook was not delivered"
            payload = json.loads(posts[-1])
            assert payload["status"] == "completed"
            assert payload["id"] == body["id"]

            rendered = client.post("/v1/scrape", json={"url": base + "/js", "render": "browser", "respect_robots": False})
            assert rendered.status_code == 200, rendered.text
            assert "dungscraper-js-ok" in rendered.json()["data"]["markdown"]
            assert rendered.json()["data"]["render_mode"] == "browser"

            http_only = client.post(
                "/v1/scrape",
                json={"url": base + "/js", "render": "http", "respect_robots": False, "formats": ["markdown"]},
            )
            assert "dungscraper-js-ok" not in (http_only.json()["data"]["markdown"] or "")
            auto = client.post("/v1/scrape", json={"url": base + "/js", "render": "auto", "respect_robots": False})
            assert auto.json()["data"]["render_mode"] == "browser"
            assert "dungscraper-js-ok" in auto.json()["data"]["markdown"]
    finally:
        live.shutdown()


@pytest.mark.asyncio
async def test_browser_shutdown():
    await shutdown_browser()
