"""Return only the parts of a page that answer a question, inside a token budget.

Sections are ranked with BM25 against the query. The best ones are kept in
reading order with their heading path, so an agent reads 800 tokens instead of 12,000.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from dungscraper.core.chunk import estimate_tokens

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")
_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "with", "what", "how",
    "do", "does", "i", "it", "this", "that", "be", "as", "at", "by", "from", "can", "you",
}


def _terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP and len(w) > 1]


def split_sections(markdown: str, *, target_words: int = 140) -> list[dict[str, Any]]:
    """Blocks of about target_words, each remembering the heading path above it."""
    sections: list[dict[str, Any]] = []
    path: list[tuple[int, str]] = []
    block: list[str] = []
    words = 0
    start = 0
    offset = 0

    def flush() -> None:
        nonlocal block, words, start
        text = "\n".join(block).strip()
        if text:
            sections.append({"heading": " > ".join(h for _, h in path), "text": text, "offset": start})
        block, words = [], 0

    in_code = False
    for line in (markdown or "").splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith("```"):
            in_code = not in_code
        heading = _HEADING.match(stripped)
        if heading and in_code:
            # Converted pages sometimes leave a fence open. A level 1-2 heading on its own line is not code.
            if len(heading.group(1)) <= 2 and (not block or not block[-1].strip()):
                in_code = False
            else:
                heading = None
        if heading:
            flush()
            level = len(heading.group(1))
            path[:] = [(lv, h) for lv, h in path if lv < level]
            path.append((level, heading.group(2)))
            start = offset + len(line)
        else:
            if not block:
                start = offset
            block.append(stripped)
            words += len(stripped.split())
            if words >= target_words and not in_code and not stripped.strip():
                flush()
        offset += len(line)
    flush()
    return sections


_BOILERPLATE = ("references", "external links", "further reading", "see also", "notes", "citations", "footnotes")


def bm25_rank(sections: list[dict[str, Any]], query: str, *, k1: float = 1.5, b: float = 0.4) -> list[float]:
    q_terms = _terms(query)
    if not q_terms or not sections:
        return [0.0] * len(sections)
    docs = [_terms(f"{s['heading']} {s['heading']} {s['text']}") for s in sections]  # heading counts double
    avg = sum(len(d) for d in docs) / max(len(docs), 1) or 1
    df: Counter[str] = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1
    n = len(docs)
    scores = []
    for doc in docs:
        freq = Counter(doc)
        score = 0.0
        for term in q_terms:
            if term not in freq:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            tf = freq[term]
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * len(doc) / avg))
        # Very short blocks match a word easily; damp them. Reference lists rarely answer anything.
        score *= min(1.0, len(doc) / 40) ** 0.5
        last = (sections[len(scores)]["heading"] or "").split(" > ")[-1].lower()
        if last in _BOILERPLATE and not set(q_terms) & set(_terms(last)):
            score *= 0.2
        scores.append(score)
    return scores


def focus_markdown(markdown: str, query: str = "", *, max_tokens: int = 1500) -> dict[str, Any]:
    """Pick the best sections for query within max_tokens. Without a query, keep the start of the page."""
    max_tokens = max(100, int(max_tokens))
    sections = split_sections(markdown)
    total_tokens = estimate_tokens(markdown or "")
    if total_tokens <= max_tokens and not query:
        return {"markdown": markdown, "tokens": total_tokens, "total_tokens": total_tokens, "sections": [], "trimmed": False}
    scores = bm25_rank(sections, query)
    if query and any(scores):
        order = sorted(range(len(sections)), key=lambda i: (-scores[i], i))
    else:
        order = list(range(len(sections)))
    chosen: list[int] = []
    used = 0
    top = max(scores) if scores else 0.0
    for rank, index in enumerate(order):
        if query and scores[index] <= max(0.0, top * 0.3):
            continue
        cost = estimate_tokens(sections[index]["text"]) + estimate_tokens(sections[index]["heading"]) + 4
        if used + cost > max_tokens:
            room = max_tokens - used - estimate_tokens(sections[index]["heading"]) - 4
            # A strong match that does not fit whole is kept in part rather than lost.
            if rank < 3 and room >= 80 and index not in chosen:
                sections[index] = {**sections[index], "text": sections[index]["text"][: room * 4].rsplit(" ", 1)[0] + " …"}
                chosen.append(index)
                used = max_tokens
            continue
        chosen.append(index)
        used += cost
    chosen.sort()
    parts: list[str] = []
    last_heading = None
    picked = []
    for index in chosen:
        section = sections[index]
        if section["heading"] and section["heading"] != last_heading:
            level = min(section["heading"].count(" > ") + 1, 6)
            parts.append(f"{'#' * level} {section['heading'].split(' > ')[-1]}")
            last_heading = section["heading"]
        parts.append(section["text"])
        picked.append({"heading": section["heading"], "score": round(scores[index], 3), "offset": section["offset"]})
    body = "\n\n".join(parts)
    return {
        "markdown": body,
        "tokens": estimate_tokens(body),
        "total_tokens": total_tokens,
        "sections": picked,
        "trimmed": True,
        "matched": bool(query and any(scores)),
    }
