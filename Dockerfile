FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 dungscraper \
    && mkdir -p /ms-playwright /app \
    && chown -R dungscraper:dungscraper /ms-playwright /app

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -m playwright install-deps chromium \
    && python -m playwright install chromium \
    && chmod -R a+rX /ms-playwright

USER dungscraper
EXPOSE 8741
CMD ["dungscraper", "serve"]
