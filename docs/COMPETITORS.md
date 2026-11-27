# How DungScraper compares

Notes from public docs and pricing pages, read in November 2026. Pricing changes often, so check the source before you decide. Items marked "(general knowledge)" were not re-checked.

## Summary

| Tool | Model | Cost | Strength | Weakness for an agent user |
|---|---|---|---|---|
| **DungScraper** | Local, MIT, `pip install`, or self-host with Docker | Free | No key, local MCP, focus under a token budget, CSS/XPath with no LLM, cited research with no LLM, best-first crawling, bring-your-own proxy rotation, watch, Reddit and classifieds | One machine, no managed or residential proxies, no service run by us, retrieval not a reasoning agent, young project |
| **Firecrawl** | Hosted API, self-host possible | Free 1,000 credits/mo. Hobby $19 (5k), Standard $99 (100k), Growth $399 (500k), Scale $749 (1M) | Polished API, scale, agent endpoint, interact, change tracking | Needs a key. Per-credit billing. LLM extraction adds credits (+4/page JSON). Free tier is 10 req/min on scrape, map, search |
| **WaterCrawl** | Open source (Django, Scrapy, Celery) plus hosted | Hosted free 1,000 credits (100/day). Paid tiers are low-cost | Crawl, search, sitemap, screenshots, plugins, Dify and n8n integrations, MCP server | Self-hosting needs Docker Compose with Postgres, Redis, MinIO, Nginx. MCP needs an API key |
| **Crawl4AI** | Open source Python, Docker server | Free | Fit Markdown (pruning, BM25, LLM filters), CSS/XPath strategies, deep crawl BFS/DFS/best-first, MCP over SSE and WebSocket in the Docker server | Heavier setup, no search tool, no change watching |
| **Jina Reader** | Hosted URL prefix `r.jina.ai/<url>` | 20 req/min with no key. Higher limits with a key. Billed by output tokens. The free key is non-commercial | Simplest possible interface | Hosted only, rate limits, search needs a key, no crawl |
| **Spider** | Hosted API | Pay as you go from about $1 per GB plus request-minutes, about $0.0001-0.0003 per page. Unlimited plans from $40/mo | Cheap at volume, fast | Hosted, key needed |
| **ScrapeGraphAI** | Hosted API and library | Free 500 credits. Starter $17 (10k), Growth $85 (100k), Pro $425 (750k) | LLM-first extraction | Extract costs 5 credits, search 2 per result |
| **Apify Website Content Crawler** | Platform actor | About $0.20 per 1,000 pages on raw HTTP, $0.50-$5 per 1,000 with a browser, plus platform usage. Free $5 credit, Starter $19 | Marketplace, scheduling, proxies | Platform account and usage billing |
| **Playwright MCP, browser-use** | Browser control for agents (general knowledge) | Free, open source | Click, type and navigate real pages | No clean Markdown, crawl or map. Token-heavy page snapshots |

## What the leaders do that matters to agents

- **Firecrawl.** Its `/agent` endpoint does LLM-driven research and extraction from a prompt (5 free runs a day, then dynamic credit pricing, `maxCredits` caps spend). One call turns a URL into Markdown with metadata. Formats are requested by name. MCP tools: scrape, map, crawl, check_crawl_status, search, agent, parse. Hosted keyless MCP exposes search, scrape and parse. Scrape and crawl cost 1 credit per page, map 1 per call, search 2 per 10 results. Change tracking is a scrape format that costs the standard credit for git-diff.
- **WaterCrawl.** Crawl and search with live progress, sitemap download, screenshots as PDF or JPG, and OpenAI post-processing. MCP tools: scrape-url, search, download-sitemap, manage-crawl, manage-search, monitor-request.
- **Crawl4AI.** Also has proxy rotation (`RoundRobinProxyStrategy` with sticky sessions), deep-crawl strategies with scorers and filters, and `AdaptiveCrawler`, which crawls toward a query and stops when coverage looks sufficient. The best open-source content filtering: pruning, BM25 and LLM filters give "fit markdown". It also generates CSS schemas with an LLM once, then extracts without one.
- **Jina Reader.** The interface to beat for simplicity: prepend a prefix to any URL.

## What 0.5.0 takes from them

| Idea | From | In DungScraper 0.5.0 |
|---|---|---|
| Prefix reader | Jina | `GET /r/<url>`, text/markdown, optional `?query=&max_tokens=` |
| Fit Markdown by query | Crawl4AI BM25 filter | `read_focused`, `focus`, `POST /v1/focus`: BM25 sections with heading paths under a token budget. No LLM, no key |
| Schema extraction without LLM | Crawl4AI | `extract_fields`, `extract-css`, `POST /v1/extract/css`: CSS and XPath, text/attribute/html/regex, lists, nesting |
| Firecrawl response names | Firecrawl | `metadata.sourceURL`, `statusCode`, format aliases `rawHtml`, `screenshot@fullPage`, `summary` on `/v1/scrape` |
| Change tracking | Firecrawl | `watch` (already in 0.4.0), free and stored locally |
| Agents announce the tool | New | Every MCP result starts with `🦴 DungScraper v<version> · <tool> · <tier> · <ms>` and the tool descriptions ask the agent to tell the user |
| Self-check | New | `dungscraper doctor` |
| Proxy pool | Crawl4AI, WaterCrawl, Firecrawl (managed) | `DUNGSCRAPER_PROXIES` / `_PROXY_FILE`: HTTP and SOCKS, sticky per site, cool-down with doubling backoff, retry on 403/429 and transport errors with the next proxy, on the HTTP and browser paths. Bring your own proxies: none are bundled |
| Scale | Spider, Firecrawl | Batch of up to 200 URLs with global and per-host concurrency and per-host spacing, optional disk cache |
| Research | Firecrawl `/agent`, Crawl4AI adaptive crawling | `research`: search, fetch, follow promising links within page and time budgets, BM25-ranked cited passages and brief. No LLM needed. Optional answer from your OpenAI or Anthropic key |
| Deep-crawl scoring | Crawl4AI | `bfs`, `dfs`, `best_first`; keyword, depth, freshness and authority scorers; include/exclude patterns, content-type filter, `min_score` |
| Self-host | Firecrawl, WaterCrawl | Dockerfile with health check, compose with Redis and worker, API keys, per-key rate limits, `/v1/usage`, Render/Fly/Railway templates |

## Where DungScraper still loses

- Proxies are bring-your-own. There is no bundled, managed or residential pool, so heavy bot protection (Cloudflare Turnstile, DataDome) can still block it.
- `research` ranks and cites passages. It is not an LLM agent that plans, clicks and fills forms like Firecrawl's `/agent`, and it has no embedding or sufficiency model like Crawl4AI's adaptive crawler.
- Crawl4AI has more deep-crawl scorers and filters than the four heuristics here.
- "Hosted" means self-hosted: the image, compose file and platform templates are included, but nobody runs a service for you. They were checked for consistency and the auth and limits were exercised live, but a `docker build` and platform deploys were not run from this repository.
- Rate limits and usage counters are in memory, per process.
- Single-machine throughput.
- Young project with fewer users and less mileage than the others.

## Where it wins

- Zero setup cost: no key, no account, no Docker, no credits.
- Research with citations and no model: free, repeatable, and every claim has a source URL.
- Everything stays on your machine.
- Free structured reading for Reddit, RSS, skelbiu.lt and autoplius.lt, with robots.txt respected and seller phone numbers never extracted.
- Token-budgeted reading and LLM-free extraction, which cuts agent cost.

## Sources

firecrawl.dev/pricing and docs.firecrawl.dev (billing, change tracking, agent, MCP), watercrawl.dev and github.com/watercrawl/watercrawl-mcp, docs.crawl4ai.com (proxy-security, adaptive-crawling), jina.ai/reader, spider.cloud pricing, scrapegraphai.com pricing, apify.com pricing.
