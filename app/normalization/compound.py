"""Compound identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``CompoundLookup`` protocol,
which a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary
``app.normalization.organism``/``app.normalization.publication``/
``app.normalization.gene``/``app.normalization.protein`` established.

**Compound is organism-agnostic**, like ``Publication``: ``app/models/
compound.py`` has no ``organism_id`` column at all. ``normalize_compound``
therefore takes no organism parameter, and every ``NormalizationResult`` it
returns has ``organism_id=None`` (see ``app.normalization.types.
NormalizationResult``'s own docstring: ``organism_id`` is ``None`` "for
genuinely organism-agnostic entities (``Compound``, ``Publication`` have no
``organism_id`` column at all)").

**Schema, verified directly from ``app/models/compound.py``, not assumed --
the single most consequential finding in this increment:**

* **No Compound identifier column carries any database-level uniqueness
  constraint at all** -- not ``chebi_id``, not ``kegg_compound_id``, not
  ``pubchem_cid``, not ``metacyc_id``, not ``inchikey``. The model's own
  docstring states this explicitly: "No uniqueness constraint is placed on
  ``chebi_id``/``kegg_compound_id``/``pubchem_cid``/``metacyc_id``/
  ``inchikey``: unlike the analogous identifier fields on ``gene`` and
  ``publication``, the specification does not require one here." This is a
  deliberate schema choice, not an oversight this module should paper over
  -- every strong-identifier lookup here must always be treated with full
  candidate-list discipline (``AMBIGUOUS`` is a live, expected outcome for
  *every* Level 1 field, not a defensive edge case as it was for Gene's
  globally-unique columns).
* ``canonical_name: str`` -- ``NOT NULL``, indexed, but **not unique**.
  ``docs/02_database_schema.md``'s "Table: compound" section has no
  "Constraints" subsection at all (same situation as ``Protein.name``) --
  ``canonical_name`` is simply the only NOT NULL, non-identity column, and
  is accordingly this module's entire creation-completeness rule for ``NEW``
  (see "Creation completeness" below).
* ``formula: str | None``, ``charge: int | None``, ``molecular_weight:
  Decimal | None``, ``inchi: Text | None``, ``smiles: Text | None``,
  ``notes: Text | None``: metadata, not identity anchors (see "Identifier
  hierarchy" below). ``molecular_weight``, ``smiles``, and ``notes`` are
  excluded from ``CompoundIdentity``/``CompoundCandidate`` entirely -- they
  play no role anywhere in this module's decisions, matching the same
  "keep the type minimal" treatment ``app.normalization.gene`` gives
  ``Gene.description``/``name``/``chromosome`` and ``app.normalization.
  protein`` gives ``Protein.subunit_state``/``localization_consensus``/
  ``notes``.
* ``is_generic: bool`` -- ``NOT NULL DEFAULT FALSE`` on the persisted row,
  but represented as ``bool | None`` on ``CompoundIdentity`` (an *incoming*
  claim, where "the source did not say" must remain distinguishable from
  "the source said False" -- ``docs/01_overview.md``'s "Unknown information
  should remain unknown" applies here as much as anywhere else). Carried as
  metadata only in this increment -- see "Generic-compound policy" below.
* ``compound_synonym``: a separate table, ``UniqueConstraint(compound_id,
  synonym)`` -- unique *per compound*, not globally. The same synonym string
  can legitimately be attached to more than one compound (common/ambiguous
  names), which is exactly why synonym lookup is Level 3 candidate
  generation only, never identity.

**Identifier hierarchy, as implemented:**

* LEVEL 1 (chemical identity anchors, queried and reconciled symmetrically
  -- no field is treated as more authoritative than another; the schema
  itself draws no such distinction, since none of them carry a uniqueness
  constraint): ``chebi_id``, ``kegg_compound_id``, ``pubchem_cid``,
  ``metacyc_id``, ``inchikey``. ``inchikey`` (the fixed-length hash intended
  for exact-match lookup) is Level 1; ``inchi`` (the full, verbose
  structure string) is Level 2, matching ordinary cheminformatics practice
  of indexing on InChIKey rather than raw InChI.
* LEVEL 2 (corroborating structural/chemical metadata -- may appear in a
  ``reason`` string, but never independently establishes or blocks a
  match): ``inchi``, ``formula``, ``charge``, ``is_generic``. **Open policy
  question, deliberately not decided here** (see "Chemical contradiction
  policy" below): whether a Level 1 match with disagreeing Level 2 metadata
  should ever become ``CONFLICTED`` rather than ``MATCHED``. No existing
  specification in this repository answers this, so this module does not
  invent an answer -- Level 2 fields are inert for status purposes.
* LEVEL 3 (candidate generation only, never independently ``MATCHED``):
  ``canonical_name``, ``synonyms`` (via the separate ``compound_synonym``
  table).
* NOT identity, ever, and not represented on ``CompoundIdentity``/
  ``CompoundCandidate`` at all: ``molecular_weight``, ``smiles``, ``notes``.

**Chemical contradiction policy.** Once a single candidate is established
via Level 1 reconciliation, this module compares only the *other* supplied
Level 1 fields against that candidate's own values (exact literal string
comparison; a candidate field that is ``None`` is compatible, not a
disagreement -- same convention as ``app.normalization.gene``/
``app.normalization.publication``). A genuine disagreement there --
different, both non-``None`` values for the same Level 1 field -- is a hard
identity contradiction and produces ``CONFLICTED``. Level 2 fields
(``inchi``/``formula``/``charge``/``is_generic``) are never compared for
conflict purposes in this increment; see the open policy questions recorded
in the completion report for this increment.

**Protonation, charge, and stereochemistry safety.** No proton
stripping/addition, no charge neutralization, no formula normalization, no
stereochemical-marker stripping, no D/L or cis/trans normalization, no
mapping between a canonical accession and a differently-protonated or
differently-configured one -- anywhere. Every string comparison in this
module is exact and literal. If two chemically distinct forms are
represented as separate rows with separate Level 1 identifiers (as ChEBI,
for instance, routinely does for distinct protonation states), they remain
distinct here; nothing in this module ever infers that two different
identifiers "really" mean the same compound.

**Generic-compound policy.** ``is_generic`` is carried as metadata on both
``CompoundIdentity`` and ``CompoundCandidate`` but plays no role in any
lookup, match, conflict, or creation-completeness decision in this
increment. Whether a generic-vs-specific mismatch on an otherwise Level 1
match should block ``MATCHED``, or whether generic candidates should be
excluded from Level 3 weak-candidate collision guarding, is an open policy
question this module does not invent an answer for -- no existing
specification (``docs/02_database_schema.md``, ``docs/03_agent_behavior.md``)
addresses it, and no other code in this repository reads ``is_generic`` for
identity purposes.

**No fuzzy matching, ever.** Exact string comparison only, at every level.
No case-folding, no punctuation-insensitive matching, no substring matching,
no synonym tables beyond the schema's own ``compound_synonym`` -- none of
these has an existing, documented, deterministic rule anywhere in this
repository, and inventing one here would violate
``.cursor/rules/01-scientific-integrity.mdc``.

**Connector adapters.** ``compound_identity_from_kegg`` is implemented: KEGG
is the only connector in this repository that currently exposes a
compound-level record at a compatible abstraction level
(``app.connectors.kegg.KeggCompoundRecord``). No ChEBI, PubChem, or MetaCyc
connector exists yet, so no adapter for any of them is implemented here --
fabricating one against an API this repository does not yet call would be
speculative, not justified by existing code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import SourceType
from app.normalization.identifiers import (
    CandidateSetState,
    classify_candidates,
    require_non_empty,
    unique_by_id,
)
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

if TYPE_CHECKING:
    from app.connectors.kegg import KeggCompoundRecord

_ENTITY_TYPE = "compound"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no stereochemical-marker
    stripping, no protonation/charge rewriting, no chemical canonicalization
    (see module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_synonyms(synonyms: tuple[str, ...]) -> tuple[str, ...]:
    """Trim each synonym, drop blanks, and collapse exact (not fuzzy) duplicates.

    Order-preserving. Collapsing literal repeats is query-efficiency hygiene
    only -- it never changes which candidates a distinct synonym string can
    surface.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for synonym in synonyms:
        stripped = synonym.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            cleaned.append(stripped)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class CompoundIdentity:
    """Source-neutral description of one incoming compound identity claim.

    Not coupled to any one connector's record class. Deliberately excludes
    ``molecular_weight``/``smiles``/``notes`` -- see module docstring.
    ``is_generic`` is ``bool | None``: ``None`` means the source made no
    claim either way, distinct from an explicit ``False``.

    Requires at least one identity signal: a Level 1 identifier
    (``chebi_id``/``kegg_compound_id``/``pubchem_cid``/``metacyc_id``/
    ``inchikey``), or a Level 3 candidate-generation field
    (``canonical_name``/a nonblank synonym). ``inchi``/``formula``/``charge``/
    ``is_generic`` alone are corroborating metadata only and are never, by
    themselves, sufficient to construct an identity -- consistent with
    ``app.normalization.gene``/``app.normalization.protein`` treating their
    own non-identity metadata fields (``gene_id``, ``ec_number``, ...) the
    same way. Carries no confidence score: no specification defines one for
    compound identity.
    """

    source: SourceType
    source_identifier: str

    chebi_id: str | None = None
    kegg_compound_id: str | None = None
    pubchem_cid: str | None = None
    metacyc_id: str | None = None
    inchikey: str | None = None

    inchi: str | None = None
    formula: str | None = None
    charge: int | None = None
    is_generic: bool | None = None

    canonical_name: str | None = None
    synonyms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "chebi_id", _clean(self.chebi_id))
        object.__setattr__(self, "kegg_compound_id", _clean(self.kegg_compound_id))
        object.__setattr__(self, "pubchem_cid", _clean(self.pubchem_cid))
        object.__setattr__(self, "metacyc_id", _clean(self.metacyc_id))
        object.__setattr__(self, "inchikey", _clean(self.inchikey))
        object.__setattr__(self, "inchi", _clean(self.inchi))
        object.__setattr__(self, "formula", _clean(self.formula))
        object.__setattr__(self, "canonical_name", _clean(self.canonical_name))
        object.__setattr__(self, "synonyms", _clean_synonyms(self.synonyms))

        if not any(
            (
                self.chebi_id,
                self.kegg_compound_id,
                self.pubchem_cid,
                self.metacyc_id,
                self.inchikey,
                self.canonical_name,
                self.synonyms,
            )
        ):
            raise ValueError(
                "CompoundIdentity requires at least one identity signal (chebi_id, "
                "kegg_compound_id, pubchem_cid, metacyc_id, inchikey, canonical_name, "
                "or a nonblank synonym) -- inchi/formula/charge/is_generic alone are "
                "corroborating metadata, not Compound identity"
            )


@dataclass(frozen=True, slots=True)
class CompoundCandidate:
    """A read-only snapshot of one existing ``Compound`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. ``canonical_name`` is ``str`` (not ``str | None``), matching
    the schema's ``NOT NULL`` constraint; ``is_generic`` is ``bool`` (not
    ``bool | None``), matching the schema's ``NOT NULL DEFAULT FALSE``.

    Deliberately has no ``synonyms`` field: unlike ``Gene.aliases_json`` (a
    JSONB column directly on ``Gene``), ``compound_synonym`` is a separate
    table reached only through ``CompoundLookup.by_synonym`` -- a synonym
    match already resolves to a specific candidate via that lookup, so there
    is nothing to carry back for comparison here.
    """

    id: UUID
    canonical_name: str

    chebi_id: str | None = None
    kegg_compound_id: str | None = None
    pubchem_cid: str | None = None
    metacyc_id: str | None = None
    inchikey: str | None = None

    inchi: str | None = None
    formula: str | None = None
    charge: int | None = None
    is_generic: bool = False


@runtime_checkable
class CompoundLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_compound`` never touches SQLAlchemy.

    No organism parameter anywhere: ``Compound`` has no organism scope (see
    module docstring). Every method is a simple exact-value lookup. No
    method here can insert, update, or delete a row. Deliberately has no
    ``by_formula``/``by_charge``/``by_molecular_weight``/``by_ec_number``
    method: none of those is Compound identity (see module docstring).
    """

    def by_chebi_id(self, chebi_id: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``chebi_id`` (0, 1, or more)."""
        ...

    def by_kegg_compound_id(self, kegg_compound_id: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``kegg_compound_id`` (0, 1, or more)."""
        ...

    def by_pubchem_cid(self, pubchem_cid: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``pubchem_cid`` (0, 1, or more)."""
        ...

    def by_metacyc_id(self, metacyc_id: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``metacyc_id`` (0, 1, or more)."""
        ...

    def by_inchikey(self, inchikey: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``inchikey`` (0, 1, or more)."""
        ...

    def by_canonical_name(self, canonical_name: str) -> Sequence[CompoundCandidate]:
        """All compounds with this exact ``canonical_name`` (0, 1, or more)."""
        ...

    def by_synonym(self, synonym: str) -> Sequence[CompoundCandidate]:
        """All compounds carrying this exact synonym, via ``compound_synonym`` (0, 1, or more)."""
        ...


def _describe_identifier_disagreement(
    identity: CompoundIdentity, candidate: CompoundCandidate
) -> str | None:
    """Compare supplied Level 1 identifiers against a resolved candidate's own.

    Exact comparison only. A candidate field that is ``None`` is compatible
    (missing metadata the incoming record could later fill in, not a
    disagreement) -- same convention as ``app.normalization.gene``/
    ``app.normalization.publication``.

    Checked: ``chebi_id``, ``kegg_compound_id``, ``pubchem_cid``,
    ``metacyc_id``, ``inchikey`` -- the Level 1 set. Deliberately **not**
    checked: ``inchi``, ``formula``, ``charge``, ``is_generic`` (Level 2,
    inert for conflict purposes in this increment -- see module docstring's
    "Chemical contradiction policy"), ``canonical_name``/synonyms (Level 3,
    never conflict-relevant).
    """
    checks: tuple[tuple[str, str | None, str | None], ...] = (
        ("chebi_id", identity.chebi_id, candidate.chebi_id),
        ("kegg_compound_id", identity.kegg_compound_id, candidate.kegg_compound_id),
        ("pubchem_cid", identity.pubchem_cid, candidate.pubchem_cid),
        ("metacyc_id", identity.metacyc_id, candidate.metacyc_id),
        ("inchikey", identity.inchikey, candidate.inchikey),
    )
    for field_name, supplied, existing in checks:
        if supplied is not None and existing is not None and supplied != existing:
            return (
                f"supplied {field_name} {supplied!r} disagrees with existing compound's "
                f"{field_name} {existing!r}"
            )
    return None


def _has_creation_complete_metadata(identity: CompoundIdentity) -> bool:
    """The schema-derived Compound creation-completeness rule.

    ``docs/02_database_schema.md`` ("Table: compound") defines no
    "Constraints" section. ``canonical_name`` is the only NOT NULL,
    non-identity column (``app/models/compound.py``), so it is the only
    thing this module requires present before ``NEW`` may be considered.
    Not invented: this is the literal schema constraint, nothing more. No
    canonical name is ever synthesized from an external identifier.
    """
    return bool(identity.canonical_name)


def _weak_candidates(
    identity: CompoundIdentity, lookup: CompoundLookup
) -> tuple[CompoundCandidate, ...]:
    """Level 3 candidate generation: ``canonical_name``/synonyms.

    Candidate generation only -- never returns a match verdict on its own.
    Every weak field's results are pooled before deduplication, so pointing
    at the same one Compound from several weak fields still yields exactly
    one candidate, not inflated ambiguity.
    """
    pooled: list[CompoundCandidate] = []
    if identity.canonical_name is not None:
        pooled.extend(lookup.by_canonical_name(identity.canonical_name))
    for synonym in identity.synonyms:
        pooled.extend(lookup.by_synonym(synonym))
    return unique_by_id(pooled)


def normalize_compound(
    identity: CompoundIdentity, *, lookup: CompoundLookup
) -> NormalizationResult:
    """Resolve one source-supplied compound identity against existing ``Compound`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is always ``None`` on the returned result -- ``Compound``
    is organism-agnostic (see module docstring).
    """
    anchor_results: list[tuple[str, tuple[CompoundCandidate, ...]]] = []
    if identity.chebi_id is not None:
        anchor_results.append(("chebi_id", unique_by_id(lookup.by_chebi_id(identity.chebi_id))))
    if identity.kegg_compound_id is not None:
        anchor_results.append(
            (
                "kegg_compound_id",
                unique_by_id(lookup.by_kegg_compound_id(identity.kegg_compound_id)),
            )
        )
    if identity.pubchem_cid is not None:
        anchor_results.append(
            ("pubchem_cid", unique_by_id(lookup.by_pubchem_cid(identity.pubchem_cid)))
        )
    if identity.metacyc_id is not None:
        anchor_results.append(
            ("metacyc_id", unique_by_id(lookup.by_metacyc_id(identity.metacyc_id)))
        )
    if identity.inchikey is not None:
        anchor_results.append(("inchikey", unique_by_id(lookup.by_inchikey(identity.inchikey))))

    if anchor_results:
        by_id: dict[UUID, CompoundCandidate] = {}
        single_match_ids: set[UUID] = set()
        ambiguous_ids: set[UUID] = set()
        agreeing_anchors: list[str] = []

        for anchor_name, candidates in anchor_results:
            for candidate in candidates:
                by_id[candidate.id] = candidate
            state = classify_candidates(tuple(candidate.id for candidate in candidates))
            if state is CandidateSetState.SINGLE_MATCH:
                single_match_ids.add(candidates[0].id)
                agreeing_anchors.append(anchor_name)
            elif state is CandidateSetState.AMBIGUOUS:
                ambiguous_ids.update(candidate.id for candidate in candidates)
            # NO_MATCH contributes nothing.

        if single_match_ids or ambiguous_ids:
            if len(single_match_ids) == 1 and not ambiguous_ids:
                candidate_id = next(iter(single_match_ids))
                disagreement = _describe_identifier_disagreement(identity, by_id[candidate_id])
                if disagreement is None:
                    return NormalizationResult(
                        status=NormalizationStatus.MATCHED,
                        source=identity.source,
                        source_identifier=identity.source_identifier,
                        entity_type=_ENTITY_TYPE,
                        match_method=MatchMethod.EXACT_IDENTIFIER,
                        matched_entity_id=candidate_id,
                        reason=f"resolved via {', '.join(agreeing_anchors)}",
                    )
                return NormalizationResult(
                    status=NormalizationStatus.CONFLICTED,
                    source=identity.source,
                    source_identifier=identity.source_identifier,
                    entity_type=_ENTITY_TYPE,
                    match_method=MatchMethod.EXACT_IDENTIFIER,
                    matched_entity_id=candidate_id,
                    reason=disagreement,
                )

            # More than one distinct existing entity is potentially implicated
            # -- either two anchors each cleanly resolved a *different*
            # entity (a real conflict), or an anchor was itself ambiguous
            # with no other anchor providing a competing clean single match
            # (genuine ambiguity, not yet a conflict between two clear
            # answers). Either way, never pick one.
            all_ids = tuple(sorted(single_match_ids | ambiguous_ids))
            if len(single_match_ids) >= 2:
                return NormalizationResult(
                    status=NormalizationStatus.CONFLICTED,
                    source=identity.source,
                    source_identifier=identity.source_identifier,
                    entity_type=_ENTITY_TYPE,
                    match_method=MatchMethod.EXACT_IDENTIFIER,
                    candidate_entity_ids=all_ids,
                    reason=(
                        "different supplied identifiers resolved to different existing compounds"
                    ),
                )
            return NormalizationResult(
                status=NormalizationStatus.AMBIGUOUS,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                candidate_entity_ids=all_ids,
                reason="a supplied identifier matches more than one existing compound",
            )
        # else: every strong anchor was NO_MATCH -- fall through to Level 3.

    weak_candidates = _weak_candidates(identity, lookup)
    if weak_candidates:
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.CANDIDATE_SYNONYM,
            candidate_entity_ids=tuple(sorted(candidate.id for candidate in weak_candidates)),
            reason=(
                "no exact identifier matched, but an existing compound shares the "
                "supplied canonical_name/synonym -- not enough to independently "
                "establish a match"
            ),
        )

    if anchor_results and _has_creation_complete_metadata(identity):
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            reason="no existing compound matched the supplied identifier(s)",
        )

    if anchor_results:
        reason = (
            "no existing compound matched the supplied identifier(s), and no "
            "canonical_name was supplied to safely create a new one"
        )
    else:
        reason = (
            "only a weak canonical_name/synonym signal was supplied, with no matching "
            "existing compound -- insufficient to create or match a compound"
        )
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.NONE,
        reason=reason,
    )


def compound_identity_from_kegg(record: KeggCompoundRecord) -> CompoundIdentity:
    """Pure adapter: a KEGG connector's normalized compound record -> a source-neutral identity.

    No I/O, no network, no inference -- exact copying of identifiers/metadata
    already present on ``record``. ``record`` itself is never mutated -- it
    is a frozen dataclass, and nothing here does anything but read its
    fields.

    * ``source_identifier``/``kegg_compound_id`` are ``record.entry_id``
      (e.g. ``"C00031"``), always present.
    * ``canonical_name`` is ``record.names[0]`` when present -- KEGG's
      ``NAME`` field is a semicolon-separated list in source order, and the
      first entry is its own primary/preferred name (the same convention
      ``app.normalization.gene.gene_identity_from_sgd`` follows for SGD's
      ``standard_name``). The remaining names become ``synonyms``. Neither
      is invented if ``record.names`` is empty.
    * ``formula`` is copied directly (Level 2 metadata only -- see module
      docstring). ``record.exact_mass``/``record.mol_weight`` are not
      copied: molecular weight is excluded from ``CompoundIdentity``
      entirely.
    """
    names = record.names
    return CompoundIdentity(
        source=SourceType.KEGG,
        source_identifier=record.entry_id,
        kegg_compound_id=record.entry_id,
        canonical_name=names[0] if names else None,
        synonyms=names[1:] if len(names) > 1 else (),
        formula=record.formula,
    )


__all__ = [
    "CompoundCandidate",
    "CompoundIdentity",
    "CompoundLookup",
    "compound_identity_from_kegg",
    "normalize_compound",
]
