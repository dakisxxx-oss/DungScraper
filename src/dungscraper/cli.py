"""Command line. `dungscraper serve` starts the API; the other commands call it."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from dungscraper.client import DungScraper
from dungscraper.errors import DungScraperError
from dungscraper.local import local_contacts, local_crawl, local_jobs, local_map, local_scrape, local_search
from dungscraper.models import ContactsRequest, CrawlRequest, JobsRequest, MapRequest, SearchRequest

app = typer.Typer(add_completion=False, no_args_is_help=True, help="DungScraper scrapes and crawls the web for agents.")


LONG_RUNNING = {"serve", "worker", "mcp"}


def ensure_utf8_streams() -> None:
    """Windows consoles and pipes default to a legacy code page. Print Unicode safely."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    stdin_reconfigure = getattr(sys.stdin, "reconfigure", None)
    if stdin_reconfigure is not None:
        try:
            stdin_reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


@app.callback()
def _root(
    ctx: typer.Context,
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="DEBUG, INFO, WARNING, or ERROR. CLI commands default to WARNING. Same as DUNGSCRAPER_LOG_LEVEL.",
    ),
) -> None:
    # Settings are read once, so the level has to be in the environment before any command runs.
    if log_level:
        os.environ["DUNGSCRAPER_LOG_LEVEL"] = log_level.upper()
    elif ctx.invoked_subcommand not in LONG_RUNNING:
        os.environ.setdefault("DUNGSCRAPER_LOG_LEVEL", "WARNING")


def _echo_error(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1)


def _dump(data) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _csv_line(values: list[str]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(values)
    return buffer.getvalue()


def format_contacts(data: dict, fmt: str) -> str:
    """json is the full result. compact is one email per line. csv is a header plus one row per email."""
    if fmt == "json":
        return _dump(data)
    rows = []
    for item in data.get("emails", []):
        where = (item.get("found_at") or [{}])[0].get("url", "")
        mx = item.get("mx")
        rows.append([item["email"], item.get("kind", ""), "" if mx is None else str(mx).lower(), where])
    if fmt == "csv":
        lines = [_csv_line(["email", "kind", "mx", "found_at"])] + [_csv_line(r) for r in rows]
        return "\n".join(lines)
    return "\n".join("  ".join(part for part in row if part) for row in rows)


def format_jobs(data: dict, fmt: str) -> str:
    """json is the full result. compact is one job per line. csv is a header plus one row per job."""
    if fmt == "json":
        return _dump(data)
    rows = []
    for item in data.get("jobs", []):
        rows.append(
            [item.get("title", ""), item["url"], ",".join(item.get("tags", [])), item.get("apply_email") or ""]
        )
    if fmt == "csv":
        lines = [_csv_line(["title", "url", "tags", "apply_email"])] + [_csv_line(r) for r in rows]
        return "\n".join(lines)
    return "\n".join("  ".join(part for part in row if part) for row in rows)


def _check_format(fmt: str) -> str:
    if fmt not in ("json", "compact", "csv"):
        raise typer.BadParameter("format must be json, compact or csv")
    return fmt


def _print(data, as_json: bool, markdown_key: str | None = None) -> None:
    if as_json or markdown_key is None:
        typer.echo(_dump(data))
        return
    text = data.get(markdown_key) if isinstance(data, dict) else None
    typer.echo(text if text else _dump(data))


def _client(base_url: Optional[str], api_key: Optional[str]) -> DungScraper:
    return DungScraper(base_url=base_url, api_key=api_key)


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Bind host. Defaults to DUNGSCRAPER_HOST or 127.0.0.1 (this computer only)."),
    port: Optional[int] = typer.Option(None, help="Bind port. Defaults to DUNGSCRAPER_PORT or 8741."),
    reload: bool = typer.Option(False, help="Reload on code changes."),
) -> None:
    """Run the API, playground, and streamable HTTP MCP server."""
    import uvicorn

    from dungscraper.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "dungscraper.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
    )


@app.command()
def worker() -> None:
    """Process queued crawl jobs. Requires DUNGSCRAPER_REDIS_URL."""
    from dungscraper.worker import main as worker_main

    worker_main()


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="stdio or streamable-http"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
) -> None:
    """Run the MCP server over stdio. No API process or Docker is required.

    Set DUNGSCRAPER_BASE_URL only when a DungScraper server is already running
    and this process should call it instead of fetching itself.
    """
    from dungscraper.mcp_server import build_mcp

    server = build_mcp()
    if transport == "stdio":
        server.run("stdio")
        return
    if transport in {"streamable-http", "http"}:
        server.run("streamable-http", host=host, port=port, stateless_http=True)
        return
    _echo_error(DungScraperError("invalid_transport", "transport must be stdio or streamable-http"))


@app.command("install-claude")
def install_claude(
    config: Optional[str] = typer.Option(None, help="Config path. Default is the Claude Desktop path for this OS."),
    launcher: str = typer.Option("auto", help="auto, uvx, pipx, or python."),
    from_spec: Optional[str] = typer.Option(None, "--from", help="uvx/pipx package spec or path. Default: dungscraper."),
    dry_run: bool = typer.Option(False, help="Print the config change without writing it."),
) -> None:
    """Add DungScraper to Claude Desktop. Backs up the config and does not restart Claude."""
    from dungscraper.install_claude import claude_config_path, install_claude_config, launch_command

    path = Path(config) if config else claude_config_path()
    command = launch_command(launcher, from_spec=from_spec)
    if dry_run:
        typer.echo(json.dumps({"path": str(path), "command": command}, indent=2))
        typer.echo("Dry run. Claude was not restarted.")
        return
    backup = install_claude_config(path, command=command)
    typer.echo(f"Wrote {path}")
    if backup:
        typer.echo(f"Backed up the previous file to {backup}")
    typer.echo("Claude was not restarted. Quit Claude Desktop and open it again to load DungScraper.")


@app.command()
def scrape(
    url: str,
    render: str = typer.Option("auto"),
    local: bool = typer.Option(False, help="Scrape in this process instead of calling the API."),
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON response."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Scrape one URL and print Markdown."""
    try:
        if local:
            data = asyncio.run(local_scrape(url, render))
        else:
            with _client(base_url, api_key) as client:
                data = client.scrape(url, render=render)
        _print(data, as_json, "markdown")
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def crawl(
    url: str,
    limit: int = typer.Option(10),
    max_depth: int = typer.Option(2),
    wait: bool = typer.Option(True),
    local: bool = typer.Option(False, help="Crawl in this process instead of calling the API. Always waits."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Start a crawl. Prints the job, including pages when --wait is set."""
    try:
        if local:
            data = asyncio.run(local_crawl(CrawlRequest(url=url, limit=limit, max_depth=max_depth, wait=True)))
        else:
            with _client(base_url, api_key) as client:
                data = client.crawl(url, limit=limit, max_depth=max_depth, wait=wait)
        typer.echo(_dump(data))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command("crawl-status")
def crawl_status(
    job_id: str,
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Print the status of a crawl job."""
    try:
        with _client(base_url, api_key) as client:
            data = client.crawl_status(job_id)
        typer.echo(_dump(data))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command("map")
def map_command(
    url: str,
    search: Optional[str] = typer.Option(None),
    limit: int = typer.Option(100),
    local: bool = typer.Option(False, help="Map in this process instead of calling the API."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """List URLs discovered on a site."""
    try:
        if local:
            data = asyncio.run(local_map(MapRequest(url=url, search=search, limit=limit)))
        else:
            with _client(base_url, api_key) as client:
                data = client.map(url, search=search, limit=limit)
        links = data.get("links") or []
        for link in links:
            typer.echo(link["url"])
        if not links:
            typer.echo("No URLs found.", err=True)
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def search(
    query: str,
    limit: int = typer.Option(5),
    provider: str = typer.Option("auto"),
    scrape_results: bool = typer.Option(True, "--scrape/--no-scrape"),
    local: bool = typer.Option(False, help="Search in this process instead of calling the API."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Search and print the hits."""
    try:
        if local:
            data = asyncio.run(
                local_search(SearchRequest(query=query, limit=limit, provider=provider, scrape=scrape_results))
            )
        else:
            with _client(base_url, api_key) as client:
                data = client.search(query, limit=limit, provider=provider, scrape=scrape_results)
        typer.echo(_dump(data))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def contacts(
    url: str,
    depth: int = typer.Option(1, min=0, max=3, help="0 reads only this page. 1 also reads contact, imprint, careers and about pages it links to."),
    limit: int = typer.Option(8, min=1, max=30, help="Most pages to read."),
    render: str = typer.Option("auto"),
    check_mx: bool = typer.Option(False, "--check-mx", help="Look up whether each email domain has a mail server."),
    fmt: str = typer.Option("json", "--format", help="json (full), compact (one email per line) or csv."),
    local: bool = typer.Option(False, help="Run in this process instead of calling the API."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Find public business emails and careers links. Default output is JSON with where each email was found."""
    fmt = _check_format(fmt)
    try:
        if local:
            data = asyncio.run(
                local_contacts(
                    ContactsRequest(url=url, depth=depth, limit=limit, render=render, check_mx=check_mx)
                )
            )
        else:
            with _client(base_url, api_key) as client:
                data = client.contacts(url, depth=depth, limit=limit, render=render, check_mx=check_mx)
        typer.echo(format_contacts(data, fmt))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def jobs(
    url: str,
    limit: int = typer.Option(100, min=1, max=500),
    render: str = typer.Option("auto", help="auto, http or browser. Use browser for pages that load jobs with JavaScript."),
    fmt: str = typer.Option("json", "--format", help="json (full), compact (one job per line) or csv."),
    local: bool = typer.Option(False, help="Run in this process instead of calling the API."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """List links to job postings on a careers page. Tags junior and remote hints."""
    fmt = _check_format(fmt)
    try:
        if local:
            data = asyncio.run(local_jobs(JobsRequest(url=url, limit=limit, render=render)))
        else:
            with _client(base_url, api_key) as client:
                data = client.jobs(url, limit=limit, render=render)
        typer.echo(format_jobs(data, fmt))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def extract(
    url: Optional[str] = typer.Argument(None),
    prompt: str = typer.Option("", help="What to pull out of the page."),
    schema_path: Optional[str] = typer.Option(None, "--schema", help="Path to a JSON Schema file."),
    base_url: Optional[str] = typer.Option(None),
    api_key: Optional[str] = typer.Option(None),
) -> None:
    """Extract JSON from a page. Needs an OpenAI-compatible model configured on the server."""
    schema = None
    if schema_path:
        try:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _echo_error(DungScraperError("invalid_schema", f"Could not read schema file {schema_path}: {exc}"))
    try:
        with _client(base_url, api_key) as client:
            data = client.extract(url=url, prompt=prompt or None, schema=schema)
        typer.echo(_dump(data))
    except DungScraperError as exc:
        _echo_error(exc)


@app.command()
def chunk(
    path: Optional[str] = typer.Argument(None, help="Markdown file. Reads stdin when omitted."),
    max_tokens: int = typer.Option(500),
    overlap_tokens: int = typer.Option(50),
) -> None:
    """Chunk a Markdown file for retrieval. Runs locally, no server required."""
    from dungscraper.core.chunk import chunk_markdown

    try:
        text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    except OSError as exc:
        _echo_error(DungScraperError("file_error", f"Could not read {path}: {exc}"))
    chunks = chunk_markdown(text, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    typer.echo(_dump([item.model_dump() for item in chunks]))


def main() -> None:
    ensure_utf8_streams()
    try:
        app()
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("DUNGSCRAPER_DEBUG"):
            raise
        if isinstance(exc, ValidationError):
            problems = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
            )
            typer.echo(f"error: invalid input. {problems}", err=True)
        else:
            typer.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(1)
