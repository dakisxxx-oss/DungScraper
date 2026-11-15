"""Spot careers pages and job posting links in a page.

Pure functions work on HTML, so they can be tested with fixtures. `find_job_links`
fetches one page with the normal scrape path (robots, SSRF guard, escalation).
"""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from dungscraper.config import Settings
from dungscraper.core.cache import Cache
from dungscraper.core.fetch import BrowserManager
from dungscraper.core.urls import clean_request_url, dedupe_key, extension_skipped
from dungscraper.errors import DungScraperError
from dungscraper.models import JobLink, JobsData, JobsRequest, ScrapeRequest

CAREER_TEXT = re.compile(
    r"\b(careers?|jobs?|hiring|vacanc(?:y|ies)|open positions?|open roles?|join (?:us|our team|the team)"
    r"|work (?:with|at|for) us|karjera|darbo vietos?|laisvos darbo vietos|karriere|stellenangebote"
    r"|offres d.emploi|empleo|trabaja con nosotros)\b",
    re.IGNORECASE,
)
CAREER_TOKENS = {
    "career", "careers", "jobs", "job", "karjera", "karriere", "stellenangebote", "vacancies",
    "vacancy", "hiring", "openings", "positions", "recruiting", "jobboard", "empleo", "darbas",
}
CAREER_HOST_PREFIXES = ("careers.", "career.", "jobs.", "karriere.", "karjera.")
ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "personio.de",
    "personio.com", "recruitee.com", "teamtailor.com", "bamboohr.com", "breezy.hr", "jobvite.com",
    "workday.com", "myworkdayjobs.com", "join.com", "pinpointhq.com", "rippling.com", "comeet.com",
    "hrmos.co", "applytojob.com", "icims.com", "ultipro.com", "dayforcehcm.com",
)
# A path segment from this set followed by another segment means "one posting" (slug or id).
POSTING_PARENTS = {
    "job", "jobs", "position", "positions", "vacancy", "vacancies", "opening", "openings", "posting",
    "postings", "career", "careers", "role", "roles", "o", "j", "apply", "darbas", "karjera",
}
APPLY_TEXT = re.compile(
    r"\b(apply(?: now| here| today| for this (?:job|role|position))?|view (?:job|position|role|opening)"
    r"|see (?:job|position|details))\b",
    re.IGNORECASE,
)
NON_POSTING_SEGMENTS = {
    "login", "signin", "sign-in", "register", "privacy", "terms", "cookies", "cookie", "blog", "news",
    "press", "faq", "benefits", "culture", "about", "contact", "imprint", "impressum", "rss", "feed",
    "alert", "alerts", "subscribe", "talent-community", "search",
}
JUNIOR = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|graduate|intern(?:ship)?|trainee|apprentice(?:ship)?|working student"
    r"|werkstudent|praktikum|stažuotojas|praktika)\b",
    re.IGNORECASE,
)
REMOTE = re.compile(
    r"\b(remote(?:ly)?|work from home|wfh|anywhere|telecommute|nuotoli\w*|homeoffice|home office)\b", re.IGNORECASE
)


def _tokens(path: str) -> list[str]:
    return [part for part in re.split(r"[/_\-.]+", path.lower()) if part]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_ats_host(host: str) -> bool:
    return any(host == known or host.endswith("." + known) for known in ATS_HOSTS)


def is_career_link(url: str, text: str = "") -> bool:
    """A link to a careers or jobs hub (or an ATS board) from an ordinary page."""
    parts = urlsplit(url)
    host = _host(url)
    if is_ats_host(host) or host.startswith(CAREER_HOST_PREFIXES):
        return True
    if any(token in CAREER_TOKENS for token in _tokens(parts.path)):
        return True
    return bool(CAREER_TEXT.search(text or "")) and len(text or "") <= 60


def json_ld_blocks(soup: BeautifulSoup) -> list:
    blocks: list = []
    for script in soup.find_all("script", type=lambda value: bool(value) and "ld+json" in value.lower()):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw, strict=False))
        except ValueError:
            continue
    return blocks


def walk_json(node, depth: int = 0):
    """Yield every dict inside a JSON-LD document."""
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_json(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from walk_json(value, depth + 1)


def _tags(text: str, *, telecommute: bool = False) -> list[str]:
    found: list[str] = []
    if JUNIOR.search(text):
        found.append("junior")
    if telecommute or REMOTE.search(text):
        found.append("remote")
    return found


def _card_text(anchor, title: str) -> str:
    """Text of the listing card around a link: location, level, and remote hints live here."""
    for parent in anchor.parents:
        if parent.name in {"li", "article", "tr", "div", "section"}:
            text = parent.get_text(" ", strip=True)
            if len(text) <= 300:
                return text
            break
    return title


def _looks_like_posting(url: str, text: str, page_path: str) -> str | None:
    """Return why a link looks like one posting, or None."""
    parts = urlsplit(url)
    host = _host(url)
    ats = is_ats_host(host)
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments and not parts.query:
        return None
    if parts.path.rstrip("/") == page_path.rstrip("/") and not parts.query:
        return None
    if not ats and any(segment.lower() in NON_POSTING_SEGMENTS for segment in segments):
        return None
    for segment in segments[:-1]:
        if segment.lower() in POSTING_PARENTS:
            return f"path:/{segment.lower()}/<id>"
    if ats and len(segments) >= 2:
        return f"ats:{host}"
    if re.search(r"(?:^|&)(?:gh_jid|jobid|job_id|jid)=\w+", parts.query, re.IGNORECASE):
        return "query:job id"
    if APPLY_TEXT.search(text or "") and (segments or parts.query):
        return "text:apply"
    return None


INLINE_ITEM = "details, .vc_toggle, .accordion-item, .accordion__item, .wp-block-accordion-item"
INLINE_TITLE = ".vc_toggle_title, .accordion-title, .accordion__title, summary, h2, h3, h4, h5"
JOB_BODY = re.compile(
    r"\b(responsibilit\w+|requirements?|qualifications?|your profile|we are looking for|we[’']re looking for"
    r"|your resume|your cv|send your|apply)\b",
    re.IGNORECASE,
)


def _inline_listings(soup: BeautifulSoup, base_url: str, limit: int) -> list[JobLink]:
    """Pages that list roles as collapsed sections with no link per role (apply by email)."""
    found: list[JobLink] = []
    seen: set[str] = set()
    for node in soup.select(INLINE_ITEM):
        title_node = node.select_one(INLINE_TITLE)
        title = title_node.get_text(" ", strip=True) if title_node is not None else ""
        if not title or len(title) > 150 or title.endswith("?") or title.lower() in seen:
            continue
        body = node.get_text(" ", strip=True)
        if not JOB_BODY.search(body):
            continue
        seen.add(title.lower())
        apply_email = None
        for anchor in node.find_all("a", href=True):
            if anchor["href"].lower().startswith("mailto:"):
                match = re.search(r"[^\s:?,;]+@[^\s?,;]+", unquote(anchor["href"][7:]))
                if match:
                    apply_email = match.group(0).lower()
                    break
        found.append(
            JobLink(
                url=base_url,
                title=title,
                reason="inline:collapsed section",
                tags=_tags(f"{title} {body[:1500]}"),
                apply_email=apply_email,
            )
        )
        if len(found) >= limit:
            break
    return found


def extract_job_links(html: str, base_url: str, *, limit: int = 100) -> tuple[list[JobLink], int]:
    """Return (job posting links, number of anchors seen) for one page."""
    soup = BeautifulSoup(html, "lxml")
    page_path = urlsplit(base_url).path or "/"
    found: dict[str, JobLink] = {}
    total = 0

    # Structured data first: JobPosting entries are the most reliable source.
    for block in json_ld_blocks(soup):
        for item in walk_json(block):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" not in kinds:
                continue
            url = item.get("url") or item.get("sameAs")
            if isinstance(url, list):
                url = url[0] if url else None
            if not isinstance(url, str) or not url.strip():
                continue
            absolute = urljoin(base_url, url.strip()).split("#", 1)[0]
            if not absolute.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or "")[:200]
            telecommute = str(item.get("jobLocationType") or "").upper() == "TELECOMMUTE"
            level = " ".join(str(item.get(key) or "") for key in ("title", "experienceRequirements", "employmentType"))
            found.setdefault(
                dedupe_key(absolute),
                JobLink(
                    url=absolute,
                    title=title,
                    reason="jsonld:JobPosting",
                    tags=_tags(level, telecommute=telecommute),
                ),
            )

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")) or extension_skipped(absolute):
            continue
        total += 1
        title = anchor.get_text(" ", strip=True)[:200]
        reason = _looks_like_posting(absolute, title, page_path)
        if reason is None:
            continue
        key = dedupe_key(absolute)
        existing = found.get(key)
        if existing is not None:
            if not existing.title and title:
                existing.title = title
            continue
        card = _card_text(anchor, title)
        found[key] = JobLink(
            url=absolute,
            title=title,
            reason=reason,
            tags=_tags(f"{title} {absolute} {card}"),
        )

    jobs = list(found.values())
    if not jobs:
        jobs = _inline_listings(soup, base_url, limit)
    return jobs[:limit], total


async def find_job_links(
    request: JobsRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache: Cache | None = None,
    browser: BrowserManager | None = None,
) -> JobsData:
    from dungscraper.core.scrape import scrape_url

    scraped = await scrape_url(
        ScrapeRequest(
            url=clean_request_url(request.url),
            formats=["raw_html", "links"],
            render=request.render,
            respect_robots=request.respect_robots,
            timeout_ms=request.timeout_ms,
        ),
        settings=settings,
        client=client,
        cache=cache,
        browser=browser,
    )
    if not scraped.raw_html:
        raise DungScraperError(
            "unsupported_content",
            f"{scraped.url} has no HTML to scan for job links.",
            status=415,
        )
    jobs, total = extract_job_links(scraped.raw_html, scraped.url, limit=request.limit)
    notes: list[str] = []
    if not jobs:
        notes.append("No job posting links found. The page may list jobs through a script or an embedded board.")
    return JobsData(
        url=scraped.url,
        jobs=jobs,
        total_links=total,
        status_code=scraped.status_code,
        render_mode=scraped.render_mode,
        notes=notes,
    )
