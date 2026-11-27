"""Scores for deciding which link a crawl follows next.

A score is a number from 0 to 1, higher is better. `best_first` crawling always fetches the
highest-scoring link in the frontier; `bfs` and `dfs` ignore scores except for `min_score`.

Scorers:
  keyword    anchor text and URL words that match the crawl's keywords
  depth      shallower pages first
  freshness  years or dates in the URL, newer first
  authority  same site beats subdomain beats other sites; short, query-free URLs beat deep ones
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from dungscraper.core.urls import host_key

SCORERS = ("keyword", "depth", "freshness", "authority")
DEFAULT_WEIGHTS = {"keyword": 3.0, "depth": 1.0, "freshness": 1.0, "authority": 1.0}
_WORD = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"(?<!\d)(19[9]\d|20\d\d)(?!\d)")


def words(text: str) -> list[str]:
    return _WORD.findall(unquote(text or "").lower())


def keyword_score(url: str, text: str, keywords: list[str]) -> float:
    """Share of keywords found in the anchor text (full credit) or in the URL (most credit)."""
    wanted = [word for keyword in keywords for word in words(keyword)]
    if not wanted:
        return 0.0
    parts = urlsplit(url)
    anchor = set(words(text))
    in_url = set(words(parts.path + " " + parts.query))
    host = set(words(parts.hostname or ""))
    total = 0.0
    for word in wanted:
        if word in anchor:
            total += 1.0
        elif word in in_url:
            total += 0.8
        elif word in host:
            total += 0.4
        elif any(len(word) >= 4 and (item.startswith(word) or word.startswith(item)) and len(item) >= 4 for item in anchor | in_url):
            total += 0.5
    return min(1.0, total / len(wanted))


def depth_score(depth: int) -> float:
    return 1.0 / (1 + max(0, depth))


def freshness_score(url: str, now: datetime | None = None) -> float:
    """1.0 for this year, 0.2 less per year back. 0.3 when the URL carries no date."""
    year_now = (now or datetime.now(timezone.utc)).year
    years = [int(match) for match in _YEAR.findall(urlsplit(url).path) if int(match) <= year_now + 1]
    if not years:
        return 0.3
    return max(0.0, 1.0 - 0.2 * max(0, year_now - max(years)))


def authority_score(url: str, root: str) -> float:
    left, right = host_key(url), host_key(root)
    if left == right:
        base = 1.0
    elif left.endswith("." + right) or right.endswith("." + left):
        base = 0.7
    else:
        base = 0.25
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    penalty = 0.1 * max(0, len(segments) - 2) + (0.1 if parts.query else 0.0)
    return max(0.0, base - penalty)


def score_link(
    url: str,
    text: str,
    depth: int,
    root: str,
    *,
    keywords: list[str] | None = None,
    scorers: list[str] | None = None,
    weights: dict[str, float] | None = None,
    parent_relevance: float = 0.0,
    now: datetime | None = None,
) -> float:
    """Weighted mean of the chosen scorers, nudged toward the relevance of the page the link was found on."""
    keywords = keywords or []
    active = [name for name in (scorers or list(SCORERS)) if name in SCORERS]
    if not keywords:
        active = [name for name in active if name != "keyword"]
    if not active:
        return 0.5
    merged = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = 0.0
    weight_sum = 0.0
    for name in active:
        if name == "keyword":
            value = keyword_score(url, text, keywords)
        elif name == "depth":
            value = depth_score(depth)
        elif name == "freshness":
            value = freshness_score(url, now)
        else:
            value = authority_score(url, root)
        weight = max(0.0, float(merged.get(name, 1.0)))
        total += value * weight
        weight_sum += weight
    base = total / weight_sum if weight_sum else 0.5
    if keywords and parent_relevance > 0:
        base = 0.8 * base + 0.2 * min(1.0, parent_relevance)
    return round(base, 4)


def page_relevance(markdown: str, keywords: list[str]) -> float:
    """Share of keywords that appear in the page text."""
    wanted = [word for keyword in keywords for word in words(keyword)]
    if not wanted or not markdown:
        return 0.0
    text = set(words(markdown))
    return sum(1 for word in wanted if word in text) / len(wanted)
