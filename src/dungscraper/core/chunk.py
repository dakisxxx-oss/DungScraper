"""Heading-aware, token-approximate chunks for RAG."""

from __future__ import annotations

import re

from dungscraper.models import Chunk

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def estimate_tokens(text: str) -> int:
    """Approximate cl100k tokens without downloading a tokenizer.

    English and code land near four characters per token. The count is stable
    and good enough to pack a context window; it is not a billing meter.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _blocks(text: str) -> list[tuple[str, str]]:
    """Return (heading, block) pairs. Fenced code stays in one block."""
    heading = ""
    buffer: list[str] = []
    in_code = False
    blocks: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            blocks.append((heading, "\n".join(buffer).strip("\n")))
            buffer = []

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("```"):
            buffer.append(line)
            in_code = not in_code
            if not in_code:
                flush()
            continue
        if in_code:
            buffer.append(line)
            continue
        if not line.strip():
            flush()
            continue
        match = _HEADING.match(line)
        if match and not buffer:
            heading = match.group(2).strip()
            blocks.append((heading, line))
            continue
        buffer.append(line)
    flush()
    return [(heading, block) for heading, block in blocks if block.strip()]


def _hard_windows(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    size = max_tokens * 4
    overlap = overlap_tokens * 4
    step = max(size - overlap, size // 2)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        pieces.append(text[start : start + size].strip())
        if start + size >= len(text):
            break
        start += step
    return [piece for piece in pieces if piece]


def chunk_markdown(
    text: str,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 50,
    metadata: dict | None = None,
) -> list[Chunk]:
    meta = dict(metadata or {})
    blocks = _blocks(text)
    if not blocks and text.strip():
        blocks = [("", text.strip())]

    packed: list[tuple[str, str]] = []
    current_heading = ""
    current: list[str] = []

    def current_text() -> str:
        return "\n\n".join(part for part in current if part).strip()

    def flush() -> None:
        nonlocal current
        body = current_text()
        if body:
            packed.append((current_heading, body))
        current = []

    for heading, block in blocks:
        current_heading = heading or current_heading
        only_heading = bool(current) and all(part.lstrip().startswith("#") for part in current)
        prospective = "\n\n".join([*current, block]).strip()
        if current and estimate_tokens(prospective) > max_tokens and not only_heading:
            flush()
        if estimate_tokens(block) > max_tokens:
            prefix = ""
            if current and all(part.lstrip().startswith("#") for part in current):
                prefix = current_text() + "\n\n"
                current = []
            else:
                flush()
            for piece in _hard_windows(block, max_tokens, overlap_tokens):
                packed.append((current_heading, (prefix + piece).strip()))
                prefix = ""
            continue
        current.append(block)
    flush()

    chunks: list[Chunk] = []
    previous_tail = ""
    for heading, body in packed:
        piece = body
        if previous_tail and overlap_tokens > 0 and previous_tail not in piece:
            piece = f"{previous_tail}\n\n{piece}".strip()
            while estimate_tokens(piece) > max_tokens and "\n\n" in piece:
                piece = piece.split("\n\n", 1)[1]
        if heading and heading not in piece[:180]:
            piece = f"## {heading}\n\n{piece}"
        token_count = estimate_tokens(piece)
        tail_chars = overlap_tokens * 4
        previous_tail = piece[-tail_chars:].strip() if overlap_tokens else ""
        chunk_meta = dict(meta)
        if heading:
            chunk_meta["heading"] = heading
        chunk_meta["token_count"] = token_count
        chunks.append(
            Chunk(index=len(chunks), text=piece.strip(), token_count=token_count, metadata=chunk_meta)
        )
    return chunks
