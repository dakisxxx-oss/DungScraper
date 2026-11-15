from pathlib import Path

import pytest

from dungscraper.core.contacts import (
    classify_email,
    decode_cloudflare,
    deobfuscate,
    extract_emails_from_text,
    extract_page,
    find_contacts,
    follow_score,
    reject_reason,
)
from dungscraper.core.fetch import make_client
from dungscraper.errors import DungScraperError
from dungscraper.models import ContactsRequest
from site_server import HITS, site  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def by_email(items):
    return {item.email: item for item in items}


def test_deobfuscate_simple_forms():
    assert deobfuscate("jobs [at] acme [dot] io") == "jobs@acme.io"
    assert deobfuscate("jobs(at)acme(dot)io") == "jobs@acme.io"
    assert deobfuscate("jobs {at} acme {dot} co {dot} uk") == "jobs@acme.co.uk"
    assert deobfuscate("see you at noon") == "see you at noon"
    hits = extract_emails_from_text("Write to vardas (eta) imone [dot] lt today")
    assert [(item.email, item.source) for item in hits] == [("vardas@imone.lt", "obfuscated")]


def test_plain_email_is_not_reported_as_obfuscated():
    hits = extract_emails_from_text("Mail info@acme.io or info [at] acme [dot] io")
    assert [(item.email, item.source) for item in hits] == [("info@acme.io", "text")]


def test_cloudflare_decoding():
    token = "4a3e2f2b270a2b29272f6739252c3e3d2b382f642325"
    assert decode_cloudflare(token) == "team@acme-software.io"
    assert decode_cloudflare("zz") is None
    assert decode_cloudflare("abc") is None


@pytest.mark.parametrize(
    "email,reason",
    [
        ("logo@2x.png", "asset_file"),
        ("sprite@3x.webp", "asset_file"),
        ("you@example.com", "placeholder_domain"),
        ("someone@example.org", "placeholder_domain"),
        ("name@domain.com", "placeholder_domain"),
        ("x@mail.example.com", "placeholder_domain"),
        ("yourname@acme.io", "placeholder_local"),
        ("0123456789abcdef0123456789abcdef@o12345.ingest.sentry.io", "technical"),
        ("abcdefabcdefabcdefabcdef@acme.io", "technical"),
        ("x@d111.cloudfront.net", "technical"),
        ("noreply@acme.io", "noreply"),
        ("no-reply@acme.io", "noreply"),
    ],
)
def test_rejects_irrelevant_addresses(email, reason):
    assert reject_reason(email) == reason


@pytest.mark.parametrize("email", ["info@acme.io", "anna.kowalski@acme.co.uk", "hr@acme.lt", "jobs@karjera.acme.de"])
def test_accepts_real_looking_addresses(email):
    assert reject_reason(email) is None


def test_classify_email():
    assert classify_email("jobs@acme.io") == "jobs"
    assert classify_email("hr.team@acme.io") == "jobs"
    assert classify_email("sales-emea@acme.io") == "sales"
    assert classify_email("info@acme.io") == "general"
    assert classify_email("privacy@acme.io") == "legal"
    assert classify_email("anna.kowalski@acme.io") == "other"


def test_extract_page_finds_every_source_and_drops_noise():
    page = extract_page(fixture("contact_page.html"), "https://acme-software.io/contact")
    found = by_email(page.emails)
    assert set(found) == {
        "hello@acme-software.io",
        "sales@acme-software.io",
        "info@acme-software.io",
        "press@acme-software.io",
        "media@acme-software.io",
        "jobs@acme-software.io",
        "support@acme-software.io",
        "team@acme-software.io",
    }
    sources = {item.email: item.source for item in page.emails}
    assert sources["hello@acme-software.io"] == "jsonld"
    assert sources["sales@acme-software.io"] == "jsonld"
    assert sources["press@acme-software.io"] == "mailto"
    assert sources["jobs@acme-software.io"] == "obfuscated"
    assert sources["support@acme-software.io"] == "obfuscated"
    assert sources["team@acme-software.io"] == "cloudflare"
    # Placeholders, image names, Sentry keys and noreply are counted but not returned.
    assert {"you@example.com", "name@domain.com", "noreply@acme-software.io"} <= page.ignored
    assert "logo@2x.png" not in found
    assert not any("sentry" in item for item in found)  # the DSN lives in a script and is never read


def test_extract_page_gives_context_for_text_and_mailto():
    page = extract_page(fixture("contact_page.html"), "https://acme-software.io/contact")
    contexts = {(item.email, item.source): item.context for item in page.emails}
    assert "Hiring" in contexts[("jobs@acme-software.io", "obfuscated")]
    assert "General questions" in contexts[("info@acme-software.io", "text")]
    assert "Organization" in contexts[("hello@acme-software.io", "jsonld")]
    assert "press team" in contexts[("press@acme-software.io", "mailto")]


def test_extract_page_collects_career_links_and_skips_other_links():
    page = extract_page(fixture("contact_page.html"), "https://acme-software.io/contact")
    urls = {link.url for link in page.career_links}
    assert "https://acme-software.io/careers" in urls
    assert "https://boards.greenhouse.io/acmesoftware" in urls
    assert "https://acme-software.io/about" not in urls


def test_follow_score_prefers_contact_pages():
    assert follow_score("https://acme.io/contact", "") == 0
    assert follow_score("https://acme.io/de/kontakt", "") == 0
    assert follow_score("https://acme.io/impressum", "") == 1
    assert follow_score("https://acme.io/careers", "Careers") == 2
    assert follow_score("https://acme.io/careers/senior-engineer", "Senior Engineer") is None
    assert follow_score("https://acme.io/about", "") == 3
    assert follow_score("https://acme.io/blog/contact-lens-guide", "") is None
    assert follow_score("https://acme.io/steam-deck", "") is None


async def run(settings, site, **options):
    async with make_client(settings) as client:
        return await find_contacts(
            ContactsRequest(url=site + "/", delay_ms=0, **options),
            settings=settings,
            client=client,
        )


async def test_depth_zero_reads_only_the_given_page(settings, site):
    data = await run(settings, site, depth=0)
    assert [item.email for item in data.emails] == ["home@acme-site.io"]
    assert data.pages_scanned == 1
    assert data.emails[0].found_at[0].source == "mailto"
    assert data.emails[0].found_at[0].url.endswith("/")


async def test_depth_one_follows_contact_and_careers_and_respects_robots(settings, site):
    data = await run(settings, site, depth=1)
    emails = by_email(data.emails)
    assert "info@acme-site.io" not in emails
    assert {"home@acme-site.io", "info@acme-software.io", "jobs@acme-software.io", "team@acme-software.io"} <= set(emails)
    assert "secret@acme-site.io" not in emails
    assert "blog@acme-site.io" not in emails
    assert data.skipped_robots == 1
    assert "/private/contact" not in {path for path in HITS if path != "/robots.txt"}
    assert "/blog/post" not in HITS
    # jobs@ was seen in the contact page text and in the careers page mailto link.
    jobs = emails["jobs@acme-software.io"]
    assert jobs.kind == "jobs"
    assert len({loc.url for loc in jobs.found_at}) == 2
    urls = {link.url for link in data.career_links}
    assert site + "/careers" in urls
    assert "https://boards.greenhouse.io/acmesoftware" in urls
    assert data.ignored_addresses >= 4
    assert all(item.same_domain is False for item in data.emails)  # 127.0.0.1 is not the mail domain


async def test_limit_caps_pages(settings, site):
    data = await run(settings, site, depth=1, limit=2)
    assert data.pages_scanned <= 2


async def test_robots_blocked_start_page_is_an_error(settings, site):
    async with make_client(settings) as client:
        with pytest.raises(DungScraperError) as caught:
            await find_contacts(
                ContactsRequest(url=site + "/private/contact", delay_ms=0),
                settings=settings,
                client=client,
            )
    assert caught.value.code == "robots_disallowed"


async def test_robots_can_be_ignored_when_asked(settings, site):
    data = await run(settings, site, depth=1, respect_robots=False)
    assert "secret@acme-site.io" in by_email(data.emails)
    assert data.skipped_robots == 0
