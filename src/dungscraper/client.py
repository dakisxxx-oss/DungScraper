"""Small synchronous client for the DungScraper HTTP API."""

from __future__ import annotations

import os
from typing import Any

import httpx

from dungscraper.errors import DungScraperAPIError


class DungScraper:
    """Talk to a running DungScraper server.

    The server does the fetching. Use ``--local`` on the CLI when you want the
    same machine to scrape without a server.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 180,
    ) -> None:
        self.base_url = (base_url or os.environ.get("DUNGSCRAPER_BASE_URL") or "http://127.0.0.1:8741").rstrip("/")
        if api_key is None:
            api_key = os.environ.get("DUNGSCRAPER_API_KEY")
        self.api_key = api_key or None
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers=self._headers(), base_url=self.base_url)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> DungScraper:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        try:
            response = self._http().request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise DungScraperAPIError(
                "connection_error",
                f"Could not reach DungScraper at {self.base_url}. Start it with `dungscraper serve`, "
                f"or pass --local to run in this process. ({exc})",
                status=503,
            ) from exc
        try:
            body = response.json()
        except Exception as exc:
            raise DungScraperAPIError(
                "bad_response",
                f"DungScraper returned a non-JSON response ({response.status_code}).",
                status=response.status_code,
            ) from exc
        if response.status_code >= 400 or body.get("success") is False:
            error = body.get("error") or {}
            raise DungScraperAPIError(
                error.get("code") or "http_error",
                error.get("message") or response.text[:500],
                status=response.status_code,
                payload=body,
            )
        return body

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def scrape(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/scrape", {"url": url, **options})["data"]

    def batch_scrape(self, urls: list[str], **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/batch/scrape", {"urls": urls, **options})["data"]

    def crawl(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/crawl", {"url": url, **options})["data"]

    def crawl_status(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/crawl/{job_id}")["data"]

    def cancel_crawl(self, job_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/crawl/{job_id}")["data"]

    def map(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/map", {"url": url, **options})["data"]

    def search(self, query: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/search", {"query": query, **options})["data"]

    def extract(self, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/extract", options)["data"]

    def chunk(self, text: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/chunk", {"text": text, **options})["data"]
