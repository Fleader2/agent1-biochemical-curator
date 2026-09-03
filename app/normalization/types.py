"""Shared entity-normalization result types.

These are plain Python types, not database-backed: no schema or migration
exists for them, and none is proposed. ``NormalizationStatus`` and
``MatchMethod`` are deliberately kept separate from ``app.models.enums``
(which *is* schema-backed, one PostgreSQL ``ENUM`` per class) to avoid any
implication that a normalization verdict is itself persisted anywhere --
Phase 4 has no persistence layer yet, matching the same boundary Phase 3's
connectors kept around database writes.

``NormalizationResult.organism_id`` exists from this first increment even
though organism normalization itself is a later increment (Increment 2),
specifically so that entity-specific result construction (Increment 4
onward: Gene, Protein, EnzymeComplex -- all organism-scoped in the schema)
never needs a breaking change to this type's shape. It is ``None`` for
genuinely organism-agnostic entities (``Compound``, ``Publication`` have no
``organism_id`` column at all) and for an optionally-scoped ``Reaction``
normalized without resolved organism context (``reaction.organism_id`` is
nullable in the schema). This module never assumes or defaults to a
particular organism -- there is no default value for ``organism_id`` other
than ``None``, and nothing here reads settings or any other global state to
fill it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.models.enums import SourceType


class NormalizationStatus(StrEnum):
    """The verdict of attempting to resolve one source record to a canonical entity."""

    MATCHED = "MATCHED"
    NEW = "NEW"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"
    UNRESOLVED = "UNRESOLVED"


class MatchMethod(StrEnum):
    """How a verdict was reached, when it involved a match at all."""

    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"
    EXPLICIT_CROSS_REFERENCE = "EXPLICIT_CROSS_REFERENCE"
    CANDIDATE_SYNONYM = "CANDIDATE_SYNONYM"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """The outcome of normalizing one source-scoped record against existing entities.

    Deliberately carries no confidence score: no current specification
    defines how one would be calculated for entity identity (as opposed to
    claim confidence, which ``docs/03_agent_behavior.md`` does define), so
    none is invented here.

    Invariants (enforced in ``__post_init__``, not just documented) -- only
    the ones that are genuinely universal across every future entity
    normalizer, not one fixed structural shape per status:

    * ``MATCHED`` requires ``matched_entity_id`` and carries no
      ``candidate_entity_ids`` -- a match is exactly one entity, agreed with.
    * ``NEW``/``UNRESOLVED`` carry neither. The two are structurally
      identical; which one applies to a given zero-candidate lookup is an
      entity-specific decision based on identifier strength (see
      ``app.normalization.identifiers.classify_candidates``), not something
      this type or ``identifiers.py`` decides on its own.
    * ``AMBIGUOUS`` never carries ``matched_entity_id`` and requires at
      least *one* ``candidate_entity_id``. This is deliberately looser than
      ``app.normalization.identifiers.CandidateSetState.AMBIGUOUS`` (which
      is 2+ *by candidate count alone*): ``NormalizationStatus.AMBIGUOUS`` is
      a higher-level scientific-identity verdict, and a Level-3 synonym/name
      lookup can find exactly one candidate that is still insufficient
      evidence for ``MATCHED`` -- e.g. ``match_method=CANDIDATE_SYNONYM``
      with a single candidate is deliberately valid.
    * ``CONFLICTED`` requires *some* entity context -- ``matched_entity_id``
      is set, or ``candidate_entity_ids`` is non-empty, or both -- but no
      single shape is imposed beyond that. A conflict can be one exact
      identifier anchor whose other supplied metadata disagrees with the row
      it resolved to (``matched_entity_id`` set, no candidates), or two
      authoritative identifiers on the same incoming record that resolve to
      *different* existing entities (no single ``matched_entity_id``,
      multiple ``candidate_entity_ids`` -- e.g. a PubMed record whose PMID
      and DOI already belong to two different ``Publication`` rows).
    * ``CANDIDATE_SYNONYM`` (a Level-3, inherently weaker method) must never
      be paired with ``MATCHED``.

    ``candidate_entity_ids`` is not deduplicated or reordered here: this
    type stores exactly what it is given, and canonicalizing a candidate set
    (if ever needed) belongs in the utility that produces it, not silently
    inside this type.
    """

    status: NormalizationStatus
    source: SourceType
    source_identifier: str
    entity_type: str
    match_method: MatchMethod
    organism_id: UUID | None = None
    matched_entity_id: UUID | None = None
    candidate_entity_ids: tuple[UUID, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.source_identifier.strip():
            raise ValueError("source_identifier must not be empty")
        if not self.entity_type.strip():
            raise ValueError("entity_type must not be empty")

        if (
            self.match_method is MatchMethod.CANDIDATE_SYNONYM
            and self.status is NormalizationStatus.MATCHED
        ):
            raise ValueError("CANDIDATE_SYNONYM must never be paired with MATCHED")

        if self.status is NormalizationStatus.MATCHED:
            if self.matched_entity_id is None:
                raise ValueError("MATCHED requires matched_entity_id")
            if self.candidate_entity_ids:
                raise ValueError("MATCHED must not carry candidate_entity_ids")
        elif self.status in (NormalizationStatus.NEW, NormalizationStatus.UNRESOLVED):
            if self.matched_entity_id is not None:
                raise ValueError(f"{self.status} must not carry matched_entity_id")
            if self.candidate_entity_ids:
                raise ValueError(f"{self.status} must not carry candidate_entity_ids")
        elif self.status is NormalizationStatus.AMBIGUOUS:
            if self.matched_entity_id is not None:
                raise ValueError("AMBIGUOUS must not carry matched_entity_id")
            if not self.candidate_entity_ids:
                raise ValueError("AMBIGUOUS requires at least one candidate_entity_id")
        elif self.status is NormalizationStatus.CONFLICTED:
            if self.matched_entity_id is None and not self.candidate_entity_ids:
                raise ValueError(
                    "CONFLICTED requires matched_entity_id or candidate_entity_ids "
                    "(some entity context)"
                )


__all__ = ["MatchMethod", "NormalizationResult", "NormalizationStatus"]
