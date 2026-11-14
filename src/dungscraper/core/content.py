"""Turn HTML into LLM-ready Markdown and page metadata.

Several extractors run and the highest-scoring Markdown wins. A short article
with a table and a code block should beat a nav-heavy full-page conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify
from readability import Document

from dungscraper.models import LinkItem, PageMetadata

_MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)


def _rel_has(value, needle: str) -> bool:
    if not value:
        return False
    items = value if isinstance(value, list) else [value]
    return any(needle in str(item).lower() for item in items)

JUNK_SELECTORS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "nav",
    "footer",
    "[role='navigation']",
    "[role='contentinfo']",
    "[role='banner']",
    ".cookie",
    ".cookies",
    ".cookie-banner",
    ".cookie-consent",
    "#cookie-banner",
    "#onetrust-banner-sdk",
    "[id*='cookie']",
    "[class*='cookie']",
    ".advertisement",
    ".ads",
    ".ad-container",
    "[class*='advert']",
    ".newsletter",
    ".popup",
    ".modal",
    ".share-buttons",
    ".social-share",
    ".related-posts",
    ".sidebar",
]

BOILERPLATE_PHRASES = (
    "accept all cookies",
    "we use cookies",
    "subscribe to our newsletter",
    "sign up for our newsletter",
    "advertisement",
    "cookie policy",
    "manage preferences",
)


@dataclass
class Extracted:
    markdown: str
    html: str
    links: list[LinkItem] = field(default_factory=list)
    metadata: PageMetadata = field(default_factory=PageMetadata)


def extract_metadata(html: str, url: str) -> PageMetadata:
    soup = BeautifulSoup(html, "lxml")
    og: dict[str, str] = {}
    description = None
    author = None
    published = None
    keywords: list[str] = []
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").strip()
        content = (meta.get("content") or "").strip()
        if not prop or not content:
            continue
        lowered = prop.lower()
        if lowered.startswith("og:"):
            og[lowered[3:]] = content
        elif lowered in {"description", "twitter:description"} and not description:
            description = content
        elif lowered == "author" and not author:
            author = content
        elif lowered in {"article:published_time", "pubdate", "date"} and not published:
            published = content
        elif lowered == "keywords":
            keywords = [part.strip() for part in content.split(",") if part.strip()]
    title_tag = soup.find("title")
    title = og.get("title") or (title_tag.get_text(" ", strip=True) if title_tag else None) or None
    description = og.get("description") or description
    canonical_tag = soup.find("link", rel=lambda value: _rel_has(value, "canonical"))
    canonical = None
    if canonical_tag and canonical_tag.get("href"):
        canonical = urljoin(url, canonical_tag["href"])
    icon = soup.find("link", rel=lambda value: _rel_has(value, "icon"))
    favicon = urljoin(url, icon["href"]) if icon and icon.get("href") else None
    html_tag = soup.find("html")
    language = (html_tag.get("lang") or html_tag.get("xml:lang")) if html_tag else None
    if isinstance(language, list):
        language = language[0] if language else None
    return PageMetadata(
        title=title or None,
        description=description or None,
        language=language or None,
        canonical=canonical,
        author=author,
        published=published,
        favicon=favicon,
        keywords=keywords,
        og=og,
    )


def extract_links(html: str, base_url: str) -> list[LinkItem]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    links: list[LinkItem] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        absolute = absolute.split("#", 1)[0]
        if absolute in seen:
            continue
        seen.add(absolute)
        text = anchor.get_text(" ", strip=True)[:200]
        links.append(LinkItem(url=absolute, text=text))
    return links


def preclean(html: str, extra_selectors: list[str] | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    selectors = list(JUNK_SELECTORS)
    if extra_selectors:
        selectors.extend(extra_selectors)
    for selector in selectors:
        try:
            for node in soup.select(selector):
                node.decompose()
        except Exception:
            continue
    return str(soup)


def html_to_markdown(html: str, base_url: str) -> str:
    def language(element) -> str:
        classes: list[str] = []
        code = element.find("code") if hasattr(element, "find") else None
        if code is not None and code.get("class"):
            classes.extend(code.get("class") or [])
        if element.get("class"):
            classes.extend(element.get("class") or [])
        for name in classes:
            if name.startswith(("language-", "lang-")):
                return name.split("-", 1)[1]
        return ""

    rendered = markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        code_language_callback=language,
        strip=["script", "style"],
    )
    return cleanup_markdown(absolutize_markdown(rendered or "", base_url))


def cleanup_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def absolutize_markdown(markdown: str, base_url: str) -> str:
    def replace(match: re.Match[str]) -> str:
        bang, text, target = match.group(1), match.group(2), match.group(3)
        if target.startswith(("http://", "https://", "mailto:", "data:", "#")):
            return match.group(0)
        return f"{bang}[{text}]({urljoin(base_url, target)})"

    return _MD_LINK.sub(replace, markdown)


def score_markdown(markdown: str) -> float:
    if not markdown or not markdown.strip():
        return 0.0
    words = markdown.split()
    headings = len(_HEADING.findall(markdown))
    fences = markdown.count("```")
    table_rows = len(re.findall(r"^\|", markdown, flags=re.MULTILINE))
    link_count = len(re.findall(r"\]\(", markdown))
    lowered = markdown.lower()
    boilerplate = sum(lowered.count(phrase) for phrase in BOILERPLATE_PHRASES)
    score = min(len(markdown), 20_000) / 25
    score += headings * 30
    score += fences * 25
    score += table_rows * 8
    score += min(link_count, 20) * 2
    score -= boilerplate * 80
    if words and link_count / max(len(words), 1) > 0.35:
        score -= 250
    if len(words) < 25:
        score *= 0.45
    return score


CHALLENGE_MARKERS = (
    "just a moment",
    "enable javascript and cookies to continue",
    "attention required",
    "verify you are a human",
    "cf-challenge",
    "cf-browser-verification",
    "cf-chl",
    "__cf_chl",
    "challenges.cloudflare.com",
    "cf-turnstile",
    "are you a robot",
    "px-captcha",
    "perimeterx",
    "datadome",
    "captcha-delivery.com",
    "akamai",
    "_abck",
    "access denied",
    "request blocked",
    "performing security verification",
)


def challenge_vendor(html: str, status: int = 200, headers: dict | None = None) -> str | None:
    """Name the vendor behind a challenge page, if this response is one."""
    if not is_challenge(html, status):
        return None
    blob = (html or "").lower()
    if headers:
        blob += " " + " ".join(f"{key}:{value}" for key, value in headers.items()).lower()
    if any(marker in blob for marker in ("datadome", "captcha-delivery.com")):
        return "datadome"
    if any(marker in blob for marker in ("perimeterx", "px-captcha", "_pxhd", "px-cloud.net")):
        return "perimeterx"
    if any(marker in blob for marker in ("akamai", "_abck", "ak_bmsc")):
        return "akamai"
    if any(
        marker in blob
        for marker in (
            "cf-chl",
            "__cf_chl",
            "cf-ray",
            "just a moment",
            "challenges.cloudflare.com",
            "cf-turnstile",
            "cf-browser-verification",
            "performing security verification",
        )
    ):
        return "cloudflare"
    return "unknown"


def is_challenge(html: str, status: int = 200) -> bool:
    """True when the HTTP response is a block, interstitial, or empty shell."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    words = len(soup.get_text(" ", strip=True).split())
    lowered = (html or "").lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS) and words < 180:
        return True
    if status in {403, 429, 503} and words < 100:
        return True
    if status == 200 and words < 5 and len(html or "") < 400:
        return True
    return False


def should_escalate(html: str, status: int = 200) -> str | None:
    """Why an auto scrape should retry this HTTP response in the browser."""
    if is_challenge(html, status):
        return "challenge"
    if looks_js_heavy(html):
        return "js_shell"
    return None


def looks_js_heavy(html: str) -> bool:
    """True when the visible text is a shell and the body is probably injected.

    A short real paragraph with an analytics script, such as example.com, stays
    on the HTTP path. An empty app root or a "enable JavaScript" notice does not.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    script_count = (html or "").lower().count("<script")
    lowered = (html or "").lower()
    hints = (
        'id="__next"',
        "id='__next'",
        "data-reactroot",
        "__next_data__",
        "ng-version",
        "you need to enable javascript",
        "please enable javascript",
        "enable javascript to run",
    )
    if any(hint in lowered for hint in hints) and len(text) < 800:
        return True
    visible_words = len(text.split())
    # A sentence or two of real copy is enough to stay on the fast path.
    if script_count >= 1 and visible_words < 8:
        return True
    raw = BeautifulSoup(html or "", "lxml")
    script_chars = sum(len(node.get_text() or "") for node in raw.find_all("script"))
    # Quotes and other script-rendered shells hide the article in inline JSON.
    if visible_words < 40 and script_chars > max(400, len(text) * 3):
        return True
    return False


def _candidate_html(html: str, url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
    if article and len(article.get_text(" ", strip=True)) >= 40:
        candidates.append(("article", str(article)))
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="html",
            include_links=True,
            include_tables=True,
            include_images=True,
            include_formatting=True,
            favor_recall=True,
        )
        if extracted and len(BeautifulSoup(extracted, "lxml").get_text(" ", strip=True)) >= 40:
            candidates.append(("trafilatura", extracted))
    except Exception:
        pass
    try:
        summary = Document(html).summary(html_partial=True)
        if summary and len(BeautifulSoup(summary, "lxml").get_text(" ", strip=True)) >= 40:
            candidates.append(("readability", summary))
    except Exception:
        pass
    body = soup.find("body")
    if body:
        candidates.append(("body", str(body)))
    return candidates


def merge_structures(primary: str, others: list[str]) -> str:
    """Keep tables and code blocks the winning extractor dropped."""
    extras: list[str] = []
    if "```" not in primary:
        for other in others:
            fences = re.findall(r"```[^\n]*\n.*?```", other, flags=re.DOTALL)
            extras.extend(fences[:2])
            if extras:
                break
    if not re.search(r"^\|", primary, flags=re.MULTILINE):
        for other in others:
            tables = re.findall(r"(?:^\|.+\|\n){2,}", other, flags=re.MULTILINE)
            if tables:
                extras.append(tables[0].strip())
                break
    if not extras:
        return primary
    return primary.rstrip() + "\n\n" + "\n\n".join(extras)


_OVERLAY_PHRASES = (
    "join stack overflow",
    "sign up with google",
    "sign up with github",
    "cnn values your feedback",
    "how relevant is this ad to you",
    "accept all cookies",
    "we value your privacy",
    "subscribe to our newsletter",
    "set up your homepage",
    "select a few topics",
)


def _noisy_heading(title: str) -> bool:
    lowered = title.lower()
    return any(
        phrase in lowered
        for phrase in (
            "join stack overflow",
            "sign up",
            "log in",
            "feedback",
            "cookie",
            "subscribe",
            "advertisement",
            "privacy",
            "homepage",
            "topics you're interested",
            "set up your",
        )
    )


def _prefix_is_noise(prefix: str) -> bool:
    if any(phrase in prefix for phrase in _OVERLAY_PHRASES):
        return True
    hits = sum(1 for phrase in ("password", "sign up", "log in", "email") if phrase in prefix)
    return hits >= 2


def strip_overlays(markdown: str) -> str:
    """Drop a leading signup, cookie, or ad block when a real article follows it."""
    text = markdown or ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^#{1,3} ", line):
            continue
        title = line.lstrip("#").strip()
        if _noisy_heading(title) or len(title) < 12:
            continue
        prefix = "\n".join(lines[:index]).lower()
        if prefix.strip() and _prefix_is_noise(prefix):
            cleaned = "\n".join(lines[index:]).strip()
            if len(cleaned.split()) >= 20:
                return cleaned
        break
    return text


def _ensure_title(markdown: str, title: str | None) -> str:
    if not title:
        return markdown
    head = markdown[:300].lower()
    if title.lower() in head:
        return markdown
    if markdown.lstrip().startswith("#"):
        return markdown
    return f"# {title}\n\n{markdown}".strip()


def html_to_content(
    html: str,
    url: str,
    *,
    only_main_content: bool = True,
    exclude_selectors: list[str] | None = None,
    include_selectors: list[str] | None = None,
) -> Extracted:
    metadata = extract_metadata(html, url)
    cleaned = preclean(html, exclude_selectors)
    chosen_html = cleaned
    markdown = ""

    if include_selectors:
        soup = BeautifulSoup(cleaned, "lxml")
        pieces: list[str] = []
        for selector in include_selectors:
            try:
                pieces.extend(str(node) for node in soup.select(selector))
            except Exception:
                continue
        if pieces:
            chosen_html = "\n".join(pieces)
            markdown = html_to_markdown(chosen_html, url)
    elif only_main_content:
        ranked: list[tuple[float, int, str, str]] = []
        for name, fragment in _candidate_html(cleaned, url):
            rendered = html_to_markdown(fragment, url)
            score = score_markdown(rendered)
            if name == "trafilatura":
                score += 15
            if name == "article":
                score += 10
            ranked.append((score, rendered.count("]("), fragment, rendered))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked:
            best_score, best_links, best_html, best_md = ranked[0]
            for score, links, fragment, rendered in ranked[1:]:
                # Link indexes (Hacker News, tag pages) lose their hrefs in some extractors.
                if best_links < 3 and links >= 8 and score >= best_score * 0.75:
                    best_html, best_md = fragment, rendered
                    break
            chosen_html = best_html
            others = [rendered for _score, _links, _fragment, rendered in ranked if rendered != best_md]
            markdown = merge_structures(best_md, others)
        else:
            chosen_html = cleaned
            markdown = ""
    else:
        markdown = html_to_markdown(cleaned, url)

    markdown = cleanup_markdown(markdown)
    if only_main_content:
        markdown = strip_overlays(markdown)
    markdown = _ensure_title(markdown, metadata.title)
    links = extract_links(chosen_html if only_main_content else cleaned, url)
    return Extracted(markdown=markdown, html=chosen_html, links=links, metadata=metadata)
