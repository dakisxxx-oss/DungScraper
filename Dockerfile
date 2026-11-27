FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DUNGSCRAPER_HOST=0.0.0.0 \
    DUNGSCRAPER_DATA_DIR=/data \
    DUNGSCRAPER_CACHE_DIR=/data/cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 dungscraper \
    && mkdir -p /ms-playwright /app /data \
    && chown -R dungscraper:dungscraper /ms-playwright /app /data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -m playwright install-deps chromium \
    && python -m playwright install chromium \
    && chmod -R a+rX /ms-playwright

USER dungscraper
VOLUME ["/data"]
EXPOSE 8741

# Platforms such as Render and Railway pass the port in $PORT. Everywhere else it is 8741.
# The server refuses to start on 0.0.0.0 without DUNGSCRAPER_API_KEYS, on purpose.
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=8 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', os.environ.get('DUNGSCRAPER_PORT', '8741')), timeout=3)"

CMD ["sh", "-c", "DUNGSCRAPER_PORT=${PORT:-${DUNGSCRAPER_PORT:-8741}} exec dungscraper serve"]
