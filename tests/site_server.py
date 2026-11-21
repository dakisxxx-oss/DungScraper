"""A tiny local web site for tests that need real HTTP without the internet."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# A tiny site for the full flow: contacts fan out from the home page.
PAGES = {
    "/": (
        "<html><body><p>Home: <a href='mailto:home@acme-site.io'>home</a></p>"
        "<a href='/contact'>Contact</a> <a href='/careers'>Careers</a>"
        "<a href='/private/contact'>Contact (private)</a> <a href='/blog/post'>Blog</a></body></html>"
    ),
    "/contact": fixture("contact_page.html"),
    "/careers": fixture("careers_page.html"),
    "/blog/post": "<html><body>blog@acme-site.io</body></html>",
    "/private/contact": "<html><body>secret@acme-site.io</body></html>",
    "/robots.txt": "User-agent: *\nDisallow: /private/\n",
}
HITS: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        HITS.append(self.path)
        body = PAGES.get(self.path.split("?", 1)[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        content_type = "text/plain" if self.path.endswith("robots.txt") else "text/html; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        return


@pytest.fixture
def site():
    HITS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


