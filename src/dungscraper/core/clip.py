"""Keep MCP tool results inside an agent's context window."""

from __future__ import annotations


def clip_text(text: str, *, max_chars: int = 12_000, offset: int = 0) -> tuple[str, dict]:
    total = len(text or "")
    start = min(max(offset, 0), total)
    limit = max(max_chars, 1)
    end = min(total, start + limit)
    clipped = (text or "")[start:end]
    info = {
        "truncated": end < total,
        "offset": start,
        "next_offset": end if end < total else None,
        "total_chars": total,
        "returned_chars": len(clipped),
    }
    if info["truncated"]:
        clipped += (
            f"\n\n[truncated {total - end} characters; "
            f"call again with offset={end} to continue]"
        )
    return clipped, info


def clip_payload(data: dict, *, max_chars: int, offset: int = 0, page_chars: int = 4_000) -> dict:
    """Clip markdown fields on a scrape, crawl, search, or batch payload."""
    if not isinstance(data, dict):
        return data
    if isinstance(data.get("markdown"), str):
        text, info = clip_text(data["markdown"], max_chars=max_chars, offset=offset)
        data["markdown"] = text
        data["truncation"] = info
    pages = data.get("pages")
    if isinstance(pages, list) and len(pages) > 8:
        data["omitted_pages"] = len(pages) - 8
        data["pages"] = pages[:8]
        pages = data["pages"]
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict) and isinstance(page.get("markdown"), str):
                text, info = clip_text(page["markdown"], max_chars=page_chars, offset=0)
                page["markdown"] = text
                page["truncation"] = info
    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("markdown"), str):
                text, info = clip_text(item["markdown"], max_chars=page_chars, offset=0)
                item["markdown"] = text
                item["truncation"] = info
            nested = item.get("data")
            if isinstance(nested, dict) and isinstance(nested.get("markdown"), str):
                text, info = clip_text(nested["markdown"], max_chars=page_chars, offset=0)
                nested["markdown"] = text
                nested["truncation"] = info
    return data
