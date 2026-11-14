"""Structured extraction through any OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import jsonschema

from dungscraper.config import Settings
from dungscraper.core.scrape import scrape_url
from dungscraper.errors import DungScraperError
from dungscraper.log import get_logger
from dungscraper.models import ExtractData, ExtractItem, ExtractRequest, ScrapeRequest

log = get_logger("dungscraper.extract")

SYSTEM = (
    "You extract structured data from web page content for an AI agent. "
    "Return only JSON, with no markdown fences and no commentary. "
    "If a JSON Schema is provided, the JSON must validate against it. "
    "Use null when the page does not say. Do not invent facts that are not in the content."
)


def parse_json_loose(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise DungScraperError("extract_failed", "The model did not return JSON.", status=502)


def llm_configured(settings: Settings) -> bool:
    default = "https://api.openai.com/v1"
    if settings.llm_base_url.rstrip("/") != default:
        return True
    return bool(settings.llm_api_key)


def _require_llm(settings: Settings) -> None:
    if llm_configured(settings):
        return
    raise DungScraperError(
        "llm_not_configured",
        "No LLM is configured. Set DUNGSCRAPER_LLM_API_KEY for OpenAI, or point DUNGSCRAPER_LLM_BASE_URL "
        "at an OpenAI-compatible server such as Ollama (http://127.0.0.1:11434/v1) and set DUNGSCRAPER_LLM_MODEL.",
        status=400,
    )


async def llm_complete(
    messages: list[dict[str, str]],
    *,
    settings: Settings,
    model: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    _require_llm(settings)
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    payload: dict[str, Any] = {"model": model, "temperature": 0, "messages": messages}
    own = client is None
    http = client or httpx.AsyncClient(timeout=60)
    try:
        response = await http.post(url, json={**payload, "response_format": {"type": "json_object"}}, headers=headers)
        if response.status_code == 400:
            response = await http.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:400]
            raise DungScraperError(
                "extract_failed",
                f"LLM request failed with HTTP {response.status_code}: {detail}",
                status=502,
            )
        body = response.json()
        return ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    finally:
        if own:
            await http.aclose()


async def _extract_one(content: str, request: ExtractRequest, settings: Settings, model: str) -> Any:
    clipped = content[: request.max_chars]
    schema_text = json.dumps(request.schema_, indent=2) if request.schema_ else ""
    user = "Extract JSON from the page content."
    if request.prompt:
        user += f"\n\nInstruction:\n{request.prompt.strip()}"
    if schema_text:
        user += f"\n\nJSON Schema:\n{schema_text}"
    user += f"\n\nContent:\n{clipped}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    raw = await llm_complete(messages, settings=settings, model=model)
    try:
        data = parse_json_loose(raw)
        if request.schema_:
            jsonschema.validate(data, request.schema_)
        return data
    except DungScraperError:
        first_error = "not JSON"
        data = None
    except jsonschema.ValidationError as exc:
        first_error = str(exc)[:500]
        data = None
    repair = messages + [
        {"role": "assistant", "content": raw},
        {
            "role": "user",
            "content": f"That response failed validation ({first_error}). Return corrected JSON only.",
        },
    ]
    raw = await llm_complete(repair, settings=settings, model=model)
    data = parse_json_loose(raw)
    if request.schema_:
        try:
            jsonschema.validate(data, request.schema_)
        except jsonschema.ValidationError as exc:
            raise DungScraperError(
                "extract_failed",
                f"Model JSON did not match the schema: {str(exc)[:400]}",
                status=502,
            ) from exc
    return data


async def extract_structured(
    request: ExtractRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    cache=None,
    browser=None,
) -> ExtractData:
    _require_llm(settings)
    model = request.model or settings.llm_model
    results: list[ExtractItem] = []
    if request.content and request.content.strip() and not request.urls:
        try:
            data = await _extract_one(request.content, request, settings, model)
            results.append(ExtractItem(url=None, data=data))
        except DungScraperError as exc:
            results.append(ExtractItem(url=None, error=f"{exc.code}: {exc.message}"))
        return ExtractData(results=results, model=model)

    own = client is None
    http = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    try:
        for url in request.urls:
            try:
                options = request.scrape_options.model_dump()
                options["formats"] = list(dict.fromkeys([*options.get("formats", []), "markdown"]))
                scraped = await scrape_url(
                    ScrapeRequest(url=url, **options),
                    settings=settings,
                    client=http,
                    cache=cache,
                    browser=browser,
                )
                text = scraped.markdown or ""
                if request.content:
                    text = request.content + "\n\n" + text
                data = await _extract_one(text, request, settings, model)
                results.append(ExtractItem(url=url, data=data))
            except DungScraperError as exc:
                results.append(ExtractItem(url=url, error=f"{exc.code}: {exc.message}"))
    finally:
        if own:
            await http.aclose()
    if results and all(item.error for item in results):
        raise DungScraperError("extract_failed", results[0].error or "Extraction failed", status=502)
    return ExtractData(results=results, model=model)
