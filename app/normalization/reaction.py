"""Reaction identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference``/``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``ReactionLookup`` protocol,
which a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary every prior
increment established.

Reaction↔enzyme association (``reaction_enzyme``) is explicitly out of
scope -- this module never reads or reasons about it.

**Schema, verified directly from ``app/models/reaction.py``/
``app/models/reaction_participant.py``, not assumed:**

* ``internal_id: str`` -- ``NOT NULL``, **genuinely database-unique**
  (``unique=True``, unlike every external identifier below). Despite being
  the one truly unique column on this table, it is a *persistence*
  identifier assigned once a row is created, not incoming biological
  identity -- ``ReactionIdentity`` has no ``internal_id`` field at all, it
  is never used to look anything up, and this module never generates one
  (``docs/02_database_schema.md``: "Reaction IDs must remain stable after
  creation" -- assigning one is a persistence-layer decision). It is
  carried on ``ReactionCandidate`` purely as inert display metadata, since
  a real persisted row genuinely has one.
* ``name: str`` -- ``NOT NULL``. Not indexed, not unique.
* ``organism_id: UUID | None`` -- nullable FK to ``organism.id``. **Unlike
  ``Compartment``, no model docstring or migration documents what
  ``organism_id IS NULL`` means for ``Reaction`` -- there is no verified
  evidence of a "standard/reference reaction" concept the way Compartment's
  13 seed rows are documented.** Absent that evidence, this module does
  **not** replicate Compartment's "``None`` is compatible with any
  requested organism" reconciliation rule -- inventing that would be
  assuming a generic/global reaction model the source does not support
  (Increment 8 instructions, Step 8). ``normalize_reaction`` therefore
  requires a non-``None`` ``organism_id`` (mirroring Gene/Protein's
  convention), and a candidate whose own ``organism_id`` is anything other
  than *exactly* the requested one -- including ``None`` -- is treated as
  a different scope. What a null-organism ``Reaction`` row is *for* is
  recorded as an open policy question in this increment's completion
  report, not resolved here.
* ``reversible: bool | None`` -- nullable. Pure metadata: never read for
  identity, never used to reorder or reinterpret participants, never
  inferred. ``docs/03_agent_behavior.md``'s "Reversibility Behavior":
  reversibility must never be inferred from arrow notation or convention;
  ``reversible = NULL`` must remain unresolved. This module goes further --
  it does not even *consider* ``reversible`` for reaction identity at all,
  in either direction.
* ``reaction_type: str | None``, ``ec_number: str | None`` (indexed, not
  unique) -- metadata, never identity anchors (see "EC number policy"/
  "Reaction-type policy" below).
* ``kegg_reaction_id: str | None`` (indexed), ``rhea_id: str | None``
  (indexed), ``metacyc_reaction_id: str | None`` (**not even indexed**) --
  **none carries a database uniqueness constraint**, matching the same
  permissive pattern already found on ``Compound``'s and ``Compartment``'s
  external identifiers. Full candidate-list discipline applies to all
  three unconditionally.
* ``balanced_mass``, ``balanced_charge``, ``status``, ``curation_state``,
  ``notes``: pure curation/validation metadata, excluded entirely from
  ``ReactionIdentity``/``ReactionCandidate`` -- they play no role in
  identity, matching the same "keep the type minimal" treatment every
  prior increment gives its own non-identity metadata columns. Mass/charge
  balance in particular belongs to a separate deterministic validation
  process (``.cursor/rules/01-scientific-integrity.mdc``: "Use deterministic
  software... for... mass balance[,] charge balance"), not to identity
  normalization.
* ``reaction_participant``: ``reaction_id``/``compound_id`` ``NOT NULL``,
  ``compartment_id`` nullable, ``role`` (``ReactionParticipantRole``)
  ``NOT NULL``, ``stoichiometry`` (``Numeric``, i.e. ``Decimal``)
  ``NOT NULL`` with a ``CHECK (stoichiometry > 0)`` constraint (enforced by
  the database, re-validated here defensively). **No uniqueness constraint
  of any kind** -- nothing prevents two structurally identical participant
  rows from existing under one reaction. This module never aggregates or
  deduplicates participants on the caller's behalf (see "Participant
  canonicalization" below): duplicates, if supplied, are preserved exactly
  as given.

**No structural/participant lookup capability exists anywhere in this
repository's architecture.** ``ReactionLookup`` accordingly has no
``by_structure``-shaped method, and this module does not invent a
persistence API to add one (Increment 8 instructions, Step 5: "Do not
invent a persistence API solely for this increment"). This has a real
consequence, stated plainly: **this module cannot discover a
"structure-only" candidate** -- a reaction sharing an incoming record's
exact participant structure but no external identifier and no matching
name is architecturally invisible to it. Structural comparison here
therefore only ever *corroborates or contradicts* a candidate already
found through an external identifier (see "Structural agreement/
disagreement on a strong-ID match" below); it never independently
discovers candidates the way ``by_name``/``by_kegg_reaction_id`` do. This
is a deliberate, reported limitation (see this increment's completion
report), not an oversight -- persistence-level structural indexing is
future work.

**Identifier hierarchy, as implemented:**

* LEVEL 1 (external reaction identifiers, global, symmetric peers --
  mirrors ``app.normalization.compound``'s treatment of its five Level 1
  fields): ``kegg_reaction_id``, ``metacyc_reaction_id``, ``rhea_id``.
  Compared and stored exactly as supplied -- no case-folding, no prefix
  stripping, no namespace conversion.
* LEVEL 2 (structural participant signature -- corroborates or contradicts
  an already-resolved Level 1 match only; never independently discovers or
  establishes a match, per the lookup-capability limitation above):
  the participant multiset (``compound_id``, ``role``, ``stoichiometry``,
  ``compartment_id`` per participant).
* LEVEL 3 (candidate generation only, organism-scoped, never independently
  ``MATCHED``): ``name``.
* NOT identity, ever: ``ec_number``, ``reaction_type``, ``reversible``,
  ``internal_id``, and (excluded entirely from both types)
  ``balanced_mass``, ``balanced_charge``, ``status``, ``curation_state``,
  ``notes``.

**Structural agreement/disagreement on a strong-ID match.** Once exactly
one candidate is resolved via Level 1 external-ID reconciliation, this
module compares the incoming ``participants`` against that candidate's own
``participants`` using an exact, order-independent, *multiplicity-sensitive*
signature (see ``participants_structurally_equal``): identical structure
corroborates the match; the candidate having no recorded participants at
all is compatible (missing structure, not a contradiction -- the same
"``None``/absent is compatible" convention every prior increment applies to
missing metadata); but a *supplied*, *different* participant structure is a
hard contradiction and produces ``CONFLICTED`` (Increment 8 instructions,
Step 13: "Prefer ``CONFLICTED`` for direct exact-structure disagreement
unless source documentation establishes representation tolerance" -- no
such documentation exists in this repository).

**No proportional-stoichiometry equivalence, no reversed-orientation
equivalence.** ``A + B -> C`` and ``2 A + 2 B -> 2 C`` are different exact
structures here -- no canonical-ratio reduction is performed. ``A -> B``
and ``B -> A`` are different exact structures -- reversibility is never
used to treat reversed participant orientation as equivalent, even when
both records are marked ``reversible=True``. Both are open policy
questions, not resolved here (see the completion report) -- inventing
either equivalence would be exactly the kind of chemical-equivalence logic
Increment 8's instructions forbid inventing.

**No proton/water/charge-state normalization, ever.** If one supplied
structure includes an explicit ``H+``/``H2O`` participant and another does
not, they are different exact structures. This module never adds, removes,
or reinterprets participants to make two structures agree -- that is
exactly the kind of "silently repairing the equation" scientific-integrity
forbids, and is a separate concern from the deterministic mass/charge
balance validation ``balanced_mass``/``balanced_charge`` represent (a
different process entirely, out of scope here).

**No generic/specific compound substitution.** Every ``compound_id`` on a
participant is treated as a fully opaque, already-resolved identity (from
``app.normalization.compound``, upstream of this module) -- this module
never inspects whether a compound is generic, never expands a generic
compound into specific ones or vice versa, and never treats a generic and
a specific compound's UUIDs as interchangeable just because one might be a
"kind of" the other.

**EC number and reaction-type policy.** Both are inert metadata: no
``ReactionLookup.by_ec_number``/``by_reaction_type`` method exists, EC/
reaction-type equality never produces ``MATCHED``, and disagreement on an
otherwise-resolved match never independently produces ``CONFLICTED``. Many
distinct reactions share one EC-classified catalytic activity; that
classifies an activity, not a specific transformation.

**No fuzzy matching, ever.** Exact string/value comparison only, at every
level. No case-folding, no synonym expansion, no punctuation-insensitive
matching -- none has an existing, documented, deterministic rule anywhere
in this repository, and inventing one here would violate
``.cursor/rules/01-scientific-integrity.mdc``.

**Connector adapters.** ``reaction_identity_from_kegg`` is implemented:
``app.connectors.kegg.KeggReactionRecord`` is the only existing
reaction-level connector record in this repository. Its ``equation`` field
is raw, unstructured text (KEGG compound entry IDs and coefficients in one
string) -- this module never parses it, so the resulting
``ReactionIdentity`` always has empty ``participants`` (Increment 8
instructions, Step 24: "Do not parse free-text equations into normalized
participants unless the existing connector already exposes structured
compound IDs and stoichiometry safely" -- it does not). No Rhea or MetaCyc
connector exists in this repository, so no adapter for either is
fabricated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import ReactionParticipantRole, SourceType
from app.normalization.identifiers import (
    CandidateSetState,
    classify_candidates,
    require_non_empty,
    unique_by_id,
)
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

if TYPE_CHECKING:
    from app.connectors.kegg import KeggReactionRecord

_ENTITY_TYPE = "reaction"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no punctuation rewriting, no
    equation parsing (see module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class ReactionParticipantIdentity:
    """One already-normalized reaction participant reference.

    Assumes upstream Compound/Compartment normalization has already
    resolved ``compound_id``/``compartment_id`` -- this module never
    normalizes a compound or compartment by name, and never infers a
    missing compartment. ``stoichiometry`` is ``Decimal`` (matching the
    ORM's ``Numeric`` column) with no implicit float coercion, so exact
    comparison never suffers float-precision surprises.
    """

    compound_id: UUID
    role: ReactionParticipantRole
    stoichiometry: Decimal
    compartment_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.compound_id is None:
            raise ValueError("ReactionParticipantIdentity requires compound_id")
        if not isinstance(self.role, ReactionParticipantRole):
            raise ValueError(
                f"ReactionParticipantIdentity.role must be a ReactionParticipantRole, "
                f"got {self.role!r}"
            )
        if self.stoichiometry is None or self.stoichiometry <= 0:
            raise ValueError("ReactionParticipantIdentity.stoichiometry must be positive")


def _participant_signature(
    participants: Sequence[ReactionParticipantIdentity],
) -> tuple[tuple[str, str, str, str], ...]:
    """A deterministic, order-independent, multiplicity-sensitive structural signature.

    A sorted tuple, not a set: two structurally identical duplicate
    participant rows must not silently collapse into one (the schema does
    not prevent duplicates -- see module docstring -- and no aggregation
    policy is documented, so none is invented). Every field is stringified
    before sorting so ``UUID``/enum/``Decimal``/``None`` never need to be
    compared against each other directly.
    """
    rows = tuple(
        (
            str(p.compound_id),
            p.role.value,
            str(p.compartment_id) if p.compartment_id is not None else "",
            str(p.stoichiometry),
        )
        for p in participants
    )
    return tuple(sorted(rows))


def participants_structurally_equal(
    left: Sequence[ReactionParticipantIdentity], right: Sequence[ReactionParticipantIdentity]
) -> bool:
    """Exact, order-independent structural equality of two participant sets.

    Compares ``compound_id``, ``role``, ``stoichiometry``, and
    ``compartment_id`` per participant -- never compound names, never
    proportionally-reduced stoichiometry, never reversed orientation (see
    module docstring). Exposed publicly since structural equality is a
    reusable, independently meaningful concept -- not only an internal
    step of ``normalize_reaction``.
    """
    return _participant_signature(left) == _participant_signature(right)


@dataclass(frozen=True, slots=True)
class ReactionIdentity:
    """Source-neutral description of one incoming reaction identity claim.

    Not coupled to any one connector's record class. Deliberately carries
    no ``organism_id`` (supplied separately, like Gene/Protein) and no
    ``internal_id`` (a persistence identifier, never incoming identity --
    see module docstring). Excludes ``balanced_mass``/``balanced_charge``/
    ``status``/``curation_state``/``notes`` entirely -- pure curation
    metadata, no identity role.

    Requires at least one identity signal: a Level 1 identifier
    (``kegg_reaction_id``/``metacyc_reaction_id``/``rhea_id``), a Level 3
    candidate-generation field (``name``), or a fully-specified participant
    structure (``participants``) -- otherwise there is nothing to normalize
    against. ``ec_number``/``reaction_type``/``reversible`` alone are never
    sufficient (inert metadata, see module docstring). Carries no
    confidence score: no specification defines one for reaction identity.
    """

    source: SourceType
    source_identifier: str

    kegg_reaction_id: str | None = None
    metacyc_reaction_id: str | None = None
    rhea_id: str | None = None

    name: str | None = None
    reaction_type: str | None = None
    ec_number: str | None = None
    reversible: bool | None = None

    participants: tuple[ReactionParticipantIdentity, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "kegg_reaction_id", _clean(self.kegg_reaction_id))
        object.__setattr__(self, "metacyc_reaction_id", _clean(self.metacyc_reaction_id))
        object.__setattr__(self, "rhea_id", _clean(self.rhea_id))
        object.__setattr__(self, "name", _clean(self.name))
        object.__setattr__(self, "reaction_type", _clean(self.reaction_type))
        object.__setattr__(self, "ec_number", _clean(self.ec_number))

        if not any(
            (
                self.kegg_reaction_id,
                self.metacyc_reaction_id,
                self.rhea_id,
                self.name,
                self.participants,
            )
        ):
            raise ValueError(
                "ReactionIdentity requires at least one identity signal (kegg_reaction_id, "
                "metacyc_reaction_id, rhea_id, name, or a nonempty participants tuple) -- "
                "ec_number/reaction_type/reversible alone are inert metadata, not Reaction "
                "identity"
            )


@dataclass(frozen=True, slots=True)
class ReactionCandidate:
    """A read-only snapshot of one existing ``Reaction`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. ``internal_id``/``name`` are ``str`` (not ``str | None``),
    matching the schema's ``NOT NULL`` constraints. ``internal_id`` is
    carried as inert display metadata only -- never compared, never looked
    up (see module docstring).
    """

    id: UUID
    organism_id: UUID | None
    internal_id: str
    name: str

    kegg_reaction_id: str | None = None
    metacyc_reaction_id: str | None = None
    rhea_id: str | None = None

    reversible: bool | None = None
    reaction_type: str | None = None
    ec_number: str | None = None

    participants: tuple[ReactionParticipantIdentity, ...] = ()


@runtime_checkable
class ReactionLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_reaction`` never touches SQLAlchemy.

    ``by_kegg_reaction_id``/``by_metacyc_reaction_id``/``by_rhea_id`` are
    **global** -- no organism parameter (mirrors
    ``app.normalization.compound``'s treatment of its own external
    identifiers). ``by_name`` is **organism-scoped**. No method here can
    insert, update, or delete a row. Deliberately has no ``by_ec_number``,
    ``by_reaction_type``, free-text-equation, participant-count, enzyme, or
    pathway method -- none of those is Reaction identity (see module
    docstring). Deliberately has **no structural/participant lookup method**
    either -- no existing architecture provides one, and this module does
    not invent a persistence API to add one (see module docstring's
    "structure-only candidate" limitation).
    """

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        """All reactions, in any organism, with this exact ``kegg_reaction_id`` (0, 1, or more)."""
        ...

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        """All reactions, in any organism, with this exact ``metacyc_reaction_id``
        (0, 1, or more).
        """
        ...

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        """All reactions, in any organism, with this exact ``rhea_id`` (0, 1, or more)."""
        ...

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ReactionCandidate]:
        """Reactions in ``organism_id`` with this exact ``name`` (0, 1, or more)."""
        ...


def _organism_scoped_candidates(
    organism_id: UUID, candidates: Sequence[ReactionCandidate]
) -> tuple[ReactionCandidate, ...]:
    """Deduplicate by id and assert every candidate's ``organism_id`` exactly matches.

    Used only for ``by_name``: that method is contractually required to
    filter by the exact requested ``organism_id`` itself -- a candidate
    that does not is a broken-lookup-implementation signal, not a
    legitimate outcome.
    """
    unique = unique_by_id(candidates)
    for candidate in unique:
        if candidate.organism_id != organism_id:
            raise ValueError(
                f"ReactionLookup returned candidate {candidate.id} from organism "
                f"{candidate.organism_id!r}, but normalize_reaction was called with "
                f"organism_id={organism_id!r} -- by_name must scope its query to the "
                "requested organism"
            )
    return unique


def _describe_identifier_disagreement(
    identity: ReactionIdentity, candidate: ReactionCandidate
) -> str | None:
    """Compare supplied Level 1 identifiers against a resolved candidate's own.

    Exact comparison only. A candidate field that is ``None`` is compatible
    (missing metadata, not a disagreement) -- same convention as every
    prior increment's own ``_describe_identifier_disagreement``.
    """
    checks: tuple[tuple[str, str | None, str | None], ...] = (
        ("kegg_reaction_id", identity.kegg_reaction_id, candidate.kegg_reaction_id),
        ("metacyc_reaction_id", identity.metacyc_reaction_id, candidate.metacyc_reaction_id),
        ("rhea_id", identity.rhea_id, candidate.rhea_id),
    )
    for field_name, supplied, existing in checks:
        if supplied is not None and existing is not None and supplied != existing:
            return (
                f"supplied {field_name} {supplied!r} disagrees with existing reaction's "
                f"{field_name} {existing!r}"
            )
    return None


def _describe_structural_disagreement(
    identity: ReactionIdentity, candidate: ReactionCandidate
) -> str | None:
    """Compare supplied participants against a resolved candidate's own recorded ones.

    Missing structure on either side is compatible (nothing to contradict).
    A supplied, different structure is a hard contradiction -- see module
    docstring's "Structural agreement/disagreement on a strong-ID match".
    """
    if not identity.participants or not candidate.participants:
        return None
    if not participants_structurally_equal(identity.participants, candidate.participants):
        return (
            "supplied reaction participants differ from the existing reaction's recorded "
            "participants"
        )
    return None


def normalize_reaction(
    identity: ReactionIdentity, *, organism_id: UUID, lookup: ReactionLookup
) -> NormalizationResult:
    """Resolve one source-supplied reaction identity against existing ``Reaction`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is required and has no default -- no null-organism
    "global reaction" fallback is assumed (see module docstring). Every
    returned ``NormalizationResult.organism_id`` equals the supplied
    ``organism_id`` regardless of status, following the convention
    established for Gene/Protein normalization.
    """
    anchor_results: list[tuple[str, tuple[ReactionCandidate, ...]]] = []
    if identity.kegg_reaction_id is not None:
        anchor_results.append(
            (
                "kegg_reaction_id",
                unique_by_id(lookup.by_kegg_reaction_id(identity.kegg_reaction_id)),
            )
        )
    if identity.metacyc_reaction_id is not None:
        anchor_results.append(
            (
                "metacyc_reaction_id",
                unique_by_id(lookup.by_metacyc_reaction_id(identity.metacyc_reaction_id)),
            )
        )
    if identity.rhea_id is not None:
        anchor_results.append(("rhea_id", unique_by_id(lookup.by_rhea_id(identity.rhea_id))))

    if anchor_results:
        by_id: dict[UUID, ReactionCandidate] = {}
        single_match_ids: set[UUID] = set()
        cross_organism_ids: set[UUID] = set()
        ambiguous_ids: set[UUID] = set()
        agreeing_anchors: list[str] = []

        for anchor_name, candidates in anchor_results:
            for candidate in candidates:
                by_id[candidate.id] = candidate
            state = classify_candidates(tuple(candidate.id for candidate in candidates))
            if state is CandidateSetState.SINGLE_MATCH:
                candidate = candidates[0]
                if candidate.organism_id == organism_id:
                    single_match_ids.add(candidate.id)
                    agreeing_anchors.append(anchor_name)
                else:
                    cross_organism_ids.add(candidate.id)
            elif state is CandidateSetState.AMBIGUOUS:
                ambiguous_ids.update(candidate.id for candidate in candidates)
            # NO_MATCH contributes nothing.

        if cross_organism_ids:
            # A supplied external identifier already belongs to a Reaction
            # in a *different* organism. Always a conflict -- never
            # silently ignored, never NEW (that would hide an
            # already-claimed identifier), and never picked as a match
            # just because it is the only clean hit.
            all_ids = tuple(sorted(cross_organism_ids | single_match_ids | ambiguous_ids))
            return NormalizationResult(
                status=NormalizationStatus.CONFLICTED,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=organism_id,
                candidate_entity_ids=all_ids,
                reason=(
                    "a supplied external reaction identifier already belongs to an existing "
                    "reaction in a different organism"
                ),
            )

        if single_match_ids or ambiguous_ids:
            if len(single_match_ids) == 1 and not ambiguous_ids:
                candidate_id = next(iter(single_match_ids))
                candidate = by_id[candidate_id]
                disagreement = _describe_identifier_disagreement(identity, candidate)
                if disagreement is None:
                    disagreement = _describe_structural_disagreement(identity, candidate)
                if disagreement is None:
                    return NormalizationResult(
                        status=NormalizationStatus.MATCHED,
                        source=identity.source,
                        source_identifier=identity.source_identifier,
                        entity_type=_ENTITY_TYPE,
                        match_method=MatchMethod.EXACT_IDENTIFIER,
                        organism_id=organism_id,
                        matched_entity_id=candidate_id,
                        reason=f"resolved via {', '.join(agreeing_anchors)}",
                    )
                return NormalizationResult(
                    status=NormalizationStatus.CONFLICTED,
                    source=identity.source,
                    source_identifier=identity.source_identifier,
                    entity_type=_ENTITY_TYPE,
                    match_method=MatchMethod.EXACT_IDENTIFIER,
                    organism_id=organism_id,
                    matched_entity_id=candidate_id,
                    reason=disagreement,
                )

            all_ids = tuple(sorted(single_match_ids | ambiguous_ids))
            if len(single_match_ids) >= 2:
                return NormalizationResult(
                    status=NormalizationStatus.CONFLICTED,
                    source=identity.source,
                    source_identifier=identity.source_identifier,
                    entity_type=_ENTITY_TYPE,
                    match_method=MatchMethod.EXACT_IDENTIFIER,
                    organism_id=organism_id,
                    candidate_entity_ids=all_ids,
                    reason=(
                        "different supplied identifiers resolved to different existing reactions"
                    ),
                )
            return NormalizationResult(
                status=NormalizationStatus.AMBIGUOUS,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=organism_id,
                candidate_entity_ids=all_ids,
                reason="a supplied identifier matches more than one existing reaction",
            )
        # else: every strong anchor was NO_MATCH -- fall through to Level 3.

    weak_candidates: tuple[ReactionCandidate, ...] = ()
    if identity.name is not None:
        weak_candidates = _organism_scoped_candidates(
            organism_id, lookup.by_name(organism_id, identity.name)
        )
    if weak_candidates:
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.CANDIDATE_SYNONYM,
            organism_id=organism_id,
            candidate_entity_ids=tuple(sorted(candidate.id for candidate in weak_candidates)),
            reason=(
                "no exact identifier matched, but an existing reaction in this organism "
                "shares the supplied name -- not enough to independently establish a match"
            ),
        )

    if anchor_results and identity.name is not None:
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            organism_id=organism_id,
            reason="no existing reaction matched the supplied identifier(s) in this organism",
        )

    if anchor_results:
        reason = (
            "no existing reaction matched the supplied identifier(s), and no name was "
            "supplied to safely create a new one"
        )
    else:
        reason = (
            "only weak/structural signals were supplied, with no matching existing reaction "
            "in this organism -- insufficient to create or match a reaction"
        )
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.NONE,
        organism_id=organism_id,
        reason=reason,
    )


def reaction_identity_from_kegg(record: KeggReactionRecord) -> ReactionIdentity:
    """Pure adapter: a KEGG connector's normalized reaction record -> a source-neutral identity.

    No I/O, no network, no inference -- exact copying of identifiers/metadata
    already present on ``record``. ``record`` itself is never mutated -- it
    is a frozen dataclass, and nothing here does anything but read its
    fields.

    * ``source_identifier``/``kegg_reaction_id`` are ``record.entry_id``
      (e.g. ``"R00299"``), always present.
    * ``name`` is ``record.names[0]`` when present -- KEGG's ``NAME`` field
      is a semicolon-separated list in source order, and the first entry is
      its own primary name (the same convention
      ``app.normalization.compound.compound_identity_from_kegg`` follows).
    * ``ec_number`` is ``record.enzymes[0]`` when present -- a KEGG reaction
      may list more than one EC number; only the first is copied (inert
      metadata regardless, see module docstring), and the rest remain
      reachable on ``record`` itself for any caller that needs them.
    * ``participants`` is always empty: ``record.equation`` is raw,
      unstructured text, and this function never parses it (see module
      docstring).
    """
    names = record.names
    enzymes = record.enzymes
    return ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier=record.entry_id,
        kegg_reaction_id=record.entry_id,
        name=names[0] if names else None,
        ec_number=enzymes[0] if enzymes else None,
    )


__all__ = [
    "ReactionCandidate",
    "ReactionIdentity",
    "ReactionLookup",
    "ReactionParticipantIdentity",
    "normalize_reaction",
    "participants_structurally_equal",
    "reaction_identity_from_kegg",
]
