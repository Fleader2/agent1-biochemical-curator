"""Organism identity normalization.

Read-only, decision-only: nothing here writes to the database. There is no
SQLAlchemy ``Session`` in this module, no ``INSERT``/``UPDATE``/``DELETE``,
no ``SourceCrossReference`` or ``ExternalRecord`` write -- candidate lookups
are performed through the injected, read-only ``OrganismLookup`` protocol,
which a later, separate persistence increment implements against the real
database. This mirrors the same retrieval/persistence boundary Phase 3's
connectors kept around database writes.

**Generality.** This normalizer supports arbitrary microbial organisms. It
never hard-codes a species or strain name, never reads organism identity
from ``app.config.settings`` or any other global/module-level state, and
never assumes a single organism exists in the database or that
``scientific_name`` alone is unique. Organism identity always comes
explicitly from the caller-supplied ``OrganismIdentity`` and, on the way
back out, from an explicit ``organism_id``/``matched_entity_id`` on the
returned ``NormalizationResult`` -- never inferred.

**Schema, verified directly from ``app/models/organism.py``, not assumed:**

* ``scientific_name: str`` -- ``NOT NULL``. Because of this, no ``NEW``
  verdict is ever returned without a supplied ``scientific_name``: there
  would be no way to satisfy this column later.
* ``strain: str | None``.
* ``(scientific_name, strain)`` is unique **only when ``strain IS NOT
  NULL``** (a partial index) -- strain-less rows are never deduplicated by
  the database, and multiple strain-specific rows for the same species are
  expected and permitted.
* ``ncbi_taxonomy_id: int | None`` -- indexed, but **not** unique.
* ``kegg_code: str | None`` -- **not** indexed, **not** unique.
* ``biocyc_id: str | None`` -- **not** indexed, **not** unique.

Because none of ``ncbi_taxonomy_id``/``kegg_code``/``biocyc_id`` is
database-unique, a lookup by any one of them can legitimately return zero,
one, or more than one row even when the identifier itself is genuinely
correct -- this module always classifies candidate counts
(``app.normalization.identifiers.classify_candidates``) rather than assuming
"the" match exists just because an identifier was supplied.

**No taxonomy inference is performed.** "S. cerevisiae" is never expanded to
"Saccharomyces cerevisiae", no strain-synonym table is consulted, no
species-name fuzzy matching happens, and no assessment of whether a bare
``scientific_name`` is "specific enough" to safely create a new row is
attempted -- no deterministic, documented rule for that exists anywhere in
this repository, and inventing one here would violate
``.cursor/rules/01-scientific-integrity.mdc``. A ``scientific_name``-only
identity with no existing strain-less match is therefore ``UNRESOLVED``, not
``NEW`` -- see ``_resolve_from_scientific_name_only``.
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
)
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

_ENTITY_TYPE = "organism"


def _clean(value: str | None) -> str | None:
    """Trim surrounding whitespace and turn a blank string into ``None``.

    Nothing beyond that: no case changes, no punctuation changes, no
    abbreviation expansion (see module docstring).
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class OrganismIdentity:
    """Source-neutral description of one incoming organism identity claim.

    Not coupled to any one connector's record class -- any source (KEGG,
    SGD, BRENDA, a future one) constructs this from whatever fields it
    actually supplies, without this module knowing anything about that
    source's wire format. ``ncbi_taxonomy_id`` is ``int`` to match
    ``Organism.ncbi_taxonomy_id`` exactly; every other identifier field is a
    trimmed, blank-becomes-``None`` string (see ``_clean``).

    Requires at least one identity anchor (``scientific_name``,
    ``ncbi_taxonomy_id``, ``kegg_code``, or ``biocyc_id``) -- otherwise there
    is nothing to normalize against. Carries no confidence score: no
    specification defines one for organism identity.
    """

    source: SourceType
    source_identifier: str
    scientific_name: str | None = None
    strain: str | None = None
    ncbi_taxonomy_id: int | None = None
    kegg_code: str | None = None
    biocyc_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "scientific_name", _clean(self.scientific_name))
        object.__setattr__(self, "strain", _clean(self.strain))
        object.__setattr__(self, "kegg_code", _clean(self.kegg_code))
        object.__setattr__(self, "biocyc_id", _clean(self.biocyc_id))

        if not any(
            (
                self.scientific_name,
                self.ncbi_taxonomy_id is not None,
                self.kegg_code,
                self.biocyc_id,
            )
        ):
            raise ValueError(
                "OrganismIdentity requires at least one organism identity anchor "
                "(scientific_name, ncbi_taxonomy_id, kegg_code, or biocyc_id)"
            )


@dataclass(frozen=True, slots=True)
class OrganismCandidate:
    """A read-only snapshot of one existing ``Organism`` row, for identity comparison only.

    Never a full ORM object -- keeps this module decoupled from SQLAlchemy
    and makes clear this is a snapshot, not something that can be mutated
    and saved. Carries only the fields needed for identity/conflict checks.
    """

    id: UUID
    scientific_name: str
    strain: str | None
    ncbi_taxonomy_id: int | None
    kegg_code: str | None
    biocyc_id: str | None


@runtime_checkable
class OrganismLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_organism`` never touches SQLAlchemy.

    Every method returns a snapshot sequence, never an ORM ``Session`` or
    ``Query`` -- an implementation (a real database-backed adapter, or a fake
    for tests) owns all actual querying. Nothing declared here can insert,
    update, or delete a row.
    """

    def by_ncbi_taxonomy_id(self, ncbi_taxonomy_id: int) -> Sequence[OrganismCandidate]:
        """All existing organisms with this ``ncbi_taxonomy_id`` (0, 1, or more)."""
        ...

    def by_kegg_code(self, kegg_code: str) -> Sequence[OrganismCandidate]:
        """All existing organisms with this ``kegg_code`` (0, 1, or more)."""
        ...

    def by_biocyc_id(self, biocyc_id: str) -> Sequence[OrganismCandidate]:
        """All existing organisms with this ``biocyc_id`` (0, 1, or more)."""
        ...

    def by_scientific_name_and_strain(
        self, scientific_name: str, strain: str
    ) -> Sequence[OrganismCandidate]:
        """All existing organisms with this exact ``(scientific_name, strain)`` pair."""
        ...

    def by_scientific_name_without_strain(
        self, scientific_name: str
    ) -> Sequence[OrganismCandidate]:
        """All existing *strain-less* organisms (``strain IS NULL``) with this scientific_name."""
        ...


def _unique_candidates(candidates: Sequence[OrganismCandidate]) -> tuple[OrganismCandidate, ...]:
    """Deduplicate by ``id``, preserving first-seen order.

    ``classify_candidates()`` is deliberately cardinality-only (Increment 1)
    and must not be changed to add deduplication itself -- so a lookup
    implementation that could return the same row twice (e.g. from a join)
    is normalized here, before classification, so a duplicate never
    manufactures an ``AMBIGUOUS`` verdict.
    """
    seen: dict[UUID, OrganismCandidate] = {}
    for candidate in candidates:
        seen.setdefault(candidate.id, candidate)
    return tuple(seen.values())


def _describe_metadata_disagreement(
    identity: OrganismIdentity, candidate: OrganismCandidate
) -> str | None:
    """Compare supplied ``scientific_name``/``strain`` against a resolved candidate's own.

    Exact string comparison only (both sides already whitespace-trimmed) --
    no case-insensitivity, no fuzzy matching. Returns a human-readable
    disagreement description, or ``None`` if everything supplied is
    compatible (or nothing relevant was supplied to compare).
    """
    if (
        identity.scientific_name is not None
        and identity.scientific_name != candidate.scientific_name
    ):
        return (
            f"supplied scientific_name {identity.scientific_name!r} disagrees with "
            f"existing organism's scientific_name {candidate.scientific_name!r}"
        )
    if identity.strain is not None and identity.strain != candidate.strain:
        return (
            f"supplied strain {identity.strain!r} disagrees with existing organism's "
            f"strain {candidate.strain!r}"
        )
    return None


def _resolve_from_strong_anchors(
    identity: OrganismIdentity,
    anchor_results: list[tuple[str, tuple[OrganismCandidate, ...]]],
    lookup: OrganismLookup,
) -> NormalizationResult:
    """Reconcile one or more strong-anchor lookups (each already deduplicated).

    "Strong" here means: ``ncbi_taxonomy_id``, ``kegg_code``, ``biocyc_id``,
    or ``(scientific_name, strain)`` when ``strain`` was supplied -- every
    anchor that is queried by exact value, as opposed to the weaker
    strain-less ``scientific_name``-only path.

    ``lookup`` is used only for the collision guard below -- every anchor
    lookup itself has already run by the time this function is called.
    """
    by_id: dict[UUID, OrganismCandidate] = {}
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
        if identity.scientific_name is None:
            return NormalizationResult(
                status=NormalizationStatus.UNRESOLVED,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.NONE,
                reason=(
                    "no existing organism matched the supplied identifier(s), and no "
                    "scientific_name was supplied to safely create a new one"
                ),
            )
        if identity.strain is not None:
            # (scientific_name, strain) is itself already a strong anchor
            # (queried above, in anchor_results) and also came back empty --
            # no separate collision guard is needed here.
            return NormalizationResult(
                status=NormalizationStatus.NEW,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.NONE,
                reason="no existing organism matched the supplied identifier(s)",
            )
        return _resolve_new_vs_name_collision(identity, lookup)

    if len(single_match_ids) == 1 and not ambiguous_ids:
        candidate_id = next(iter(single_match_ids))
        disagreement = _describe_metadata_disagreement(identity, by_id[candidate_id])
        if disagreement is None:
            return NormalizationResult(
                status=NormalizationStatus.MATCHED,
                source=identity.source,
                source_identifier=identity.source_identifier,
                entity_type=_ENTITY_TYPE,
                match_method=MatchMethod.EXACT_IDENTIFIER,
                organism_id=candidate_id,
                matched_entity_id=candidate_id,
                reason=f"resolved via {', '.join(agreeing_anchors)}",
            )
        return NormalizationResult(
            status=NormalizationStatus.CONFLICTED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=candidate_id,
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
            reason="different supplied identifiers resolved to different existing organisms",
        )
    return NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=all_ids,
        reason="a supplied identifier matches more than one existing organism",
    )


def _resolve_new_vs_name_collision(
    identity: OrganismIdentity, lookup: OrganismLookup
) -> NormalizationResult:
    """Collision guard: a supplied strong external identifier matched nothing.

    Called only when every strong external-identifier anchor (``ncbi_taxonomy_id``
    /``kegg_code``/``biocyc_id``) came back ``NO_MATCH``, ``scientific_name`` was
    supplied, and no ``strain`` was supplied (a supplied strain is already
    covered by the ``(scientific_name, strain)`` strong anchor itself).

    A ``NEW`` verdict here would tell a later persistence step it is safe to
    create a row -- but an existing strain-less organism sharing this exact
    ``scientific_name`` may simply be missing the supplied external
    identifier (never backfilled, or sourced from a connector this organism
    was never resolved against), not be a genuinely different organism.
    Persistence must not be the one to discover that overlap, so it is
    checked here: an exact ``by_scientific_name_without_strain`` lookup, not
    a fuzzy or partial one.

    A single such candidate deliberately does *not* become ``MATCHED``: the
    incoming record explicitly claims a strong identifier this candidate did
    not corroborate (it may hold a different value, or none at all), so
    scientific_name alone is not treated as proof of equivalence the way it
    is in ``_resolve_from_scientific_name_only`` (where no strong identifier
    was ever claimed in the first place, so there is nothing left
    uncorroborated). This is exactly the case ``NormalizationResult``'s own
    docstring anticipates: "a Level-3 synonym/name lookup can find exactly
    one candidate that is still insufficient evidence for MATCHED."

    This function does not inspect ``ncbi_taxonomy_id``/``kegg_code``/
    ``biocyc_id`` on the returned candidates to distinguish "field is simply
    absent" from "field holds an explicitly different value" -- neither
    ``_describe_metadata_disagreement`` nor any other logic in this module
    does, since it compares only ``scientific_name``/``strain``. Both
    sub-cases are reported as ``AMBIGUOUS`` here rather than invented as a
    new conflict category.
    """
    candidates = _unique_candidates(
        lookup.by_scientific_name_without_strain(identity.scientific_name)
    )
    state = classify_candidates(tuple(candidate.id for candidate in candidates))

    if state is CandidateSetState.NO_MATCH:
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            reason="no existing organism matched the supplied identifier(s) or scientific_name",
        )

    candidate_ids = tuple(sorted(candidate.id for candidate in candidates))
    return NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.CANDIDATE_SYNONYM,
        candidate_entity_ids=candidate_ids,
        reason=(
            "supplied strong identifier(s) matched no existing organism, but an "
            "existing strain-less organism shares this exact scientific_name -- "
            "not enough to independently establish a match"
        ),
    )


def _resolve_from_scientific_name_only(
    identity: OrganismIdentity, scientific_name: str, lookup: OrganismLookup
) -> NormalizationResult:
    """The weak path: no strong anchor was supplied, only a bare ``scientific_name``.

    Queries strain-less existing rows only (``strain IS NULL``) -- never
    strain-specific rows, since nothing here claims a strain.
    """
    candidates = _unique_candidates(lookup.by_scientific_name_without_strain(scientific_name))
    state = classify_candidates(tuple(candidate.id for candidate in candidates))

    if state is CandidateSetState.NO_MATCH:
        return NormalizationResult(
            status=NormalizationStatus.UNRESOLVED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            reason=(
                "no strain-less organism matches this scientific_name, and no "
                "documented, deterministic rule justifies creating one from a "
                "bare scientific name alone"
            ),
        )
    if state is CandidateSetState.SINGLE_MATCH:
        candidate_id = candidates[0].id
        return NormalizationResult(
            status=NormalizationStatus.MATCHED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=candidate_id,
            matched_entity_id=candidate_id,
            reason="resolved via scientific_name (no strain supplied or existing)",
        )
    return NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=tuple(sorted(candidate.id for candidate in candidates)),
        reason="multiple strain-less organisms share this scientific_name",
    )


def normalize_organism(
    identity: OrganismIdentity, *, lookup: OrganismLookup
) -> NormalizationResult:
    """Resolve one source-supplied organism identity against existing ``Organism`` rows.

    Read-only: only calls ``lookup``'s query methods, never writes. See the
    module docstring for the schema-derived candidate-list discipline this
    follows, and ``docs/03_agent_behavior.md``/task-level Phase 4 rules for
    why ambiguity and conflict are always reported rather than resolved
    automatically.
    """
    anchor_results: list[tuple[str, tuple[OrganismCandidate, ...]]] = []

    if identity.ncbi_taxonomy_id is not None:
        anchor_results.append(
            (
                "ncbi_taxonomy_id",
                _unique_candidates(lookup.by_ncbi_taxonomy_id(identity.ncbi_taxonomy_id)),
            )
        )
    if identity.kegg_code is not None:
        anchor_results.append(
            ("kegg_code", _unique_candidates(lookup.by_kegg_code(identity.kegg_code)))
        )
    if identity.biocyc_id is not None:
        anchor_results.append(
            ("biocyc_id", _unique_candidates(lookup.by_biocyc_id(identity.biocyc_id)))
        )
    if identity.scientific_name is not None and identity.strain is not None:
        anchor_results.append(
            (
                "scientific_name+strain",
                _unique_candidates(
                    lookup.by_scientific_name_and_strain(identity.scientific_name, identity.strain)
                ),
            )
        )

    if anchor_results:
        return _resolve_from_strong_anchors(identity, anchor_results, lookup)

    if identity.scientific_name is not None:
        return _resolve_from_scientific_name_only(identity, identity.scientific_name, lookup)

    # OrganismIdentity.__post_init__ guarantees at least one anchor is
    # present, so this is unreachable in practice -- kept only so this
    # function stays obviously total rather than relying on that invariant
    # holding at every call site.
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.NONE,
        reason="insufficient organism identity information",
    )


__all__ = [
    "OrganismCandidate",
    "OrganismIdentity",
    "OrganismLookup",
    "normalize_organism",
]
