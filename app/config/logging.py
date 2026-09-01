"""Structured application logging.

Two log categories are kept distinguishable, as required by
``docs/03_agent_behavior.md``:

* ``application`` — software/debug logging
* ``scientific`` — curation events that form part of the scientific audit trail

Secrets are never logged. Callers pass explicit fields via ``extra`` and must not
place credentials in them.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

CATEGORY_APPLICATION: Final = "application"
CATEGORY_SCIENTIFIC: Final = "scientific"

_LOGGER_ROOT: Final = "agent1"

_RESERVED_RECORD_KEYS: Final = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "category": getattr(record, "category", CATEGORY_APPLICATION),
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class TextLogFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = request_id_var.get()
        return f"{base} [request_id={request_id}]" if request_id else base


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Attach a single structured handler to the application logger.

    Existing handlers are replaced so that repeated calls (for example in tests)
    do not duplicate log output.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonLogFormatter() if log_format == "json" else TextLogFormatter())

    logger = logging.getLogger(_LOGGER_ROOT)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return an application logger for software-level events."""
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")


def get_scientific_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a logger that stamps records as part of the scientific audit trail."""
    return logging.LoggerAdapter(
        logging.getLogger(f"{_LOGGER_ROOT}.scientific.{name}"),
        extra={"category": CATEGORY_SCIENTIFIC},
    )
