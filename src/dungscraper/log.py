"""Structured logging. JSON by default so a process manager can ship it."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from dungscraper.config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            payload.update(data)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(settings: Settings) -> None:
    logger = logging.getLogger("dungscraper")
    logger.setLevel(settings.log_level.upper())
    logger.handlers.clear()
    handler = logging.StreamHandler()
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str = "dungscraper") -> logging.Logger:
    return logging.getLogger(name)
