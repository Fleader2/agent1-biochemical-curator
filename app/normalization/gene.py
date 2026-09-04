"""Gene identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``GeneLookup`` protocol, which
a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary
``app.normalization.organism``/``app.normalization.publication`` established.

**Gene is always organism-scoped.** Unlike ``Publication`` (organism-agnostic,
``organism_id`` always ``None``), ``Gene.organism_id`` is ``NOT NULL``
(``app/models/gene.py``). ``normalize_gene`` therefore takes an explicit,
required ``organism_id`` keyword argument with no default -- there is no
global/current-organism state anywhere in this module. The organism context
is already resolved by the time Gene normalization runs (a prior
``app.normalization.organism.normalize_organism`` call, in practice) -- it is
input, not something this module discovers -- which is why, unlike
``Organism`` normalization's own ``organism_id`` (only populated once an
``Organism`` row is actually ``MATCHED``), every ``NormalizationResult`` this
module returns carries ``organism_id=<the supplied organism_id>`` regardless
of status. See ``app.normalization.types.NormalizationResult``'s docstring:
``organism_id`` exists on that shared type specifically for "Increment 4
onward: Gene, Protein, EnzymeComplex -- all organism-scoped in the schema"
callers like this one.

**Global vs. organism-scoped lookups -- read this before touching
``GeneLookup``.** ``sgd_id``/``ncbi_gene_id``/``kegg_gene_id`` each carry a
**global** (table-wide, not per-organism) unique-when-present partial index
(``app/models/gene.py``), so they are looked up *globally*
(``by_sgd_id(sgd_id)``, no ``organism_id`` parameter) -- an organism-scoped
query for one of these would be actively wrong: it could silently return zero
rows for an identifier that is already claimed by a Gene in a *different*
organism, and this module would then misclassify that as ``NEW``, creating
what would become a duplicate global-identifier claim. A global match
belonging to a different organism than requested is therefore always resolved
as ``CONFLICTED``, never silently ignored and never ``NEW`` (see
"Cross-organism conflict" below). ``systematic_name``/``symbol``/``alias``
carry **no** uniqueness constraint at all (global or organism-scoped) and are
looked up *organism-scoped* (``by_symbol(organism_id, symbol)``) -- searching
them globally would surface same-named genes from unrelated organisms as
spurious candidates, which is exactly the kind of cross-organism collision
Gene normalization must never manufacture on its own.

**Schema, verified directly from ``app/models/gene.py``, not assumed:**

* ``organism_id: UUID`` -- ``NOT NULL``, foreign key to ``organism.id``.
* ``sgd_id``, ``ncbi_gene_id``, ``kegg_gene_id``: ``str | None`` -- each has
  its own **global** partial unique index (``WHERE <col> IS NOT NULL``).
  Treated as Level 1 identity anchors here.
* ``symbol``, ``systematic_name``: ``str | None``, each indexed but **not**
  unique -- explicitly not globally unique per the model docstring ("No
  uniqueness is placed on ``symbol`` or ``systematic_name``").
  ``docs/02_database_schema.md`` additionally documents an *application*
  (not DB) constraint: "At least one of the following should normally be
  present: symbol, systematic_name, ncbi_gene_id, sgd_id" -- used below as
  this module's exact, non-invented creation-completeness rule for ``NEW``.
* ``uniprot_id``: ``str | None``, also globally unique-when-present -- but
  see "Schema-policy mismatch" below: this module deliberately does **not**
  use it for Gene identity.
* ``name``, ``description``, ``chromosome``: ``str | None`` metadata, never
  used for identity, conflict, or creation-completeness decisions here --
  deliberately excluded from ``GeneIdentity``/``GeneCandidate`` to keep both
  types minimal.
* ``aliases_json``: ``JSONB``, a list on the model. This module's
  ``GeneIdentity.aliases``/``GeneCandidate.aliases`` represent it as
  ``tuple[str, ...]`` -- the exact JSON shape/plural cardinality mapping is a
  persistence-layer concern, out of scope here.

**Schema-policy mismatch, deliberately not resolved here.** ``Gene.uniprot_id``
exists in the current schema, with its own global unique-when-present index --
structurally identical to ``sgd_id``/``ncbi_gene_id``/``kegg_gene_id``. Phase 4
Gene normalization intentionally does **not** use it as Gene identity anyway:
the underlying biological relationship (one gene to potentially many
UniProt-identified protein products -- isoforms, alternative splicing,
post-translational processing) is one-to-many, not one-to-one, and treating a
single UniProt accession as though it uniquely identified a Gene would
misrepresent that relationship. UniProt-based resolution belongs to a later
Protein-normalization increment, which will associate ``Protein`` rows (and,
through them, ``Gene`` rows) with UniProt accessions explicitly, and via
source cross-references -- not by asserting one on a Gene normalization
decision here. This is a normalization-*policy* choice, not a schema change:
``app/models/gene.py``'s ``uniprot_id`` column is untouched, and nothing here
modifies it. SGD's UniProt cross-reference remains available on the SGD
source record itself (``SgdNormalizedRecord.uniprot_id``) for that later
increment -- ``gene_identity_from_sgd`` below does not copy it into
``GeneIdentity``.

**Identifier classification (Increment 4 instructions, section C):**

* LEVEL 1 (authoritative exact Gene identifiers, each globally unique when
  present): ``sgd_id``, ``ncbi_gene_id``, ``kegg_gene_id``.
* LEVEL 2 (explicit source-supplied cross-references distinct from Level 1):
  none apply distinctly to ``Gene`` under the current schema.
* LEVEL 3 (candidate generation only, never independently ``MATCHED``):
  ``systematic_name``, ``symbol``, ``aliases``.
* LEVEL 4 (out of scope for identity entirely): ``description``, ``name``,
  ``chromosome``, ``uniprot_id`` (see "Schema-policy mismatch" above), and
  anything protein-level (EC number, protein name).

**No fuzzy matching, ever.** Exact string comparison only, at every level.
No case-folding, no synonym tables, no abbreviation expansion -- none has an
existing, documented, deterministic rule anywhere in this repository, and
inventing one here would violate ``.cursor/rules/01-scientific-integrity.mdc``.
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
    from app.connectors.sgd import SgdNormalizedRecord

_ENTITY_TYPE = "gene"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no canonicalization, no
    abbreviation expansion (see module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_aliases(aliases: tuple[str, ...]) -> tuple[str, ...]:
    """Trim each alias, drop blanks, and collapse exact (not fuzzy) duplicates.

    Order-preserving. Collapsing literal repeats is query-efficiency hygiene
    only -- it never changes which candidates a distinct alias string can
    surface.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        stripped = alias.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            cleaned.append(stripped)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class GeneIdentity:
    """Source-neutral description of one incoming gene identity claim.

    Not coupled to any one connector's record class -- any source (SGD,
    NCBI, KEGG, a future one) constructs this from whatever fields it
    actually supplies, without this module knowing anything about that
    source's wire format. Deliberately carries no ``organism_id``: organism
    scope is supplied separately, as a required keyword argument to
    ``normalize_gene``, not as part of the source-supplied identity claim
    (see module docstring). Deliberately carries no ``uniprot_id`` at all --
    see the module docstring's "Schema-policy mismatch" section: UniProt
    identity belongs to a later Protein-normalization increment, not here.

    Requires at least one identity signal -- a Level 1 identifier
    (``sgd_id``/``ncbi_gene_id``/``kegg_gene_id``) or a Level 3
    candidate-generation field (``systematic_name``/``symbol``/a nonblank
    alias) -- otherwise there is nothing to normalize against. Carries no
    confidence score: no specification defines one for gene identity.
    """

    source: SourceType
    source_identifier: str

    sgd_id: str | None = None
    ncbi_gene_id: str | None = None
    kegg_gene_id: str | None = None

    systematic_name: str | None = None
    symbol: str | None = None
    aliases: tuple[str, ...] = ()
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "sgd_id", _clean(self.sgd_id))
        object.__setattr__(self, "ncbi_gene_id", _clean(self.ncbi_gene_id))
        object.__setattr__(self, "kegg_gene_id", _clean(self.kegg_gene_id))
        object.__setattr__(self, "systematic_name", _clean(self.systematic_name))
        object.__setattr__(self, "symbol", _clean(self.symbol))
        object.__setattr__(self, "aliases", _clean_aliases(self.aliases))
        object.__setattr__(self, "description", _clean(self.description))

        if not any(
            (
                self.sgd_id,
                self.ncbi_gene_id,
                self.kegg_gene_id,
                self.systematic_name,
                self.symbol,
                self.aliases,
            )
        ):
            raise ValueError(
                "GeneIdentity requires at least one identity signal (sgd_id, "
                "ncbi_gene_id, kegg_gene_id, systematic_name, symbol, or a nonblank "
                "alias)"
            )


@dataclass(frozen=True, slots=True)
class GeneCandidate:
    """A read-only snapshot of one existing ``Gene`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. Carries only the fields needed for identity/conflict checks;
    ``organism_id`` is included specifically so a global strong-identifier
    match can be checked against the caller's requested organism (see
    ``normalize_gene``'s cross-organism-conflict handling), never assumed
    from how the candidate was looked up. Deliberately carries no
    ``uniprot_id`` -- see the module docstring.
    """

    id: UUID
    organism_id: UUID

    sgd_id: str | None
    ncbi_gene_id: str | None
    kegg_gene_id: str | None

    systematic_name: str | None
    symbol: str | None
    aliases: tuple[str, ...]
    description: str | None


@runtime_checkable
class GeneLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_gene`` never touches SQLAlchemy.

    ``by_sgd_id``/``by_ncbi_gene_id``/``by_kegg_gene_id`` are **global** --
    no ``organism_id`` parameter -- matching those columns' global
    unique-when-present indexes. ``by_systematic_name``/``by_symbol``/
    ``by_alias`` are **organism-scoped** -- ``organism_id`` first -- since
    those fields carry no uniqueness constraint at all and must never be
    searched globally (see module docstring). No method here can insert,
    update, or delete a row. Deliberately has no ``by_uniprot_id`` method at
    all (see module docstring).

    An organism-scoped method's implementation must filter by
    ``organism_id`` in the query itself (never fetch unscoped and filter
    afterward) -- ``normalize_gene`` also independently verifies this (see
    ``_organism_scoped_candidates``), but that check exists as a defensive
    backstop, not as a substitute for a correctly scoped query.
    """

    def by_sgd_id(self, sgd_id: str) -> Sequence[GeneCandidate]:
        """All genes, in any organism, with this ``sgd_id`` (0, 1, or more)."""
        ...

    def by_ncbi_gene_id(self, ncbi_gene_id: str) -> Sequence[GeneCandidate]:
        """All genes, in any organism, with this ``ncbi_gene_id`` (0, 1, or more)."""
        ...

    def by_kegg_gene_id(self, kegg_gene_id: str) -> Sequence[GeneCandidate]:
        """All genes, in any organism, with this ``kegg_gene_id`` (0, 1, or more)."""
        ...

    def by_systematic_name(
        self, organism_id: UUID, systematic_name: str
    ) -> Sequence[GeneCandidate]:
        """Genes in ``organism_id`` with this ``systematic_name`` (0, 1, or more)."""
        ...

    def by_symbol(self, organism_id: UUID, symbol: str) -> Sequence[GeneCandidate]:
        """Genes in ``organism_id`` with this ``symbol`` (0, 1, or more)."""
        ...

    def by_alias(self, organism_id: UUID, alias: str) -> Sequence[GeneCandidate]:
        """Genes in ``organism_id`` carrying this alias (0, 1, or more)."""
        ...


def _organism_scoped_candidates(
    organism_id: UUID, candidates: Sequence[GeneCandidate]
) -> tuple[GeneCandidate, ...]:
    """Deduplicate by id and assert every candidate belongs to ``organism_id``.

    Used only for the organism-scoped weak lookups
    (``by_systematic_name``/``by_symbol``/``by_alias``): those methods are
    contractually required to filter by ``organism_id`` themselves, so a
    candidate from a different organism appearing here indicates a broken
    lookup implementation, not a legitimate outcome -- unlike the global
    strong-identifier lookups, where a different-organism match is an
    expected, valid result handled explicitly in ``normalize_gene`` (see
    module docstring). Raising here is deliberately loud rather than
    defensive-filtering, so a broken lookup implementation is caught
    immediately rather than silently narrowing results.
    """
    unique = unique_by_id(candidates)
    for candidate in unique:
        if candidate.organism_id != organism_id:
            raise ValueError(
                f"GeneLookup returned candidate {candidate.id} from organism "
                f"{candidate.organism_id}, but normalize_gene was called with "
                f"organism_id={organism_id} -- every organism-scoped GeneLookup method "
                "must scope its query to the requested organism"
            )
    return unique


def _describe_identifier_disagreement(
    identity: GeneIdentity, candidate: GeneCandidate
) -> str | None:
    """Compare supplied Level 1 identifiers + ``systematic_name`` against a resolved candidate.

    Exact comparison only. A candidate field that is ``None`` is compatible
    (missing metadata the incoming record could later fill in, not a
    disagreement) -- same convention as
    ``app.normalization.publication._describe_identifier_disagreement``.

    Checked: ``sgd_id``, ``ncbi_gene_id``, ``kegg_gene_id``,
    ``systematic_name``. ``uniprot_id`` is deliberately never checked --
    it is not a Gene identity field in this module at all (see module
    docstring).

    Deliberately **not** checked: ``symbol`` (no documented symbol-conflict
    policy exists anywhere in this repository -- reported as an open
    question, see the increment report, rather than inventing an aggressive
    rule), ``aliases``, ``description`` (per instructions, never
    conflict-relevant).
    """
    checks: tuple[tuple[str, str | None, str | None], ...] = (
        ("sgd_id", identity.sgd_id, candidate.sgd_id),
        ("ncbi_gene_id", identity.ncbi_gene_id, candidate.ncbi_gene_id),
        ("kegg_gene_id", identity.kegg_gene_id, candidate.kegg_gene_id),
        ("systematic_name", identity.systematic_name, candidate.systematic_name),
    )
    for field_name, supplied, existing in checks:
        if supplied is not None and existing is not None and supplied != existing:
            return (
                f"supplied {field_name} {supplied!r} disagrees with existing gene's "
                f"{field_name} {existing!r}"
            )
    return None


def _has_creation_complete_metadata(identity: GeneIdentity) -> bool:
    """The documented Gene creation-completeness rule, verbatim from the schema doc.

    ``docs/02_database_schema.md`` ("Table: gene", Constraints): "At least
    one of the following should normally be present: symbol, systematic_name,
    ncbi_gene_id, sgd_id." Not invented here. ``kegg_gene_id`` is a genuine
    Level 1 identity anchor (able to MATCH/CONFLICT) but is *not* part of
    this documented completeness set, so supplying only ``kegg_gene_id``
    does not by itself justify ``NEW`` -- no new rule is invented here just
    to make a KEGG-only record creatable.
    """
    return bool(
        identity.symbol or identity.systematic_name or identity.ncbi_gene_id or identity.sgd_id
    )


def _weak_candidates(
    identity: GeneIdentity, organism_id: UUID, lookup: GeneLookup
) -> tuple[GeneCandidate, ...]:
    """Level 3 candidate generation: ``systematic_name``/``symbol``/aliases, organism-scoped.

    Candidate generation only -- never returns a match verdict on its own.
    Every weak field's results are pooled before deduplication/organism
    verification, so pointing at the same one Gene from several weak fields
    still yields exactly one candidate, not inflated ambiguity.
    """
    pooled: list[GeneCandidate] = []
    if identity.systematic_name is not None:
        pooled.extend(lookup.by_systematic_name(organism_id, identity.systematic_name))
    if identity.symbol is not None:
        pooled.extend(lookup.by_symbol(organism_id, identity.symbol))
    for alias in identity.aliases:
        pooled.extend(lookup.by_alias(organism_id, alias))
    return _organism_scoped_candidates(organism_id, pooled)


def normalize_gene(
    identity: GeneIdentity, *, organism_id: UUID, lookup: GeneLookup
) -> NormalizationResult:
    """Resolve one source-supplied gene identity against existing ``Gene`` rows in ``organism_id``.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is required and has no default -- there is no
    global/current-organism fallback anywhere in this module. Every
    returned ``NormalizationResult.organism_id`` equals the supplied
    ``organism_id`` regardless of status (see module docstring).
    """
    strong_anchor_results: list[tuple[str, tuple[GeneCandidate, ...]]] = []
    if identity.sgd_id is not None:
        strong_anchor_results.append(("sgd_id", unique_by_id(lookup.by_sgd_id(identity.sgd_id))))
    if identity.ncbi_gene_id is not None:
        strong_anchor_results.append(
            ("ncbi_gene_id", unique_by_id(lookup.by_ncbi_gene_id(identity.ncbi_gene_id)))
        )
    if identity.kegg_gene_id is not None:
        strong_anchor_results.append(
            ("kegg_gene_id", unique_by_id(lookup.by_kegg_gene_id(identity.kegg_gene_id)))
        )

    if strong_anchor_results:
        by_id: dict[UUID, GeneCandidate] = {}
        single_match_ids: set[UUID] = set()  # clean single match, same organism
        cross_organism_ids: set[UUID] = set()  # clean single match, a DIFFERENT organism
        ambiguous_ids: set[UUID] = set()  # defensive: 2+ candidates from one anchor
        agreeing_anchors: list[str] = []

        for anchor_name, candidates in strong_anchor_results:
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
            # A globally unique identifier the incoming record supplied
            # already belongs to a Gene in a *different* organism. This is
            # always a conflict -- never silently ignored, never NEW (that
            # would hide an already-claimed global identifier), and never
            # picked as a match just because it is the only clean hit.
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
                    "a supplied global identifier already belongs to an existing gene "
                    "in a different organism"
                ),
            )

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
                    organism_id=organism_id,
                    candidate_entity_ids=all_ids,
                    reason="different supplied identifiers resolved to different existing genes",
                )
            return NormalizationResult(
                status=NormalizationStatus.AMBIGUOUS,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=organism_id,
                candidate_entity_ids=all_ids,
                reason="a supplied identifier matches more than one existing gene",
            )
        # else: every strong anchor was NO_MATCH anywhere -- fall through to Level 3.

    # Level 3: no strong identifier established a verdict above (either none
    # was supplied, or all supplied ones matched nothing globally). A
    # same-organism weak-name/alias collision must still block NEW here --
    # the same collision-guard principle app.normalization.organism uses.
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
                "no exact identifier matched, but an existing gene in this organism "
                "shares a supplied systematic_name/symbol/alias -- not enough to "
                "independently establish a match"
            ),
        )

    if strong_anchor_results and _has_creation_complete_metadata(identity):
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            organism_id=organism_id,
            reason="no existing gene matched the supplied identifier(s) in this organism",
        )

    if strong_anchor_results:
        reason = (
            "no existing gene matched the supplied identifier(s), and the supplied "
            "metadata (symbol/systematic_name/ncbi_gene_id/sgd_id) is insufficient to "
            "safely create a new one"
        )
    else:
        reason = (
            "only weak name/alias signals were supplied, with no matching existing "
            "gene in this organism -- insufficient to create or match a gene"
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


def gene_identity_from_sgd(record: SgdNormalizedRecord) -> GeneIdentity:
    """Pure adapter: an SGD connector's normalized record -> a source-neutral identity.

    No I/O, no network, no inference -- exact copying of identifiers/metadata
    already present on ``record``. ``record`` itself is never mutated -- it
    is a frozen dataclass, and nothing here does anything but read its
    fields.

    * ``source_identifier`` is the SGD ID, always present on
      ``SgdNormalizedRecord``.
    * ``standard_name`` maps to ``symbol`` -- SGD's "standard gene name" is
      this schema's ``symbol`` (e.g. ``"CDC28"``).
    * ``systematic_name``, ``aliases``, ``description`` copied exactly.
    * ``record.uniprot_id`` is **not** copied -- see the module docstring's
      "Schema-policy mismatch" section: it remains available on ``record``
      itself for a later Protein-normalization increment, and is
      deliberately never promoted into ``GeneIdentity``.
    * ``ncbi_gene_id``/``kegg_gene_id`` are left ``None``: SGD's own locus
      record exposes neither as a structured field (only NCBI's/KEGG's own
      connectors would supply those), and this function does not parse them
      out of ``record.external_links`` -- doing so would be inference, not
      exact copying, since ``SgdExternalLink`` entries carry only
      ``category``/``display_name``/``link``, not a validated identifier
      value.
    """
    return GeneIdentity(
        source=SourceType.SGD,
        source_identifier=record.sgd_id,
        sgd_id=record.sgd_id,
        systematic_name=record.systematic_name,
        symbol=record.standard_name,
        aliases=record.aliases,
        description=record.description,
    )


__all__ = [
    "GeneCandidate",
    "GeneIdentity",
    "GeneLookup",
    "gene_identity_from_sgd",
    "normalize_gene",
]
