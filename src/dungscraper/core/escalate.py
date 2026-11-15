"""Challenge-only fetch ladder.

Plain HTTP runs first. Curl impersonation, Patchright, Camoufox, a
FlareSolverr-compatible sidecar, and opt-in SeleniumBase run only after a
challenge page. Missing optional packages are skipped with ``tier_unavailable``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from dungscraper.config import Settings
from dungscraper.core.content import challenge_vendor, is_challenge
from dungscraper.core.feeds import discover_feed, feed_fetch_result, medium_feed_url
from dungscraper.core.fetch import BrowserManager, RawFetch, merge_headers
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import ScrapeRequest

log = get_logger("dungscraper.escalate")

BROWSER_TIERS = {"patchright", "camoufox", "seleniumbase"}


class TierUnavailable(DungScraperError):
    def __init__(self, tier: str, package: str) -> None:
        super().__init__(
            "tier_unavailable",
            f"The {tier} tier needs {package}. Install it with: pip install 'dungscraper[stealth]'.",
            status=501,
        )
        self.tier = tier


@dataclass
class Clearance:
    cookies: dict[str, str]
    user_agent: str


@dataclass
class CookieJar:
    by_domain: dict[str, Clearance] = field(default_factory=dict)

    def get(self, url: str) -> Clearance | None:
        return self.by_domain.get(_domain(url))

    def put(self, url: str, cookies: dict[str, str], user_agent: str) -> None:
        if not cookies:
            return
        self.by_domain[_domain(url)] = Clearance(cookies=dict(cookies), user_agent=user_agent)


JAR = CookieJar()


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def ladder_for(settings: Settings, render: str) -> list[str]:
    order = [tier for tier in settings.tier_order() if tier != "http"]
    if render == "http":
        return []
    if not settings.flaresolverr_url:
        order = [tier for tier in order if tier != "flaresolverr"]
    if not settings.solve_captcha:
        order = [tier for tier in order if tier != "seleniumbase"]
    if render == "browser":
        return [tier for tier in order if tier in BROWSER_TIERS or tier == "flaresolverr"]
    return ["rss", *order]


async def escalate_challenge(
    start: RawFetch,
    *,
    request: ScrapeRequest,
    settings: Settings,
    client: httpx.AsyncClient,
    browser: BrowserManager,
    proxy: str | None,
    runners: dict | None = None,
) -> RawFetch:
    """Try later tiers only while the latest response is still a challenge."""
    current = start
    current.tier = current.tier or "http"
    if request.render == "http" or not is_challenge(current.text or "", current.status_code):
        return current
    runners = runners if runners is not None else _production_runners(
        request, settings, client, browser, proxy
    )
    for name in ladder_for(settings, request.render):
        runner = runners.get(name)
        if runner is None:
            continue
        try:
            fetched = await runner(current)
        except TierUnavailable as exc:
            current.warnings.append(f"tier_unavailable:{exc.tier}")
            continue
        except DungScraperError as exc:
            current.warnings.append(f"{name}_failed:{exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            log.info("tier failed", extra={"data": {"tier": name, "error": str(exc)}})
            current.warnings.append(f"{name}_failed")
            continue
        if fetched is None:
            continue
        if "tier_unavailable:patchright" in fetched.warnings:
            fetched.tier = "playwright"
        else:
            fetched.tier = name
        fetched.warnings = [*current.warnings, *fetched.warnings, f"fetch_tier:{fetched.tier}"]
        if not is_challenge(fetched.text or "", fetched.status_code):
            return fetched
        current = fetched
    return current


def _production_runners(request, settings, client, browser, proxy):
    async def rss(_current: RawFetch):
        return await _fetch_feed(request.url, _current.text or "", client)

    async def curl(_current: RawFetch):
        return await _fetch_curl(request, settings, proxy)

    async def patchright(_current: RawFetch):
        return await _fetch_patchright(request, settings, browser, proxy)

    async def camoufox(_current: RawFetch):
        return await _fetch_camoufox(request, settings, proxy)

    async def flaresolverr(_current: RawFetch):
        return await _fetch_flaresolverr(request, settings, proxy)

    async def seleniumbase(_current: RawFetch):
        return await _fetch_seleniumbase(request, settings, proxy)

    return {
        "rss": rss,
        "curl": curl,
        "patchright": patchright,
        "camoufox": camoufox,
        "flaresolverr": flaresolverr,
        "seleniumbase": seleniumbase,
    }


async def _fetch_feed(url: str, html: str, client: httpx.AsyncClient) -> RawFetch | None:
    feed_url = medium_feed_url(url) or discover_feed(html, url)
    if not feed_url:
        return None
    response = await client.get(feed_url, timeout=20, headers={"Accept": "application/rss+xml, application/atom+xml, */*"})
    if response.status_code >= 400:
        return None
    source = "medium" if medium_feed_url(url) else "alternate"
    return feed_fetch_result(url, response.text, source=source)


async def _fetch_curl(request: ScrapeRequest, settings: Settings, proxy: str | None) -> RawFetch:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError as exc:
        raise TierUnavailable("curl", "curl_cffi") from exc
    clearance = JAR.get(request.url)
    headers = merge_headers(settings, request.headers)
    cookies = dict(request.cookies)
    if clearance:
        cookies.update(clearance.cookies)
        if clearance.user_agent:
            headers["User-Agent"] = clearance.user_agent
    timeout = (request.timeout_ms or int(settings.request_timeout_seconds * 1000)) / 1000
    impersonate = "chrome131"
    try:
        session = AsyncSession(impersonate=impersonate)
    except Exception:
        session = AsyncSession(impersonate="chrome")
    async with session:
        response = await session.get(
            request.url,
            headers=headers,
            cookies=cookies or None,
            proxy=proxy,
            timeout=timeout,
            allow_redirects=True,
        )
    body = response.content or b""
    text = ""
    try:
        text = response.text or ""
    except Exception:
        text = body.decode("utf-8", "replace")
    fetched = RawFetch(
        url=str(getattr(response, "url", request.url)),
        status_code=response.status_code,
        headers={key: value for key, value in response.headers.items()},
        content=body[: settings.max_response_bytes],
        text=text,
        content_type=response.headers.get("content-type", ""),
        render_mode="curl",
        tier="curl",
    )
    response_cookies = {}
    raw_cookies = getattr(response, "cookies", None)
    if raw_cookies is not None:
        try:
            response_cookies = dict(raw_cookies)
        except Exception:
            response_cookies = {}
    if any(name.startswith("cf_") or name in {"cf_clearance"} for name in response_cookies):
        JAR.put(request.url, response_cookies, headers.get("User-Agent", ""))
    return fetched


async def _fetch_patchright(
    request: ScrapeRequest, settings: Settings, browser: BrowserManager, proxy: str | None
) -> RawFetch:
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        fetched = await browser.fetch(
            request.url,
            headers=request.headers,
            cookies=request.cookies,
            proxy=proxy,
            timeout_ms=request.timeout_ms,
            wait_until=request.wait_until,
            wait_for=request.wait_for,
            wait_ms=request.wait_ms,
            actions=request.actions,
            screenshot="screenshot" in request.formats,
            screenshot_full_page=request.screenshot_full_page,
        )
        fetched.tier = "playwright"
        fetched.warnings.append("tier_unavailable:patchright")
        return fetched
    timeout = request.timeout_ms or int(settings.browser_timeout_seconds * 1000)
    async with async_playwright() as playwright:
        launched = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = await launched.new_context(
            user_agent=merge_headers(settings, request.headers).get("User-Agent"),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            proxy={"server": proxy} if proxy else None,
        )
        page = await context.new_page()
        try:
            response = await page.goto(request.url, wait_until=request.wait_until, timeout=timeout)
            html = await page.content()
            status = response.status if response is not None else 200
            return RawFetch(
                url=page.url,
                status_code=status,
                headers={"content-type": "text/html"},
                content=html.encode(),
                text=html,
                content_type="text/html",
                render_mode="browser",
                tier="patchright",
            )
        finally:
            await context.close()
            await launched.close()


async def _fetch_camoufox(request: ScrapeRequest, settings: Settings, proxy: str | None) -> RawFetch:
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        raise TierUnavailable("camoufox", "camoufox") from exc
    timeout = request.timeout_ms or int(settings.browser_timeout_seconds * 1000)
    async with AsyncCamoufox(headless=True, proxy={"server": proxy} if proxy else None) as browser:
        page = await browser.new_page()
        response = await page.goto(request.url, timeout=timeout)
        html = await page.content()
        status = response.status if response is not None else 200
        return RawFetch(
            url=page.url,
            status_code=status,
            headers={"content-type": "text/html"},
            content=html.encode(),
            text=html,
            content_type="text/html",
            render_mode="browser",
            tier="camoufox",
        )


async def _fetch_flaresolverr(request: ScrapeRequest, settings: Settings, proxy: str | None) -> RawFetch:
    if not settings.flaresolverr_url:
        return None
    payload: dict = {"cmd": "request.get", "url": request.url, "maxTimeout": 60000}
    if proxy:
        payload["proxy"] = {"url": proxy}
    async with httpx.AsyncClient(timeout=70) as http:
        response = await http.post(settings.flaresolverr_url, json=payload)
    if response.status_code >= 400:
        raise DungScraperError(
            "flaresolverr_error",
            f"FlareSolverr sidecar returned HTTP {response.status_code}.",
            status=502,
        )
    body = response.json()
    if body.get("status") != "ok":
        raise DungScraperError(
            "flaresolverr_error",
            body.get("message") or "FlareSolverr could not solve this page.",
            status=502,
        )
    solution = body.get("solution") or {}
    cookies = {item["name"]: item["value"] for item in solution.get("cookies") or [] if item.get("name")}
    user_agent = solution.get("userAgent") or ""
    JAR.put(request.url, cookies, user_agent)
    html = solution.get("response") or ""
    return RawFetch(
        url=solution.get("url") or request.url,
        status_code=int(solution.get("status") or 200),
        headers={"content-type": "text/html"},
        content=html.encode(),
        text=html,
        content_type="text/html",
        render_mode="flaresolverr",
        tier="flaresolverr",
    )


async def _fetch_seleniumbase(request: ScrapeRequest, settings: Settings, proxy: str | None) -> RawFetch:
    if not settings.solve_captcha:
        return None
    try:
        from seleniumbase import SB
    except ImportError as exc:
        raise TierUnavailable("seleniumbase", "seleniumbase") from exc

    def _run() -> tuple[str, str]:
        with SB(uc=True, headless=True, proxy=proxy) as sb:
            sb.open(request.url)
            sb.solve_captcha()
            return sb.get_page_source(), ""

    import asyncio

    html, _ua = await asyncio.to_thread(_run)
    return RawFetch(
        url=request.url,
        status_code=200,
        headers={"content-type": "text/html"},
        content=html.encode(),
        text=html,
        content_type="text/html",
        render_mode="browser",
        tier="seleniumbase",
        warnings=["captcha_opt_in"],
    )
