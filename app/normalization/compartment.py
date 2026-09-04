"""Compartment identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``CompartmentLookup``
protocol, which a later, separate persistence increment implements against
the real database. This mirrors the same retrieval/persistence boundary
``app.normalization.organism``/``app.normalization.gene``/
``app.normalization.protein``/``app.normalization.compound`` established.

**Compartment is optionally organism-scoped -- a genuine, documented
nullable dimension, not an oversight.** ``app/models/compartment.py``'s own
docstring and migration ``0002_reference_data`` together *verify* (this is
not an inference) that ``organism_id IS NULL`` has a specific, deliberate
meaning: "The standard compartment seed rows created by migration
``0002_reference_data`` intentionally leave it ``NULL``, since no organism
seed row is specified in ``docs/02_database_schema.md``." The seed migration
inserts the 13 standard compartments (``cytosol``, ``mitochondrial matrix``,
... -- ``docs/02_database_schema.md``, "Initial Seed Data") with
``organism_id=NULL`` as organism-agnostic reference/template rows, not as
"unknown organism" placeholders. Because this semantic is *documented in
code*, not merely plausible, ``normalize_compartment`` accepts
``organism_id: UUID | None`` (required, keyword-only, **no default** -- the
caller must always state its intent explicitly, including explicitly
passing ``None`` to request global/reference scope) rather than requiring a
non-``None`` organism the way ``app.normalization.gene``/
``app.normalization.protein`` do for their genuinely ``NOT NULL``
``organism_id`` columns. This is a deliberate, schema-justified departure
from those two modules' pattern, not a mechanical copy.

**Schema, verified directly from ``app/models/compartment.py``, not
assumed:**

* ``organism_id: UUID | None`` -- nullable, FK to ``organism.id``
  (``ondelete="RESTRICT"``). See above.
* ``name: str`` -- ``NOT NULL``. **Not indexed, not unique** -- unlike
  every other entity normalized so far (``Gene.symbol``/``systematic_name``
  are at least indexed; ``Compound.canonical_name`` is indexed), Compartment
  has *no index at all* on any column except the primary key. This is the
  least-constrained schema of any entity normalized to date; candidate-list
  discipline is not a defensive nicety here, it is the only thing standing
  between this module and silently picking an arbitrary row.
* ``abbreviation: str | None``, ``ontology_id: str | None`` -- neither
  indexed nor unique.
* ``notes: Text | None`` -- metadata, excluded entirely from
  ``CompartmentIdentity``/``CompartmentCandidate`` (never an identity
  signal, per instructions -- the same treatment every prior increment gives
  its own free-text notes/description column).
* No ``docs/02_database_schema.md`` "Constraints" subsection exists for
  ``compartment`` (same situation as ``Protein``/``Compound``) --
  ``name``'s ``NOT NULL`` constraint is the entire, literal
  creation-completeness rule for ``NEW`` (see "Creation completeness"
  below).

**Identifier hierarchy, as implemented:**

* LEVEL 1 (global identity anchor): ``ontology_id``. Treated as global (no
  organism parameter on ``CompartmentLookup.by_ontology_id``), mirroring
  ``app.normalization.protein``'s treatment of ``uniprot_id``: an ontology
  identifier (e.g. a Gene Ontology cellular-component term, or Cell
  Ontology term) is conceptually organism-independent, and an
  organism-scoped query could hide an ontology ID already attached to a
  compartment row in a different organism, risking a false ``NEW``. No
  database uniqueness is assumed (none exists) -- full candidate-list
  discipline applies (see "Exact ontology reconciliation" below). No prefix
  stripping, no namespace mapping, no case-folding: the identifier is
  compared and stored exactly as supplied.
* LEVEL 2/3 (candidate generation only, pooled together, never
  independently ``MATCHED``): ``name``, ``abbreviation``. The instructions
  framing these as two separate tiers ("Level 2 -- exact organism-scoped
  name", "Level 3 -- exact abbreviation") was considered and deliberately
  *not* adopted as two behaviorally distinct tiers: neither column is
  indexed or unique, no existing normalizer in this repository (Gene,
  Protein, Compound -- 3 of the 4 prior increments) treats an analogous
  bare display-name field as match-capable, and the instructions themselves
  repeatedly warn against exactly the kind of near-miss confusion
  compartment names invite (``cytosol`` vs. ``cytoplasm``, ``mitochondrion``
  vs. ``mitochondrial matrix``). ``app.normalization.organism`` is the one
  prior exception (a bare, unqualified ``scientific_name`` *can* reach
  ``MATCHED`` when it is the only signal supplied at all) -- but that
  exception rests on a species name being a comparatively specific,
  low-collision biological identifier, a property compartment names do not
  share (a small, standard, frequently-confused vocabulary). This module
  does not extend that exception to Compartment. ``name`` and
  ``abbreviation`` are therefore both organism-scoped, both weak, and are
  pooled into one candidate-generation step (mirroring
  ``app.normalization.gene``'s pooling of ``systematic_name``/``symbol``/
  ``aliases``): any nonzero result is ``AMBIGUOUS``, never ``MATCHED``, even
  a single unique candidate, even when name and abbreviation agree.
* NOT identity, ever: ``notes``.

**Exact ontology reconciliation, including the nullable-organism dimension.**
A global ``by_ontology_id`` lookup can return 0, 1, or many rows (no
uniqueness is enforced). Reconciliation against the requested
``organism_id`` (which may itself be ``None``) uses the same "a ``None``
value is compatible, not a disagreement" convention every prior increment
already applies to *metadata* fields (``app.normalization.gene``/
``app.normalization.publication``/``app.normalization.compound``'s
``_describe_identifier_disagreement`` functions), extended here to the
``organism_id`` field itself:

* 0 rows: proceed to the weak (``name``/``abbreviation``) collision guard
  before ever considering ``NEW``.
* 1 row, and that row's ``organism_id`` is either exactly the requested one
  *or* ``None`` (a generic/reference compartment -- compatible with any
  requested scope, per the verified seed-row semantics above): ``MATCHED``.
* 1 row, and that row's ``organism_id`` is a *different*, non-``None``
  organism than requested: ``CONFLICTED`` -- never silently dropped, never
  ``NEW`` (that would hide an already-claimed ontology ID), never treated
  as a match just because it is the only clean hit.
* 2+ rows, every one either the requested organism or ``None``:
  ``AMBIGUOUS`` -- competing rows within scope, never picked from
  arbitrarily.
* 2+ rows where at least one belongs to a genuinely different, non-``None``
  organism: ``CONFLICTED``.

**Ontology/name disagreement, once ``MATCHED``.** Once an ontology ID
resolves a single compatible candidate, this module does **not** compare
the incoming ``name``/``abbreviation`` against that candidate's own values
for conflict purposes -- consistent with treating ``name``/``abbreviation``
as non-identity-capable (see above): a resolved ontology match is not
second-guessed by weaker signals. This is an open policy question, not a
final scientific judgment -- see the module's completion report for this
increment.

**Creation completeness.** ``NEW`` requires ``ontology_id`` to have been
supplied (consistent with every prior increment: Gene, Protein, and Compound
all require their own Level 1 signal before ``NEW`` is possible -- a bare
``name``/``abbreviation`` alone, with no external verification, is never
enough) *and* ``name`` to be present (the schema's only NOT NULL,
non-identity column, and the entire documented completeness rule -- nothing
more is invented). No compartment name is ever synthesized from an
abbreviation or ontology ID.

**No fuzzy matching, no synonym expansion, ever.** Exact string comparison
only, at every level. ``cytosol`` is never equated with ``cytoplasm``,
``mitochondrion`` is never equated with ``mitochondrial matrix``, ``ER`` is
never expanded to ``endoplasmic reticulum`` -- none of these has an
existing, documented, deterministic rule anywhere in this repository, and
inventing one here would violate
``.cursor/rules/01-scientific-integrity.mdc``. This module also performs no
ontology-hierarchy traversal (a child/parent organelle relationship, even a
true one, never establishes or implies identity here).

**Connector adapters deliberately omitted.** No connector in this
repository exposes a compartment-*definition* record. SGD does expose GO
cellular-component annotations (``app.connectors.sgd.SgdGoAnnotation``,
``aspect == "cellular component"``, e.g. a GO ID like ``"GO:0005829"`` with
term ``"cytosol"``) -- but those are gene/protein-side *localization
evidence* (``docs/03_agent_behavior.md``'s "Compartment Curation Behavior"
section is explicitly about localization *claims*, a distinct curation
concern), not compartment entity definitions submitted for identity
normalization. Converting one directly into a ``CompartmentIdentity`` would
conflate "evidence that some gene product localizes here" with "here is a
compartment," the same category error
``app.normalization.protein``/``app.normalization.gene`` already declined to
make for SGD's gene-side UniProt cross-reference. No KEGG or BRENDA
connector exposes any compartment/localization concept at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import SourceType
from app.normalization.identifiers import require_non_empty, unique_by_id
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

_ENTITY_TYPE = "compartment"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no synonym rewriting, no
    abbreviation expansion, no ontology-namespace canonicalization (see
    module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class CompartmentIdentity:
    """Source-neutral description of one incoming compartment identity claim.

    Not coupled to any one connector's record class. Deliberately carries no
    ``organism_id``: organism scope is supplied separately, as a required
    (but possibly-``None``-valued) keyword argument to
    ``normalize_compartment`` (see module docstring). Deliberately carries
    no ``notes`` field -- never an identity signal.

    Requires at least one identity signal: ``ontology_id`` (Level 1), or
    ``name``/``abbreviation`` (candidate generation only). Carries no
    confidence score: no specification defines one for compartment identity.
    """

    source: SourceType
    source_identifier: str

    ontology_id: str | None = None
    name: str | None = None
    abbreviation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "ontology_id", _clean(self.ontology_id))
        object.__setattr__(self, "name", _clean(self.name))
        object.__setattr__(self, "abbreviation", _clean(self.abbreviation))

        if not any((self.ontology_id, self.name, self.abbreviation)):
            raise ValueError(
                "CompartmentIdentity requires at least one identity signal (ontology_id, "
                "name, or abbreviation)"
            )


@dataclass(frozen=True, slots=True)
class CompartmentCandidate:
    """A read-only snapshot of one existing ``Compartment`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. ``organism_id`` is ``UUID | None``, matching the schema
    exactly -- ``None`` means a standard/reference compartment (see module
    docstring), not "unknown." ``name`` is ``str`` (not ``str | None``),
    matching the schema's ``NOT NULL`` constraint.
    """

    id: UUID
    organism_id: UUID | None
    name: str

    abbreviation: str | None = None
    ontology_id: str | None = None


@runtime_checkable
class CompartmentLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_compartment`` never touches SQLAlchemy.

    ``by_ontology_id`` is **global** -- no organism parameter (mirrors
    ``app.normalization.protein.ProteinLookup.by_uniprot_id``).
    ``by_name``/``by_abbreviation`` are **organism-scoped**: their
    ``organism_id`` parameter is ``UUID | None`` and must be matched
    *exactly* by every returned candidate, including ``None`` requesting
    only genuinely organism-``None`` rows -- these methods do **not**
    implicitly fall back to including standard/reference (``organism_id IS
    NULL``) rows when a specific organism is requested (see module
    docstring's completion-report open questions: whether they should is
    deliberately left undecided in this increment, not silently resolved
    either way). No method here can insert, update, or delete a row.
    Deliberately has no lookup by ``notes``, no fuzzy/substring method, no
    parent-organelle or reaction-membership method.
    """

    def by_ontology_id(self, ontology_id: str) -> Sequence[CompartmentCandidate]:
        """All compartments, in any organism, with this exact ``ontology_id`` (0, 1, or more)."""
        ...

    def by_name(self, organism_id: UUID | None, name: str) -> Sequence[CompartmentCandidate]:
        """Compartments whose ``organism_id`` exactly equals ``organism_id`` and whose
        ``name`` exactly equals ``name`` (0, 1, or more).
        """
        ...

    def by_abbreviation(
        self, organism_id: UUID | None, abbreviation: str
    ) -> Sequence[CompartmentCandidate]:
        """Compartments whose ``organism_id`` exactly equals ``organism_id`` and whose
        ``abbreviation`` exactly equals ``abbreviation`` (0, 1, or more).
        """
        ...


def _organism_scoped_candidates(
    organism_id: UUID | None, candidates: Sequence[CompartmentCandidate]
) -> tuple[CompartmentCandidate, ...]:
    """Deduplicate by id and assert every candidate's ``organism_id`` exactly matches.

    Used only for ``by_name``/``by_abbreviation``: those methods are
    contractually required to filter by the exact requested ``organism_id``
    themselves (including matching ``None`` to ``None`` only) -- a candidate
    that does not is a broken-lookup-implementation signal, not a legitimate
    outcome. This is a strict equality check, deliberately not the
    "``None`` is compatible with anything" reconciliation
    ``_reconcile_ontology_candidates`` uses for the global ``ontology_id``
    anchor -- the two mean different things (contract verification vs.
    business-level scope compatibility).
    """
    unique = unique_by_id(candidates)
    for candidate in unique:
        if candidate.organism_id != organism_id:
            raise ValueError(
                f"CompartmentLookup returned candidate {candidate.id} from organism "
                f"{candidate.organism_id!r}, but normalize_compartment was called with "
                f"organism_id={organism_id!r} -- by_name/by_abbreviation must scope their "
                "query to exactly the requested organism"
            )
    return unique


def _has_creation_complete_metadata(identity: CompartmentIdentity) -> bool:
    """The schema-derived Compartment creation-completeness rule.

    ``docs/02_database_schema.md`` ("Table: compartment") defines no
    "Constraints" section. ``name`` is the only NOT NULL, non-identity
    column (``app/models/compartment.py``), so it is the only thing this
    module requires present before ``NEW`` may be considered. Not invented:
    this is the literal schema constraint, nothing more.
    """
    return bool(identity.name)


def _weak_candidates(
    identity: CompartmentIdentity, organism_id: UUID | None, lookup: CompartmentLookup
) -> tuple[CompartmentCandidate, ...]:
    """Candidate generation only: ``name``/``abbreviation``, organism-scoped.

    Never returns a match verdict on its own. Both fields' results are
    pooled before deduplication, so pointing at the same one Compartment
    from both fields still yields exactly one candidate, not inflated
    ambiguity.
    """
    pooled: list[CompartmentCandidate] = []
    if identity.name is not None:
        pooled.extend(lookup.by_name(organism_id, identity.name))
    if identity.abbreviation is not None:
        pooled.extend(lookup.by_abbreviation(organism_id, identity.abbreviation))
    return _organism_scoped_candidates(organism_id, pooled)


def _reconcile_ontology_candidates(
    organism_id: UUID | None,
    source: SourceType,
    source_identifier: str,
    candidates: tuple[CompartmentCandidate, ...],
) -> NormalizationResult | None:
    """Reconcile a deduplicated, global ``by_ontology_id`` result against ``organism_id``.

    Returns ``None`` for a genuinely empty candidate set, signaling the
    caller to fall through to the weak collision guard. See the module
    docstring's "Exact ontology reconciliation" section for the exact
    policy implemented here.
    """
    if not candidates:
        return None

    def _compatible(candidate_organism_id: UUID | None) -> bool:
        return candidate_organism_id == organism_id or candidate_organism_id is None

    if len(candidates) == 1:
        candidate = candidates[0]
        if _compatible(candidate.organism_id):
            reason = "resolved via ontology_id"
            if candidate.organism_id is None:
                reason += " (standard/reference compartment)"
            return NormalizationResult(
                status=NormalizationStatus.MATCHED,
                source=source,
                source_identifier=source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=organism_id,
                matched_entity_id=candidate.id,
                reason=reason,
            )
        return NormalizationResult(
            status=NormalizationStatus.CONFLICTED,
            source=source,
            source_identifier=source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=organism_id,
            candidate_entity_ids=(candidate.id,),
            reason=(
                "supplied ontology_id already belongs to an existing compartment in a "
                "different organism"
            ),
        )

    # 2+ candidates for the exact same ontology_id -- the schema does not
    # prevent this (no uniqueness is enforced anywhere on Compartment).
    # Never pick one.
    all_ids = tuple(sorted(candidate.id for candidate in candidates))
    if all(_compatible(candidate.organism_id) for candidate in candidates):
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=source,
            source_identifier=source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=organism_id,
            candidate_entity_ids=all_ids,
            reason="the supplied ontology_id matches more than one existing compartment",
        )
    return NormalizationResult(
        status=NormalizationStatus.CONFLICTED,
        source=source,
        source_identifier=source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        organism_id=organism_id,
        candidate_entity_ids=all_ids,
        reason=(
            "the supplied ontology_id is attached to existing compartment rows spanning "
            "more than one organism"
        ),
    )


def normalize_compartment(
    identity: CompartmentIdentity, *, organism_id: UUID | None, lookup: CompartmentLookup
) -> NormalizationResult:
    """Resolve one source-supplied compartment identity against existing ``Compartment`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is required (keyword-only, no default) but may itself
    be ``None`` -- an explicit request to normalize against
    standard/reference compartment scope (see module docstring). Every
    returned ``NormalizationResult.organism_id`` equals the supplied
    ``organism_id`` regardless of status, following the convention
    established for Gene/Protein normalization.
    """
    if identity.ontology_id is not None:
        candidates = unique_by_id(lookup.by_ontology_id(identity.ontology_id))
        result = _reconcile_ontology_candidates(
            organism_id, identity.source, identity.source_identifier, candidates
        )
        if result is not None:
            return result
        # else: zero exact candidates anywhere -- fall through to the weak path.

    weak_candidates = _weak_candidates(identity, organism_id, lookup)
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
                "no exact ontology_id matched, but an existing compartment in this scope "
                "shares the supplied name/abbreviation -- not enough to independently "
                "establish a match"
            ),
        )

    if identity.ontology_id is not None and _has_creation_complete_metadata(identity):
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            organism_id=organism_id,
            reason="no existing compartment matched the supplied ontology_id in this scope",
        )

    if identity.ontology_id is not None:
        reason = (
            "no existing compartment matched the supplied ontology_id, and no name was "
            "supplied to safely create a new one"
        )
    else:
        reason = (
            "only a weak name/abbreviation signal was supplied, with no matching existing "
            "compartment in this scope -- insufficient to create or match a compartment"
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


__all__ = [
    "CompartmentCandidate",
    "CompartmentIdentity",
    "CompartmentLookup",
    "normalize_compartment",
]
