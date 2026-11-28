"""FastAPI application: REST, playground, and streamable HTTP MCP."""

from __future__ import annotations

import contextvars
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request as StarletteRequest

from dungscraper import __version__
from dungscraper.api.routes import router
from dungscraper.config import Settings, get_settings
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.mcp_server import build_mcp
from dungscraper.playground import PLAYGROUND_HTML
from dungscraper.services import shutdown, startup

log = get_logger("dungscraper.api")
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

DESCRIPTION = """
DungScraper turns web pages into Markdown and JSON for AI agents.

* **scrape** fetches one URL. It tries a fast HTTP request first and renders with Chromium when the page is thin or scripted.
* **crawl** walks a site with a depth limit, robots.txt, sitemap seeds, per-domain spacing, and near-duplicate skipping.
* **map** lists URLs from sitemaps and the links on the entry page.
* **search** runs DuckDuckGo (no key) or SearXNG, Brave, or Serper, then scrapes the hits.
* **extract** asks an OpenAI-compatible model for JSON and checks it against a schema.
* **chunk** splits Markdown for retrieval.

MCP is on `POST /mcp` (streamable HTTP). The same tools are available over stdio with `dungscraper mcp`.
""".strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    mcp = build_mcp()
    mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="0.0.0.0")
    session_manager = mcp._lowlevel_server._session_manager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = await startup(settings)
        async with session_manager.run():
            yield
        await shutdown(app.state.services)

    app = FastAPI(
        title="DungScraper",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    origins = (
        ["*"]
        if settings.cors_origins.strip() == "*"
        else [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(DungScraperError)
    async def dungscraper_error(_request: Request, exc: DungScraperError):
        body = {
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id_var.get(),
            },
        }
        return JSONResponse(status_code=exc.status, content=body, headers=exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed.",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                    "request_id": request_id_var.get(),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "internal_error",
                    "message": "DungScraper hit an unexpected error.",
                    "request_id": request_id_var.get(),
                },
            },
        )

    @app.get("/", include_in_schema=False)
    async def playground() -> HTMLResponse:
        return HTMLResponse(PLAYGROUND_HTML)

    @app.get("/health", tags=["meta"], summary="Process health")
    async def health(request: Request):
        redis_state = "disabled"
        services = getattr(request.app.state, "services", None)
        if services is not None and services.redis is not None:
            try:
                await services.redis.ping()
                redis_state = "ok"
            except Exception:
                redis_state = "error"
        return {
            "status": "ok",
            "version": __version__,
            "redis": redis_state,
            "crawl_mode": settings.resolved_crawl_mode(),
            "auth": bool(settings.parsed_api_keys),
            "anonymous": bool(settings.allow_anonymous),
            "proxies": len(settings.proxy_list),
        }

    app.include_router(router)

    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp

    class MCPEndpoint:
        """ASGI endpoint. A bare function would be wrapped as a request handler."""

        def __init__(self) -> None:
            self.asgi = StreamableHTTPASGIApp(session_manager)

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and settings.parsed_api_keys:
                import secrets

                starlette_request = StarletteRequest(scope)
                header = starlette_request.headers.get("authorization", "")
                token = header.split(" ", 1)[1].strip() if header.lower().startswith("bearer ") else ""
                token = token or starlette_request.headers.get("x-api-key", "")
                matched = next((key for key in settings.parsed_api_keys if token and secrets.compare_digest(token, key)), None)
                if token and matched is None:
                    response = JSONResponse(
                        status_code=401,
                        content={
                            "success": False,
                            "error": {"code": "unauthorized", "message": "Missing or unknown API key."},
                        },
                    )
                    await response(scope, receive, send)
                    return
                if matched is None and not settings.allow_anonymous:
                    response = JSONResponse(
                        status_code=401,
                        content={
                            "success": False,
                            "error": {"code": "unauthorized", "message": "Missing or unknown API key."},
                        },
                    )
                    await response(scope, receive, send)
                    return
                peer = scope.get("client") or ("", 0)
                ip = peer[0] if isinstance(peer, (list, tuple)) and peer else ""
                try:
                    scope["app"].state.services.usage.check(matched, "/mcp", client_ip=ip)
                except DungScraperError as exc:
                    response = JSONResponse(
                        status_code=exc.status,
                        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
                        headers=exc.headers or None,
                    )
                    await response(scope, receive, send)
                    return
            await self.asgi(scope, receive, send)

    app.add_route("/mcp", MCPEndpoint())
    return app


app = create_app()
