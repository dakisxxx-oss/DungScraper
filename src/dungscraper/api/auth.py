"""API keys. If none are configured, local use is open and a warning is logged."""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

from dungscraper.errors import DungScraperError


async def require_api_key(request: Request) -> str | None:
    """Return the key that matched, or None when the server has no keys and is open."""
    keys = request.app.state.services.settings.parsed_api_keys
    if not keys:
        return None
    header = request.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(header)
    if scheme.lower() != "bearer":
        token = request.headers.get("x-api-key", "")
    matched = next((key for key in keys if token and secrets.compare_digest(token, key)), None)
    if matched is None:
        raise DungScraperError(
            "unauthorized",
            "Missing or unknown API key. Send Authorization: Bearer <key> or X-API-Key.",
            status=401,
        )
    return matched
