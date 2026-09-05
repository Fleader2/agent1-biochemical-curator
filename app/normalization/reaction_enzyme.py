"""Reaction<->enzyme association identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``
-- candidate lookups are performed through the injected, read-only
``ReactionEnzymeLookup`` protocol, which a later, separate persistence
increment implements against the real database. This mirrors the same
retrieval/persistence boundary every prior increment established.

**This increment is relationship identity normalization, not biological
reasoning.** It never determines whether a protein *actually* catalyzes a
reaction -- it only determines whether a *proposed* Reaction<->enzyme
relationship already exists as a distinct row, conflicts with one, is
ambiguous, may safely become a new row, or is unresolved. Both the Reaction
and the Protein/EnzymeComplex referenced are already-normalized UUIDs,
supplied by upstream normalizers (``app.normalization.reaction``,
``app.normalization.protein``) -- this module never queries ``Reaction``,
``Protein``, ``EnzymeComplex``, ``Gene``, ``Claim``, or ``Evidence``.

**Schema, verified directly from ``app/models/reaction.py``'s
``ReactionEnzyme`` class, not assumed:**

* ``reaction_id: UUID`` -- ``NOT NULL``, FK to ``reaction.id``
  (``ondelete="RESTRICT"``).
* ``protein_id: UUID | None`` -- nullable FK to ``protein.id``.
* ``complex_id: UUID | None`` -- nullable FK to ``enzyme_complex.id``.
* ``relationship: str`` -- ``NOT NULL``, a **plain ``VARCHAR`` column, not a
  database enum** -- despite ``docs/02_database_schema.md`` listing example
  values (``CATALYZES``, ``REQUIRED_FOR``, ``PUTATIVE_CATALYST``,
  ``ISOENZYME``), nothing in the schema constrains it to that set. The
  model's own docstring: "The specification states that exactly one of
  ``protein_id``/``complex_id`` 'should normally' be populated -- soft
  language, not 'must' -- so no CHECK constraint is added here." This
  module enforces the *stricter* reading the increment instructions require
  (exactly one, always -- see ``ReactionEnzymeIdentity``) even though the
  database itself does not.
* ``confidence_summary: Decimal | None``, ``notes: str | None`` -- pure
  metadata, excluded entirely from ``ReactionEnzymeIdentity``/
  ``ReactionEnzymeCandidate``. Confidence and free-text notes belong to a
  later evidence/confidence-scoring layer, never to relationship identity
  (see "Evidence neutrality" below).
* **No index, no uniqueness constraint of any kind exists on this table** --
  not on ``(reaction_id, protein_id)``, not on ``(reaction_id, complex_id)``,
  not on ``relationship``. Nothing in the schema prevents two rows from
  recording the same reaction/protein pair, possibly with different
  ``relationship`` values, or even as exact duplicates. Full candidate-list
  discipline applies unconditionally -- ``AMBIGUOUS`` is a live, expected
  outcome, not a defensive edge case.
* No ``organism_id`` column, and no organism reference of any kind, exists
  on ``reaction_enzyme`` at all. Organism is only knowable *indirectly*, via
  ``Reaction.organism_id``/``Protein.organism_id``/
  ``EnzymeComplex.organism_id`` -- none of which this module reads (it
  never queries those tables). Organism-consistency checking between a
  Reaction and its associated Protein/EnzymeComplex is therefore **not
  performed here** -- it is an open policy question (see this increment's
  completion report), not silently resolved or invented.

**Field-naming note.** The increment instructions describe fields named
``enzyme_complex_id`` and ``catalytic_role``; the actual schema names them
``complex_id`` and ``relationship``. This module uses the schema's own
names throughout, per the same "verify, do not assume" discipline every
prior increment has followed.

**Identity hierarchy.** There is exactly one identity anchor, in two
mutually exclusive shapes: ``(reaction_id, protein_id)`` or
``(reaction_id, complex_id)``. ``ReactionEnzymeIdentity`` enforces that
exactly one of ``protein_id``/``complex_id`` is supplied -- never both,
never neither -- so a single normalization request only ever queries one
of ``by_reaction_and_protein``/``by_reaction_and_complex``, never both.
This is a structural guarantee, not a runtime check: **Reaction+Protein and
Reaction+Complex are two disjoint identity spaces this module never
bridges** -- it never infers that a protein's complex membership makes a
direct Reaction+Protein association equivalent to a Reaction+Complex one,
and it never treats one as evidence toward the other. Each is independent.

**``relationship`` is treated as inert metadata for identity purposes**, not
as part of the identity anchor -- a resolved ``(reaction_id, protein_id)``
match is ``MATCHED`` regardless of whether the incoming and existing rows'
``relationship`` values agree. No documented policy in this repository
states whether ``CATALYZES`` vs. ``PUTATIVE_CATALYST`` on the same
reaction/protein pair should be the same relationship (with a metadata
discrepancy) or genuinely distinct relationships, and the schema does not
enforce either reading (no uniqueness constraint touches ``relationship``
at all). Treating it as inert is the conservative default, consistent with
how ``app.normalization.gene`` leaves ``symbol`` disagreement unchecked and
``app.normalization.compound``/``app.normalization.compartment`` leave
their own Level 2 metadata fields inert -- this is recorded as an open
policy question (see completion report), not decided permanently.

**Isoenzymes and multi-function proteins are both allowed, deliberately.**
Two different ``(reaction_id, protein_id)`` pairs sharing the same
``reaction_id`` (two proteins catalyzing one reaction) are two independent
associations, never collapsed. Two different pairs sharing the same
``protein_id`` (one protein catalyzing two reactions) are likewise
independent. Every ``compound_id``-shaped UUID here (``protein_id``,
``complex_id``, ``reaction_id``) is treated as fully opaque -- no gene
family, EC classification, UniProt family, or subunit-composition
relationship is ever inferred from it.

**EC number never participates, anywhere.** No lookup method, no identity
role, no conflict role. Many distinct proteins share one EC-classified
catalytic activity; that classifies an activity, not a specific
relationship, and EC numbers belong to a later evidence-extraction phase
(``docs/03_agent_behavior.md``), not identity normalization.

**Evidence neutrality.** This module never reads or reasons about claims,
evidence, confidence scores, publications, reviewer identity, or review
history -- none of those exist on ``reaction_enzyme`` at all (they live on
``Claim``/``Evidence``, entirely separate tables this module never
queries), and Step 15 of this increment's instructions excludes them
explicitly regardless.

**No fuzzy matching, ever.** UUID equality only, at every level. No
approximate matching, no textual comparison of any kind (``relationship``
is never even compared, let alone fuzzily).

**Connector adapters deliberately omitted.** No connector in this
repository exposes a structured Reaction<->enzyme association (a
``(reaction_id, protein_id_or_complex_id)`` pair) at any abstraction level.
KEGG's ``KeggReactionRecord.enzymes`` is a tuple of EC number strings, not
protein/complex identifiers, and EC numbers are never used for this
purpose (see above) -- inferring an association from it, from a KEGG
pathway, or from a GO annotation would be exactly the kind of biological
inference this increment's instructions forbid ("This increment must not
determine whether a protein truly catalyzes a reaction").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import SourceType
from app.normalization.identifiers import (
    CandidateSetState,
    classify_candidates,
    require_non_empty,
    unique_by_id,
)
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

_ENTITY_TYPE = "reaction_enzyme"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no rewriting.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class ReactionEnzymeIdentity:
    """Source-neutral description of one proposed Reaction<->enzyme relationship.

    ``reaction_id``/``protein_id``/``complex_id`` are already-normalized
    UUIDs from upstream normalizers -- this module never resolves a
    Reaction, Protein, or EnzymeComplex by name, EC number, or any other
    weak signal itself. Deliberately excludes ``confidence_summary``/
    ``notes`` (pure metadata, no identity role) and anything evidence-shaped
    (claims, publications, reviewers -- see module docstring's "Evidence
    neutrality").

    Requires ``reaction_id`` and *exactly one* of ``protein_id``/
    ``complex_id`` -- never both, never neither (Increment 9 instructions,
    Step 2), even though the database schema itself only says this "should
    normally" hold (soft language, no CHECK constraint -- see module
    docstring). ``relationship`` is optional at construction (kept for
    later creation-completeness checking only -- see
    ``_has_creation_complete_metadata``); it is never compared for identity
    or conflict purposes (see module docstring).
    """

    source: SourceType
    source_identifier: str
    reaction_id: UUID

    protein_id: UUID | None = None
    complex_id: UUID | None = None

    relationship: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if self.reaction_id is None:
            raise ValueError("ReactionEnzymeIdentity requires reaction_id")
        if (self.protein_id is None) == (self.complex_id is None):
            raise ValueError(
                "ReactionEnzymeIdentity requires exactly one of protein_id or complex_id "
                "-- never both, never neither"
            )
        object.__setattr__(self, "relationship", _clean(self.relationship))


@dataclass(frozen=True, slots=True)
class ReactionEnzymeCandidate:
    """A read-only snapshot of one existing ``ReactionEnzyme`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. Carries only the fields needed for identity comparison;
    ``confidence_summary``/``notes`` are deliberately absent (see module
    docstring).
    """

    id: UUID
    reaction_id: UUID
    protein_id: UUID | None = None
    complex_id: UUID | None = None
    relationship: str | None = None


@runtime_checkable
class ReactionEnzymeLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_reaction_enzyme`` never touches
    SQLAlchemy.

    Exactly two methods, one per identity shape -- no ``by_reaction``/
    ``by_protein``/``by_complex`` standalone method exists, since neither
    is this module's identity anchor on its own (only the
    ``(reaction_id, protein_id)``/``(reaction_id, complex_id)`` *pair* is).
    No method here can insert, update, or delete a row. Deliberately has no
    ``by_ec_number``, gene, publication, evidence, or free-text method --
    none of those is Reaction<->enzyme relationship identity (see module
    docstring).
    """

    def by_reaction_and_protein(
        self, reaction_id: UUID, protein_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        """Existing associations with this exact ``(reaction_id, protein_id)`` pair
        (0, 1, or more).
        """
        ...

    def by_reaction_and_complex(
        self, reaction_id: UUID, complex_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        """Existing associations with this exact ``(reaction_id, complex_id)`` pair
        (0, 1, or more).
        """
        ...


def _has_creation_complete_metadata(identity: ReactionEnzymeIdentity) -> bool:
    """The schema-derived creation-completeness rule.

    ``relationship`` is the only NOT NULL, non-identity column on
    ``reaction_enzyme`` (``app/models/reaction.py``), so it is the only
    thing this module requires present before ``NEW`` may be considered.
    Not invented: this is the literal schema constraint, nothing more.
    """
    return bool(identity.relationship)


def normalize_reaction_enzyme(
    identity: ReactionEnzymeIdentity, *, lookup: ReactionEnzymeLookup
) -> NormalizationResult:
    """Resolve one proposed Reaction<->enzyme relationship against existing rows.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is always ``None`` on the returned result --
    ``reaction_enzyme`` has no organism column at all (see module
    docstring).
    """
    if identity.protein_id is not None:
        candidates = unique_by_id(
            lookup.by_reaction_and_protein(identity.reaction_id, identity.protein_id)
        )
    else:
        assert identity.complex_id is not None  # guaranteed by __post_init__'s XOR check
        candidates = unique_by_id(
            lookup.by_reaction_and_complex(identity.reaction_id, identity.complex_id)
        )

    state = classify_candidates(tuple(candidate.id for candidate in candidates))

    if state is CandidateSetState.SINGLE_MATCH:
        return NormalizationResult(
            status=NormalizationStatus.MATCHED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            matched_entity_id=candidates[0].id,
            reason="resolved via exact reaction/enzyme pair",
        )

    if state is CandidateSetState.AMBIGUOUS:
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            candidate_entity_ids=tuple(sorted(candidate.id for candidate in candidates)),
            reason="more than one existing association shares this exact reaction/enzyme pair",
        )

    # NO_MATCH.
    if _has_creation_complete_metadata(identity):
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            reason="no existing association matches this exact reaction/enzyme pair",
        )
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.NONE,
        reason=(
            "no existing association matches this exact reaction/enzyme pair, and no "
            "relationship was supplied to safely create a new one"
        ),
    )


__all__ = [
    "ReactionEnzymeCandidate",
    "ReactionEnzymeIdentity",
    "ReactionEnzymeLookup",
    "normalize_reaction_enzyme",
]
