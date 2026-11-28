"""API keys. If none are configured, local use is open and a warning is logged."""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

from dungscraper.errors import DungScraperError


def client_ip(request: Request) -> str:
    """Peer address. X-Forwarded-For is ignored so clients cannot spoof their bucket."""
    if request.client and request.client.host:
        return request.client.host
    return ""


def extract_api_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(header)
    if scheme.lower() != "bearer":
        token = request.headers.get("x-api-key", "")
    return token or ""


async def require_api_key(request: Request) -> str | None:
    """Return the matching key, or None when the request is open (no keys, or anonymous is on)."""
    settings = request.app.state.services.settings
    keys = settings.parsed_api_keys
    if not keys:
        return None
    token = extract_api_token(request)
    if token:
        matched = next((key for key in keys if secrets.compare_digest(token, key)), None)
        if matched is None:
            raise DungScraperError(
                "unauthorized",
                "Missing or unknown API key. Send Authorization: Bearer <key> or X-API-Key.",
                status=401,
            )
        return matched
    if not keys or settings.allow_anonymous:
        return None
    raise DungScraperError(
        "unauthorized",
        "Missing or unknown API key. Send Authorization: Bearer <key> or X-API-Key.",
        status=401,
    )
