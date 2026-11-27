<p align="center"><img src="docs/hero.svg" alt="DungScraper" width="100%"></p>

<p align="center">
<img alt="version" src="https://img.shields.io/badge/version-0.5.0-2dd4bf">
<img alt="license" src="https://img.shields.io/badge/license-MIT-f59e0b">
<img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776ab">
<img alt="tests" src="https://img.shields.io/badge/tests-170%20passing-22c55e">
<img alt="MCP" src="https://img.shields.io/badge/MCP-ready-818cf8">
<img alt="no api key" src="https://img.shields.io/badge/API%20key-not%20needed-f472b6">
</p>

**DungScraper gives AI agents the web.** Scrape, crawl, search, and pull structured data from any page, on your own machine. There is no account, no API key and no per-page bill.

<p align="center"><img src="docs/demo.svg" alt="terminal demo" width="100%"></p>

## 30-second quickstart

```bash
pip install git+https://github.com/dakisxxx-oss/DungScraper.git
python -m playwright install chromium     # only needed for JavaScript-heavy pages
dungscraper doctor                        # checks your setup
```

Add it to Claude Desktop, Cursor or any MCP client:

```json
{ "mcpServers": { "dungscraper": { "command": "dungscraper", "args": ["mcp"] } } }
```

For Claude Desktop, `dungscraper install-claude` writes that entry for you. Then ask your agent to read a page.

> **Your agent will say it is using DungScraper.** Every tool result starts with `🦴 DungScraper v0.5.0 · <tool> · <tier> · <ms>`, and the tool descriptions tell the agent to mention it. Set `DUNGSCRAPER_BANNER=0` to turn the line off.

## What you get

| | |
|---|---|
| **Clean Markdown** | Main content with tables, code and links. HTTP first, a real browser when the page needs JavaScript. |
| **Token-budget reading** | `read_focused` ranks sections by your question (BM25) and returns only what fits. The demo above sends 278 tokens instead of 12,069. |
| **Extraction with no LLM** | `extract_fields` takes a CSS or XPath schema, with lists and nesting, and returns JSON. Fast, free and repeatable. |
| **Crawl, map, batch, search** | Crawl a site, list its URLs, scrape many URLs, search the web. |
| **Change watching** | `watch_page_tool` reports new, removed and price-changed ads, or a diff of any page. |
| **Site readers** | Reddit with no API key, RSS/Atom, skelbiu.lt and autoplius.lt listings, schema.org and OpenGraph on any page. |
| **Hard pages** | Waits out "preparing your page" interstitials, falls back to Chrome or Edge, supports actions and screenshots. |
| **Three ways in** | MCP server, REST API (Firecrawl-style `/v1/scrape`, plus `GET /r/<url>` like Jina Reader), and a CLI and Python SDK. |
| **Polite and private** | Respects robots.txt, listens on 127.0.0.1, blocks private-network targets by default. |

## How it compares

Based on each project's public docs, November 2026. Pricing changes, so check the source. See [docs/COMPETITORS.md](docs/COMPETITORS.md) for details and sources.

| | **DungScraper** | Firecrawl | Crawl4AI | WaterCrawl | Jina Reader |
|---|---|---|---|---|---|
| Cost | Free, MIT | Free 1,000 credits, then from $19/mo | Free, open source | Free 1,000 credits hosted, or self-host | Free 20 req/min, then tokens |
| API key needed | **No** | Yes | No | Yes (hosted) | Optional |
| Runs on your machine | **Yes, one `pip install`** | Self-host is heavy | Yes | Docker stack (Postgres, Redis) | No |
| MCP server | Yes, local, no key | Yes | Yes, via Docker | Yes, needs a key | Hosted MCP |
| Scrape, crawl, map, search | Yes | Yes | Crawl, no search | Yes | Read and search |
| Query-focused, token-budgeted output | **Yes, no LLM** | LLM extraction, extra credits | Yes (BM25 and LLM filters) | No | No |
| CSS/XPath extraction with no LLM | **Yes** | No | Yes | No | No |
| Change watching | **Yes** | Change tracking, uses credits | No | No | No |
| Reddit with no key, local classifieds | **Yes** | No | No | No | No |
| Managed proxies and scale | **No** | Yes | No | Hosted | Yes |
| Autonomous research agent | **No** | Yes | No | No | No |
| Deep-crawl scoring strategies | **No** | Partial | Yes | No | No |
| Hosted option | Coming | Yes | Cloud | Yes | Yes |

Where it loses: DungScraper runs on one machine. It has no residential proxy pool, no managed cloud, and no autonomous agent. If you need to scrape a million pages a month behind heavy bot protection, use a hosted service. If you want your agent to read the web today for free, start here.

## MCP tools

`scrape` · `batch_scrape` · `crawl` · `crawl_status` · `map_site` · `search_web` · `extract` · `chunk_text` · `reddit` · `read_feed` · `watch_page_tool` · `read_focused` · `extract_fields` · `find_contacts_tool` · `find_jobs_tool`

```bash
dungscraper scrape https://example.com --local          # Markdown
dungscraper focus https://en.wikipedia.org/wiki/Web_scraping "is it legal" --max-tokens 300
dungscraper extract-css https://news.ycombinator.com --schema '{"baseSelector":"tr.athing","title":".titleline > a"}'
dungscraper watch https://www.skelbiu.lt/skelbimai/transportas/dviraciai/
curl "http://127.0.0.1:8741/r/https://example.com?query=pricing&max_tokens=500"   # after: dungscraper serve
```

The full reference is in [docs/REFERENCE.md](docs/REFERENCE.md).

## Roadmap

- [ ] Publish to PyPI
- [ ] Scheduled `watch` with email or Telegram alerts
- [ ] Deep-crawl scoring (best-first by keyword)
- [ ] More local site readers (aruodas.lt, other classifieds)
- [ ] Hosted version with managed proxies

## Support the project

DungScraper is free and stays free to run on your own machine.

- **Star the repo.** It helps other people find it.
- **Hosted version, coming.** Managed proxies, scheduled watches and alerts, no setup. [Open an issue titled "Hosted waitlist"](https://github.com/dakisxxx-oss/DungScraper/issues/new?title=Hosted%20waitlist) to get in line.
- **Need it for your business?** Open an issue for custom site readers, integration help or support.

## Use it responsibly

Respect each site's terms and robots.txt. DungScraper never extracts private seller phone numbers and refuses what robots.txt disallows. See [SECURITY.md](SECURITY.md).

MIT licensed. See [LICENSE](LICENSE).
