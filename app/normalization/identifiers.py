"""Shared, source-agnostic normalization primitives.

Everything here is pure (no I/O, no database access) and knows nothing about
any particular entity type, source, or organism -- that separation is
deliberate. Turning a candidate lookup's raw shape (zero, one, or many rows)
into a full ``app.normalization.types.NormalizationResult`` (``NEW`` vs.
``UNRESOLVED``, ``MATCHED`` vs. ``AMBIGUOUS``, with ``reason`` text, an
``entity_type``, an ``organism_id``, ...) is an entity-specific normalizer's
job (Gene, Protein, Publication, ...; later increments), not this module's.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class CandidateSetState(StrEnum):
    """The shape of a candidate lookup -- not a normalization verdict.

    Deliberately distinct from ``NormalizationStatus``: how many rows an
    identifier lookup returned is the same question regardless of what kind
    of entity was being looked up, so classifying it is safe to share.
    Whether a ``NO_MATCH`` should become ``NormalizationStatus.NEW`` (safe to
    create) or ``UNRESOLVED`` (identifier too weak to act on) depends on how
    strong the identifier that produced zero candidates actually was --
    information only the entity-specific caller has, so that decision is
    deliberately left to it rather than made here.
    """

    NO_MATCH = "NO_MATCH"
    SINGLE_MATCH = "SINGLE_MATCH"
    AMBIGUOUS = "AMBIGUOUS"


def classify_candidates(candidate_ids: Sequence[UUID]) -> CandidateSetState:
    """Classify a candidate-id list by count alone.

    Callers are responsible for supplying an already-correctly-scoped
    candidate list before calling this -- for example, filtering by
    ``organism_id`` in the query that produces ``candidate_ids`` for any
    organism-scoped entity (``Gene``, ``Protein``, ``EnzymeComplex`` always;
    ``Reaction`` when organism context is resolved). This function has no
    concept of organism, source, or entity type, and never will.
    """
    if len(candidate_ids) == 0:
        return CandidateSetState.NO_MATCH
    if len(candidate_ids) == 1:
        return CandidateSetState.SINGLE_MATCH
    return CandidateSetState.AMBIGUOUS


def require_non_empty(value: str, *, field_name: str = "value") -> str:
    """Strip and validate that a required identifier-ish string was actually supplied.

    Shared hygiene only -- not identifier-*shape* validation, which stays
    source-specific (e.g. ``app.connectors.sgd.classify_sgd_identifier``).
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    return stripped


class _HasId(Protocol):
    """Structural type: any candidate snapshot with a UUID ``id`` attribute."""

    id: UUID


def unique_by_id[T: _HasId](candidates: Sequence[T]) -> tuple[T, ...]:
    """Deduplicate a candidate sequence by ``.id``, preserving first-seen order.

    Promoted here (Phase 4, Increment 4) after the identical dedup-by-id
    logic was hand-written a third time across entity normalizers
    (``app.normalization.organism``, ``app.normalization.publication``, and
    now ``app.normalization.gene``) -- narrow, generic, and exactly the kind
    of behavior worth sharing, unlike a general matcher/fuzzy-matching
    framework, which this module still never provides. A lookup
    implementation that could return the same row twice (e.g. from a join)
    must not manufacture ``CandidateSetState.AMBIGUOUS`` on that account
    alone; ``classify_candidates()`` is deliberately cardinality-only and
    must not itself add deduplication, so callers dedupe first, here.

    ``app.normalization.organism``'s and ``app.normalization.publication``'s
    own private ``_unique_candidates`` helpers are left as-is rather than
    retrofitted to call this -- both are already committed and approved, and
    consolidating them is a cosmetic change with no behavior difference, not
    something this increment's scope requires.
    """
    seen: dict[UUID, T] = {}
    for candidate in candidates:
        seen.setdefault(candidate.id, candidate)
    return tuple(seen.values())


__all__ = [
    "CandidateSetState",
    "classify_candidates",
    "require_non_empty",
    "unique_by_id",
]
