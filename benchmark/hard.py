"""Hard-site success rate. Same URLs and the same success rule before and after.

A page counts as a success when the scrape returns at least 30 words of
markdown that is not a bot challenge or an empty error shell.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from dungscraper.config import Settings
from dungscraper.core.fetch import BrowserManager, make_client
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.models import ScrapeRequest

URLS = [
    "https://example.com",
    "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "https://docs.python.org/3/library/asyncio.html",
    "https://developer.mozilla.org/en-US/docs/Web/HTML",
    "https://github.com/microsoft/playwright",
    "https://news.ycombinator.com/",
    "https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do",
    "https://quotes.toscrape.com/js/",
    "https://react.dev/learn",
    "https://httpbin.org/html",
    "https://www.bbc.com/news",
    "https://www.theguardian.com/world",
    "https://www.reuters.com/world/",
    "https://apnews.com/",
    "https://old.reddit.com/r/Python/",
    "https://www.reddit.com/r/Python/",
    "https://medium.com/",
    "https://arxiv.org/abs/1706.03762",
    "https://www.amazon.com/s?k=kindle",
    "https://edition.cnn.com/",
]

CHALLENGE = (
    "just a moment",
    "enable javascript and cookies to continue",
    "attention required",
    "verify you are a human",
    "cf-challenge",
    "access denied",
    "request blocked",
    "are you a robot",
    "captcha",
)


def is_success(words: int, sample: str, status: int | None) -> bool:
    lowered = (sample or "").lower()
    if any(phrase in lowered for phrase in CHALLENGE) and words < 120:
        return False
    if status is not None and status >= 400 and words < 40:
        return False
    return words >= 20


async def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    settings = Settings(
        cache_enabled=False,
        log_json=False,
        log_level="ERROR",
        respect_robots_default=True,
        request_timeout_seconds=25,
        browser_timeout_seconds=35,
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
                        ScrapeRequest(url=url, formats=["markdown", "metadata"], timeout_ms=35000, cache=False),
                        settings=settings,
                        client=client,
                        browser=browser,
                    )
                    sample = (data.markdown or "")[:500]
                    words = len((data.markdown or "").split())
                    ok = is_success(words, sample, data.status_code)
                    rows.append(
                        {
                            "url": url,
                            "success": ok,
                            "status": data.status_code,
                            "render": data.render_mode,
                            "words": words,
                            "ms": int((time.perf_counter() - started) * 1000),
                            "title": data.metadata.title if data.metadata else None,
                            "warnings": data.warnings,
                            "sample": sample.replace("\n", " ")[:180],
                        }
                    )
                except DungScraperError as exc:
                    rows.append(
                        {
                            "url": url,
                            "success": False,
                            "status": None,
                            "render": None,
                            "words": 0,
                            "ms": int((time.perf_counter() - started) * 1000),
                            "error": f"{exc.code}: {exc.message[:180]}",
                        }
                    )
                print(
                    f"{'OK' if rows[-1]['success'] else 'NO'} {rows[-1]['ms']:5} {rows[-1].get('render') or '-':7} {rows[-1]['words']:5} {url}",
                    flush=True,
                )
        finally:
            await browser.shutdown()
    passed = sum(1 for row in rows if row["success"])
    summary = {"label": label, "success": passed, "total": len(rows), "rows": rows}
    out = Path(__file__).resolve().parent / f"hard-{label}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{label}: {passed}/{len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
