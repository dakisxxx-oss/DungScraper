"""Public business contact data from a page or a small part of a site.

Finds email addresses in mailto links, visible text, schema.org JSON-LD,
Cloudflare-protected links, and simple obfuscated forms like `name [at] host [dot] com`.
Obviously useless addresses (image names, placeholders, error-tracker keys) are dropped.
Careers links are collected on the way. Fetching goes through the normal scrape path,
so robots.txt and the SSRF guard apply.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from dungscraper.config import Settings
from dungscraper.core.cache import Cache
from dungscraper.core.content import extract_links
from dungscraper.core.fetch import BrowserManager
from dungscraper.core.joblinks import is_career_link, json_ld_blocks, walk_json
from dungscraper.core.limiter import DomainRateLimiter
from dungscraper.core.robots import RobotsGate
from dungscraper.core.urls import clean_request_url, dedupe_key, extension_skipped, host_key, same_site
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import (
    CareerLink,
    ContactEmail,
    ContactPage,
    ContactsData,
    ContactsRequest,
    EmailLocation,
    LinkItem,
    ScrapeRequest,
)

log = get_logger("dungscraper.contacts")

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]*@(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9\-])"
)
_AT_TOKEN = re.compile(r"\s*[\[\(\{]\s*(?:at|@|ät|eta|arroba)\s*[\]\)\}]\s*", re.IGNORECASE)
_DOT_TOKEN = re.compile(r"\s*[\[\(\{]\s*(?:dot|punkt|\.)\s*[\]\)\}]\s*", re.IGNORECASE)
_SPACE = re.compile(r"\s+")

# Files and assets that look like `name@2x.png`.
ASSET_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp", "tif", "tiff", "css", "js", "mjs",
    "map", "json", "xml", "woff", "woff2", "ttf", "eot", "otf", "mp4", "webm", "mov", "mp3", "wav",
    "pdf", "zip", "gz", "html", "htm", "php", "txt", "wasm",
}
PLACEHOLDER_LABELS = {
    "example", "yourdomain", "yourcompany", "yoursite", "yourwebsite", "mydomain", "mysite", "domain",
    "test", "sample", "email", "company",
}
PLACEHOLDER_DOMAINS = {"domain.tld", "email.tld", "mail.com.example"}
PLACEHOLDER_LOCALS = {
    "you", "your", "yourname", "your.name", "your-name", "youremail", "your.email", "your-email",
    "username", "user", "firstname.lastname", "first.last", "name.surname", "someone", "somebody",
    "xxx", "xxxx", "john.doe", "jane.doe", "johndoe", "janedoe",
}
TECHNICAL_DOMAIN_SUFFIXES = (
    "sentry.io", "sentry-next.wixpress.com", "sentry.wixpress.com", "wixpress.com", "bugsnag.com",
    "rollbar.com", "cloudfront.net", "amazonaws.com", "azureedge.net", "googleapis.com", "gstatic.com",
    "googleusercontent.com", "schema.org", "w3.org", "localhost", "invalid", "test", "local", "internal",
)
NOREPLY = re.compile(r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|mailer[-_.]?daemon|bounce[sd]?)\b", re.I)
HEX_KEY = re.compile(r"^[0-9a-f]{20,}$")

KIND_TOKENS: list[tuple[str, set[str]]] = [
    ("jobs", {
        "jobs", "job", "career", "careers", "karjera", "recruiting", "recruitment", "recruiter", "hr",
        "talent", "people", "hiring", "apply", "applications", "bewerbung", "bewerbungen", "darbas",
    }),
    ("legal", {"legal", "privacy", "dpo", "datenschutz", "gdpr", "compliance", "abuse", "security", "impressum", "copyright"}),
    ("sales", {"sales", "business", "partners", "partnership", "bd", "enterprise", "vertrieb"}),
    ("support", {"support", "help", "helpdesk", "service", "customerservice", "care"}),
    ("press", {"press", "media", "pr", "news", "marketing", "presse"}),
    ("general", {
        "info", "contact", "hello", "hi", "office", "mail", "enquiries", "inquiries", "kontakt", "kontaktai",
        "post", "team", "admin", "general", "reception", "mailbox",
    }),
]
KIND_ORDER = {name: index for index, (name, _tokens) in enumerate(KIND_TOKENS)}

# Links worth following from a page to find more contact data. Lower score is read first.
ARTICLE_SEGMENTS = {
    "blog", "news", "article", "articles", "post", "posts", "stories", "story", "insights", "resources",
    "docs", "documentation", "forum", "community", "shop", "store", "product", "products", "tag", "category",
}
_B = r"(?<![A-Za-z])"
FOLLOW_RULES: list[tuple[int, re.Pattern[str]]] = [
    (0, re.compile(_B + r"(contacts?|kontakt\w*|contacto|contatti|contatto|get[- ]in[- ]touch|reach[- ]us|write[- ]to[- ]us|yhteystiedot|kapcsolat)", re.I)),
    (1, re.compile(_B + r"(impressum|imprint|legal[- ]notice|company[- ]info)", re.I)),
    (2, re.compile(_B + r"(careers?|jobs?|karjera|karriere|hiring|vacanc\w+)", re.I)),
    (3, re.compile(_B + r"(about|apie|ueber|über|team|company|who[- ]we[- ]are|our[- ]story)", re.I)),
    (4, re.compile(_B + r"(press|media|newsroom)", re.I)),
]


@dataclass
class RawEmail:
    email: str
    source: str
    context: str = ""


@dataclass
class PageExtract:
    emails: list[RawEmail] = field(default_factory=list)
    links: list[LinkItem] = field(default_factory=list)
    career_links: list[LinkItem] = field(default_factory=list)
    ignored: set[str] = field(default_factory=set)


def normalize_email(value: str) -> str:
    return value.strip().strip(".,;:<>()[]{}\"'").lower()


def reject_reason(email: str) -> str | None:
    """Why this address is not a useful public contact, or None when it looks fine."""
    if "@" not in email:
        return "no_at"
    local, _, domain = email.rpartition("@")
    if not local or not domain or len(email) > 254 or len(local) > 64:
        return "length"
    labels = domain.split(".")
    tld = labels[-1]
    if tld in ASSET_TLDS:
        return "asset_file"
    if len(labels) >= 2 and labels[-2] in PLACEHOLDER_LABELS:
        return "placeholder_domain"
    if domain in PLACEHOLDER_DOMAINS:
        return "placeholder_domain"
    if local in PLACEHOLDER_LOCALS:
        return "placeholder_local"
    if any(domain == suffix or domain.endswith("." + suffix) for suffix in TECHNICAL_DOMAIN_SUFFIXES):
        return "technical"
    if HEX_KEY.match(local):
        return "technical"
    if NOREPLY.match(local):
        return "noreply"
    return None


def classify_email(email: str) -> str:
    local = email.rpartition("@")[0]
    tokens = {part for part in re.split(r"[._+\-]+", local) if part}
    for name, words in KIND_TOKENS:
        if tokens & words:
            return name
    return "other"


def deobfuscate(text: str) -> str:
    """Turn `name [at] host [dot] com` into `name@host.com`. Plain text is left alone."""
    return _DOT_TOKEN.sub(".", _AT_TOKEN.sub("@", text))


def decode_cloudflare(token: str) -> str | None:
    """Cloudflare email protection stores the address as hex with a one byte XOR key."""
    token = token.strip()
    if len(token) < 4 or len(token) % 2:
        return None
    try:
        key = int(token[:2], 16)
        return "".join(chr(int(token[i : i + 2], 16) ^ key) for i in range(2, len(token), 2))
    except ValueError:
        return None


def _window(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = _SPACE.sub(" ", text[left:right]).strip()
    return ("..." if left else "") + snippet + ("..." if right < len(text) else "")


def _nearby(node, limit: int = 160) -> str:
    parent = node.parent if node is not None else None
    text = parent.get_text(" ", strip=True) if parent is not None else ""
    text = _SPACE.sub(" ", text)
    return text[:limit]


def _emails_in(text: str) -> list[re.Match[str]]:
    return list(EMAIL_RE.finditer(text))


def extract_emails_from_text(text: str) -> list[RawEmail]:
    """Plain and obfuscated addresses in visible text, with a context snippet for each."""
    found: list[RawEmail] = []
    plain = {normalize_email(match.group(0)) for match in _emails_in(text)}
    for match in _emails_in(text):
        found.append(RawEmail(normalize_email(match.group(0)), "text", _window(text, match.start(), match.end())))
    clear = deobfuscate(text)
    if clear != text:
        for match in _emails_in(clear):
            email = normalize_email(match.group(0))
            if email not in plain:
                found.append(RawEmail(email, "obfuscated", _window(clear, match.start(), match.end())))
    return found


def _json_ld_emails(soup: BeautifulSoup) -> list[RawEmail]:
    found: list[RawEmail] = []
    for block in json_ld_blocks(soup):
        for item in walk_json(block):
            value = item.get("email")
            values = value if isinstance(value, list) else [value]
            label = item.get("@type")
            label = label[0] if isinstance(label, list) and label else label
            name = item.get("name") or item.get("legalName") or ""
            context = " ".join(str(part) for part in (label, name) if part)[:160]
            for entry in values:
                if not isinstance(entry, str):
                    continue
                cleaned = unquote(entry.strip())
                if cleaned.lower().startswith("mailto:"):
                    cleaned = cleaned[7:]
                for match in _emails_in(deobfuscate(cleaned)):
                    found.append(RawEmail(normalize_email(match.group(0)), "jsonld", context))
    return found


def extract_page(html: str, url: str) -> PageExtract:
    """Everything useful for contact discovery in one HTML document."""
    soup = BeautifulSoup(html, "lxml")
    result = PageExtract()
    raw: list[RawEmail] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            body = unquote(href[7:]).split("?", 1)[0]
            label = anchor.get_text(" ", strip=True)
            for part in re.split(r"[,;]", body):
                for match in _emails_in(deobfuscate(part.strip())):
                    email = normalize_email(match.group(0))
                    context = label if len(label.split()) >= 3 else _nearby(anchor)
                    raw.append(RawEmail(email, "mailto", _SPACE.sub(" ", context)[:160]))
        elif "/cdn-cgi/l/email-protection#" in lowered:
            decoded = decode_cloudflare(href.split("#", 1)[1])
            if decoded:
                for match in _emails_in(decoded):
                    raw.append(RawEmail(normalize_email(match.group(0)), "cloudflare", _nearby(anchor)))

    for node in soup.find_all(attrs={"data-cfemail": True}):
        decoded = decode_cloudflare(str(node.get("data-cfemail")))
        if decoded:
            for match in _emails_in(decoded):
                raw.append(RawEmail(normalize_email(match.group(0)), "cloudflare", _nearby(node)))

    raw.extend(_json_ld_emails(soup))

    for tag in soup(["script", "style", "noscript", "template", "head"]):
        tag.decompose()
    raw.extend(extract_emails_from_text(soup.get_text(" ")))

    result.emails = []
    for item in raw:
        reason = reject_reason(item.email)
        if reason:
            result.ignored.add(item.email)
            continue
        result.emails.append(item)

    result.links = extract_links(html, url)
    result.career_links = [link for link in result.links if is_career_link(link.url, link.text)]
    return result


def follow_score(url: str, text: str) -> int | None:
    """How promising a link is for more contact data. None means do not follow."""
    path = urlsplit(url).path
    if any(part.lower() in ARTICLE_SEGMENTS for part in path.split("/")):
        return None
    haystack = f"{path} {text or ''}"
    for score, pattern in FOLLOW_RULES:
        if pattern.search(haystack):
            # A careers hub is worth a visit. Single postings below it are not.
            if score == 2 and len([part for part in path.split("/") if part]) > 1:
                continue
            return score
    return None


_LANG_PREFIX = re.compile(r"^/[a-z]{2}(?:[-_][a-zA-Z]{2})?(?=/|$)")


def _is_translation_of(link_url: str, page_url: str) -> bool:
    """True for /de/jobs/ when the page is /jobs/ (the same page in another language)."""
    link, page = urlsplit(link_url), urlsplit(page_url)
    if host_key(link_url) != host_key(page_url):
        return False
    link_path = _LANG_PREFIX.sub("", link.path).rstrip("/") or "/"
    page_path = _LANG_PREFIX.sub("", page.path).rstrip("/") or "/"
    return link_path == page_path


def _same_domain(email: str, host: str) -> bool:
    domain = email.rpartition("@")[2]
    return bool(domain and host) and (domain == host or host.endswith("." + domain) or domain.endswith("." + host))


class _Collector:
    def __init__(self, root_host: str) -> None:
        self.root_host = root_host
        self.emails: dict[str, ContactEmail] = {}
        self.careers: dict[str, CareerLink] = {}
        self.ignored: set[str] = set()

    def add_page(self, url: str, extract: PageExtract) -> None:
        self.ignored |= extract.ignored
        for item in extract.emails:
            entry = self.emails.get(item.email)
            if entry is None:
                entry = ContactEmail(
                    email=item.email,
                    kind=classify_email(item.email),
                    same_domain=_same_domain(item.email, self.root_host),
                )
                self.emails[item.email] = entry
            if any(loc.url == url and loc.source == item.source for loc in entry.found_at):
                continue
            if len(entry.found_at) < 5:
                entry.found_at.append(EmailLocation(url=url, source=item.source, context=item.context))  # type: ignore[arg-type]
        for link in extract.career_links:
            key = dedupe_key(link.url)
            if _is_translation_of(link.url, url):
                continue
            if key not in self.careers and len(self.careers) < 30:
                self.careers[key] = CareerLink(url=link.url, text=link.text[:120], found_at=url)

    def sorted_emails(self) -> list[ContactEmail]:
        return sorted(
            self.emails.values(),
            key=lambda item: (not item.same_domain, KIND_ORDER.get(item.kind, 99), -len(item.found_at), item.email),
        )


async def find_contacts(
    request: ContactsRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    cache: Cache | None = None,
    browser: BrowserManager | None = None,
    limiter: DomainRateLimiter | None = None,
) -> ContactsData:
    from dungscraper.core.scrape import scrape_url

    limiter = limiter or DomainRateLimiter()
    respect = settings.respect_robots_default if request.respect_robots is None else request.respect_robots
    gate = RobotsGate(client, settings.robots_user_agent, respect=respect, fail_open=settings.robots_fail_open)
    root = clean_request_url(request.url)
    root_host = host_key(root)
    collector = _Collector(root_host)
    pages: list[ContactPage] = []
    errors: list[dict] = []
    skipped_robots = 0
    seen = {dedupe_key(root)}
    level: list[str] = [root]
    semaphore = asyncio.Semaphore(3)

    async def one(url: str):
        async with semaphore:
            await limiter.wait(host_key(url), request.delay_ms / 1000)
            try:
                data = await scrape_url(
                    ScrapeRequest(
                        url=url,
                        formats=["raw_html", "markdown", "links"],
                        render=request.render,
                        respect_robots=respect,
                        timeout_ms=request.timeout_ms,
                        only_main_content=False,
                    ),
                    settings=settings,
                    client=client,
                    cache=cache,
                    browser=browser,
                    robots=gate,
                )
                return url, data, None
            except DungScraperError as exc:
                return url, None, exc
            except Exception as exc:  # noqa: BLE001
                log.exception("contacts page failed", extra={"data": {"url": url}})
                return url, None, DungScraperError("fetch_failed", str(exc), status=502)

    depth = 0
    while level and len(pages) + len(errors) + skipped_robots < request.limit:
        room = request.limit - (len(pages) + len(errors) + skipped_robots)
        batch = level[:room]
        results = await asyncio.gather(*(one(url) for url in batch))
        candidates: list[tuple[int, int, str]] = []
        for order, (url, data, error) in enumerate(results):
            if error is not None:
                if depth == 0 and url == root:
                    raise error
                if error.code == "robots_disallowed":
                    skipped_robots += 1
                else:
                    errors.append({"url": url, "code": error.code, "message": error.message})
                continue
            assert data is not None
            if data.raw_html:
                extract = extract_page(data.raw_html, data.url)
            elif data.markdown:
                extract = PageExtract()
                for item in extract_emails_from_text(data.markdown):
                    if reject_reason(item.email):
                        extract.ignored.add(item.email)
                    else:
                        extract.emails.append(item)
                extract.links = list(data.links or [])
                extract.career_links = [l for l in extract.links if is_career_link(l.url, l.text)]
            else:
                errors.append({"url": url, "code": "no_content", "message": "The page had no HTML or text."})
                continue
            before = len(collector.emails)
            collector.add_page(data.url, extract)
            pages.append(
                ContactPage(
                    url=data.url,
                    status_code=data.status_code,
                    emails=len({item.email for item in extract.emails}),
                    career_links=len(extract.career_links),
                )
            )
            log.debug("contacts page", extra={"data": {"url": data.url, "new_emails": len(collector.emails) - before}})
            if depth < request.depth:
                for link in extract.links:
                    key = dedupe_key(link.url)
                    if key in seen or extension_skipped(link.url):
                        continue
                    if not same_site(link.url, root, allow_subdomains=True):
                        continue
                    score = follow_score(link.url, link.text)
                    if score is None:
                        continue
                    seen.add(key)
                    candidates.append((score, order, link.url))
        candidates.sort()
        level = [url for _score, _order, url in candidates]
        depth += 1
        if depth > request.depth:
            break

    emails = collector.sorted_emails()
    if request.check_mx and emails:
        from dungscraper.core.mx import check_domains

        verdicts = await check_domains({item.email.rsplit("@", 1)[-1] for item in emails})
        for item in emails:
            item.mx = verdicts.get(item.email.rsplit("@", 1)[-1])
    return ContactsData(
        url=root,
        domain=root_host,
        emails=emails,
        career_links=list(collector.careers.values()),
        pages=pages,
        pages_scanned=len(pages),
        skipped_robots=skipped_robots,
        ignored_addresses=len(collector.ignored),
        errors=errors,
    )
