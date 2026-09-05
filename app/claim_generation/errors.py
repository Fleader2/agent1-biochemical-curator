"""Structured errors for the Claim Generation layer.

Mirrors ``app.extraction.errors``/``app.persistence.errors``'s shape: a
small, flat exception hierarchy under one base class, one member per
distinct thing that can go wrong.
"""

from __future__ import annotations


class ClaimGenerationError(Exception):
    """Base class for Claim-Generation-layer exceptions.

    Also raised directly for a malformed top-level call to
    ``generate_candidate_claims`` -- a ``typing_hints`` sequence whose
    length does not match ``extractions``, or an ``extractions`` entry that
    is not an ``EvidenceExtraction`` at all. The call cannot proceed at
    all in either case, the same category of failure
    ``app.extraction.errors.UnsupportedInputError`` represents for
    ``extract_evidence``.
    """


class EntityTypingError(ClaimGenerationError):
    """An entity-typing hint itself is invalid or unusable.

    Raised when a caller-supplied ``EntityKind`` (or a raw
    ``SUBJECT_TYPE``/``OBJECT_TYPE`` prompt-contract value, see
    ``app.claim_generation.prompts``) does not name a kind this module
    knows how to dispatch -- never for "this text was not enough to
    normalize," which is not a typing error but an ordinary, expected
    outcome (see ``NormalizationFailureError``'s docstring for the
    distinction).
    """


class NormalizationFailureError(ClaimGenerationError):
    """Calling into ``app.normalization.*`` failed unexpectedly.

    Wraps any exception normalization raises that is *not* the ordinary
    "this text alone is not sufficient identity signal" case (a plain
    ``ValueError`` from an ``Identity`` dataclass's own ``__post_init__``,
    e.g. a bare title with no PMID/PMCID/DOI for
    ``PublicationIdentity``) -- that case is not an error at all, it
    simply leaves the entity unresolved (Increment 13 instructions, Step
    4: "If an entity type cannot be determined safely, leave it
    unresolved. Do not guess."). This error exists specifically so that a
    genuine bug (a broken ``Lookup`` implementation, an unexpected
    ``TypeError``) is never silently absorbed as if it were an ordinary
    unresolved-entity outcome, and so that raw normalization exceptions
    are never exposed to callers of this package without context (Step
    20: "Do not expose raw normalization exceptions.").
    """


class ClaimValidationError(ClaimGenerationError):
    """A ``CandidateClaim`` fails required-field or consistency validation.

    Covers everything ``app.claim_generation.validation`` checks: a
    missing/blank ``predicate``, a ``supporting_span``/``source``/
    ``source_identifier`` that does not match the originating
    ``EvidenceExtraction``, a numeric value with no accompanying original
    text, a unit with neither a text nor a numeric value, or an entity
    reference typed inconsistently with its own role (e.g. an ``organism``
    reference whose ``entity_kind`` is not ``ORGANISM``).
    """


__all__ = [
    "ClaimGenerationError",
    "ClaimValidationError",
    "EntityTypingError",
    "NormalizationFailureError",
]
