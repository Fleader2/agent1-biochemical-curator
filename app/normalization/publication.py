"""Publication identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``PublicationLookup`` protocol,
which a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary
``app.normalization.organism`` established for organism normalization.

**Publication is organism-agnostic.** ``normalize_publication`` takes no
organism parameter, and every ``NormalizationResult`` it returns has
``organism_id=None`` -- ``Publication`` has no ``organism_id`` column at all
(``app/models/publication.py``), unlike the organism-scoped entities later
increments will normalize.

**Schema, verified directly from ``app/models/publication.py``, not assumed:**

* ``pmid: str | None`` -- unique **only when** ``pmid IS NOT NULL`` (a
  partial index), same for ``pmcid``/``doi``. Because of this, a lookup by
  any one of them can, in principle, still return more than one row (a
  pre-existing data anomaly, or a defensive test double) even though the
  schema means to prevent it -- this module always classifies candidate
  counts (``app.normalization.identifiers.classify_candidates``) rather than
  assuming "the" match exists just because an identifier was supplied.
* ``title: str`` -- ``NOT NULL``. Not used for identity resolution at all
  (see below) -- this module never queries by title, so this NOT NULL
  constraint has no bearing on when a ``NEW`` verdict may be returned (unlike
  ``Organism.scientific_name``, which *is* an identity anchor).
* ``journal: str | None``, ``year: int | None`` -- metadata only.

**Authoritative identifiers only.** ``pmid``, ``pmcid``, and ``doi`` are the
only identity anchors. ``title``/``journal``/``year`` are never queried as
identifiers, never used for fuzzy or synonym-based candidate discovery, and
never independently establish or block a match -- see the module-level
"Metadata policy" note below ``_describe_identifier_disagreement``.

**No identifier normalization beyond whitespace.** No DOI case-folding, no
DOI prefix stripping, no PMID/PMCID syntax rewriting -- none of these have an
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
)
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

if TYPE_CHECKING:
    from app.connectors.pubmed import PubMedNormalizedRecord

_ENTITY_TYPE = "publication"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case-folding, no prefix stripping (see module
    docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    """Source-neutral description of one incoming publication identity claim.

    Not coupled to any one connector's record class -- any source (PubMed,
    PMC, a future one) constructs this from whatever fields it actually
    supplies, without this module knowing anything about that source's wire
    format. ``year`` matches ``Publication.year``/``PubMedNormalizedRecord.year``
    exactly (``int | None``); every identifier/metadata string field is
    trimmed, blank-becomes-``None`` (see ``_clean``).

    Requires at least one authoritative identifier (``pmid``, ``pmcid``, or
    ``doi``) -- ``title``/``journal``/``year`` alone are never sufficient to
    normalize against, and construction raises rather than admitting a
    title-only identity. This is a deliberate choice (see module docstring):
    the alternative of accepting a title-only identity and returning
    ``UNRESOLVED`` from ``normalize_publication`` was considered and rejected
    in favor of matching ``OrganismIdentity``'s existing convention of
    validating identity anchors at construction time. Carries no confidence
    score: no specification defines one for publication identity.
    """

    source: SourceType
    source_identifier: str
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None
    journal: str | None = None
    year: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "pmid", _clean(self.pmid))
        object.__setattr__(self, "pmcid", _clean(self.pmcid))
        object.__setattr__(self, "doi", _clean(self.doi))
        object.__setattr__(self, "title", _clean(self.title))
        object.__setattr__(self, "journal", _clean(self.journal))

        if not any((self.pmid, self.pmcid, self.doi)):
            raise ValueError(
                "PublicationIdentity requires at least one authoritative identifier "
                "(pmid, pmcid, or doi) -- title/journal/year alone are not sufficient"
            )


@dataclass(frozen=True, slots=True)
class PublicationCandidate:
    """A read-only snapshot of one existing ``Publication`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. Carries only the fields needed for identity/conflict checks.
    ``title`` is ``str`` (not ``str | None``), matching the schema's
    ``NOT NULL`` constraint -- but see the module docstring: it is never used
    for identity resolution regardless.
    """

    id: UUID
    pmid: str | None
    pmcid: str | None
    doi: str | None
    title: str
    journal: str | None
    year: int | None


@runtime_checkable
class PublicationLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_publication`` never touches SQLAlchemy.

    Every method returns a snapshot sequence, never an ORM ``Session`` or
    ``Query``, and never ``Optional`` even though the schema's partial unique
    indexes mean to guarantee at most one row per identifier -- an
    implementation should still return every matching row it actually finds,
    so this module's own candidate-list discipline (rather than an assumed
    invariant) is what decides whether a lookup counts as a clean match. No
    method here can insert, update, or delete a row. Deliberately has no
    title-based lookup method at all: title is never an identity anchor for
    ``Publication`` (see module docstring).
    """

    def by_pmid(self, pmid: str) -> Sequence[PublicationCandidate]:
        """All existing publications with this ``pmid`` (0, 1, or more)."""
        ...

    def by_pmcid(self, pmcid: str) -> Sequence[PublicationCandidate]:
        """All existing publications with this ``pmcid`` (0, 1, or more)."""
        ...

    def by_doi(self, doi: str) -> Sequence[PublicationCandidate]:
        """All existing publications with this ``doi`` (0, 1, or more)."""
        ...


def _unique_candidates(
    candidates: Sequence[PublicationCandidate],
) -> tuple[PublicationCandidate, ...]:
    """Deduplicate by ``id``, preserving first-seen order.

    ``classify_candidates()`` is deliberately cardinality-only and must not
    be changed to add deduplication itself -- so a lookup implementation that
    could return the same row twice (e.g. from a join) is normalized here,
    before classification, so a duplicate never manufactures an
    ``AMBIGUOUS`` verdict.
    """
    seen: dict[UUID, PublicationCandidate] = {}
    for candidate in candidates:
        seen.setdefault(candidate.id, candidate)
    return tuple(seen.values())


def _describe_identifier_disagreement(
    identity: PublicationIdentity, candidate: PublicationCandidate
) -> str | None:
    """Compare supplied ``pmid``/``pmcid``/``doi`` against a resolved candidate's own.

    Exact string comparison only (both sides already whitespace-trimmed) --
    no case-insensitivity, no DOI-prefix normalization. A candidate field
    that is ``None`` is treated as compatible (missing metadata the incoming
    record could later fill in, not a disagreement) -- deliberately the
    opposite of leaving a field unresolved: only an *explicit*, non-``None``
    existing value that differs from the supplied one counts as a
    disagreement. This is different from
    ``app.normalization.organism._describe_metadata_disagreement``'s
    ``strain`` check (there, a candidate's ``strain IS NULL`` is itself a
    distinct identity from any strain-specific row, so it *does* count as a
    disagreement); ``pmid``/``pmcid``/``doi`` being merely unset on an
    existing row carries no such meaning here.

    **Metadata policy.** ``title``/``journal``/``year`` are never compared
    here, even when supplied and even when they differ from the candidate's
    own values. This increment's decision rules (Phase 4, Increment 3)
    single out identifier disagreement as the only thing that matters for
    ``CONFLICTED``, and explicitly caution against inventing a
    title/journal/year conflict policy without an existing, documented rule
    to follow -- none exists in this repository. Whether a title/journal/year
    mismatch on an otherwise-identifier-matched row should ever surface (even
    as a non-blocking note in ``reason``) is left as an open policy question
    for a later increment, not decided here.
    """
    if identity.pmid is not None and candidate.pmid is not None and identity.pmid != candidate.pmid:
        return (
            f"supplied pmid {identity.pmid!r} disagrees with existing publication's "
            f"pmid {candidate.pmid!r}"
        )
    if (
        identity.pmcid is not None
        and candidate.pmcid is not None
        and identity.pmcid != candidate.pmcid
    ):
        return (
            f"supplied pmcid {identity.pmcid!r} disagrees with existing publication's "
            f"pmcid {candidate.pmcid!r}"
        )
    if identity.doi is not None and candidate.doi is not None and identity.doi != candidate.doi:
        return (
            f"supplied doi {identity.doi!r} disagrees with existing publication's "
            f"doi {candidate.doi!r}"
        )
    return None


def _resolve_from_anchors(
    identity: PublicationIdentity,
    anchor_results: list[tuple[str, tuple[PublicationCandidate, ...]]],
) -> NormalizationResult:
    """Reconcile one or more identifier-anchor lookups (each already deduplicated).

    Mirrors ``app.normalization.organism._resolve_from_strong_anchors``'s
    reconciliation shape, with no weak/collision-guard path: every anchor
    ``Publication`` supports (``pmid``/``pmcid``/``doi``) is already a strong,
    exact-value identifier, and ``PublicationIdentity.__post_init__``
    guarantees at least one was supplied.
    """
    by_id: dict[UUID, PublicationCandidate] = {}
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

    if not single_match_ids and not ambiguous_ids:
        if identity.title is not None:
            return NormalizationResult(
                status=NormalizationStatus.NEW,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.NONE,
                reason="no existing publication matched the supplied identifier(s)",
            )
        # Publication.title is NOT NULL (app/models/publication.py). An
        # identifier-only record with no title is sufficient to MATCH an
        # existing row (checked above this branch runs), but not to justify
        # creating one -- that would require inventing a title. No
        # placeholder/PMID/DOI-as-title is manufactured here or anywhere
        # else in this module.
        return NormalizationResult(
            status=NormalizationStatus.UNRESOLVED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            reason=(
                "no existing publication matched the supplied identifier(s), and no "
                "title was supplied to safely create a new one"
            ),
        )

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

    # More than one distinct existing entity is potentially implicated --
    # either two anchors each cleanly resolved a *different* entity (a real
    # conflict), or an anchor was itself ambiguous with no other anchor
    # providing a competing clean single match (genuine ambiguity, not yet
    # a conflict between two clear answers). Either way, never pick one.
    all_ids = tuple(sorted(single_match_ids | ambiguous_ids))
    if len(single_match_ids) >= 2:
        return NormalizationResult(
            status=NormalizationStatus.CONFLICTED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            candidate_entity_ids=all_ids,
            reason="different supplied identifiers resolved to different existing publications",
        )
    return NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=all_ids,
        reason="a supplied identifier matches more than one existing publication",
    )


def normalize_publication(
    identity: PublicationIdentity, *, lookup: PublicationLookup
) -> NormalizationResult:
    """Resolve one source-supplied publication identity against existing ``Publication`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes.
    ``organism_id`` is never set on the returned result -- ``Publication`` is
    organism-agnostic, and this function takes no organism parameter at all.
    """
    anchor_results: list[tuple[str, tuple[PublicationCandidate, ...]]] = []

    if identity.pmid is not None:
        anchor_results.append(("pmid", _unique_candidates(lookup.by_pmid(identity.pmid))))
    if identity.pmcid is not None:
        anchor_results.append(("pmcid", _unique_candidates(lookup.by_pmcid(identity.pmcid))))
    if identity.doi is not None:
        anchor_results.append(("doi", _unique_candidates(lookup.by_doi(identity.doi))))

    if anchor_results:
        return _resolve_from_anchors(identity, anchor_results)

    # PublicationIdentity.__post_init__ guarantees at least one identifier is
    # present, so this is unreachable in practice -- kept only so this
    # function stays obviously total rather than relying on that invariant
    # holding at every call site (same defensive pattern as
    # app.normalization.organism.normalize_organism).
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.NONE,
        reason="insufficient publication identity information",
    )


def publication_identity_from_pubmed(record: PubMedNormalizedRecord) -> PublicationIdentity:
    """Pure adapter: a PubMed connector's normalized record -> a source-neutral identity.

    No I/O, no network, no inference -- exact copying of identifiers/metadata
    already present on ``record``. ``source_identifier`` is the PMID, which
    ``PubMedNormalizedRecord.pmid`` guarantees is always present (unlike
    ``pmcid``/``doi``, which PubMed may not have assigned). ``record`` itself
    is never mutated -- it is a frozen dataclass, and nothing here does
    anything but read its fields.
    """
    return PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier=record.pmid,
        pmid=record.pmid,
        pmcid=record.pmcid,
        doi=record.doi,
        title=record.title,
        journal=record.journal,
        year=record.year,
    )


__all__ = [
    "PublicationCandidate",
    "PublicationIdentity",
    "PublicationLookup",
    "normalize_publication",
    "publication_identity_from_pubmed",
]
