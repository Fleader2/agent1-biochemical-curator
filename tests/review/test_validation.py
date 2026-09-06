"""Tests for ``app.review.validation``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.review.validation import clean_optional, require_non_empty_str, require_timezone_aware


def test_require_non_empty_str_accepts_and_trims():
    assert require_non_empty_str("  alice  ", field_name="reviewer") == "alice"


def test_require_non_empty_str_rejects_blank():
    with pytest.raises(ValueError):
        require_non_empty_str("   ", field_name="reviewer")


def test_require_non_empty_str_rejects_non_string():
    with pytest.raises(ValueError):
        require_non_empty_str(123, field_name="reviewer")  # type: ignore[arg-type]


def test_clean_optional_none_stays_none():
    assert clean_optional(None) is None


def test_clean_optional_blank_becomes_none():
    assert clean_optional("   ") is None


def test_clean_optional_trims_non_blank():
    assert clean_optional("  see figure 2  ") == "see figure 2"


def test_clean_optional_rejects_non_string():
    with pytest.raises(TypeError):
        clean_optional(123)  # type: ignore[arg-type]


def test_require_timezone_aware_accepts_aware_datetime():
    value = datetime(2024, 1, 1, tzinfo=UTC)
    assert require_timezone_aware(value, field_name="timestamp") is value


def test_require_timezone_aware_rejects_naive_datetime():
    with pytest.raises(ValueError):
        require_timezone_aware(datetime(2024, 1, 1), field_name="timestamp")


def test_require_timezone_aware_rejects_non_datetime():
    with pytest.raises(TypeError):
        require_timezone_aware("2024-01-01", field_name="timestamp")  # type: ignore[arg-type]
