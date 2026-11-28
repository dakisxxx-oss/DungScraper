# Self-hosting DungScraper

DungScraper is a normal web service. Run it on your own machine, a VPS, or a container platform. Nothing here deploys anything by itself, and no account is created for you.

If you only want to try it, you do not need to self-host: `pip install dungscraper` works with no account, and a rate-limited public demo is documented in the README.

## One-command install (Ubuntu or Debian)

On a fresh amd64 or arm64 server, including Oracle Cloud Always Free Ampere:

```bash
curl -fsSL https://raw.githubusercontent.com/dakisxxx-oss/DungScraper/main/scripts/install.sh | sudo bash
```

The script installs Docker if it is missing, clones or updates `/opt/dungscraper`, creates a `.env` with a random API key (existing keys are never rotated), opens TCP 8741 on the host firewall, starts the API, Redis and worker, and prints the URL and key. It is safe to run again.

The first run builds the Docker image on the machine (several minutes; there is no published multi-arch image yet). Later runs reuse the image and come up quickly.

On Oracle Cloud, also add an ingress rule in the VCN's default security list for TCP port 8741. Ubuntu images from Oracle have iptables `REJECT` rules in `/etc/iptables/rules.v4`; the installer inserts an `ACCEPT` for 8741 before that line and keeps it persisted. A domain and TLS can be put in front later.

To serve anonymously (no key, per-IP limits) while keeping your private key for unlimited use, add this to `/opt/dungscraper/.env` and recreate the containers:

```
DUNGSCRAPER_ALLOW_ANONYMOUS=true
DUNGSCRAPER_ANON_RATE_LIMIT_PER_MINUTE=10
DUNGSCRAPER_ANON_RATE_LIMIT_PER_DAY=200
DUNGSCRAPER_ANON_MAX_PAGES=5
DUNGSCRAPER_ANON_TIMEOUT_SECONDS=30
```


**What you get:** the REST API (`/v1/...`), the playground at `/`, MCP over HTTP at `/mcp`, API-key auth, optional per-key rate limits and a usage endpoint, a `/health` endpoint, a Docker image with a health check, and a compose file with Redis and a crawl worker.

**What you do not get:** managed proxies, a billing system, multi-tenant accounts, or counters shared between replicas. Keys are a list you control. Limits and usage counters are in memory per process.

## 1. Make a key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`DUNGSCRAPER_API_KEYS` takes several keys, comma separated. Send one as `Authorization: Bearer <key>` or `X-API-Key: <key>`. The server refuses to listen beyond 127.0.0.1 with no key.

## 2. Docker

```bash
docker build -t dungscraper .
docker run -p 8741:8741 -e DUNGSCRAPER_API_KEYS=<your key> -v dungscraper-data:/data dungscraper
curl localhost:8741/health
curl -H "Authorization: Bearer <your key>" localhost:8741/v1/usage
```

`/data` holds the disk cache and watch snapshots. The container has a health check on `/health`.

## 3. docker compose (API, Redis, crawl worker)

```bash
cp .env.example .env      # set DUNGSCRAPER_API_KEYS
docker compose up --build -d
```

Compose refuses to start without a key. It binds to 127.0.0.1 by default. To serve other machines set `DUNGSCRAPER_BIND=0.0.0.0` and put a TLS proxy (Caddy, nginx, Traefik, your cloud load balancer) in front.

## 4. One-click-ish platforms

All three build the repository's `Dockerfile`, which honours the `$PORT` variable they set and answers `/health`. They run a single instance with crawls in process (`DUNGSCRAPER_CRAWL_MODE=inline`), so there is no Redis to pay for. Crawl jobs live in memory and are lost on restart; use `wait: true` or the compose setup if that matters.

### Render

1. Push this repository to your GitHub account (it is already there if you cloned from it).
2. In Render choose **New > Blueprint** and select the repository. It reads `render.yaml`.
3. Render generates `DUNGSCRAPER_API_KEYS`. Read it in the service's **Environment** tab.

### Fly.io

```bash
fly launch --no-deploy --copy-config      # edit app name in fly.toml if asked
fly secrets set DUNGSCRAPER_API_KEYS=<your key>
fly deploy
```

`fly.toml` scales to zero when idle and checks `/health`.

### Railway

1. **New Project > Deploy from GitHub repo**. Railway reads `railway.json`.
2. Under **Variables** add `DUNGSCRAPER_API_KEYS`.
3. Under **Settings > Networking** generate a domain.

## 5. Limits, usage and proxies

| Variable | Meaning |
| --- | --- |
| `DUNGSCRAPER_RATE_LIMIT_PER_MINUTE` | Per key, sliding 60 s window. `0` is off. |
| `DUNGSCRAPER_RATE_LIMIT_PER_DAY` | Per key, resets 00:00 UTC. |
| `DUNGSCRAPER_KEY_LIMITS` | `keyA=120,keyB=30` gives a key its own per-minute limit. |
| `DUNGSCRAPER_PROXIES` | Your proxies, comma separated. |
| `DUNGSCRAPER_CACHE_DIR` | Disk cache folder (`/data/cache` in the image). |

Over a limit the API answers `429` with `Retry-After` and `X-RateLimit-Limit` / `X-RateLimit-Remaining`. `GET /v1/usage` returns a key's request counts and what is left, identified by a short id so the key itself never appears.

## What has and has not been verified

Verified by the test suite and live runs: key auth (401 without a key, 200 with), per-key limits and `429` headers, `/v1/usage`, the refusal to listen publicly without a key, and that the compose file, Dockerfile, `render.yaml`, `fly.toml` and `railway.json` are consistent with each other. An actual `docker compose` build and run has been verified on Oracle Cloud Always Free Ampere (arm64, Ubuntu 24.04), including Playwright after installing `libatomic1`. Not run from this repository: a Docker build in CI, or a Render, Fly or Railway deployment. Check the first deploy on your platform before relying on it.
