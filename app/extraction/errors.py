"""Structured errors for the evidence-extraction layer.

Mirrors ``app.persistence.errors``'s shape (a small, flat exception
hierarchy under one base class) but with members specific to what can go
wrong while turning candidate statement data into a validated
``EvidenceExtraction`` -- see ``app.extraction.types``/``validation`` for
what each check actually enforces.
"""

from __future__ import annotations


class ExtractionError(Exception):
    """Base class for evidence-extraction-layer exceptions."""


class UnsupportedInputError(ExtractionError):
    """The input to ``extract_evidence`` itself is unusable.

    For example: empty source text, or a candidate list containing
    something that is not a ``CandidateStatement`` at all. This is distinct
    from a single malformed candidate (see ``ExtractionValidationError``) --
    it means the call cannot proceed at all.
    """


class MalformedSpanError(ExtractionError):
    """A ``SourceSpan``'s own offsets/structure are internally inconsistent.

    Raised for spans that are wrong on their face -- a negative offset, an
    end at or before the start, or quoted text whose length does not match
    the offset range -- independent of whether the span was ever checked
    against real source text.
    """


class GroundingError(ExtractionError):
    """A candidate's quoted text could not be grounded in the supplied source text.

    Covers every way grounding can fail: the quoted text does not appear
    in the text at all, explicit offsets were supplied but do not match the
    quoted text, or the quoted text appears more than once and the
    candidate did not supply explicit offsets to disambiguate which
    occurrence it means.
    """


class ExtractionValidationError(ExtractionError):
    """A candidate statement fails required-field or content validation.

    For example: an empty ``subject_text``/``predicate_text``, or a
    ``measurement_value`` supplied without ``measurement_units``. Distinct
    from ``MalformedSpanError``/``GroundingError``, which are specifically
    about the source span rather than the statement's own content.
    """


class LLMFormattingError(ExtractionError):
    """A raw prompt-contract payload could not be parsed into a ``CandidateStatement``.

    Reserved for the boundary between whatever produced the raw fields
    (an LLM response following ``app.extraction.prompts``'s field names, or
    a human transcribing one) and this package's own typed representation
    -- a missing required key, or a value that does not match one of the
    controlled vocabularies (``EvidenceType``/``Directness``), raises this
    rather than ``ExtractionValidationError``, since the problem is in how
    the payload was *formatted*, not in the scientific content once parsed.
    """


__all__ = [
    "ExtractionError",
    "ExtractionValidationError",
    "GroundingError",
    "LLMFormattingError",
    "MalformedSpanError",
    "UnsupportedInputError",
]
