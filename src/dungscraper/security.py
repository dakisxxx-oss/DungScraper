"""SSRF guard. Private and link-local targets are blocked unless configured otherwise."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from dungscraper.errors import DungScraperError

BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata.internal"}


def assert_public_url(url: str, *, allow_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise DungScraperError("invalid_url", "Only http and https URLs are allowed.", status=400)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise DungScraperError("invalid_url", "URL is missing a host.", status=400)
    if allow_private:
        return
    if host in BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise DungScraperError(
            "blocked_url",
            f"{host} is a local or internal host. Set DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS=true to scrape it.",
            status=403,
        )
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise DungScraperError("dns_error", f"Could not resolve {host}.", status=400) from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    for address in addresses:
        if not address.is_global:
            raise DungScraperError(
                "blocked_url",
                f"{host} resolves to a non-public address. Set DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS=true to allow it.",
                status=403,
            )
