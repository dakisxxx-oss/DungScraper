# DungScraper benchmark

Measured by `python benchmark/bench.py` in this environment. Latency is wall clock for one uncached scrape with the default auto renderer. Link counts are Markdown links, including in-page anchors. Firecrawl was not run here, so there is no head-to-head number.

| URL | OK | Render | ms | Words | Headings | Links | Boilerplate |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `https://example.com` | yes | http | 258 | 28 | 1 | 0 | 0 |
| `https://en.wikipedia.org/wiki/Web_scraping` | yes | http | 594 | 5030 | 19 | 400 | 0 |
| `https://docs.python.org/3/tutorial/appetite.html` | yes | http | 189 | 809 | 1 | 6 | 0 |
| `https://httpbin.org/html` | yes | http | 45 | 606 | 1 | 0 | 0 |
| `https://quotes.toscrape.com/` | yes | http | 130 | 271 | 2 | 52 | 0 |
| `https://quotes.toscrape.com/js/` | yes | browser | 670 | 237 | 1 | 2 | 0 |
| `https://news.ycombinator.com/` | yes | http | 496 | 1039 | 0 | 194 | 0 |
| `https://www.rfc-editor.org/rfc/rfc9110.html` | yes | http | 3588 | 68902 | 312 | 4713 | 0 |
| `https://www.mozilla.org/en-US/about/manifesto/` | yes | http | 463 | 1075 | 8 | 23 | 0 |
| `https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf` | yes | http | 489 | 6 | 1 | 0 | 0 |
