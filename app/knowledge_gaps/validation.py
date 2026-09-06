"""Shared validation helpers for ``app.knowledge_gaps``.

The same local, self-contained, non-shared-module convention every other
data-contract package in this repository already uses (``app.extraction
.validation``, ``app.confidence.validation``, ``app.review.validation``,
...). Raises ``app.knowledge_gaps.errors.InvalidGapCandidateError``
directly rather than the generic ``TypeError``/``ValueError`` every other
package's helpers use -- Increment 21 explicitly asks for named exceptions,
so this package's own dataclasses use them uniformly instead of the usual
built-in exceptions.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.knowledge_gaps.errors import InvalidGapCandidateError


def require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidGapCandidateError(f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def clean_optional(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidGapCandidateError(f"{field_name} must be a str or None, got {value!r}")
    stripped = value.strip()
    return stripped or None


def require_uuid_tuple(value: Iterable[UUID], *, field_name: str) -> tuple[UUID, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise InvalidGapCandidateError(f"{field_name} must be a tuple of UUID, got {value!r}")
    result = tuple(value)
    for item in result:
        if not isinstance(item, UUID):
            raise InvalidGapCandidateError(
                f"{field_name} must contain only UUID values, got {item!r}"
            )
    return result


def require_non_blank_string_tuple(
    value: Iterable[str], *, field_name: str
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise InvalidGapCandidateError(f"{field_name} must be a tuple of str, got {value!r}")
    result = tuple(value)
    for item in result:
        if not isinstance(item, str) or not item.strip():
            raise InvalidGapCandidateError(
                f"{field_name} must contain only non-blank strings, got {item!r}"
            )
    return result


__all__ = [
    "clean_optional",
    "require_non_blank_string_tuple",
    "require_non_empty_str",
    "require_uuid_tuple",
]
