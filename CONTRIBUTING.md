# Contributing

Thanks for helping. Small, focused changes are easiest to review.

## Set up

```bash
git clone https://github.com/dakisxxx-oss/DungScraper.git
cd DungScraper
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium
```

`./install.sh` or `.\install.ps1` does the same for end users.

## Test

```bash
python -m pytest -q -m "not integration"
```

Integration tests fetch public pages and need the internet: `python -m pytest -m integration`. CI runs the first command on Windows and Ubuntu with Python 3.12.

New behavior needs a test. Prefer fixture HTML in `tests/fixtures` and the local test server in `tests/site_server.py` over live sites.

## Style

- Match the surrounding code. Type hints, short functions, `from __future__ import annotations`.
- No new heavy dependencies without a good reason.
- No secrets, personal data, or machine-specific paths in code, tests, fixtures, or docs.
- Respect robots.txt and the SSRF guard in anything that fetches pages. Do not add CAPTCHA solving that is on by default.

## Pull requests

Describe what changed and why, and say how you tested it. Keep unrelated cleanups out of the same PR.
