# Security and responsible use

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's "Report a vulnerability" button on the Security tab of this repository (private advisory). Include the version, steps to reproduce, and the impact. You will get an answer as soon as the maintainer can read it.

## What the defaults protect

- The server binds to `127.0.0.1`. Set `DUNGSCRAPER_API_KEYS` before you expose it on another interface.
- Private, link-local and cloud-metadata addresses are blocked (SSRF guard) unless `DUNGSCRAPER_ALLOW_PRIVATE_NETWORKS=true`.
- robots.txt is respected unless a request sets `respect_robots=false`.
- CAPTCHA solving is off (`DUNGSCRAPER_SOLVE_CAPTCHA=false`).
- Secrets belong in environment variables or an untracked `.env` file. `.env` is git-ignored.

## Responsible use

You are responsible for what you fetch. Please:

- Respect robots.txt, rate limits, and each site's terms of service.
- Do not collect personal data about private individuals. The `contacts` command is meant for public business contact points such as `info@` or `jobs@` addresses that a company publishes for the public. Comply with GDPR and similar laws, and with anti-spam rules, before you contact anyone.
- Do not use the tool to get around paywalls, logins, or access controls you are not allowed to pass.
- Keep request volume low. Defaults include a per-domain delay; do not remove it for sites you do not own.
- Do not point it at services you are not authorized to test.

The optional anti-bot tiers (`stealth` extra, FlareSolverr) are off unless you install and configure them. Use them only for sites you are allowed to access.
