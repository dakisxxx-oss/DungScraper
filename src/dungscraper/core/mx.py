"""Check whether an email domain can receive mail (MX or address record)."""

from __future__ import annotations

import asyncio

import dns.asyncresolver
import dns.exception
import dns.resolver


async def domain_accepts_mail(domain: str, *, timeout: float = 4.0) -> bool | None:
    """True if the domain has a mail server, False if it has none, None if unknown.

    A domain with no MX record but with an A or AAAA record still counts as true
    (RFC 5321 implicit MX). Timeouts and network errors give None, not False.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout
    try:
        answer = await resolver.resolve(domain, "MX")
        # A "null MX" (a single "." target) says the domain takes no mail.
        targets = [str(r.exchange).rstrip(".") for r in answer]
        return any(targets)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return False
    except dns.resolver.NoAnswer:
        pass
    except (dns.exception.Timeout, OSError):
        return None
    for record in ("A", "AAAA"):
        try:
            await resolver.resolve(domain, record)
            return True
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN:
            return False
        except (dns.exception.Timeout, dns.resolver.NoNameservers, OSError):
            return None
    return False


async def check_domains(domains: set[str]) -> dict[str, bool | None]:
    ordered = sorted(domains)
    results = await asyncio.gather(*(domain_accepts_mail(d) for d in ordered))
    return dict(zip(ordered, results, strict=True))
