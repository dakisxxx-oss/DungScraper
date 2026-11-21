# DungScraper

Self-hosted web scraping and crawling for AI agents. One tool turns a URL into Markdown, crawls a site, lists its URLs, searches the web, finds public business contacts and job links, and extracts JSON. It runs on your computer and needs no account or API key.

**Why use it**

- **One command to install.** `install.ps1` on Windows, `install.sh` on macOS and Linux.
- **Private by default.** Runs on your computer, listens on 127.0.0.1, no account, no API key.
- **Clean Markdown.** Main content only, with tables, code, links and images kept.
- **Handles hard pages.** HTTP first, a real Chromium browser when a page needs JavaScript.
- **Made for agents.** REST API, Python SDK, CLI and an MCP server for Claude and other agents.
- **Public company info.** Business emails with the page where each was found, and job links tagged junior and remote.
- **Polite.** Respects robots.txt.

Version 0.3.0 keeps the 0.2.0 contacts and jobs tools and reads Reddit with no API key.

## Kas naujo

0.3.0 sujungia abu darbus. Reddit veikia be API raktų (viešas JSON, tada RSS, tada old.reddit HTML). Kontaktai ir darbo skelbimai lieka: `contacts`, `jobs`, `find_contacts_tool`, `find_jobs_tool`. Taip pat `read_feed`, `outline` apkarpytam `scrape`, ir `Crawl-delay`.

## Install in 1 minute

Needs Python 3.11 or newer and git. No admin rights.

```powershell
git clone https://github.com/dakisxxx-oss/DungScraper.git
cd DungScraper
.\install.ps1
```

On macOS or Linux run `./install.sh` instead of `.\install.ps1`. Add `-NoBrowser` (Windows) or `--no-browser` (macOS, Linux) to skip the 150 MB Chromium download; plain HTTP scraping still works, JavaScript-only pages do not. The installers are safe to run again. If PowerShell blocks the script, run `powershell -ExecutionPolicy Bypass -File .\install.ps1`.

Other ways: `pip install git+https://github.com/dakisxxx-oss/DungScraper.git`, then `python -m playwright install chromium`, or Docker (below). `dungscraper install-claude` is optional and adds the tools to Claude Desktop ([INSTALL_CLAUDE.md](INSTALL_CLAUDE.md)).

## Use it

`--local` runs everything in your own process. No server is needed.

```powershell
.\.venv\Scripts\dungscraper.exe scrape --local https://example.com
.\.venv\Scripts\dungscraper.exe search "python junior remote jobs" --limit 5 --no-scrape --local
.\.venv\Scripts\dungscraper.exe contacts https://example.com --depth 1 --local
.\.venv\Scripts\dungscraper.exe jobs https://example.com/careers --local
```

(On macOS and Linux the program is `.venv/bin/dungscraper`. After `.\.venv\Scripts\Activate.ps1` or `source .venv/bin/activate` you can type just `dungscraper`.) `map` and `crawl` take `--local` too. Without `--local` the commands call the API, which you start with `.\run.ps1` or `./run.sh`; the playground is then at <http://127.0.0.1:8741>. The CLI prints only warnings and errors by default. Use `--log-level INFO` for more.

### Contacts and jobs

`contacts <url>` collects **public business** email addresses and careers links from a page, and with `--depth 1` (default) also from the contact, imprint, careers and about pages it links to. It reads `mailto:` links, visible text, schema.org JSON-LD, Cloudflare-protected links, and simple forms like `name [at] host [dot] com`. Each email comes with the page URL and a text snippet where it was found, a rough kind (`jobs`, `general`, `sales`, `support`, `press`, `legal`, `other`), and whether it matches the site's domain. Image names such as `logo@2x.png`, placeholders such as `you@example.com`, error-tracker keys and `noreply` addresses are dropped. robots.txt is respected. API: `POST /v1/contacts`.

`jobs <url>` lists links on a careers page that look like single job postings (JSON-LD `JobPosting`, job-board hosts such as Greenhouse, Lever or Workable, `/jobs/<slug>` paths, apply links). Entries get `junior` and `remote` tags when the text says so. Pages that list roles as collapsed sections with an apply-by-email address are reported with `apply_email`. API: `POST /v1/jobs`.

Output and checks (0.2.0):

- `--format compact` prints one email or one job per line. `--format csv` prints a header and one row per item. The default `json` is the full result.
- `contacts --check-mx` looks up whether each email domain has a mail server and sets `mx` to `true`, `false` or `null` (unknown). It makes one DNS lookup per domain.
- `jobs --render browser` loads the page in Chromium first. Use it for career pages that fill in their jobs with JavaScript.
- Roles listed as collapsed sections get their own link, `<page>#<role-name>`, so each row is distinct.
- Both are MCP tools too: `find_contacts_tool` and `find_jobs_tool`.

Use these for public company information only. See [SECURITY.md](SECURITY.md) for responsible use.

DungScraper is Python on purpose. [Trafilatura](https://github.com/adbar/trafilatura) is still the strongest open-source main-content extractor, and it is Python-only. The same language then covers FastAPI, Playwright, PyMuPDF, the MCP server, the SDK, and the CLI, so there is one process model to deploy.

## Docker and manual setup

Docker, from this directory:

```bash
docker compose up --build
```

That starts Redis, the `dungscraper` API, and `dungscraper-worker`. The image is `dungscraper`. The API listens on `127.0.0.1:8741` (this computer only). The dev key is `dungscraper-dev-key`; change `DUNGSCRAPER_API_KEYS` before you publish the port anywhere else.

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
| `POST /v1/scrape` | One URL to Markdown. HTTP first, then Chromium on script shells, empty bodies, and challenge pages. |
| `POST /v1/batch/scrape` | Up to 20 URLs. A failure on one URL does not fail the batch. |
| `POST /v1/crawl` | A site, with depth, limit, path filters, sitemap, robots.txt, dedupe, and an optional webhook. |
| `GET /v1/crawl/{id}` | Poll a crawl. `DELETE` cancels it. |
| `POST /v1/map` | URLs from sitemaps plus links. `render=browser` reads JavaScript navigation. |
| `POST /v1/search` | Search, then scrape the hits. Falls through providers when one rate-limits. |
| `POST /v1/contacts` | Public business emails (with where each was found) and careers links from a page or a few linked pages. |
| `POST /v1/jobs` | Links to job postings on a careers page, tagged junior and remote. |
| `POST /v1/extract` | JSON from a page or from text, checked against a JSON Schema. |
| `POST /v1/chunk` | Heading-aware chunks for retrieval. |
| `POST /v1/diff` | Unified diff of markdown against a baseline or the previous snapshot. |
| `POST /v1/crawl/{id}/resume` | Continue a crawl from the frontier saved with each page. |

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

`render` is `auto` (default), `http`, or `browser`. Auto uses HTTP first, then Chromium when the page is a script shell, the body is empty, or the status is a block or challenge (403, 429, interstitial text). Screenshots, waits, and actions also use the browser. The browser hides the automation flag and sends a normal desktop fingerprint. PDF and DOCX URLs stay on HTTP with a plain user agent, because some hosts answer a Chrome user agent with a challenge page.

`onlyMainContent` defaults to true: nav, cookie banners, and obvious ads are removed, and the better of Trafilatura, Readability, and the `<article>` element is kept. Tables and code blocks dropped by one extractor are copied back from another. Set it to false for the full page. Links and image URLs are absolute.

`DUNGSCRAPER_PROXY_URLS` is an optional comma-separated list. Requests rotate through it. A per-request `proxy` overrides the list.

### Reddit and other site APIs

Reddit works with no API key. A subreddit listing (`hot`, `new`, `top`, `rising`), a post and its comments, a search, or a user page is read in this order:

1. The official OAuth API, only when `DUNGSCRAPER_REDDIT_CLIENT_ID` and `DUNGSCRAPER_REDDIT_CLIENT_SECRET` are both set. If that call fails, the public chain below is the fallback.
2. Public JSON on `old.reddit.com`, then `www.reddit.com`, then `np.reddit.com`. Requests share a gap (`DUNGSCRAPER_REDDIT_MIN_INTERVAL_SECONDS`, default 1.25). HTTP 429 waits for `Retry-After` up to 8 seconds and retries once.
3. The same resource as RSS.
4. `old.reddit.com` HTML.

Optional keys, if you already have a script app:

```bash
export DUNGSCRAPER_REDDIT_CLIENT_ID=your_id
export DUNGSCRAPER_REDDIT_CLIENT_SECRET=your_secret
export DUNGSCRAPER_REDDIT_USERNAME=your_username
export DUNGSCRAPER_REDDIT_PASSWORD=your_password
export DUNGSCRAPER_REDDIT_USER_AGENT='dungscraper/0.1 by yourname'
```

The same adapter hook reads other allowed APIs and falls back to a normal fetch when they fail: Wikipedia's REST HTML (with a contact URL in the user agent), GitHub's REST API (`DUNGSCRAPER_GITHUB_TOKEN` optional), the arXiv Atom API, Hacker News' Firebase API, and YouTube captions. Captions come from the public timedtext list, and from the watch page when that list is empty. `max_comment_depth` limits Reddit and Hacker News threads.

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
  -d '{"url":"https://docs.python.org/3/","search":"tutorial","limit":50,"render":"auto"}'
```

`render=auto` fetches the entry page over HTTP and switches to the browser when that page is a JavaScript shell or a challenge. `render=browser` always renders it, which is what you want for a nav that only exists after scripts run.

```bash
curl -s http://127.0.0.1:8741/v1/batch/scrape \
  -H 'content-type: application/json' \
  -d '{"urls":["https://example.com","https://httpbin.org/html"],"concurrency":2}'
```

```bash
curl -s http://127.0.0.1:8741/v1/search \
  -H 'content-type: application/json' \
  -d '{"query":"self hosted web crawler","limit":3,"provider":"auto"}'
```

`provider=auto` tries Brave, then Serper, then SearXNG, when those are configured, and otherwise DuckDuckGo's HTML endpoint, which needs no key. If DuckDuckGo rate-limits or returns nothing, the next configured provider is tried. The response `notes` field says which provider was used. Point `DUNGSCRAPER_SEARXNG_URL` at your own SearXNG for a stable index. Set `"fallback": false` to stick to one provider.

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

The model is asked again once if the JSON does not validate. Pass `urls` (several pages) or `content` (markdown you already have), plus a `prompt`, a `schema`, or both. With no key and the default OpenAI base URL, a prompt or schema returns `llm_not_configured` and does not invent a result.

`mode=selector` does not call a model. `selectors` maps a field name to a CSS selector, `css:...`, or `xpath:...`. With no selectors, selector mode returns a heuristic title, headings, and first long paragraph.

```bash
curl -s http://127.0.0.1:8741/v1/extract \
  -H 'content-type: application/json' \
  -d '{"content":"<h1>Ada</h1><p class=\"bio\">Notes</p>","mode":"selector","selectors":{"name":"h1"}}'
```

`POST /v1/diff` compares `current` markdown to `baseline`, or scrapes `url` and compares it with the last snapshot stored for that URL.

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
dungscraper install-claude
dungscraper chunk article.md --max-tokens 400
dungscraper worker                                 # only when Redis is configured
```

## MCP

Stdio needs no Docker and no running API. `dungscraper mcp` fetches in this process. Set `DUNGSCRAPER_BASE_URL` only when you want the stdio process to call an API that is already up.

```bash
python -m dungscraper mcp
uvx --from /path/to/this/repo dungscraper mcp
pipx run --spec /path/to/this/repo dungscraper mcp
```

`uvx --from dungscraper` and `pipx run --spec dungscraper` are the same commands once the package is published to an index. Until then, pass the repo path or a git URL as the spec.

Install it into Claude Desktop on macOS or Windows. This writes `claude_desktop_config.json`, copies the previous file to `claude_desktop_config.json.bak-<timestamp>`, and does not restart Claude. Quit Claude and open it again to load the server.

```bash
dungscraper install-claude
dungscraper install-claude --launcher uvx --from /path/to/this/repo
dungscraper install-claude --launcher pipx --dry-run
```

`--launcher auto` uses the `dungscraper` executable on `PATH`. On Windows it writes the Microsoft Store config when `%LOCALAPPDATA%\Packages\Claude_*` exists (`LocalCache\Roaming\Claude\claude_desktop_config.json`), and otherwise `%APPDATA%\Claude\claude_desktop_config.json`. macOS uses `~/Library/Application Support/Claude/claude_desktop_config.json`. Other MCP servers in the file are left alone.

The API also serves streamable HTTP at `http://127.0.0.1:8741/mcp`. Send the same bearer token you use for the REST API when auth is on.

Tools: `scrape`, `batch_scrape`, `crawl`, `crawl_status`, `map_site`, `search_web`, `extract`, `chunk_text`, `reddit`, `read_feed`, `find_contacts_tool`, `find_jobs_tool`.

`reddit` reads a subreddit, post, search, or user with no API key. `read_feed` reads RSS or Atom. `find_contacts_tool` and `find_jobs_tool` are the 0.2.0 company tools. `scrape` returns at most 12,000 characters of markdown. The `truncation` object has `next_offset`; call again with that `offset` for the next slice. When the page has headings, `outline` lists each one with the offset to jump there. Crawl pages and batch results are clipped the same way so a long site does not fill the model context. `examples/cursor.mcp.json` shows the Cursor stdio entry.

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

### Harder sites

Twenty URLs (news, docs, a SPA, e-commerce, Q&A, Reddit, Medium), same rule both times: at least 20 words, and a short page that is still a bot interstitial does not count. Raw rows are in `benchmark/hard-before.json` and `benchmark/hard-after.json`.

| | Before | After |
| --- | ---: | ---: |
| Useful page | 14/20 | 15/20 |
| Refused because robots.txt disallows the bot | 3 | 3 |
| Still a challenge page | 3 | 2 |

The page that moved in the second round was the Stack Overflow question on `yield`: a 4-word "Just a moment" interstitial became about 16,000 words that include the answer. AP News and Medium stayed on a challenge page. Reuters and both Reddit URLs were refused because their robots.txt disallows `DungScraperBot`.

The third round stayed **15/20**. Wikipedia, GitHub, Hacker News, and arXiv come from their public APIs. Reddit with no app credentials returns `reddit_not_configured` and does not fetch HTML. Reuters is still a robots refusal. AP News and Medium still stop on a security check.

Round 4 added the free challenge ladder and measured the same set again (`benchmark/hard-round4.json`): still **15/20** from this VPS. curl_cffi ran on the Cloudflare pages and was still blocked. Patchright, Camoufox, and a Byparr sidecar were not installed here, so the browser step was the bundled Playwright fallback. AP News and Medium remain challenge pages. Stack Overflow's markdown in that run opens on the question title. A residential or home IP, plus `pip install 'dungscraper[stealth]'` or Byparr, is the setup this ladder is meant for. It will not reliably beat an interactive image captcha.

## Free anti-bot stack

`render=auto` stays on plain HTTP until the response is a challenge page (Cloudflare, DataDome, Akamai, PerimeterX, or a short 403/429 shell). Only then does it climb:

1. `http` — httpx
2. `curl` — [curl_cffi](https://github.com/lexiforest/curl_cffi) Chrome TLS and HTTP/2 impersonation
3. `patchright` — stealth Chromium. If it is not installed, the bundled Playwright browser is used and the response says `tier_unavailable:patchright`
4. `camoufox` — Firefox, optional
5. `flaresolverr` — a Byparr or FlareSolverr sidecar, only when `DUNGSCRAPER_FLARESOLVERR_URL` is set. Clearance cookies and the matching user agent are reused for later curl_cffi requests to that domain
6. `seleniumbase` — Turnstile checkbox via `solve_captcha()`, only when `DUNGSCRAPER_SOLVE_CAPTCHA=true`

JavaScript shells that are not block pages, such as `quotes.toscrape.com/js/`, still go straight to the browser. They do not run this ladder. `render=http` never escalates. The tier that returned the page is `fetch_tier`. A page that is still a challenge sets `block_reason` to the vendor name.

```bash
pip install 'dungscraper[stealth]'
python -m patchright install chromium
```

`curl_cffi` is enough for the second tier. Patchright, Camoufox, and SeleniumBase are the heavy extras. A missing package skips that tier with `tier_unavailable` instead of crashing the scrape.

Byparr speaks the FlareSolverr `/v1` API and is free:

```bash
docker compose --profile antibot up
export DUNGSCRAPER_FLARESOLVERR_URL=http://127.0.0.1:8191/v1
```

Inside Compose the sidecar is `http://byparr:8191/v1`. Leave the variable empty to skip it.

`DUNGSCRAPER_PROXIES` is an optional comma-separated list you supply. Each site sticks to one proxy for the process. The default is a direct connection from whatever IP the process uses. DungScraper does not ship a proxy list or Tor.

Medium articles, profiles, and tags that come back as a challenge fall back to the public RSS feed (`/feed/@author`, `/feed/publication`, `/feed/tag/name`). Other sites are checked for `<link rel="alternate" type="application/rss+xml">`.

Limits, plainly: no free tool reliably solves an interactive image captcha. Clearance cookies are tied to the egress IP that minted them, so a datacenter or VPS address is blocked far more often than a home connection. Some sites' terms forbid this. `robots.txt` stays on. Reddit is read through public JSON, RSS, or old.reddit HTML, and through the official API only when you set keys.

## Tests

```bash
pytest
```

Unit tests cover extraction, chunking, simhash, robots, sitemaps, SSRF checks, search parsers, PDF/DOCX, schema repair, and the API. A local site test crawls with robots.txt, a sitemap, path filters, duplicate skipping, and a webhook. Integration tests hit example.com, the Wikipedia article on web scraping, `quotes.toscrape.com/js/` in a real browser, and the W3C dummy PDF.

## Known gaps

- Managed challenges still win on many datacenter IPs. The free ladder (curl_cffi, Patchright, Camoufox, Byparr, opt-in SeleniumBase) does not reliably solve interactive image captchas. A response that is still an interstitial sets `block_reason` to the vendor name. It works better from a home IP than from a VPS.
- Sites that disallow `DungScraperBot` in robots.txt are not fetched, except where a separate adapter reads an API or feed. Reuters stays blocked unless its robots.txt allows the bot. `respect_robots: false` is for a site you are allowed to read, not a default. Reddit listings use public JSON, RSS, then old.reddit HTML. A datacenter IP may still be blocked.
- Signup walls and ad prompts can sit in front of real copy. Stack Overflow's answer is in the markdown, under a signup form. CNN's scrape opens on a feedback prompt.
- Search still depends on a provider. With no Brave, Serper, or SearXNG key, DuckDuckGo is the only option, and it can rate-limit this host.
- Extract returns `llm_not_configured` until you set an OpenAI-compatible model. It does not guess.
- Duplicate detection is textual, not semantic.
- One worker, one Redis. A crashed worker leaves a `running` job until the 24 hour TTL.
- Screenshots are JPEG base64 on the JSON response, not stored anywhere.
- Token counts are a character estimate, not a model tokenizer.
- The browser is Chromium only. Proxy rotation is a list of URLs you supply, not a paid pool.

## License

MIT.
