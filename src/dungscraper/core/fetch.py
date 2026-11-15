"""Fast HTTP fetch, with a headless Chromium fallback for pages that need it."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, async_playwright

from dungscraper.config import Settings
from dungscraper.core.content import is_challenge
from dungscraper.core.urls import safe_url
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import BrowserAction
from dungscraper.security import assert_public_url

log = get_logger("dungscraper.fetch")

BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


@dataclass
class RawFetch:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    text: str
    content_type: str
    render_mode: str
    screenshot: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    tier: str = "http"


def merge_headers(settings: Settings, extra: dict[str, str]) -> dict[str, str]:
    headers = {
        "User-Agent": settings.fetch_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    for key, value in extra.items():
        if key.lower() in BLOCKED_HEADERS:
            continue
        headers[key] = value
    return headers


def sniff_kind(content: bytes, content_type: str, url: str) -> str:
    ctype = (content_type or "").lower()
    path = urlsplit(url).path.lower()
    head = content[:1200].lstrip()
    lowered = head.lower()
    if head.startswith(b"%PDF") or "application/pdf" in ctype:
        return "pdf"
    if b"wordprocessingml" in lowered or "wordprocessingml" in ctype:
        return "docx"
    if (
        lowered.startswith(b"<")
        or b"<html" in lowered
        or b"<!doctype html" in lowered
        or "html" in ctype
        or "xml" in ctype
    ):
        return "html"
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".docx"):
        return "docx"
    if ctype.startswith("text/") or not ctype:
        return "text"
    return "binary"


def _guard(allow_private: bool):
    async def hook(request: httpx.Request) -> None:
        assert_public_url(str(request.url), allow_private=allow_private)

    return hook


def make_client(settings: Settings, *, proxy: str | None = None, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    timeout = httpx.Timeout(settings.request_timeout_seconds, connect=10.0)
    kwargs: dict = {
        "follow_redirects": True,
        "timeout": timeout,
        "headers": headers or merge_headers(settings, {}),
        "event_hooks": {"request": [_guard(settings.allow_private_networks)]},
        "limits": httpx.Limits(max_connections=50, max_keepalive_connections=20),
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int,
    timeout: float | None = None,
    **kwargs,
) -> httpx.Response:
    delay = 0.4
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, timeout=timeout, **kwargs)
        except DungScraperError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt >= retries:
                raise DungScraperError(
                    "fetch_failed",
                    f"Request to {safe_url(url)} failed after {retries + 1} attempts: {exc}",
                    status=504,
                ) from exc
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if response.status_code in {429, 502, 503, 504} and attempt < retries:
            retry_after = response.headers.get("retry-after", "")
            wait = float(retry_after) if retry_after.isdigit() else delay
            await asyncio.sleep(min(wait, 8))
            delay *= 2
            continue
        return response
    raise DungScraperError("fetch_failed", f"Request to {safe_url(url)} failed: {last_error}", status=504)


async def http_fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    settings: Settings,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float | None = None,
) -> RawFetch:
    response = await request_with_retries(
        client,
        "GET",
        url,
        retries=settings.http_retries,
        timeout=timeout or settings.request_timeout_seconds,
        headers=merge_headers(settings, headers or {}),
        cookies=cookies or None,
    )
    content = response.content[: settings.max_response_bytes]
    warnings: list[str] = []
    if len(response.content) > settings.max_response_bytes:
        warnings.append("response_truncated")
    text = ""
    content_type = response.headers.get("content-type", "")
    if sniff_kind(content, content_type, str(response.url)) in {"html", "text"}:
        text = response.text if len(response.content) <= settings.max_response_bytes else content.decode("utf-8", "replace")
    if response.status_code >= 400:
        warnings.append(f"http_status_{response.status_code}")
    return RawFetch(
        url=str(response.url),
        status_code=response.status_code,
        headers={key: value for key, value in response.headers.items()},
        content=content,
        text=text,
        content_type=content_type,
        render_mode="http",
        warnings=warnings,
    )


def _playwright_proxy(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    parts = urlsplit(proxy)
    server = urlunsplit((parts.scheme, parts.hostname or "", "", "", ""))
    if parts.port:
        server = f"{parts.scheme}://{parts.hostname}:{parts.port}"
    settings = {"server": server}
    if parts.username:
        settings["username"] = parts.username
    if parts.password:
        settings["password"] = parts.password
    return settings


class StickyProxies:
    """One proxy per site for the life of the process. New sites take the next proxy."""

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = [proxy for proxy in proxies if proxy]
        self._by_domain: dict[str, str] = {}
        self._index = 0

    def for_domain(self, domain: str) -> str | None:
        if not self.proxies:
            return None
        key = (domain or "").lower()
        if key not in self._by_domain:
            self._by_domain[key] = self.proxies[self._index % len(self.proxies)]
            self._index += 1
        return self._by_domain[key]


_sticky: dict[tuple[str, ...], StickyProxies] = {}


def sticky_proxies(settings: Settings) -> StickyProxies:
    key = tuple(settings.proxy_list)
    pool = _sticky.get(key)
    if pool is None:
        pool = StickyProxies(list(key))
        _sticky[key] = pool
    return pool


class ProxyRotator:
    """Round-robin over DUNGSCRAPER_PROXY_URL plus DUNGSCRAPER_PROXY_URLS."""

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = [proxy for proxy in proxies if proxy]
        self._index = 0

    def next(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy


_rotators: dict[tuple[str, ...], ProxyRotator] = {}


def proxy_rotator(settings: Settings) -> ProxyRotator:
    key = tuple(settings.proxy_list)
    rotator = _rotators.get(key)
    if rotator is None:
        rotator = ProxyRotator(list(key))
        _rotators[key] = rotator
    return rotator


STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


class BrowserManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.browser_concurrency)

    async def shutdown(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure(self) -> Browser:
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
        return self._browser

    async def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        proxy: str | None = None,
        timeout_ms: int | None = None,
        wait_until: str = "domcontentloaded",
        wait_for: str | None = None,
        wait_ms: int | None = None,
        actions: list[BrowserAction] | None = None,
        screenshot: bool = False,
        screenshot_full_page: bool = False,
    ) -> RawFetch:
        async with self._semaphore:
            browser = await self._ensure()
            merged = merge_headers(self.settings, headers or {})
            user_agent = merged.pop("User-Agent", self.settings.fetch_user_agent)
            # The browser sets Sec-Fetch-* itself. Forcing document values onto
            # every subresource is a tell, so those stay off the context.
            merged.pop("sec-ch-ua", None)
            merged.pop("sec-ch-ua-mobile", None)
            merged.pop("sec-ch-ua-platform", None)
            merged.pop("Upgrade-Insecure-Requests", None)
            context_kwargs: dict = {
                "user_agent": user_agent,
                "extra_http_headers": merged,
                "viewport": {"width": 1366, "height": 768},
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "color_scheme": "light",
            }
            proxy_settings = _playwright_proxy(proxy or self.settings.proxy_url or None)
            if proxy_settings:
                context_kwargs["proxy"] = proxy_settings
            context = await browser.new_context(**context_kwargs)
            allow_private = self.settings.allow_private_networks

            async def route_request(route) -> None:
                target = route.request.url
                if target.startswith(("data:", "blob:", "about:")):
                    await route.continue_()
                    return
                try:
                    assert_public_url(target, allow_private=allow_private)
                except DungScraperError:
                    await route.abort()
                    return
                await route.continue_()

            page = await context.new_page()
            await page.add_init_script(STEALTH_INIT)
            timeout = timeout_ms or int(self.settings.browser_timeout_seconds * 1000)
            page.set_default_timeout(timeout)
            try:
                await page.route("**/*", route_request)
                if cookies:
                    await context.add_cookies(
                        [{"name": key, "value": value, "url": url} for key, value in cookies.items()]
                    )
                response = None
                last_goto: Exception | None = None
                for attempt in range(2):
                    try:
                        response = await page.goto(url, wait_until=wait_until, timeout=timeout)
                        last_goto = None
                        break
                    except Exception as exc:
                        last_goto = exc
                        if attempt == 0:
                            await asyncio.sleep(0.6)
                            continue
                        raise DungScraperError(
                            "fetch_failed",
                            f"Browser navigation to {safe_url(url)} failed: {exc}",
                            status=504,
                        ) from exc
                if last_goto is not None:
                    raise DungScraperError(
                        "fetch_failed",
                        f"Browser navigation to {safe_url(url)} failed: {last_goto}",
                        status=504,
                    ) from last_goto
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=timeout)
                if wait_ms:
                    await page.wait_for_timeout(wait_ms)
                for index, action in enumerate(actions or []):
                    try:
                        await _run_action(page, action)
                    except DungScraperError:
                        raise
                    except Exception as exc:
                        raise DungScraperError(
                            "action_failed",
                            f"Browser action {index} ({action.type}) failed: {exc}",
                            status=422,
                        ) from exc
                html = await page.content()
                status = response.status if response is not None else 200
                if is_challenge(html, status):
                    try:
                        await page.wait_for_function(
                            """() => {
                                const title = (document.title || '').toLowerCase();
                                const words = (document.body && document.body.innerText || '').trim().split(/\\s+/).length;
                                return !title.includes('just a moment') && words > 40;
                            }""",
                            timeout=8000,
                        )
                    except Exception:
                        pass
                    html = await page.content()
                shot = None
                if screenshot:
                    shot = await page.screenshot(type="jpeg", quality=74, full_page=screenshot_full_page)
                warnings = [f"http_status_{status}"] if status >= 400 else []
                if is_challenge(html, status):
                    warnings.append("challenge_unresolved")
                return RawFetch(
                    url=page.url,
                    status_code=status,
                    headers={"content-type": "text/html"},
                    content=html.encode("utf-8", "replace"),
                    text=html,
                    content_type="text/html",
                    render_mode="browser",
                    screenshot=shot,
                    warnings=warnings,
                )
            finally:
                await context.close()


async def _run_action(page, action: BrowserAction) -> None:
    if action.type == "wait":
        await page.wait_for_timeout(action.ms or 1000)
        return
    if action.type == "click":
        if not action.selector:
            raise DungScraperError("invalid_action", "click requires a selector", status=400)
        await page.click(action.selector, timeout=10_000)
        return
    if action.type == "type":
        if action.selector:
            await page.fill(action.selector, action.text or "")
        else:
            await page.keyboard.type(action.text or "")
        return
    if action.type == "press":
        await page.keyboard.press(action.key or "Enter")
        return
    if action.type == "scroll":
        if action.selector:
            await page.locator(action.selector).scroll_into_view_if_needed()
        else:
            await page.evaluate("(pixels) => window.scrollBy(0, pixels)", action.pixels or 1200)
        return
    raise DungScraperError("invalid_action", f"Unknown action {action.type}", status=400)
