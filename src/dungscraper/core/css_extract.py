"""Pull JSON out of a page with CSS or XPath selectors. No model, no API key, no cost.

Schema (Crawl4AI-style, plus a short form)::

    {"baseSelector": ".card",                      # optional: one item per match
     "fields": [
        {"name": "title", "selector": "h2"},                       # text
        {"name": "link",  "selector": "a", "type": "attribute", "attribute": "href"},
        {"name": "price", "selector": ".price", "type": "regex", "pattern": "[\\d.,]+"},
        {"name": "tags",  "selector": ".tag", "type": "list"},
        {"name": "specs", "selector": "li", "type": "nested", "fields": [...]},
        {"name": "id",    "selector": "xpath://@data-id"}]}

Short form: ``{"title": "h1", "link": "a@href", "price": ".price"}``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from lxml import etree
from lxml import html as lxml_html

from dungscraper.errors import DungScraperError

MAX_ITEMS = 500
URL_ATTRS = {"href", "src", "srcset", "data-src", "action"}


def _bad(message: str) -> DungScraperError:
    return DungScraperError("invalid_schema", message, status=422)


def normalize_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        raise _bad("schema must be a non-empty JSON object.")
    if "fields" in schema:
        fields = schema["fields"]
        if not isinstance(fields, list) or not fields:
            raise _bad("schema.fields must be a non-empty list.")
        return {"baseSelector": schema.get("baseSelector") or schema.get("base_selector") or "", "fields": fields}
    fields = []
    for name, spec in schema.items():
        if name in {"baseSelector", "base_selector", "name"}:
            continue
        if isinstance(spec, dict):
            fields.append({"name": name, **spec})
        elif isinstance(spec, str):
            selector, _, attribute = spec.partition("@")
            field: dict[str, Any] = {"name": name, "selector": selector.strip()}
            if attribute.strip():
                field.update({"type": "attribute", "attribute": attribute.strip()})
            fields.append(field)
        else:
            raise _bad(f"Field {name!r} must be a selector string or an object.")
    if not fields:
        raise _bad("schema has no fields.")
    return {"baseSelector": schema.get("baseSelector") or schema.get("base_selector") or "", "fields": fields}


def _text(node: Any) -> str:
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node).strip()
    if isinstance(node, Tag):
        return re.sub(r"\s+", " ", node.get_text(" ")).strip()
    if hasattr(node, "text_content"):
        return re.sub(r"\s+", " ", node.text_content()).strip()
    return ""


def _select(root_soup: BeautifulSoup | Tag, root_lxml: Any, selector: str) -> list[Any]:
    selector = (selector or "").strip()
    if not selector:
        return [root_soup if root_lxml is None else root_lxml]
    if selector.startswith("xpath:"):
        if root_lxml is None:
            raise _bad("xpath: selectors only work on the page root or a base item.")
        try:
            found = root_lxml.xpath(selector[6:])
        except etree.XPathError as exc:
            raise _bad(f"Bad XPath {selector!r}: {exc}") from exc
        return found if isinstance(found, list) else [found]
    try:
        return list(root_soup.select(selector))
    except Exception as exc:  # noqa: BLE001
        raise _bad(f"Bad CSS selector {selector!r}: {exc}") from exc


def _apply_transform(value: Any, transform: str | None) -> Any:
    if not isinstance(value, str) or not transform:
        return value
    return {"lowercase": value.lower, "uppercase": value.upper, "strip": value.strip}.get(transform, lambda: value)()


def _field_value(field: dict[str, Any], scope_soup: Tag | BeautifulSoup, scope_lxml: Any, base_url: str) -> Any:
    kind = field.get("type") or ("attribute" if field.get("attribute") else "text")
    selector = field.get("selector", "")
    nodes = _select(scope_soup, scope_lxml, selector)
    default = field.get("default")
    if kind == "nested":
        items = []
        for node in nodes[:MAX_ITEMS]:
            items.append(_extract_fields(field.get("fields", []), node, None, base_url))
        return items
    if kind == "list" and not nodes:
        return []
    if not nodes:
        return default
    if kind == "list":
        values = []
        for node in nodes[:MAX_ITEMS]:
            values.append(_one_value(field, node, base_url))
        return [v for v in values if v not in (None, "")]
    return _one_value(field, nodes[0], base_url)


def _one_value(field: dict[str, Any], node: Any, base_url: str) -> Any:
    kind = field.get("type") or ("attribute" if field.get("attribute") else "text")
    if kind == "list":
        kind = "attribute" if field.get("attribute") else "text"
    if isinstance(node, str):  # xpath returned a text or attribute string
        value: Any = _text(node)
    elif kind == "attribute":
        attr = field.get("attribute", "")
        raw = node.get(attr) if hasattr(node, "get") else None
        if isinstance(raw, list):
            raw = " ".join(raw)
        value = urljoin(base_url, raw) if raw and attr in URL_ATTRS and base_url else raw
    elif kind == "html":
        value = node.decode_contents() if isinstance(node, Tag) else etree.tostring(node, encoding="unicode")
    elif kind == "regex":
        text = _text(node)
        try:
            match = re.search(field.get("pattern", ""), text)
        except re.error as exc:
            raise _bad(f"Bad regex {field.get('pattern')!r}: {exc}") from exc
        value = (match.group(1) if match and match.groups() else match.group(0)) if match else None
    else:
        value = _text(node)
    if value in (None, "") and field.get("default") is not None:
        value = field["default"]
    return _apply_transform(value, field.get("transform"))


def _extract_fields(fields: list[dict[str, Any]], scope_soup: Any, scope_lxml: Any, base_url: str) -> dict[str, Any]:
    if scope_lxml is None and isinstance(scope_soup, Tag):
        try:
            scope_lxml = lxml_html.fromstring(str(scope_soup))
        except (etree.ParserError, ValueError):
            scope_lxml = None
    out: dict[str, Any] = {}
    for field in fields:
        name = field.get("name")
        if not name:
            raise _bad("Every field needs a name.")
        out[name] = _field_value(field, scope_soup, scope_lxml, base_url)
    return out


def extract_with_schema(html: str, schema: Any, *, base_url: str = "") -> dict[str, Any]:
    """Return {"items": [...], "count": n} with a baseSelector, else {"data": {...}}."""
    normalized = normalize_schema(schema)
    soup = BeautifulSoup(html or "", "lxml")
    try:
        root_lxml = lxml_html.fromstring(html or "<html></html>")
    except (etree.ParserError, ValueError):
        root_lxml = None
    base = normalized["baseSelector"]
    if base:
        nodes = _select(soup, root_lxml, base)
        items = [
            _extract_fields(normalized["fields"], node, None, base_url)
            for node in nodes[:MAX_ITEMS]
            if isinstance(node, Tag)
        ]
        return {"count": len(items), "items": items}
    return {"data": _extract_fields(normalized["fields"], soup, root_lxml, base_url)}
