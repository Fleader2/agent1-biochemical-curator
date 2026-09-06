"""Shared validation helpers for ``app.result_interpretation``.

A small, local, non-shared set of helpers -- the same discipline
``app.review.validation``'s own docstring documents for itself.
``app.result_interpretation`` does not import ``app.review.validation`` (a
sibling package's private validation module) even though the helpers below
are functionally identical, for the same reason
``app.persistence.experiment_execution_types`` keeps its own local copies
rather than importing across a layer boundary.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.result_interpretation.errors import InterpretationValidationError


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
    """Reject a naive ``datetime`` -- every other timestamp in this schema is ``TIMESTAMPTZ``.

    Never guesses a timezone for a naive value; it simply refuses it, the
    same discipline ``app.review.validation.require_timezone_aware``
    already establishes.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {value!r}")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime: {value!r}")
    return value


def require_matching_execution(execution_id: UUID, result_execution_id: UUID) -> None:
    """A genuine caller-consistency check, never an ordinary data condition.

    Raised when an ``ExperimentResult`` is passed alongside an
    ``ExperimentExecution`` it does not actually belong to -- always means
    the wrong objects were routed together.
    """
    if execution_id != result_execution_id:
        raise InterpretationValidationError(
            f"ExperimentResult.execution_id ({result_execution_id!r}) does not match the "
            f"supplied ExperimentExecution.id ({execution_id!r}) -- this result does not "
            "belong to this execution"
        )


def require_matching_recommendation(
    recommendation_id: UUID, execution_recommendation_id: UUID
) -> None:
    """The same consistency check as ``require_matching_execution``, for the recommendation link."""
    if recommendation_id != execution_recommendation_id:
        raise InterpretationValidationError(
            f"ExperimentRecommendationRecord.id ({recommendation_id!r}) does not match the "
            f"supplied ExperimentExecution.recommendation_id ({execution_recommendation_id!r}) "
            "-- this recommendation does not belong to this execution"
        )


__all__ = [
    "clean_optional",
    "require_matching_execution",
    "require_matching_recommendation",
    "require_non_empty_str",
    "require_timezone_aware",
]
