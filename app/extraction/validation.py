"""Pure validation helpers for the evidence-extraction data contract.

Mirrors ``app.normalization.identifiers``'s role for the normalization
layer: small, dependency-free functions that the frozen dataclasses in
``app.extraction.types`` call from their own ``__post_init__``, plus the
one check (grounding a quoted passage against real source text) that needs
more than one object and so lives in ``app.extraction.extractor`` instead.
Nothing here performs I/O, calls an LLM, or reads a database.
"""

from __future__ import annotations

from app.extraction.errors import ExtractionValidationError, MalformedSpanError


def require_non_empty(value: str, *, field_name: str) -> str:
    """Require a non-blank string, trimmed of surrounding whitespace.

    Used for every free-text field *except* ``SourceSpan.quoted_text``,
    which must be stored verbatim -- trimming it would break the literal
    character-offset grounding it exists to guarantee (see
    ``clean_optional`` and ``require_quoted_text`` below).
    """
    stripped = value.strip()
    if not stripped:
        raise ExtractionValidationError(f"{field_name} must not be empty")
    return stripped


def require_quoted_text(value: str, *, field_name: str = "quoted_text") -> str:
    """Require non-blank quoted text, returned exactly as given (not trimmed).

    Grounding compares this value byte-for-byte against a slice of the
    source text (``app.extraction.extractor``) -- trimming it here would
    silently desynchronize it from the offsets it is supposed to prove.
    """
    if not value or not value.strip():
        raise ExtractionValidationError(f"{field_name} must not be empty")
    return value


def clean_optional(value: str | None) -> str | None:
    """Trim a string field and turn a blank result into ``None``.

    The same "blank becomes ``None``" convention every
    ``app.normalization.*`` module already uses for its own optional text
    fields.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_non_negative_int(value: int | None, *, field_name: str) -> int | None:
    """Require an optional integer field to be ``None`` or ``>= 0``."""
    if value is not None and value < 0:
        raise ExtractionValidationError(f"{field_name} must not be negative, got {value!r}")
    return value


def validate_span_offsets(character_start: int, character_end: int) -> None:
    """Require a well-formed, non-empty offset range.

    Independent of any real source text -- a span with ``character_end <=
    character_start`` or a negative ``character_start`` is malformed on its
    face, regardless of what document it claims to describe.
    """
    if character_start < 0:
        raise MalformedSpanError(f"character_start must not be negative, got {character_start!r}")
    if character_end <= character_start:
        raise MalformedSpanError(
            f"character_end ({character_end!r}) must be greater than "
            f"character_start ({character_start!r})"
        )


def validate_span_length_matches_quote(
    character_start: int, character_end: int, quoted_text: str
) -> None:
    """Require the offset range's width to exactly match the quoted text's length.

    This is a text-independent consistency check (see
    ``validate_span_offsets``'s docstring) -- it never reads the real
    source document, only compares two numbers already supplied by the
    caller. Grounding the quoted text against the *actual* source text at
    those offsets is a separate, later check
    (``app.extraction.extractor``'s ``ground_candidate``), since it needs
    the full document text this module deliberately never receives.
    """
    expected_length = character_end - character_start
    if len(quoted_text) != expected_length:
        raise MalformedSpanError(
            f"quoted_text has length {len(quoted_text)}, but character_start/"
            f"character_end imply a span of length {expected_length} -- "
            "offsets and quoted text are inconsistent"
        )


def validate_measurement_fields(
    *, measurement_value: str | None, measurement_units: str | None
) -> None:
    """Reject a unit supplied with no accompanying value.

    The reverse (a value with no units) is not rejected: many reported
    quantities are legitimately unitless (fold-change, ratios, a plain
    count) -- inventing a requirement that every value carry units would
    be exactly the kind of unsupported schema/policy invention this
    increment's instructions forbid.
    """
    if measurement_units is not None and measurement_value is None:
        raise ExtractionValidationError(
            "measurement_units was supplied without measurement_value"
        )


__all__ = [
    "clean_optional",
    "require_non_empty",
    "require_quoted_text",
    "validate_measurement_fields",
    "validate_non_negative_int",
    "validate_span_length_matches_quote",
    "validate_span_offsets",
]
