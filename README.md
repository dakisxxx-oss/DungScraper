# DungScraper

Self-hosted web scraping and crawling for AI agents. One service turns a URL into Markdown, crawls a site, lists its URLs, searches the web, and extracts JSON.

DungScraper is Python on purpose. [Trafilatura](https://github.com/adbar/trafilatura) is still the strongest open-source main-content extractor, and it is Python-only. The same language then covers FastAPI, Playwright, PyMuPDF, the MCP server, the SDK, and the CLI, so there is one process model to deploy.

## Quickstart

Docker, from this directory:

```bash
docker compose up --build
```

That starts Redis, the `dungscraper` API, and `dungscraper-worker`. The image is `dungscraper`. The API listens on port **8741**. The dev key is `dungscraper-dev-key`.

Without Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
dungscraper serve
```

Open the playground at [http://127.0.0.1:8741](http://127.0.0.1:8741) and the API docs at [http://127.0.0.1:8741/docs](http://127.0.0.1:8741/docs). With no `DUNGSCRAPER_API_KEYS` set, auth is off so you can try it immediately. `docker compose` sets a key.

```bash
curl -s http://127.0.0.1:8741/v1/scrape \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com"}' | jq -r .data.markdown
```

Health check: `GET /health` (no API key required).

## What you can call

| | |
| --- | --- |
| `POST /v1/scrape` | One URL to Markdown. HTTP first, Chromium when the page is a script shell. |
| `POST /v1/crawl` | A site, with depth, limit, path filters, sitemap, robots.txt, dedupe, and an optional webhook. |
| `GET /v1/crawl/{id}` | Poll a crawl. `DELETE` cancels it. |
| `POST /v1/map` | URLs from sitemaps plus links on the entry page. Optional `search` filter. |
| `POST /v1/search` | Search, then scrape the hits. |
| `POST /v1/extract` | JSON from a page or from text, checked against a JSON Schema. |
| `POST /v1/chunk` | Heading-aware chunks for retrieval. |

Input accepts snake_case and the camelCase names agents already send (`onlyMainContent`, `maxDepth`, `scrapeOptions`, `waitFor`). `waitFor` is milliseconds. A CSS selector is `wait_for`.

Responses are snake_case. Errors look like:

```json
{"success": false, "error": {"code": "robots_disallowed", "message": "…"}}
```

### Scrape

```bash
curl -s http://127.0.0.1:8741/v1/scrape \
  -H 'content-type: application/json' \
  -d '{
    "url": "https://en.wikipedia.org/wiki/Web_scraping",
    "formats": ["markdown", "metadata", "links"],
    "render": "auto"
  }'
```

`formats` can include `markdown`, `html` (cleaned), `raw_html`, `links`, `metadata`, `screenshot`, and `chunks`.

`render` is `auto` (default), `http`, or `browser`. Auto uses Chromium when the visible text is a shell and the real copy is in a script, or when you ask for a screenshot, a wait, or actions. PDF and DOCX URLs are downloaded and converted. A browser user agent is not used for those files, because some hosts answer it with a challenge page.

```json
{
  "url": "https://example.com",
  "actions": [
    {"type": "click", "selector": "#more"},
    {"type": "wait", "ms": 500},
    {"type": "type", "selector": "input[name=q]", "text": "dungscraper"},
    {"type": "scroll", "pixels": 1200}
  ],
  "headers": {"Accept-Language": "en"},
  "cookies": {"session": "abc"},
  "proxy": "http://user:pass@proxy.example:8080"
}
```

Metadata includes title, description, language, canonical URL, and Open Graph tags. Nav, cookie banners, and obvious ads are removed. Tables, code blocks, links, and images in the article are kept.

### Crawl

Crawls are jobs. `"wait": true` blocks until the job finishes (or `timeout_seconds` elapses, in which case you poll). Without Redis, the API runs the crawl itself. With `DUNGSCRAPER_REDIS_URL` and `DUNGSCRAPER_CRAWL_MODE=queue`, the worker process does it. Compose uses the queue.

```bash
curl -s http://127.0.0.1:8741/v1/crawl \
  -H 'content-type: application/json' \
  -d '{
    "url": "https://quotes.toscrape.com/",
    "limit": 10,
    "maxDepth": 2,
    "excludePaths": ["/tag/**"],
    "useSitemap": true,
    "dedupe": true,
    "wait": true,
    "webhook": "https://example.com/hooks/dungscraper"
  }'
```

Path filters are globs (`/blog/**`) or `re:` regular expressions. The same registrable host is kept (`www` is ignored). Subdomains need `allow_subdomains`. Tracking parameters are stripped. Near-duplicate pages are dropped with a 64-bit simhash. `robots.txt` is respected unless you pass `respect_robots: false` for a site you are allowed to fetch. Private and link-local addresses are blocked unless `DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS=true`.

### Map and search

```bash
curl -s http://127.0.0.1:8741/v1/map \
  -H 'content-type: application/json' \
  -d '{"url":"https://docs.python.org/3/","search":"tutorial","limit":50}'
```

```bash
curl -s http://127.0.0.1:8741/v1/search \
  -H 'content-type: application/json' \
  -d '{"query":"self hosted web crawler","limit":3,"provider":"auto"}'
```

`provider=auto` uses Brave, then Serper, then SearXNG, when those are configured, and otherwise DuckDuckGo's HTML endpoint, which needs no key. DuckDuckGo often rate-limits datacenter IPs. Point `DUNGSCRAPER_SEARXNG_URL` at your own SearXNG for a stable index.

### Extract and chunk

Extract needs an OpenAI-compatible chat endpoint. Ollama works:

```bash
export DUNGSCRAPER_LLM_BASE_URL=http://127.0.0.1:11434/v1
export DUNGSCRAPER_LLM_MODEL=llama3.1
export DUNGSCRAPER_LLM_API_KEY=ollama
```

```bash
curl -s http://127.0.0.1:8741/v1/extract \
  -H 'content-type: application/json' \
  -d '{
    "url": "https://quotes.toscrape.com/",
    "prompt": "List the first three quotes and their authors.",
    "schema": {
      "type": "object",
      "properties": {
        "quotes": {"type": "array", "items": {"type": "object"}}
      },
      "required": ["quotes"]
    }
  }'
```

The model is asked again once if the JSON does not validate. With no key and the default OpenAI base URL, the API returns `llm_not_configured` instead of pretending.

Chunking is local and does not call a model. Tokens are estimated at about four characters each, which is stable and does not download a tokenizer.

```bash
curl -s http://127.0.0.1:8741/v1/chunk \
  -H 'content-type: application/json' \
  -d '{"text":"# Title\n\nParagraph.","max_tokens":200,"overlap_tokens":20}'
```

## SDK and CLI

```python
from dungscraper import DungScraper

with DungScraper("http://127.0.0.1:8741", api_key="dungscraper-dev-key") as client:
    page = client.scrape("https://example.com")
    print(page["markdown"])
    job = client.crawl("https://quotes.toscrape.com/", limit=5, wait=True)
    print(job["completed"], "pages")
```

```bash
dungscraper scrape https://example.com
dungscraper scrape https://example.com --local    # no server, runs in this process
dungscraper crawl https://quotes.toscrape.com/ --limit 5
dungscraper map https://example.com
dungscraper search "web scraping markdown"
dungscraper chunk article.md --max-tokens 400
dungscraper worker                                 # only when Redis is configured
```

## MCP

The API serves streamable HTTP at `http://127.0.0.1:8741/mcp`. Send the same bearer token you use for the REST API when auth is on.

Stdio is the usual setup for Claude Desktop and Cursor. The MCP process calls the API, so the browser stays in the server and the agent does not need Playwright.

`examples/claude_desktop.json` and `examples/cursor.mcp.json`:

```json
{
  "mcpServers": {
    "dungscraper": {
      "command": "dungscraper",
      "args": ["mcp"],
      "env": {
        "DUNGSCRAPER_BASE_URL": "http://127.0.0.1:8741",
        "DUNGSCRAPER_API_KEY": "dungscraper-dev-key"
      }
    }
  }
}
```

Tools: `scrape`, `crawl`, `crawl_status`, `map_site`, `search_web`, `extract`, `chunk_text`.

If `DUNGSCRAPER_BASE_URL` is unset, `dungscraper mcp` scrapes in-process instead. A standalone streamable HTTP server is `dungscraper mcp --transport streamable-http --port 8765`.

## Configuration

See `.env.example`. The important ones:

| Variable | Default | |
| --- | --- | --- |
| `DUNGSCRAPER_API_KEYS` | empty | Comma-separated. Empty disables auth. |
| `DUNGSCRAPER_REDIS_URL` | empty | Cache and crawl queue. |
| `DUNGSCRAPER_CRAWL_MODE` | `auto` | `inline` without Redis, `queue` with it. |
| `DUNGSCRAPER_CACHE_TTL_SECONDS` | `3600` | `DUNGSCRAPER_CACHE_ENABLED=false` turns it off. |
| `DUNGSCRAPER_RESPECT_ROBOTS_DEFAULT` | `true` | Per-request `respect_robots` overrides. |
| `DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS` | `false` | Blocks loopback, link-local, and private DNS answers. |
| `DUNGSCRAPER_LLM_BASE_URL` | OpenAI | Any `/v1/chat/completions` server. |
| `DUNGSCRAPER_BRAVE_API_KEY` | | Optional search provider. |
| `DUNGSCRAPER_SERPER_API_KEY` | | Optional search provider. |
| `DUNGSCRAPER_SEARXNG_URL` | | Optional search provider. |

Retries cover timeouts and HTTP 429/502/503/504, with exponential backoff. Logs are JSON on stdout. Every response includes `x-request-id`.

## Benchmark

Ten public URLs, one uncached scrape each, default `render=auto`, measured in this environment. Firecrawl was not installed or called here, so this is not a comparison with it. Full table: [benchmark/RESULTS.md](benchmark/RESULTS.md). Re-run with `python benchmark/bench.py`.

| URL | Render | ms | Words | Headings | Links | Boilerplate hits |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| example.com | http | 258 | 28 | 1 | 0 | 0 |
| Wikipedia, Web scraping | http | 594 | 5030 | 19 | 400 | 0 |
| Python tutorial, appetite | http | 189 | 809 | 1 | 6 | 0 |
| httpbin.org/html | http | 45 | 606 | 1 | 0 | 0 |
| quotes.toscrape.com | http | 130 | 271 | 2 | 52 | 0 |
| quotes.toscrape.com/js | browser | 670 | 237 | 1 | 2 | 0 |
| news.ycombinator.com | http | 496 | 1039 | 0 | 194 | 0 |
| RFC 9110 | http | 3588 | 68902 | 312 | 4713 | 0 |
| Mozilla manifesto | http | 463 | 1075 | 8 | 23 | 0 |
| W3C dummy PDF | http | 489 | 6 | 1 | 0 | 0 |

What that shows here: static pages stay on HTTP and come back in a few hundred milliseconds. The JavaScript quotes page is detected and rendered (237 words, including the quotes, instead of the 4-word shell). Hacker News keeps its story links. The dummy PDF comes back as `Dummy PDF file`. Cookie and newsletter boilerplate did not show up in any of the ten. RFC 9110 is the slow one because the document is large, not because of the browser. Link counts include in-page anchors, which is why the RFC number is high.

## Tests

```bash
pytest
```

Unit tests cover extraction, chunking, simhash, robots, sitemaps, SSRF checks, search parsers, PDF/DOCX, schema repair, and the API. A local site test crawls with robots.txt, a sitemap, path filters, duplicate skipping, and a webhook. Integration tests hit example.com, the Wikipedia article on web scraping, `quotes.toscrape.com/js/` in a real browser, and the W3C dummy PDF.

## Known gaps

- No bot-challenge solving and no stealth browser. A host that requires a JavaScript challenge still returns the challenge page.
- Search quality depends on the provider. DuckDuckGo HTML is the keyless default and can return an empty or blocked page.
- Extract does nothing useful until you set an OpenAI-compatible model.
- Duplicate detection is textual, not semantic. Two paraphrases of the same article can both be kept.
- One worker, one Redis. A crashed worker leaves a `running` job until the 24 hour TTL.
- Map does not execute JavaScript. Crawl depth is what finds links that only exist after render, and only when `render` is `browser` or `auto` decides a page needs it.
- Screenshots are JPEG base64 on the JSON response, not stored anywhere.
- Token counts are a character estimate, not a model tokenizer.
- The browser is Chromium only. Proxy support is one URL, not a rotating pool.

## License

MIT.
