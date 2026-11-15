"""Measure DungScraper on a fixed set of public URLs and write benchmark/RESULTS.md.

Numbers come from this process. They are not a comparison with Firecrawl.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from dungscraper.config import Settings
from dungscraper.core.fetch import BrowserManager, make_client
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeRequest

URLS = [
    "https://example.com",
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://docs.python.org/3/tutorial/appetite.html",
    "https://httpbin.org/html",
    "https://quotes.toscrape.com/",
    "https://quotes.toscrape.com/js/",
    "https://news.ycombinator.com/",
    "https://www.rfc-editor.org/rfc/rfc9110.html",
    "https://www.mozilla.org/en-US/about/manifesto/",
    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
]

BOILERPLATE = ("accept all cookies", "subscribe to our newsletter", "enable javascript and cookies to continue")


def signals(markdown: str) -> dict:
    text = markdown or ""
    lowered = text.lower()
    return {
        "chars": len(text),
        "words": len(text.split()),
        "headings": len(re.findall(r"^#{1,6} ", text, flags=re.MULTILINE)),
        "links": len(re.findall(r"\]\(", text)),
        "code_fences": text.count("```") // 2,
        "boilerplate_hits": sum(1 for phrase in BOILERPLATE if phrase in lowered),
    }


async def main() -> None:
    settings = Settings(
        cache_enabled=False,
        log_json=False,
        log_level="WARNING",
        respect_robots_default=True,
        request_timeout_seconds=40,
        browser_timeout_seconds=45,
        http_retries=1,
    )
    browser = BrowserManager(settings)
    rows = []
    async with make_client(settings) as client:
        try:
            for url in URLS:
                started = time.perf_counter()
                try:
                    data = await scrape_url(
                        ScrapeRequest(url=url, formats=["markdown", "metadata"], timeout_ms=45000),
                        settings=settings,
                        client=client,
                        browser=browser,
                    )
                    elapsed = int((time.perf_counter() - started) * 1000)
                    info = signals(data.markdown or "")
                    rows.append(
                        {
                            "url": url,
                            "ok": True,
                            "status": data.status_code,
                            "render": data.render_mode,
                            "ms": elapsed,
                            "title": (data.metadata.title if data.metadata else None),
                            "warnings": data.warnings,
                            **info,
                        }
                    )
                except DungScraperError as exc:
                    rows.append(
                        {
                            "url": url,
                            "ok": False,
                            "status": None,
                            "render": None,
                            "ms": int((time.perf_counter() - started) * 1000),
                            "error": f"{exc.code}: {exc.message}",
                        }
                    )
        finally:
            await browser.shutdown()

    out = Path(__file__).resolve().parent
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    lines = [
        "# DungScraper benchmark",
        "",
        "Measured by `python benchmark/bench.py` in this environment. "
        "Latency is wall clock for one uncached scrape with the default auto renderer. "
        "Link counts are Markdown links, including in-page anchors. "
        "Firecrawl was not run here, so there is no head-to-head number.",
        "",
        "| URL | OK | Render | ms | Words | Headings | Links | Boilerplate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not row["ok"]:
            lines.append(f"| `{row['url']}` | no |  | {row['ms']} |  |  |  | {row.get('error', '')} |")
            continue
        lines.append(
            "| `{url}` | yes | {render} | {ms} | {words} | {headings} | {links} | {boilerplate_hits} |".format(**row)
        )
    text = "\n".join(lines) + "\n"
    (out / "RESULTS.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
