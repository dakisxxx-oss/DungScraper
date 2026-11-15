"""API keys. If none are configured, local use is open and a warning is logged."""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

from dungscraper.errors import DungScraperError


async def require_api_key(request: Request) -> None:
    keys = request.app.state.services.settings.parsed_api_keys
    if not keys:
        return
    header = request.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(header)
    if scheme.lower() != "bearer":
        token = request.headers.get("x-api-key", "")
    if not token or not any(secrets.compare_digest(token, key) for key in keys):
        raise DungScraperError(
            "unauthorized",
            "Missing or unknown API key. Send Authorization: Bearer <key> or X-API-Key.",
            status=401,
        )
