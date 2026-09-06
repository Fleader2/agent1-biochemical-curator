"""Pure validation helpers for the Multi-Evidence Aggregation data contract.

Mirrors ``app.confidence.validation``'s role for the single-evidence
layer: small, dependency-free functions the frozen dataclasses in
``app.confidence.aggregate_types`` call from their own ``__post_init__``.
Nothing here performs I/O, calls an LLM, or reads a database.
"""

from __future__ import annotations

from decimal import Decimal

from app.confidence.aggregate_policy import SCORE_MAX, SCORE_MIN, classify_confidence_class
from app.models.enums import ConfidenceClass


def require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {value!r}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    return stripped


def validate_score_range(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an int, got {value!r}")
    if value < SCORE_MIN or value > SCORE_MAX:
        raise ValueError(f"{field_name} must be between {SCORE_MIN} and {SCORE_MAX}, got {value!r}")


def validate_non_negative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value!r}")


def validate_aggregate_score_and_class_consistency(
    *, score: int | None, confidence_class: ConfidenceClass
) -> None:
    """Require ``score``/``confidence_class`` to agree, in both directions.

    Identical discipline to Increment 17's (now-removed) single-evidence
    version: ``score is None`` iff ``confidence_class is UNKNOWN``; a
    populated score must map to exactly the class
    ``app.confidence.aggregate_policy.classify_confidence_class`` itself
    computes.
    """
    if score is None:
        if confidence_class is not ConfidenceClass.UNKNOWN:
            raise ValueError(
                f"confidence_class must be UNKNOWN when score is None, got {confidence_class!r}"
            )
        return

    validate_score_range(score, field_name="score")
    if confidence_class is ConfidenceClass.UNKNOWN:
        raise ValueError("confidence_class must not be UNKNOWN when score is not None")

    expected = classify_confidence_class(score)
    if confidence_class is not expected:
        raise ValueError(
            f"confidence_class {confidence_class!r} does not match the class "
            f"{expected!r} that score {score!r} maps to"
        )


def validate_non_negative_decimal(value: Decimal, *, field_name: str) -> None:
    """Require a ``Decimal`` (an exact adjusted/weighted/cumulative score) to be non-negative.

    Used for ``ContributionBreakdown.adjusted_contribution``/
    ``weighted_contribution``/``cumulative_score`` -- each is a product or
    sum of non-negative quantities (a 0-100 base score, a 0-1 weight, a
    0-100 percent modifier), so none can legitimately be negative. No
    upper bound is enforced here beyond that: ``cumulative_score`` in
    particular is an intermediate running total that this repository's own
    weighting scheme keeps under 100 today (see
    ``app.confidence.aggregation`` module docstring), but this validator
    does not hard-code that as a structural ceiling.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {value!r}")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value!r}")


def validate_weight_range(value: Decimal, *, field_name: str) -> None:
    """Require a diminishing-return weight to fall within ``(0, 1]``."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {value!r}")
    if value <= 0 or value > 1:
        raise ValueError(f"{field_name} must be in the range (0, 1], got {value!r}")


def require_non_blank_string_tuple(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of str, got {value!r}")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must contain only non-empty strings, got {item!r}")
        cleaned.append(item.strip())
    return tuple(cleaned)


__all__ = [
    "require_non_blank_string_tuple",
    "require_non_empty_str",
    "validate_aggregate_score_and_class_consistency",
    "validate_non_negative_decimal",
    "validate_non_negative_int",
    "validate_score_range",
    "validate_weight_range",
]
