"""Protein identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``ProteinLookup`` protocol,
which a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary
``app.normalization.organism``/``app.normalization.gene`` established.

**Protein is organism-scoped**, like ``Gene``. ``normalize_protein`` takes an
explicit, required ``organism_id`` keyword argument with no default -- there
is no global/current-organism state anywhere in this module. Every returned
``NormalizationResult.organism_id`` equals the supplied ``organism_id``
regardless of status, following the same convention established for Gene
normalization (see ``app.normalization.gene``'s module docstring and
``app.normalization.types.NormalizationResult``'s own docstring: ``organism_id``
exists on that shared type specifically for "Increment 4 onward: Gene,
Protein, EnzymeComplex -- all organism-scoped in the schema" callers).

**Schema, verified directly from ``app/models/protein.py``, not assumed --
several details here diverge from what might be naively expected:**

* ``organism_id: UUID`` -- ``NOT NULL``, foreign key to ``organism.id``.
* ``uniprot_id: str | None`` -- indexed, but **explicitly not unique**. The
  model's own docstring is blunt about this: "``uniprot_id`` is indexed but
  not unique here: it is a distinct column from ``gene.uniprot_id``, and only
  the identifier columns on ``gene`` carry a uniqueness requirement in the
  specification." A UniProt accession is intended, biologically, to identify
  one protein record -- but this schema does not enforce that, so this
  module's global UniProt lookup can legitimately return zero, one, or more
  than one row, even for a single, genuinely correct accession, and never
  assumes otherwise (see "Exact UniProt reconciliation" below).
* ``name: str`` -- ``NOT NULL``. The **only** NOT NULL, non-identity,
  non-organism column -- ``docs/02_database_schema.md``'s "Table: protein"
  section has no "Constraints" subsection at all (unlike Gene's explicit
  "at least one of symbol/systematic_name/ncbi_gene_id/sgd_id" rule). This
  module's creation-completeness rule for ``NEW`` is accordingly the
  simplest possible schema-derived rule: ``name`` must be present (see
  "Creation completeness" below) -- nothing else is documented to require it.
* ``ec_number: str | None`` -- indexed, not unique, explicitly **not**
  Protein identity (see "EC number policy" below). Carried as inert metadata
  only.
* ``gene_id: UUID | None`` -- nullable FK to ``gene.id``. Explicitly **not**
  Protein identity (see "Gene<->Protein relationship policy" below). Carried
  as inert relationship-context metadata only.
* ``subunit_state``, ``localization_consensus``, ``notes``: metadata with no
  identity role at all -- deliberately excluded from ``ProteinIdentity``/
  ``ProteinCandidate`` entirely to keep both types minimal (the same
  treatment ``app.normalization.gene`` gives ``Gene.description``/``name``/
  ``chromosome``).
* **No alias/synonym column exists on ``Protein`` at all** -- unlike
  ``Gene.aliases_json``, there is no ``protein.aliases_json`` or equivalent.
  This is a schema finding, not an oversight: ``ProteinIdentity``/
  ``ProteinCandidate``/``ProteinLookup`` accordingly define only ``name`` as
  a Level 3 weak signal. Nothing here invents an alias mechanism the schema
  does not have.

**Identifier classification:**

* LEVEL 1 (authoritative Protein identifier): ``uniprot_id`` only. Despite
  the schema not enforcing global uniqueness (see above), it remains the
  sole intended Protein identifier this module treats as Level 1 -- absence
  of a DB constraint is a reason for defensive candidate-list discipline
  (below), not a reason to demote it.
* LEVEL 2: none apply distinctly to ``Protein`` under the current schema, or
  the Increment 4/5 normalization architecture -- ``app.models.
  source_cross_reference``'s generic polymorphic mechanism exists at the
  persistence layer, not as a field here.
* LEVEL 3 (candidate generation only, never independently ``MATCHED``):
  ``name``.
* NOT identity, ever: ``ec_number`` (see below), ``gene_id`` (see below),
  ``subunit_state``, ``localization_consensus``, ``notes``.

**Global vs. organism-scoped lookups.** ``uniprot_id`` is looked up
*globally* (``by_uniprot_id(uniprot_id)``, no ``organism_id`` parameter): a
UniProt accession is intended to identify a specific protein record
regardless of which organism the caller currently has in view, so an
organism-scoped query could hide an accession already claimed by a Protein
in a *different* organism and let this module misclassify it as ``NEW`` --
exactly the bug Gene normalization's own global-vs-scoped correction fixed
for ``sgd_id``/``ncbi_gene_id``/``kegg_gene_id``. ``name`` is looked up
*organism-scoped* (``by_name(organism_id, name)``) since it carries no
uniqueness constraint at all, global or per-organism, and searching it
globally would surface same-named proteins from unrelated organisms as
spurious candidates.

**Exact UniProt reconciliation.** Because ``Protein.uniprot_id`` is not
DB-unique, a global lookup can return 0, 1, or many rows for one accession.
This module reconciles that directly against the requested ``organism_id``
(see ``_reconcile_uniprot_candidates``):

* 0 rows: proceed to the same-organism weak (``name``) collision guard
  before ever considering ``NEW``.
* 1 row, same organism as requested: ``MATCHED``.
* 1 row, a *different* organism: ``CONFLICTED`` -- never silently dropped,
  never ``NEW`` (that would hide an already-claimed accession), and never
  treated as a match just because it is the only clean hit.
* 2+ rows, every one in the requested organism: ``AMBIGUOUS`` -- competing
  rows within scope, never picked from arbitrarily.
* 2+ rows spanning more than the requested organism (any row outside it,
  whether mixed with in-scope rows or not): ``CONFLICTED`` -- the exact same
  accession explicitly attached to Protein rows outside the requested
  organism is a stronger signal than ordinary ambiguity.

No isoform collapsing anywhere in this reconciliation: ``"P12345"`` and
``"P12345-2"`` are different literal strings and are never treated as
equivalent, canonicalized toward one another, or matched by shared prefix.

**EC number policy.** ``ec_number`` never participates in identity: no
``ProteinLookup.by_ec_number`` method exists, EC equality never produces
``MATCHED``, EC inequality never independently produces ``CONFLICTED``, and
EC presence never counts toward creation completeness. Many distinct
proteins share one EC-classified catalytic activity; EC number classifies an
activity, not a protein.

**Gene<->Protein relationship policy.** ``gene_id`` is carried as inert
relationship-context metadata only: never queried (no ``by_gene_id``
method), never part of any identity/conflict/completeness decision, and this
module never reads ``Gene`` at all (no import of ``app.models.gene`` or
``app.normalization.gene``, and specifically never consults
``Gene.uniprot_id`` -- see ``app.normalization.gene``'s own "Schema-policy
mismatch" section for why that field is off-limits to Gene normalization
too). **Open policy question, deliberately not decided here:** whether an
existing candidate's explicit, differing, non-``None`` ``gene_id`` should
ever count as a *relationship* conflict (as opposed to an *identity*
conflict, which it never is) once a Protein is already ``MATCHED`` via
``uniprot_id``. Neither ``docs/02_database_schema.md`` nor the model
docstring states that ``Protein.gene_id`` is a single-valued, authoritative,
never-revised relationship -- the schema only guarantees a Protein row has
at most one ``gene_id`` *value* at a time, which is just what a nullable FK
column is, not a policy statement about revision or conflict. Absent a
documented rule, this module does not invent one: a differing ``gene_id`` on
an otherwise-matched candidate is never treated as ``CONFLICTED`` here, and
a missing (``None``) candidate ``gene_id`` with a supplied incoming one is
simply potential future relationship metadata for persistence to consider,
not identity-relevant at all.

**No fuzzy matching, ever.** Exact string comparison only, at every level.
No case-folding, no synonym tables, no isoform-suffix stripping, no
accession-prefix stripping -- none has an existing, documented, deterministic
rule anywhere in this repository, and inventing one here would violate
``.cursor/rules/01-scientific-integrity.mdc``.

**Connector adapters deliberately omitted.** No ``protein_identity_from_sgd``
or ``protein_identity_from_brenda`` helper exists in this module. SGD's
locus/gene record (``app.connectors.sgd.SgdNormalizedRecord``) exposes a
UniProt cross-reference, but it is a *gene-side* record -- converting it
directly into a ``ProteinIdentity`` would assert a specific Gene<->Protein
relationship (one UniProt accession per gene) that Increment 4's own
correction explicitly rejected as a biological oversimplification (a gene
may correspond to more than one protein product). That SGD UniProt value
remains available on the SGD record itself for a later, dedicated
relationship-resolution/persistence increment to use deliberately, not
folded silently into Protein identity here. BRENDA
(``app.connectors.brenda``) exposes no true Protein identifier at all --
only EC numbers, organism name strings, and free-text commentary, none of
which this module treats as Protein identity (see "EC number policy" above,
and the module docstring generally: organism *name* strings are not even
organism identity here, let alone Protein identity). KEGG's connector
(``app.connectors.kegg``) exposes no gene/protein-level record type at all
(only ``KeggCompoundRecord``/``KeggReactionRecord``). Protein normalization
therefore remains fully source-neutral until a UniProt-aware or genuinely
protein-scoped connector exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import SourceType
from app.normalization.identifiers import require_non_empty, unique_by_id
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

_ENTITY_TYPE = "protein"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no isoform-suffix stripping, no
    accession-prefix stripping (see module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class ProteinIdentity:
    """Source-neutral description of one incoming protein identity claim.

    Not coupled to any one connector's record class. Deliberately carries no
    ``organism_id``: organism scope is supplied separately, as a required
    keyword argument to ``normalize_protein`` (see module docstring).
    Deliberately carries no alias/synonym field: ``Protein`` has no such
    column (see module docstring). ``gene_id``/``ec_number`` are relationship/
    annotation metadata only -- never Protein identity (see module
    docstring); supplying only one of these, with no ``uniprot_id`` and no
    ``name``, is insufficient and rejected at construction.

    Requires at least one identity signal -- ``uniprot_id`` (Level 1) or
    ``name`` (Level 3, candidate generation only) -- otherwise there is
    nothing to normalize against. Carries no confidence score: no
    specification defines one for protein identity.
    """

    source: SourceType
    source_identifier: str

    uniprot_id: str | None = None
    name: str | None = None

    gene_id: UUID | None = None
    ec_number: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "uniprot_id", _clean(self.uniprot_id))
        object.__setattr__(self, "name", _clean(self.name))
        object.__setattr__(self, "ec_number", _clean(self.ec_number))

        if not any((self.uniprot_id, self.name)):
            raise ValueError(
                "ProteinIdentity requires at least one identity signal (uniprot_id or "
                "name) -- gene_id/ec_number alone are relationship/annotation metadata, "
                "not Protein identity"
            )


@dataclass(frozen=True, slots=True)
class ProteinCandidate:
    """A read-only snapshot of one existing ``Protein`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. ``organism_id`` is included specifically so a global
    ``uniprot_id`` match can be checked against the caller's requested
    organism. ``name`` is ``str`` (not ``str | None``), matching the
    schema's ``NOT NULL`` constraint. ``gene_id``/``ec_number`` are carried
    as inert metadata only -- see module docstring.
    """

    id: UUID
    organism_id: UUID

    uniprot_id: str | None
    name: str

    gene_id: UUID | None = None
    ec_number: str | None = None


@runtime_checkable
class ProteinLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_protein`` never touches SQLAlchemy.

    ``by_uniprot_id`` is **global** -- no ``organism_id`` parameter.
    ``by_name`` is **organism-scoped** -- ``organism_id`` first. No method
    here can insert, update, or delete a row. Deliberately has no
    ``by_ec_number`` or ``by_gene_id`` method at all (see module docstring):
    neither participates in Protein identity.

    ``by_name``'s implementation must filter by ``organism_id`` in the query
    itself (never fetch unscoped and filter afterward) -- ``normalize_protein``
    also independently verifies this (see ``_organism_scoped_candidates``),
    but that check exists as a defensive backstop, not as a substitute for a
    correctly scoped query.
    """

    def by_uniprot_id(self, uniprot_id: str) -> Sequence[ProteinCandidate]:
        """All proteins, in any organism, with this exact ``uniprot_id`` (0, 1, or more)."""
        ...

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ProteinCandidate]:
        """Proteins in ``organism_id`` with this exact ``name`` (0, 1, or more)."""
        ...


def _organism_scoped_candidates(
    organism_id: UUID, candidates: Sequence[ProteinCandidate]
) -> tuple[ProteinCandidate, ...]:
    """Deduplicate by id and assert every candidate belongs to ``organism_id``.

    Used only for ``by_name``: that method is contractually required to
    filter by ``organism_id`` itself, so a candidate from a different
    organism appearing here indicates a broken lookup implementation, not a
    legitimate outcome -- unlike the global ``by_uniprot_id`` lookup, where a
    different-organism match is an expected, valid result handled explicitly
    in ``_reconcile_uniprot_candidates``. Raising here is deliberately loud
    rather than defensive-filtering, so a broken lookup implementation is
    caught immediately rather than silently narrowing results.
    """
    unique = unique_by_id(candidates)
    for candidate in unique:
        if candidate.organism_id != organism_id:
            raise ValueError(
                f"ProteinLookup returned candidate {candidate.id} from organism "
                f"{candidate.organism_id}, but normalize_protein was called with "
                f"organism_id={organism_id} -- by_name must scope its query to the "
                "requested organism"
            )
    return unique


def _has_creation_complete_metadata(identity: ProteinIdentity) -> bool:
    """The schema-derived Protein creation-completeness rule.

    ``docs/02_database_schema.md`` ("Table: protein") defines no
    "Constraints" section (unlike Gene's explicit "at least one of" rule).
    ``name`` is the only NOT NULL, non-identity, non-organism column
    (``app/models/protein.py``), so it is the only thing this module
    requires present before ``NEW`` may be considered. Not invented: this is
    the literal schema constraint, nothing more.
    """
    return bool(identity.name)


def _weak_candidates(
    identity: ProteinIdentity, organism_id: UUID, lookup: ProteinLookup
) -> tuple[ProteinCandidate, ...]:
    """Level 3 candidate generation: ``name``, organism-scoped.

    Candidate generation only -- never returns a match verdict on its own.
    """
    if identity.name is None:
        return ()
    return _organism_scoped_candidates(organism_id, lookup.by_name(organism_id, identity.name))


def _reconcile_uniprot_candidates(
    organism_id: UUID,
    source: SourceType,
    source_identifier: str,
    candidates: tuple[ProteinCandidate, ...],
) -> NormalizationResult | None:
    """Reconcile a deduplicated, global ``by_uniprot_id`` result against ``organism_id``.

    Returns ``None`` for a genuinely empty candidate set, signaling the
    caller to fall through to the same-organism weak collision guard.
    See the module docstring's "Exact UniProt reconciliation" section for
    the exact policy implemented here.
    """
    if not candidates:
        return None

    if len(candidates) == 1:
        candidate = candidates[0]
        if candidate.organism_id == organism_id:
            return NormalizationResult(
                status=NormalizationStatus.MATCHED,
                source=source,
                source_identifier=source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=organism_id,
                matched_entity_id=candidate.id,
                reason="resolved via uniprot_id",
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
                "supplied uniprot_id already belongs to an existing protein in a different organism"
            ),
        )

    # 2+ candidates for the exact same accession -- the schema does not
    # prevent this (Protein.uniprot_id is not DB-unique). Never pick one.
    organism_ids = {candidate.organism_id for candidate in candidates}
    all_ids = tuple(sorted(candidate.id for candidate in candidates))
    if organism_ids == {organism_id}:
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=source,
            source_identifier=source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=organism_id,
            candidate_entity_ids=all_ids,
            reason=(
                "the supplied uniprot_id matches more than one existing protein in this organism"
            ),
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
            "the supplied uniprot_id is attached to existing protein rows spanning "
            "more than one organism"
        ),
    )


def normalize_protein(
    identity: ProteinIdentity, *, organism_id: UUID, lookup: ProteinLookup
) -> NormalizationResult:
    """Resolve one source-supplied protein identity against existing ``Protein`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes. Never
    queries ``Gene`` (no ``by_gene_id`` method exists, and ``Gene.uniprot_id``
    is never consulted). ``organism_id`` is required and has no default.
    Every returned ``NormalizationResult.organism_id`` equals the supplied
    ``organism_id`` regardless of status (see module docstring).
    """
    if identity.uniprot_id is not None:
        candidates = unique_by_id(lookup.by_uniprot_id(identity.uniprot_id))
        result = _reconcile_uniprot_candidates(
            organism_id, identity.source, identity.source_identifier, candidates
        )
        if result is not None:
            return result
        # else: zero exact candidates anywhere -- fall through to Level 3.

    # Level 3: no exact uniprot_id match established a verdict above (either
    # none was supplied, or it matched nothing globally). A same-organism
    # name collision must still block NEW here -- the same collision-guard
    # principle app.normalization.organism/app.normalization.gene use.
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
                "no exact uniprot_id matched, but an existing protein in this organism "
                "shares the supplied name -- not enough to independently establish a match"
            ),
        )

    if identity.uniprot_id is not None and _has_creation_complete_metadata(identity):
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            organism_id=organism_id,
            reason="no existing protein matched the supplied uniprot_id in this organism",
        )

    if identity.uniprot_id is not None:
        reason = (
            "no existing protein matched the supplied uniprot_id, and no name was "
            "supplied to safely create a new one"
        )
    else:
        reason = (
            "only a weak name signal was supplied, with no matching existing protein "
            "in this organism -- insufficient to create or match a protein"
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
    "ProteinCandidate",
    "ProteinIdentity",
    "ProteinLookup",
    "normalize_protein",
]
