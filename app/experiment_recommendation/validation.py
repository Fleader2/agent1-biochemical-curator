"""Shared validation helpers for ``app.experiment_recommendation``.

The same local, self-contained, non-shared-module convention every other
data-contract package in this repository already uses (``app.knowledge_gaps
.validation``, ``app.confidence.validation``, ``app.review.validation``, ...).
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID


def require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def clean_optional(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str or None, got {value!r}")
    stripped = value.strip()
    return stripped or None


def require_uuid_tuple(value: Iterable[UUID], *, field_name: str) -> tuple[UUID, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(f"{field_name} must be a tuple of UUID, got {value!r}")
    result = tuple(value)
    for item in result:
        if not isinstance(item, UUID):
            raise TypeError(f"{field_name} must contain only UUID values, got {item!r}")
    return result


def require_non_blank_string_tuple(value: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(f"{field_name} must be a tuple of str, got {value!r}")
    result = tuple(value)
    for item in result:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must contain only non-blank strings, got {item!r}")
    return result


__all__ = [
    "clean_optional",
    "require_non_blank_string_tuple",
    "require_non_empty_str",
    "require_uuid_tuple",
]
