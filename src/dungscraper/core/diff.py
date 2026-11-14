"""Compare two markdown snapshots of a page."""

from __future__ import annotations

import difflib

from dungscraper.models import DiffData

_snapshots: dict[str, str] = {}


def markdown_diff(baseline: str, current: str, *, url: str | None = None) -> DiffData:
    before = baseline or ""
    after = current or ""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="baseline",
            tofile="current",
            lineterm="",
        )
    )
    return DiffData(
        url=url,
        changed=before.strip() != after.strip(),
        diff="\n".join(lines),
        baseline_chars=len(before),
        current_chars=len(after),
    )


def remember_snapshot(url: str, markdown: str) -> str | None:
    previous = _snapshots.get(url)
    _snapshots[url] = markdown
    return previous
