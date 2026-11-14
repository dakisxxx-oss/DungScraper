"""PDF and DOCX bytes to Markdown."""

from __future__ import annotations

import contextlib
import io

import mammoth

from dungscraper.core.content import cleanup_markdown, html_to_markdown
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger

log = get_logger("dungscraper.documents")


def pdf_to_markdown(data: bytes) -> tuple[str, dict]:
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DungScraperError("unsupported_content", f"Could not read PDF: {exc}", status=415) from exc
    meta = {key: value for key, value in (document.metadata or {}).items() if value}
    markdown = ""
    try:
        import pymupdf4llm

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            markdown = pymupdf4llm.to_markdown(document) or ""
    except Exception as exc:
        log.info("pdf markdown fallback", extra={"data": {"error": str(exc)}})
    if len(markdown.strip()) < 20:
        parts: list[str] = []
        for index, page in enumerate(document, start=1):
            text = (page.get_text("text") or "").strip()
            if text:
                parts.append(f"## Page {index}\n\n{text}")
        markdown = "\n\n".join(parts)
    title = meta.get("title")
    if title and title.lower() not in markdown[:200].lower():
        markdown = f"# {title}\n\n{markdown}"
    return cleanup_markdown(markdown), meta


def docx_to_markdown(data: bytes) -> tuple[str, dict]:
    try:
        result = mammoth.convert_to_html(io.BytesIO(data))
    except Exception as exc:
        raise DungScraperError("unsupported_content", f"Could not read DOCX: {exc}", status=415) from exc
    html = result.value or ""
    markdown = html_to_markdown(f"<article>{html}</article>", "https://document.local/")
    return cleanup_markdown(markdown), {"messages": [str(message) for message in result.messages]}
