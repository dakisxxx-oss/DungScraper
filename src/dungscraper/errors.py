"""Errors returned to API clients and agents."""

from __future__ import annotations


class DungScraperError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        super().__init__(message)


class DungScraperAPIError(DungScraperError):
    """Raised by the Python SDK when the HTTP API returns an error."""

    def __init__(self, code: str, message: str, *, status: int = 400, payload: dict | None = None) -> None:
        super().__init__(code, message, status=status, details={"payload": payload or {}})
        self.payload = payload or {}
