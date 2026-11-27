"""schema.org JSON-LD and OpenGraph, read from any page without a model."""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

MAX_NODES = 25


def _flatten(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
        return
    if not isinstance(node, dict):
        return
    graph = node.get("@graph")
    if isinstance(graph, list):
        _flatten(graph, out)
    rest = {key: value for key, value in node.items() if key not in {"@context", "@graph"}}
    if rest.get("@type") or rest.get("name"):
        out.append(rest)


def _types(node: dict[str, Any]) -> list[str]:
    value = node.get("@type")
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)] if value else []


def page_structured_data(html: str) -> dict[str, Any] | None:
    """Return {"types": [...], "json_ld": [...], "open_graph": {...}} or None when the page has none."""
    soup = BeautifulSoup(html or "", "lxml")
    nodes: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            _flatten(json.loads(raw), nodes)
        except ValueError:
            continue
    og: dict[str, str] = {}
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or ""
        content = meta.get("content")
        if prop.startswith(("og:", "product:", "article:")) and content:
            og.setdefault(prop, content.strip())
    if not nodes and not og:
        return None
    nodes = nodes[:MAX_NODES]
    types: list[str] = []
    for node in nodes:
        for kind in _types(node):
            if kind not in types:
                types.append(kind)
    data: dict[str, Any] = {"types": types, "json_ld": nodes}
    if og:
        data["open_graph"] = og
    return data
