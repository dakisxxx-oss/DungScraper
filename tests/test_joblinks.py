from pathlib import Path

from dungscraper.core.joblinks import extract_job_links, is_career_link

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://acme-software.io/careers/"


def jobs():
    html = (FIXTURES / "careers_page.html").read_text(encoding="utf-8")
    return extract_job_links(html, BASE)


def test_finds_posting_links_and_skips_navigation():
    found, total = jobs()
    urls = [item.url for item in found]
    assert "https://acme-software.io/careers/jobs/junior-python-dev" in urls  # JSON-LD
    assert "https://acme-software.io/careers/jobs/senior-backend-engineer" in urls
    assert "https://acme-software.io/careers/jobs/frontend-intern" in urls
    assert "https://jobs.lever.co/acme/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab" in urls
    assert "https://apply.workable.com/acme/j/ABC123/" in urls
    assert "https://acme-software.io/open-role?gh_jid=4567" in urls
    assert "https://acme-software.io/careers/apply-now-form" in urls
    # Not postings: the page itself, benefits, culture, privacy, blog, mail, pdf, social.
    assert "https://acme-software.io/careers/" not in urls
    assert not any("benefits" in url or "culture" in url or "privacy" in url or "blog" in url for url in urls)
    assert not any(url.endswith(".pdf") or "twitter.com" in url or url.startswith("mailto:") for url in urls)
    assert total > len(found)


def test_dedupes_links_and_keeps_the_title():
    found, _ = jobs()
    interns = [item for item in found if item.url.endswith("/frontend-intern")]
    assert len(interns) == 1
    assert interns[0].title == "Frontend Intern"


def test_tags_junior_and_remote():
    found, _ = jobs()
    tags = {item.url: set(item.tags) for item in found}
    assert tags["https://acme-software.io/careers/jobs/junior-python-dev"] == {"junior", "remote"}
    assert tags["https://acme-software.io/careers/jobs/frontend-intern"] == {"junior", "remote"}
    assert tags["https://jobs.lever.co/acme/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab"] == {"junior"}
    assert tags["https://acme-software.io/careers/jobs/senior-backend-engineer"] == set()


def test_reason_explains_the_match():
    found, _ = jobs()
    reasons = {item.url: item.reason for item in found}
    assert reasons["https://acme-software.io/careers/jobs/junior-python-dev"] == "jsonld:JobPosting"
    assert reasons["https://jobs.lever.co/acme/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab"].startswith("ats:")
    assert reasons["https://acme-software.io/open-role?gh_jid=4567"] == "query:job id"
    assert reasons["https://acme-software.io/careers/apply-now-form"].startswith("path:")


def test_limit():
    html = (FIXTURES / "careers_page.html").read_text(encoding="utf-8")
    found, _ = extract_job_links(html, BASE, limit=2)
    assert len(found) == 2


def test_empty_page_has_no_jobs():
    found, total = extract_job_links("<html><body><a href='/about'>About</a></body></html>", BASE)
    assert found == [] and total == 1


def test_is_career_link():
    assert is_career_link("https://acme.io/careers", "")
    assert is_career_link("https://acme.io/de/karriere/", "")
    assert is_career_link("https://careers.acme.io/", "")
    assert is_career_link("https://boards.greenhouse.io/acme", "")
    assert is_career_link("https://acme.io/join", "We're hiring")
    assert not is_career_link("https://acme.io/about", "About us")
    assert not is_career_link("https://acme.io/blog/post", "A long article that mentions jobs somewhere in the title text here")
