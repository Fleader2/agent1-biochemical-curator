"""Tests for structured logging."""

from __future__ import annotations

import json
import logging
import sys

import pytest

from app.config.logging import (
    CATEGORY_APPLICATION,
    CATEGORY_SCIENTIFIC,
    JsonLogFormatter,
    TextLogFormatter,
    configure_logging,
    get_logger,
    get_scientific_logger,
    request_id_var,
)

pytestmark = pytest.mark.unit


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="agent1.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event occurred",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_single_line_object() -> None:
    payload = json.loads(JsonLogFormatter().format(_record()))

    assert payload["message"] == "event occurred"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "agent1.test"
    assert payload["category"] == CATEGORY_APPLICATION
    assert payload["timestamp"]


def test_json_formatter_includes_extra_fields() -> None:
    payload = json.loads(JsonLogFormatter().format(_record(endpoint="/api/v1/health")))

    assert payload["endpoint"] == "/api/v1/health"


def test_json_formatter_includes_request_id_when_set() -> None:
    token = request_id_var.set("request-123")
    try:
        payload = json.loads(JsonLogFormatter().format(_record()))
    finally:
        request_id_var.reset(token)

    assert payload["request_id"] == "request-123"


def test_json_formatter_omits_request_id_when_unset() -> None:
    payload = json.loads(JsonLogFormatter().format(_record()))

    assert "request_id" not in payload


def test_json_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record()
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonLogFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_text_formatter_is_human_readable() -> None:
    line = TextLogFormatter().format(_record())

    assert "event occurred" in line
    assert "request_id" not in line


def test_text_formatter_includes_request_id_when_set() -> None:
    token = request_id_var.set("request-456")
    try:
        line = TextLogFormatter().format(_record())
    finally:
        request_id_var.reset(token)

    assert "request_id=request-456" in line


def test_scientific_logger_is_distinguishable() -> None:
    adapter = get_scientific_logger("curation")

    assert adapter.extra is not None
    assert adapter.extra["category"] == CATEGORY_SCIENTIFIC


def test_configure_logging_does_not_duplicate_handlers() -> None:
    configure_logging(level="INFO", log_format="json")
    configure_logging(level="DEBUG", log_format="text")

    root = logging.getLogger("agent1")
    assert len(root.handlers) == 1
    assert root.level == logging.DEBUG


def test_get_logger_is_namespaced() -> None:
    assert get_logger("services.health").name == "agent1.services.health"
