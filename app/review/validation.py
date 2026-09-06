"""Shared validation helpers for ``app.review``.

The same self-validating-at-construction convention every other data-contract
module in this repository already uses (``app.extraction.validation``,
``app.claim_generation.validation``, ``app.confidence.validation``, ...) --
a small, local, non-shared set of helpers rather than importing another
package's private validation module.
"""

from __future__ import annotations

from datetime import datetime


def require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def clean_optional(value: str | None) -> str | None:
    """Trim a string field, turning blank into ``None``. Never invents a value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected str or None, got {value!r}")
    stripped = value.strip()
    return stripped or None


def require_timezone_aware(value: datetime, *, field_name: str) -> datetime:
    """Reject a naive ``datetime`` -- ``ReviewEvent.created_at`` is ``TIMESTAMPTZ``.

    Never guesses a timezone for a naive value (that would be inventing
    information the caller did not supply) -- it simply refuses it.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {value!r}")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime: {value!r}")
    return value


__all__ = ["clean_optional", "require_non_empty_str", "require_timezone_aware"]
