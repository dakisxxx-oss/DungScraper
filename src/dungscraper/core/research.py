"""Research a question across the web with no LLM: search, read, follow promising links, cite.

1. Search (keyless DuckDuckGo by default, or whichever provider is configured) and take the top hits.
2. Fetch pages in rounds. Each page is split into sections.
3. A page that mentions enough of the question's words has its most relevant links queued for a later round.
4. Stop at max_pages or max_seconds. Rank every section from every page together with BM25 and return the
   best passages, each with its source, plus a short cited brief.
5. Only when synthesize is on and an API key is set, ask a model to write an answer from those passages.
   The passages are returned either way, and a failed model call never fails the research.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx

from dungscraper.config import Settings
from dungscraper.core.crawl_scoring import keyword_score, page_relevance
from dungscraper.core.focus import _terms, bm25_rank, split_sections
from dungscraper.core.scrape import scrape_url
from dungscraper.core.search import search_web
from dungscraper.core.urls import dedupe_key, extension_skipped, host_key
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import ResearchRequest, ScrapeRequest, SearchRequest

log = get_logger("dungscraper.research")

FOLLOW_PER_PAGE = 3
MAX_PASSAGES_PER_SOURCE = 3
PASSAGE_CHARS = 1100


def _overlap(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    have = set(_terms(text))
    return sum(1 for term in terms if term in have) / len(terms)


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_CHROME_HEADINGS = ("discussion", "comments", "share", "subscribe", "related", "newsletter", "sign up", "cookie", "menu")


def _usable(section: dict[str, Any]) -> bool:
    """Skip link lists, share buttons and other page chrome that match words but answer nothing."""
    text = section["text"]
    plain = _LINK.sub(lambda match: match.group(1), text)
    if len(plain.split()) < 12:
        return False
    if len(plain) < 0.5 * len(text):  # mostly URLs
        return False
    heading = (section["heading"] or "").lower()
    return not any(word in heading for word in _CHROME_HEADINGS)


def _fingerprint(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()[:160]


async def research(
    request: ResearchRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache=None,
    browser=None,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + request.max_seconds
    terms = list(dict.fromkeys(_terms(request.question)))
    notes: list[str] = []
    frontier: list[dict[str, Any]] = []
    seen: set[str] = set()

    def queue(url: str, priority: float, depth: int, via: str) -> None:
        key = dedupe_key(url)
        if key in seen or extension_skipped(url):
            return
        seen.add(key)
        frontier.append({"url": url, "priority": priority, "depth": depth, "via": via})

    for index, url in enumerate(request.urls):
        queue(url, 2.0 - index * 0.01, 0, "seed")

    provider = None
    if request.search:
        try:
            found = await search_web(
                SearchRequest(
                    query=request.question,
                    limit=min(10, request.search_limit),
                    provider=request.provider,
                    scrape=False,
                ),
                settings=settings,
                client=client,
            )
            provider = found.provider
            notes.extend(found.notes)
            for rank, hit in enumerate(found.results):
                relevance = _overlap(f"{hit.title or ''} {hit.snippet or ''}", terms)
                queue(hit.url, 0.5 / (rank + 1) + 0.5 * relevance, 0, f"search:{rank + 1}")
        except DungScraperError as exc:
            if not request.urls:
                raise
            notes.append(f"search skipped: {exc.message}")
    elif not request.urls:
        raise DungScraperError("invalid_request", "Give a search or at least one seed URL.", status=400)

    corpus: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    failed = 0
    stopped = "exhausted"
    while frontier:
        remaining = deadline - time.monotonic()
        if len(sources) + failed >= request.max_pages:
            stopped = "page_budget"
            break
        if remaining <= 0.5:
            stopped = "time_budget"
            break
        batch: list[dict[str, Any]] = []
        room = request.max_pages - len(sources) - failed
        while frontier and len(batch) < min(request.concurrency, room):
            best = max(range(len(frontier)), key=lambda i: (frontier[i]["priority"], -i))
            batch.append(frontier.pop(best))

        async def fetch(item: dict[str, Any]):
            try:
                data = await asyncio.wait_for(
                    scrape_url(
                        ScrapeRequest(url=item["url"], formats=["markdown", "links", "metadata"]),
                        settings=settings,
                        client=client,
                        cache=cache,
                        browser=browser,
                    ),
                    timeout=max(1.0, remaining),
                )
                return item, data, None
            except asyncio.TimeoutError:
                return item, None, "timeout"
            except DungScraperError as exc:
                return item, None, f"{exc.code}: {exc.message}"
            except Exception as exc:  # noqa: BLE001
                log.exception("research fetch failed", extra={"data": {"url": item["url"]}})
                return item, None, str(exc)

        for item, data, error in await asyncio.gather(*(fetch(item) for item in batch)):
            if data is None or not (data.markdown or "").strip():
                failed += 1
                notes.append(f"{item['url']}: {error or 'empty page'}")
                continue
            source_id = len(sources) + 1
            title = (data.metadata.title if data.metadata else None) or data.url
            sources.append(
                {"id": source_id, "url": data.url, "title": title, "depth": item["depth"], "found_via": item["via"], "passages": 0}
            )
            for section in split_sections(data.markdown or ""):
                corpus.append({**section, "source": source_id})
            if request.follow_links and item["depth"] < request.max_depth:
                if page_relevance(data.markdown or "", terms) >= 0.34:
                    candidates = []
                    for link in data.links or []:
                        score = keyword_score(link.url, link.text, terms)
                        if score <= 0:
                            continue
                        same = host_key(link.url) == host_key(data.url)
                        candidates.append((score + (0.15 if same else 0.0), link))
                    candidates.sort(key=lambda pair: -pair[0])
                    for score, link in candidates[:FOLLOW_PER_PAGE]:
                        queue(link.url, 0.3 * score, item["depth"] + 1, f"link:{source_id}")

    scores = bm25_rank(corpus, request.question)
    for index, section in enumerate(corpus):
        if not _usable(section):
            scores[index] = 0.0
        else:
            # Passages that contain more of the question's distinct words rank higher.
            scores[index] *= 0.4 + 0.6 * _overlap(f"{section['heading']} {section['text']}", terms)
    order = sorted(range(len(corpus)), key=lambda i: (-scores[i], i))
    passages: list[dict[str, Any]] = []
    taken: dict[int, int] = {}
    prints: set[str] = set()
    for index in order:
        if scores[index] <= 0 or len(passages) >= request.max_passages:
            break
        section = corpus[index]
        fingerprint = _fingerprint(section["text"])
        if fingerprint in prints or taken.get(section["source"], 0) >= MAX_PASSAGES_PER_SOURCE:
            continue
        prints.add(fingerprint)
        taken[section["source"]] = taken.get(section["source"], 0) + 1
        text = section["text"]
        if len(text) > PASSAGE_CHARS:
            text = text[:PASSAGE_CHARS].rsplit(" ", 1)[0] + " …"
        source = sources[section["source"] - 1]
        source["passages"] += 1
        passages.append(
            {
                "rank": len(passages) + 1,
                "score": round(scores[index], 3),
                "source": section["source"],
                "url": source["url"],
                "title": source["title"],
                "heading": section["heading"],
                "text": text,
            }
        )

    # Number the sources that contributed passages 1, 2, 3 in order of first appearance.
    renumber: dict[int, int] = {}
    for passage in passages:
        renumber.setdefault(passage["source"], len(renumber) + 1)
    for passage in passages:
        passage["source"] = renumber[passage["source"]]
    cited = []
    for old, new in renumber.items():
        cited.append({**sources[old - 1], "id": new})
    uncited = [source for source in sources if source["id"] not in renumber]
    sources = cited + [{**source, "id": None} for source in uncited]

    result: dict[str, Any] = {
        "question": request.question,
        "brief": _brief(request.question, passages, sources),
        "passages": passages,
        "sources": sources,
        "stats": {
            "pages_fetched": len(sources),
            "pages_failed": failed,
            "provider": provider,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "stopped_by": stopped,
            "followed_links": sum(1 for source in sources if source["found_via"].startswith("link:")),
        },
        "notes": notes,
    }
    if request.synthesize:
        answer, detail = await _synthesize(request.question, passages, settings)
        if answer:
            result["answer"] = answer
            result["answer_model"] = detail
        else:
            result["notes"].append(f"answer skipped: {detail}")
    return result


def _brief(question: str, passages: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    if not passages:
        return f"# Research: {question}\n\nNo passages matched the question in {len(sources)} pages read."
    lines = [
        f"# Research: {question}",
        "",
        f"{len(passages)} passages from {len(sources)} pages, most relevant first. Numbers in brackets are sources.",
        "",
    ]
    for passage in passages:
        heading = f"**{passage['heading'].split(' > ')[-1]}**: " if passage["heading"] else ""
        text = " ".join(passage["text"].split())
        if len(text) > 420:
            text = text[:420].rsplit(" ", 1)[0] + " …"
        lines.append(f"{passage['rank']}. {heading}{text} [{passage['source']}]")
    lines += ["", "## Sources", ""]
    lines += [f"[{source['id']}] {source['title']} - {source['url']}" for source in sources if source["id"]]
    return "\n".join(lines)


def synthesis_backend(settings: Settings) -> tuple[str, str, str, str] | None:
    """(kind, base_url, api_key, model) for the first key found, or None.

    Order: DUNGSCRAPER_LLM_API_KEY (or a custom DUNGSCRAPER_LLM_BASE_URL such as Ollama), OPENAI_API_KEY, ANTHROPIC_API_KEY.
    """
    override = os.environ.get("DUNGSCRAPER_RESEARCH_MODEL", "").strip()
    default_openai = "https://api.openai.com/v1"
    if settings.llm_api_key or settings.llm_base_url.rstrip("/") != default_openai:
        return "openai", settings.llm_base_url, settings.llm_api_key, override or settings.llm_model
    if os.environ.get("OPENAI_API_KEY"):
        base = os.environ.get("OPENAI_BASE_URL", default_openai)
        return "openai", base, os.environ["OPENAI_API_KEY"], override or settings.llm_model
    if os.environ.get("ANTHROPIC_API_KEY"):
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        return "anthropic", base, os.environ["ANTHROPIC_API_KEY"], override or "claude-3-5-haiku-latest"
    return None


async def _synthesize(question: str, passages: list[dict[str, Any]], settings: Settings) -> tuple[str | None, str]:
    backend = synthesis_backend(settings)
    if backend is None:
        return None, "no key found (set OPENAI_API_KEY, ANTHROPIC_API_KEY or DUNGSCRAPER_LLM_API_KEY)"
    if not passages:
        return None, "no passages to answer from"
    kind, base, key, model = backend
    context = "\n\n".join(f"[{p['source']}] ({p['title']}) {p['text']}" for p in passages)
    system = (
        "Answer the question using only the numbered passages. Cite sources like [1]. "
        "If the passages do not contain the answer, say so plainly. Be concise."
    )
    user = f"Question: {question}\n\nPassages:\n{context}"
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            if kind == "anthropic":
                response = await http.post(
                    base.rstrip("/") + "/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": 700, "system": system, "messages": [{"role": "user", "content": user}]},
                )
                if response.status_code >= 400:
                    return None, f"Anthropic returned HTTP {response.status_code}"
                blocks = response.json().get("content") or []
                text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            else:
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                response = await http.post(
                    base.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "temperature": 0,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    },
                )
                if response.status_code >= 400:
                    return None, f"model API returned HTTP {response.status_code}"
                text = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"model call failed: {exc}"
    text = text.strip()
    return (text or None), (model if text else "empty answer")
