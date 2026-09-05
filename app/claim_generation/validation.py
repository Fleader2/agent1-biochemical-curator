"""Pure validation helpers for the Claim Generation data contract.

Mirrors ``app.extraction.validation``'s role for the extraction layer:
small, dependency-free functions that the frozen dataclasses in
``app.claim_generation.types`` call from their own ``__post_init__``.
Nothing here performs I/O, calls an LLM, calls into
``app.normalization.*``, or reads a database.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.claim_generation.errors import ClaimValidationError
from app.normalization.types import NormalizationResult, NormalizationStatus


def require_non_empty(value: str, *, field_name: str) -> str:
    """Require a non-blank string, trimmed of surrounding whitespace."""
    stripped = value.strip()
    if not stripped:
        raise ClaimValidationError(f"{field_name} must not be empty")
    return stripped


def clean_optional(value: str | None) -> str | None:
    """Trim a string field and turn a blank result into ``None``.

    The same "blank becomes ``None``" convention ``app.normalization.*``
    and ``app.extraction.validation`` both already use for their own
    optional text fields.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_entity_reference_consistency(
    *, normalization_result: NormalizationResult | None, normalized_id: UUID | None
) -> None:
    """Require ``normalized_id`` to be populated only from a genuine ``MATCHED`` result.

    A resolved entity id may never appear without the ``MATCHED``
    ``NormalizationResult`` that justifies it (Increment 13 instructions,
    Step 17: ambiguity/unresolved outcomes must never be silently narrowed
    to one chosen id).
    """
    if normalization_result is None:
        if normalized_id is not None:
            raise ClaimValidationError(
                "normalized_id must be None when normalization_result is None"
            )
        return

    if normalization_result.status is NormalizationStatus.MATCHED:
        if normalized_id != normalization_result.matched_entity_id:
            raise ClaimValidationError(
                f"normalized_id ({normalized_id!r}) must equal "
                f"normalization_result.matched_entity_id "
                f"({normalization_result.matched_entity_id!r}) when status is MATCHED"
            )
    elif normalized_id is not None:
        raise ClaimValidationError(
            f"normalized_id must be None when normalization_result.status is "
            f"{normalization_result.status!r}, not MATCHED"
        )


def validate_value_fields(
    *, value_text: str | None, value_numeric: Decimal | None, value_unit: str | None
) -> None:
    """Require every populated value field to be backed by real reported content.

    - ``value_unit`` may never be supplied with neither ``value_text`` nor
      ``value_numeric`` -- a unit attached to nothing makes no sense (the
      same rule ``app.extraction.validation.validate_measurement_fields``
      already applies one layer up).
    - ``value_numeric`` may never be supplied without ``value_text`` --
      this package always preserves the original reported text alongside
      any value it was able to parse as a clean number (Increment 13
      instructions, Step 8: "preserve: numeric value, unit, original
      text."); a numeric value with no recorded original text would mean
      it came from somewhere other than the source passage.
    """
    if value_unit is not None and value_text is None and value_numeric is None:
        raise ClaimValidationError(
            "value_unit was supplied without value_text or value_numeric"
        )
    if value_numeric is not None and value_text is None:
        raise ClaimValidationError("value_numeric was supplied without value_text")


__all__ = [
    "clean_optional",
    "require_non_empty",
    "validate_entity_reference_consistency",
    "validate_value_fields",
]
