"""Structured errors for the Entity Mention Resolution layer.

Mirrors ``app.claim_generation.errors``/``app.extraction.errors``'s shape:
a small, flat exception hierarchy under one base class. Per this
increment's own instructions (Step 28): "Use structured outcomes for
expected no-candidate/ambiguous conditions. Reserve exceptions for
programmer errors or malformed source records." Every *expected* outcome
(no candidate, ambiguous, unsupported kind, a connector failure) is
therefore represented on ``MentionResolutionResult`` itself
(``app.entity_resolution.types``), never raised -- the exceptions below
are for genuine defects only.
"""

from __future__ import annotations


class EntityResolutionError(Exception):
    """Base class for Entity-Mention-Resolution-layer exceptions.

    Also raised directly for a malformed top-level call -- an
    ``EntityMention`` that is not actually an ``EntityMention``, or an
    unrecognized combination this module has no dispatch for at all.
    """


class UnsupportedEntityKindError(EntityResolutionError):
    """Raised only when resolution is asked to dispatch on a value that is not

    a real ``EntityKind`` member at all (a defensive, should-never-happen
    case -- mirrors ``app.claim_generation.mapping``'s identical
    dispatch-safety-net pattern). An ``EntityKind`` that is simply
    unsupported *today* (no trustworthy connector exists for it, e.g.
    ``PROTEIN``/``ORGANISM``/``COMPARTMENT``) is not an error at all -- it
    is the expected, structured
    ``MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND`` outcome.
    """


class ConnectorResolutionError(EntityResolutionError):
    """A connector call failed in a way this module did not anticipate.

    Ordinary connector failures (timeouts, HTTP errors, rate limiting) are
    caught by ``app.entity_resolution.resolver`` and converted into a
    structured ``MentionResolutionStatus.SOURCE_FAILURE`` result -- they
    never reach here. This is reserved for a genuine programmer error in
    how a connector was invoked (e.g. a malformed query that
    ``ValueError``s before any network call is even attempted).
    """


class CandidateMappingError(EntityResolutionError):
    """A retrieved external record could not be safely mapped to an existing

    ``*Identity`` type. Raised only for a genuinely malformed source
    record (e.g. a connector's own adapter raising unexpectedly) -- never
    for "this record does not carry a strong identifier," which is an
    ordinary, expected condition handled by simply not producing a
    candidate for that record.
    """


class NormalizationResolutionError(EntityResolutionError):
    """Calling into ``app.normalization.*`` failed unexpectedly while resolving a candidate.

    Mirrors ``app.claim_generation.errors.NormalizationFailureError`` --
    wraps any exception that is not the ordinary "insufficient identity
    signal" ``ValueError`` a normalizer's own ``*Identity`` construction
    can raise (which, here, should not occur at all: every candidate this
    module builds comes from a genuine external record with at least one
    real strong identifier -- see ``app.entity_resolution.adapters``).
    """


__all__ = [
    "CandidateMappingError",
    "ConnectorResolutionError",
    "EntityResolutionError",
    "NormalizationResolutionError",
    "UnsupportedEntityKindError",
]
