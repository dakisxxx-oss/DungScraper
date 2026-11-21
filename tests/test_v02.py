"""Tests for 0.2.0: output formats, MX check, MCP contact and job tools, inline job anchors."""

from __future__ import annotations

import asyncio

import pytest

from dungscraper.cli import format_contacts, format_jobs
from dungscraper.core import mx
from dungscraper.core.joblinks import extract_job_links
from dungscraper.mcp_server import build_mcp

CONTACTS = {
    "emails": [
        {
            "email": "jobs@example.org",
            "kind": "jobs",
            "mx": True,
            "found_at": [{"url": "https://example.org/jobs", "source": "mailto", "context": "x"}],
        },
        {"email": "info@example.org", "kind": "general", "mx": None, "found_at": []},
    ]
}
JOBS = {
    "jobs": [
        {"title": "Support, Junior", "url": "https://example.org/jobs#support-junior", "tags": ["junior", "remote"], "apply_email": "jobs@example.org"},
        {"title": "QA", "url": "https://example.org/jobs/qa", "tags": [], "apply_email": None},
    ]
}


def test_contacts_compact_is_one_email_per_line():
    lines = format_contacts(CONTACTS, "compact").splitlines()
    assert lines[0] == "jobs@example.org  jobs  true  https://example.org/jobs"
    assert lines[1] == "info@example.org  general"


def test_contacts_csv_has_header_and_quotes():
    lines = format_contacts(CONTACTS, "csv").splitlines()
    assert lines[0] == "email,kind,mx,found_at"
    assert lines[1] == "jobs@example.org,jobs,true,https://example.org/jobs"
    assert lines[2] == "info@example.org,general,,"


def test_jobs_csv_quotes_commas():
    lines = format_jobs(JOBS, "csv").splitlines()
    assert lines[0] == "title,url,tags,apply_email"
    assert lines[1].startswith('"Support, Junior",https://example.org/jobs#support-junior,"junior,remote"')


def test_jobs_compact_one_line_per_job():
    assert len(format_jobs(JOBS, "compact").splitlines()) == 2


def test_json_format_is_full_result():
    assert '"emails"' in format_contacts(CONTACTS, "json")


@pytest.mark.asyncio
async def test_mx_check_domains_maps_results(monkeypatch):
    async def fake(domain, **_kwargs):
        return {"good.test": True, "bad.test": False}.get(domain)

    monkeypatch.setattr(mx, "domain_accepts_mail", fake)
    result = await mx.check_domains({"good.test", "bad.test", "slow.test"})
    assert result == {"bad.test": False, "good.test": True, "slow.test": None}


def test_mcp_lists_contacts_and_jobs_tools():
    names = [tool.name for tool in asyncio.run(build_mcp().list_tools())]
    assert "find_contacts_tool" in names
    assert "find_jobs_tool" in names


def test_inline_jobs_get_distinct_anchor_urls():
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "jobs_inline.html").read_text(encoding="utf-8")
    jobs, _ = extract_job_links(html, "https://acme-software.io/jobs/")
    urls = [job.url for job in jobs]
    assert urls == [
        "https://acme-software.io/jobs/#junior-support-engineer",
        "https://acme-software.io/jobs/#office-manager",
    ]
