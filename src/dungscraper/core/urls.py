"""URL normalization, site rules, and path filters."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dungscraper.errors import DungScraperError

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
}

SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".mp4",
    ".mp3",
    ".zip",
    ".gz",
    ".tgz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".avi",
    ".mov",
    ".webm",
    ".wasm",
}


def validate_http_url(url: str) -> str:
    value = (url or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
        raise DungScraperError(
            "invalid_url",
            "URL must be an absolute http or https address.",
            status=400,
        )
    return value


def safe_url(url: str) -> str:
    """Drop userinfo so logs and errors do not keep passwords."""
    parts = urlsplit(url)
    if not parts.username and not parts.password:
        return url
    host = parts.hostname or ""
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        return host[4:]
    return host


def dedupe_key(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = host_key(url)
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query_pairs.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def clean_request_url(url: str) -> str:
    """URL we actually fetch: no fragment, no tracking params, userinfo kept."""
    parts = urlsplit(url.strip())
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query_pairs), ""))


def same_site(url: str, root: str, *, allow_subdomains: bool) -> bool:
    left, right = host_key(url), host_key(root)
    if not left or not right:
        return False
    if left == right:
        return True
    if allow_subdomains and (left.endswith("." + right) or right.endswith("." + left)):
        return True
    return False


def path_matches(path: str, pattern: str) -> bool:
    path = path or "/"
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], path) is not None
        except re.error as exc:
            raise DungScraperError("invalid_pattern", f"Invalid regular expression {pattern!r}: {exc}") from exc
    pat = pattern.strip() or "/"
    if not pat.startswith("/"):
        pat = "/" + pat
    if pat.endswith("/**"):
        base = pat[:-3].rstrip("/") or ""
        return path == base or path == f"{base}/" or path.startswith(f"{base}/")
    if fnmatch.fnmatch(path, pat):
        return True
    if "*" not in pat and "?" not in pat:
        base = pat.rstrip("/") or "/"
        if base == "/":
            return True
        return path.rstrip("/") == base or path.startswith(base + "/")
    return False


def extension_skipped(url: str) -> bool:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix in SKIP_EXTENSIONS


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
