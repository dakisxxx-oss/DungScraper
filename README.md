<p align="center"><img src="https://raw.githubusercontent.com/dakisxxx-oss/DungScraper/main/docs/hero.svg" alt="DungScraper" width="100%"></p>

<p align="center">
<a href="https://pypi.org/project/dungscraper/"><img alt="PyPI" src="https://img.shields.io/pypi/v/dungscraper"></a>
<img alt="version" src="https://img.shields.io/badge/version-0.6.0-2dd4bf">
<img alt="license" src="https://img.shields.io/badge/license-MIT-f59e0b">
<img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776ab">
<img alt="tests" src="https://img.shields.io/badge/tests-219%20passing-22c55e">
<img alt="MCP" src="https://img.shields.io/badge/MCP-ready-818cf8">
<img alt="no api key" src="https://img.shields.io/badge/API%20key-not%20needed-f472b6">
</p>

**DungScraper gives AI agents the web.** Scrape, crawl, search, and pull structured data from any page, on your own machine. There is no account, no API key and no per-page bill.

<p align="center"><img src="https://raw.githubusercontent.com/dakisxxx-oss/DungScraper/main/docs/demo.svg" alt="terminal demo" width="100%"></p>

## Quick start (no account)

Install and scrape a page. No signup, no API key, no Playwright needed for ordinary HTML:

```bash
pip install dungscraper
dungscraper scrape https://example.com --local
```

For JavaScript-heavy pages, later run `python -m playwright install chromium`. `dungscraper doctor` checks your setup. For the latest code on `main`, `pip install git+https://github.com/dakisxxx-oss/DungScraper.git`.

Add it to Claude Desktop, Cursor or any MCP client:

```json
{ "mcpServers": { "dungscraper": { "command": "dungscraper", "args": ["mcp"] } } }
```

For Claude Desktop, `dungscraper install-claude` writes that entry for you. Then ask your agent to read a page.

> **Your agent will say it is using DungScraper.** Every tool result starts with `🦴 DungScraper v0.6.0 · <tool> · <tier> · <ms>`, and the tool descriptions tell the agent to mention it. Set `DUNGSCRAPER_BANNER=0` to turn the line off.

## Try the free public API (no signup)

A demo instance is running at `http://92.5.134.188:8741`. No account and no key. Per-IP limits apply (10 requests/minute, 200/day, crawls cap at 5 pages). Private and internal addresses are blocked. It is HTTP only for now (a domain and TLS can come later). This is a single Always Free VM, not a paid SLA.

```bash
curl -s "http://92.5.134.188:8741/r/https://example.com"
```

Or `POST /v1/scrape` with `{"url":"https://example.com"}` and no Authorization header. If you have a key from a self-hosted `.env`, send it as `Authorization: Bearer <key>` to skip the anonymous caps.



## What you get

| | |
|---|---|
| **Clean Markdown** | Main content with tables, code and links. HTTP first, a real browser when the page needs JavaScript. |
| **Token-budget reading** | `read_focused` ranks sections by your question (BM25) and returns only what fits. The demo above sends 278 tokens instead of 12,069. |
| **Extraction with no LLM** | `extract_fields` takes a CSS or XPath schema, with lists and nesting, and returns JSON. Fast, free and repeatable. |
| **Research with citations** | `research` searches, reads the top pages, follows promising links within a page and time budget, and returns ranked passages with source URLs and a cited brief. No LLM needed. Add your own OpenAI or Anthropic key for a written answer. |
| **Deep crawl strategies** | `bfs`, `dfs` or `best_first` crawling. Links are scored by keywords in the text and URL, depth, freshness and same-site authority. Include/exclude patterns, content-type filter, `min_score`. |
| **Proxies and scale** | Bring your own proxies (HTTP or SOCKS) with per-site stickiness, health tracking, backoff, and retry on 403/429 with the next proxy, on both the HTTP and browser paths. Batch scrape up to 200 URLs with global and per-host limits and spacing. Optional disk cache. |
| **Self-host ready** | One-command installer, Dockerfile with health check, compose file, optional anonymous public access with per-IP limits, API keys, `/v1/usage`, and Render, Fly and Railway templates. See [DEPLOY.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/docs/DEPLOY.md). |
| **Crawl, map, search** | Crawl a site, list its URLs, search the web. |
| **Change watching** | `watch_page_tool` reports new, removed and price-changed ads, or a diff of any page. |
| **Site readers** | Reddit with no API key, RSS/Atom, skelbiu.lt and autoplius.lt listings, schema.org and OpenGraph on any page. |
| **Hard pages** | Waits out "preparing your page" interstitials, falls back to Chrome or Edge, supports actions and screenshots. |
| **Three ways in** | MCP server, REST API (Firecrawl-style `/v1/scrape`, plus `GET /r/<url>` like Jina Reader), and a CLI and Python SDK. |
| **Polite and private** | Respects robots.txt, listens on 127.0.0.1, blocks private-network targets by default. |

## How it compares

Based on each project's public docs, November 2026. Pricing changes, so check the source. See [docs/COMPETITORS.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/docs/COMPETITORS.md) for details and sources.

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
| Proxy rotation and scale | **Yes (bring your own proxies)** | Yes, managed | Yes (bring your own) | Yes (proxy management) | Hosted |
| Autonomous research agent | **Yes (retrieval with citations, no LLM; optional LLM answer)** | Yes (LLM agent, credits) | Partial (adaptive crawling) | No | No |
| Deep-crawl scoring strategies | **Yes (bfs, dfs, best-first, 4 scorers)** | Path filters and depth | Yes (more scorers and filters) | No | No |
| Hosted option | **Yes (free public demo, or self-host with Docker)** | Yes | Cloud and Docker | Yes | Yes |

"No" means not found in the project's public docs.

Where it loses, plainly:
- **Proxies are yours.** DungScraper rotates and health-checks proxies you supply. It sells and bundles none, and has no residential pool. Heavy bot protection (Cloudflare Turnstile, DataDome) can still block it.
- **Research is retrieval, not reasoning.** It ranks and cites passages. It does not click, log in or plan like Firecrawl's agent. A written answer needs your own model key.
- **The public demo is a single Always Free VM.** It is free, has no signup, and is rate-limited per IP. It has no SLA, no TLS yet (plain HTTP on a public IP), and may change address. For production, run your own instance; see [DEPLOY.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/docs/DEPLOY.md). Self-host pulls `ghcr.io/dakisxxx-oss/dungscraper` (amd64 and arm64). Render, Fly and Railway one-click deploys have not been run from this repo.
- **Scale is one machine** (or a few replicas whose rate-limit counters are separate). Crawl4AI's deep-crawl scorers and filters are more numerous than ours.
- It is a young project with fewer users and less mileage than the others.

## MCP tools

`scrape` · `batch_scrape` · `crawl` · `crawl_status` · `map_site` · `search_web` · `research` · `extract` · `chunk_text` · `reddit` · `read_feed` · `watch_page_tool` · `read_focused` · `extract_fields` · `find_contacts_tool` · `find_jobs_tool`

```bash
dungscraper scrape https://example.com --local          # Markdown
dungscraper focus https://en.wikipedia.org/wiki/Web_scraping "is it legal" --max-tokens 300
dungscraper extract-css https://news.ycombinator.com --schema '{"baseSelector":"tr.athing","title":".titleline > a"}'
dungscraper research "How does BM25 ranking work?" --max-pages 5
dungscraper crawl https://example.com --local --strategy best_first --keywords "pricing,plans" --limit 10
dungscraper batch https://example.com https://example.org --concurrency 4 --per-host 1 --delay-ms 500
DUNGSCRAPER_PROXIES="http://user:pass@proxy1:8080,socks5://proxy2:1080" dungscraper scrape https://example.com --local
dungscraper watch https://www.skelbiu.lt/skelbimai/transportas/dviraciai/
curl "http://127.0.0.1:8741/r/https://example.com?query=pricing&max_tokens=500"   # after: dungscraper serve
```

The full reference is in [docs/REFERENCE.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/docs/REFERENCE.md).

## Roadmap

- [x] Publish to PyPI
- [ ] Scheduled `watch` with email or Telegram alerts
- [ ] More local site readers (aruodas.lt, other classifieds)
- [ ] An LLM-driven browsing agent on top of `research`
- [ ] A managed hosted service (only if there is demand)

## Support the project

DungScraper is free and stays free to run on your own machine.

- **Star the repo.** It helps other people find it.
- **Run your own instance.** One command on Ubuntu or Debian; details in [DEPLOY.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/docs/DEPLOY.md).
- **Want a managed service run for you?** [Open an issue titled "Hosted waitlist"](https://github.com/dakisxxx-oss/DungScraper/issues/new?title=Hosted%20waitlist) to get in line. It is only built if enough people ask.
- **Need it for your business?** Open an issue for custom site readers, integration help or support.

## Use it responsibly

Respect each site's terms and robots.txt. DungScraper never extracts private seller phone numbers and refuses what robots.txt disallows. See [SECURITY.md](https://github.com/dakisxxx-oss/DungScraper/blob/main/SECURITY.md).

MIT licensed. See [LICENSE](https://github.com/dakisxxx-oss/DungScraper/blob/main/LICENSE).
