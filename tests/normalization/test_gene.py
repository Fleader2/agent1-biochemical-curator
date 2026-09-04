"""Tests for gene identity normalization (Phase 4, Increment 4).

Pure unit tests: no database, no HTTP, no live SGD access. ``FakeGeneLookup``
is an in-memory, read-only stand-in for ``app.normalization.gene.GeneLookup``
-- there is no SQLAlchemy adapter in this increment (deferred to a later
persistence increment), consistent with ``app.normalization.gene``'s own
module docstring. It mirrors the real lookup contract exactly:
``by_sgd_id``/``by_ncbi_gene_id``/``by_kegg_gene_id`` search **globally**
(matching those columns' global unique-when-present indexes), while
``by_systematic_name``/``by_symbol``/``by_alias`` are **organism-scoped**.
Every test uses one of two fixed, distinct organism UUIDs so no test can pass
by accidentally assuming a single/default/global organism.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.connectors.sgd import SgdAliasEntry, SgdLocusRecord, SgdNormalizedRecord, normalize_locus
from app.models.enums import SourceType
from app.normalization.gene import (
    GeneCandidate,
    GeneIdentity,
    GeneLookup,
    gene_identity_from_sgd,
    normalize_gene,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit

ORGANISM_A = UUID("11111111-1111-1111-1111-111111111111")
ORGANISM_B = UUID("22222222-2222-2222-2222-222222222222")


@dataclass(frozen=True, slots=True)
class FakeGeneLookup:
    """In-memory ``GeneLookup``: global strong-identifier lookup, organism-scoped weak lookup."""

    genes: Sequence[GeneCandidate] = ()

    def by_sgd_id(self, sgd_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.sgd_id == sgd_id]

    def by_ncbi_gene_id(self, ncbi_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.ncbi_gene_id == ncbi_gene_id]

    def by_kegg_gene_id(self, kegg_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.kegg_gene_id == kegg_gene_id]

    def by_systematic_name(
        self, organism_id: UUID, systematic_name: str
    ) -> Sequence[GeneCandidate]:
        return [
            g
            for g in self.genes
            if g.organism_id == organism_id and g.systematic_name == systematic_name
        ]

    def by_symbol(self, organism_id: UUID, symbol: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.organism_id == organism_id and g.symbol == symbol]

    def by_alias(self, organism_id: UUID, alias: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.organism_id == organism_id and alias in g.aliases]


class LeakyWeakGeneLookup:
    """A ``GeneLookup`` whose weak (organism-scoped) methods ignore organism scope entirely.

    Used only to prove ``normalize_gene`` rejects cross-organism leakage from
    the organism-scoped methods, which are contractually required to filter
    by ``organism_id`` themselves. Strong methods are legitimately global, so
    they are implemented correctly here -- only the weak methods are broken.
    """

    def __init__(self, genes: Sequence[GeneCandidate]) -> None:
        self.genes = genes

    def by_sgd_id(self, sgd_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.sgd_id == sgd_id]

    def by_ncbi_gene_id(self, ncbi_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.ncbi_gene_id == ncbi_gene_id]

    def by_kegg_gene_id(self, kegg_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.kegg_gene_id == kegg_gene_id]

    def by_systematic_name(
        self, organism_id: UUID, systematic_name: str
    ) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.systematic_name == systematic_name]

    def by_symbol(self, organism_id: UUID, symbol: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.symbol == symbol]

    def by_alias(self, organism_id: UUID, alias: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if alias in g.aliases]


def _candidate(
    *,
    organism_id: UUID,
    sgd_id: str | None = None,
    ncbi_gene_id: str | None = None,
    kegg_gene_id: str | None = None,
    systematic_name: str | None = None,
    symbol: str | None = None,
    aliases: tuple[str, ...] = (),
    description: str | None = None,
    gene_id: UUID | None = None,
) -> GeneCandidate:
    return GeneCandidate(
        id=gene_id or uuid4(),
        organism_id=organism_id,
        sgd_id=sgd_id,
        ncbi_gene_id=ncbi_gene_id,
        kegg_gene_id=kegg_gene_id,
        systematic_name=systematic_name,
        symbol=symbol,
        aliases=aliases,
        description=description,
    )


def _sgd_record(
    *,
    sgd_id: str = "S000000364",
    systematic_name: str | None = "YBR160W",
    standard_name: str | None = "CDC28",
    description: str | None = "A test gene",
    aliases: tuple[str, ...] = ("CDC28_ALIAS",),
    uniprot_id: str | None = "P00546",
) -> SgdNormalizedRecord:
    raw = SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name=systematic_name,
        standard_name=standard_name,
        locus_type="ORF",
        description=description,
        aliases=tuple(SgdAliasEntry(display_name=a, category="Alias") for a in aliases),
        uniprot_id=uniprot_id,
        external_links=(),
        raw={},
    )
    return normalize_locus(raw)


# --- GeneIdentity construction / validation --------------------------------------


def test_gene_identity_requires_at_least_one_identity_signal() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        GeneIdentity(source=SourceType.SGD, source_identifier="req-1")


def test_gene_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        GeneIdentity(source=SourceType.SGD, source_identifier="   ", sgd_id="S000000364")


def test_gene_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="  S000000364  ",
        symbol="   ",
    )
    assert identity.sgd_id == "S000000364"
    assert identity.symbol is None


def test_gene_identity_drops_blank_aliases_and_dedupes_exact_repeats() -> None:
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        aliases=("  ALIAS1  ", "", "ALIAS1", "  ", "ALIAS2"),
    )
    assert identity.aliases == ("ALIAS1", "ALIAS2")


def test_gene_identity_has_no_uniprot_id_field() -> None:
    assert not hasattr(GeneIdentity, "uniprot_id")
    assert "uniprot_id" not in inspect.signature(GeneIdentity).parameters


def test_gene_candidate_has_no_uniprot_id_field() -> None:
    assert "uniprot_id" not in inspect.signature(GeneCandidate).parameters


def test_lookup_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )
    with pytest.raises(TypeError):
        normalize_gene(identity, organism_id=ORGANISM_A)  # type: ignore[call-arg]


def test_organism_id_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )
    lookup = FakeGeneLookup(genes=())
    with pytest.raises(TypeError):
        normalize_gene(identity, lookup=lookup)  # type: ignore[call-arg]


def test_normalize_gene_organism_id_is_keyword_only_with_no_default() -> None:
    parameters = inspect.signature(normalize_gene).parameters
    assert parameters["organism_id"].default is inspect.Parameter.empty
    assert parameters["organism_id"].kind is inspect.Parameter.KEYWORD_ONLY


# --- GeneLookup API shape: global strong methods, organism-scoped weak methods ------


def test_strong_id_lookup_methods_do_not_accept_organism_id() -> None:
    for name in ("by_sgd_id", "by_ncbi_gene_id", "by_kegg_gene_id"):
        params = list(inspect.signature(getattr(GeneLookup, name)).parameters)
        assert "organism_id" not in params, f"{name} must not take organism_id"


def test_weak_lookup_methods_require_organism_id_first() -> None:
    for name in ("by_systematic_name", "by_symbol", "by_alias"):
        params = list(inspect.signature(getattr(GeneLookup, name)).parameters)
        assert params[1] == "organism_id", f"{name} must take organism_id as its first argument"


def test_gene_lookup_has_no_by_uniprot_id_method() -> None:
    assert not hasattr(GeneLookup, "by_uniprot_id")


# --- Organism scoping (weak lookups only) -------------------------------------------


def test_same_symbol_in_two_different_organisms_does_not_collide() -> None:
    gene_a_id, gene_b_id = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_A, symbol="ABC1", gene_id=gene_a_id),
            _candidate(organism_id=ORGANISM_B, symbol="ABC1", gene_id=gene_b_id),
        )
    )
    identity = GeneIdentity(source=SourceType.SGD, source_identifier="req-2", symbol="ABC1")

    result_a = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)
    result_b = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result_a.status is NormalizationStatus.AMBIGUOUS
    assert result_a.candidate_entity_ids == (gene_a_id,)
    assert result_b.status is NormalizationStatus.AMBIGUOUS
    assert result_b.candidate_entity_ids == (gene_b_id,)


def test_same_systematic_name_in_two_different_organisms_does_not_collide() -> None:
    gene_a_id, gene_b_id = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_A, systematic_name="YBR160W", gene_id=gene_a_id),
            _candidate(organism_id=ORGANISM_B, systematic_name="YBR160W", gene_id=gene_b_id),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="req-3", systematic_name="YBR160W"
    )

    result_a = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)
    result_b = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result_a.candidate_entity_ids == (gene_a_id,)
    assert result_b.candidate_entity_ids == (gene_b_id,)


def test_cross_organism_leakage_from_weak_lookup_is_rejected() -> None:
    """Weak (organism-scoped) methods must honor organism_id -- a broken one is caught."""
    other_org_gene = _candidate(organism_id=ORGANISM_B, symbol="ABC1")
    lookup = LeakyWeakGeneLookup(genes=[other_org_gene])
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-4", symbol="ABC1")

    with pytest.raises(ValueError, match="GeneLookup returned candidate"):
        normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)


# --- Strong exact identifiers (global), same-organism match -------------------------


def test_sgd_id_single_candidate_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, sgd_id="S000000364", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == gene_id
    assert result.organism_id == ORGANISM_A


def test_ncbi_gene_id_single_candidate_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, ncbi_gene_id="852457", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.NCBI, source_identifier="852457", ncbi_gene_id="852457"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id
    assert result.organism_id == ORGANISM_A


def test_kegg_gene_id_single_candidate_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, kegg_gene_id="sce:YBR160W", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.KEGG, source_identifier="sce:YBR160W", kegg_gene_id="sce:YBR160W"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id
    assert result.organism_id == ORGANISM_A


def test_all_supplied_strong_ids_resolving_same_gene_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(
                organism_id=ORGANISM_A,
                sgd_id="S000000364",
                ncbi_gene_id="852457",
                kegg_gene_id="sce:YBR160W",
                gene_id=gene_id,
            ),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        ncbi_gene_id="852457",
        kegg_gene_id="sce:YBR160W",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id
    for name in ("sgd_id", "ncbi_gene_id", "kegg_gene_id"):
        assert name in result.reason


# --- Compatible missing identifiers -------------------------------------------------


def test_sgd_id_resolves_a_incoming_ncbi_gene_id_missing_on_a_is_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(
                organism_id=ORGANISM_A, sgd_id="S000000364", ncbi_gene_id=None, gene_id=gene_id
            ),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        ncbi_gene_id="999999",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id


# --- Cross-organism conflicts (the core of this correction) ------------------------


def test_sgd_id_claimed_by_gene_in_different_organism_is_conflicted_never_new() -> None:
    foreign_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_B, sgd_id="S000000364", gene_id=foreign_gene),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert foreign_gene in result.candidate_entity_ids
    assert result.organism_id == ORGANISM_A


def test_ncbi_gene_id_claimed_by_gene_in_different_organism_is_conflicted() -> None:
    foreign_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_B, ncbi_gene_id="852457", gene_id=foreign_gene),)
    )
    identity = GeneIdentity(
        source=SourceType.NCBI, source_identifier="852457", ncbi_gene_id="852457"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert foreign_gene in result.candidate_entity_ids


def test_kegg_gene_id_claimed_by_gene_in_different_organism_is_conflicted() -> None:
    foreign_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_B, kegg_gene_id="sce:YBR160W", gene_id=foreign_gene),
        )
    )
    identity = GeneIdentity(
        source=SourceType.KEGG, source_identifier="sce:YBR160W", kegg_gene_id="sce:YBR160W"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert foreign_gene in result.candidate_entity_ids


def test_unmatched_global_strong_id_reaches_same_organism_weak_collision_guard() -> None:
    """A globally-unmatched strong ID still falls through to the weak collision guard."""
    weak_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, systematic_name="ABC1", gene_id=weak_gene),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000099999",
        sgd_id="S000099999",
        systematic_name="ABC1",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (weak_gene,)


# --- Other conflicts -------------------------------------------------------------------


def test_sgd_id_resolves_a_ncbi_gene_id_resolves_b_same_organism_is_conflicted() -> None:
    gene_a, gene_b = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_A, sgd_id="S000000364", gene_id=gene_a),
            _candidate(organism_id=ORGANISM_A, ncbi_gene_id="852457", gene_id=gene_b),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        ncbi_gene_id="852457",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A
    assert set(result.candidate_entity_ids) == {gene_a, gene_b}


def test_sgd_id_resolves_a_candidate_has_different_ncbi_gene_id_is_conflicted() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(
                organism_id=ORGANISM_A,
                sgd_id="S000000364",
                ncbi_gene_id="852457",
                gene_id=gene_id,
            ),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        ncbi_gene_id="000000",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == gene_id
    assert "ncbi_gene_id" in result.reason


def test_strong_id_match_with_disagreeing_systematic_name_is_conflicted() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(
                organism_id=ORGANISM_A,
                sgd_id="S000000364",
                systematic_name="YBR160W",
                gene_id=gene_id,
            ),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        systematic_name="YNL999W",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == gene_id
    assert "systematic_name" in result.reason


# --- Ambiguity (strong-identifier level, defensive) ---------------------------------


def test_single_strong_identifier_multiple_candidates_is_ambiguous() -> None:
    """Defensive: the schema means to prevent this globally, but the normalizer must not
    assume it.
    """
    gene_a, gene_b = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_A, kegg_gene_id="sce:DUP1", gene_id=gene_a),
            _candidate(organism_id=ORGANISM_A, kegg_gene_id="sce:DUP1", gene_id=gene_b),
        )
    )
    identity = GeneIdentity(
        source=SourceType.KEGG, source_identifier="sce:DUP1", kegg_gene_id="sce:DUP1"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {gene_a, gene_b}


def test_duplicate_candidate_ids_do_not_manufacture_ambiguity() -> None:
    gene_id = uuid4()
    candidate = _candidate(organism_id=ORGANISM_A, sgd_id="S000000364", gene_id=gene_id)
    lookup = FakeGeneLookup(genes=(candidate, candidate))
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id


# --- Level 3 candidate generation ---------------------------------------------------


def test_symbol_only_one_candidate_is_ambiguous_not_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, symbol="ABC1", gene_id=gene_id),)
    )
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-5", symbol="ABC1")

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (gene_id,)
    assert result.matched_entity_id is None


def test_systematic_name_only_one_candidate_is_ambiguous() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, systematic_name="YBR160W", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.OTHER, source_identifier="req-6", systematic_name="YBR160W"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (gene_id,)


def test_alias_only_one_candidate_is_ambiguous() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, aliases=("CDC28_ALIAS",), gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.OTHER, source_identifier="req-7", aliases=("CDC28_ALIAS",)
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (gene_id,)


def test_multiple_weak_lookups_pointing_to_same_candidate_remain_ambiguous_not_inflated() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(
                organism_id=ORGANISM_A,
                systematic_name="YBR160W",
                symbol="CDC28",
                aliases=("CDC28_ALIAS",),
                gene_id=gene_id,
            ),
        )
    )
    identity = GeneIdentity(
        source=SourceType.OTHER,
        source_identifier="req-8",
        systematic_name="YBR160W",
        symbol="CDC28",
        aliases=("CDC28_ALIAS",),
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (gene_id,)


def test_multiple_weak_candidates_is_ambiguous_with_all_ids() -> None:
    gene_a, gene_b = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_A, symbol="ABC1", gene_id=gene_a),
            _candidate(organism_id=ORGANISM_A, systematic_name="YBR160W", gene_id=gene_b),
        )
    )
    identity = GeneIdentity(
        source=SourceType.OTHER,
        source_identifier="req-9",
        symbol="ABC1",
        systematic_name="YBR160W",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {gene_a, gene_b}


# --- NEW / creation completeness -----------------------------------------------------


def test_unmatched_strong_sgd_id_no_weak_collision_is_new() -> None:
    lookup = FakeGeneLookup(genes=())
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000099999", sgd_id="S000099999"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


def test_unmatched_strong_id_with_systematic_name_collision_is_ambiguous_not_new() -> None:
    """Collision guard: a same-organism weak-name candidate must block NEW."""
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, systematic_name="ABC1", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000099999",
        sgd_id="S000099999",
        systematic_name="ABC1",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == (gene_id,)


def test_unmatched_strong_id_with_symbol_collision_is_ambiguous() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, symbol="ABC1", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000099999",
        sgd_id="S000099999",
        symbol="ABC1",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (gene_id,)


def test_only_weak_name_zero_candidates_is_unresolved_never_new() -> None:
    lookup = FakeGeneLookup(genes=())
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-10", symbol="ABC1")

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


def test_kegg_gene_id_only_unmatched_is_unresolved_not_new() -> None:
    """kegg_gene_id is a Level 1 anchor but is not part of the documented
    creation-completeness set (docs/02_database_schema.md), so alone it cannot
    justify NEW -- no new rule is invented just to make a KEGG-only record creatable.
    """
    lookup = FakeGeneLookup(genes=())
    identity = GeneIdentity(
        source=SourceType.KEGG, source_identifier="sce:UNKNOWN", kegg_gene_id="sce:UNKNOWN"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_cross_organism_conflict_cannot_become_new() -> None:
    """A cross-organism strong-ID conflict must never resolve to NEW, regardless of
    how much creation-complete metadata is also supplied.
    """
    foreign_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_B, sgd_id="S000000364", gene_id=foreign_gene),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        symbol="ABC1",
        systematic_name="YBR160W",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW


# --- Result semantics: organism_id ---------------------------------------------------


def test_organism_id_matches_supplied_value_for_matched() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, sgd_id="S1", gene_id=gene_id),)
    )
    identity = GeneIdentity(source=SourceType.SGD, source_identifier="S1", sgd_id="S1")

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.organism_id == ORGANISM_A


def test_organism_id_matches_supplied_value_for_new() -> None:
    lookup = FakeGeneLookup(genes=())
    identity = GeneIdentity(source=SourceType.SGD, source_identifier="S1", sgd_id="S1")

    result = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_ambiguous() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_B, symbol="ABC1", gene_id=gene_id),)
    )
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-11", symbol="ABC1")

    result = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_conflicted() -> None:
    gene_a, gene_b = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=(
            _candidate(organism_id=ORGANISM_B, sgd_id="S1", gene_id=gene_a),
            _candidate(organism_id=ORGANISM_B, ncbi_gene_id="N1", gene_id=gene_b),
        )
    )
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S1", sgd_id="S1", ncbi_gene_id="N1"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_unresolved() -> None:
    lookup = FakeGeneLookup(genes=())
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-12", symbol="ABC1")

    result = normalize_gene(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_cross_organism_conflicted() -> None:
    foreign_gene = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_B, sgd_id="S1", gene_id=foreign_gene),)
    )
    identity = GeneIdentity(source=SourceType.SGD, source_identifier="S1", sgd_id="S1")

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.organism_id == ORGANISM_A


# --- Determinism ---------------------------------------------------------------------


def test_same_candidate_set_different_lookup_order_same_result() -> None:
    gene_a, gene_b = uuid4(), uuid4()
    candidate_a = _candidate(organism_id=ORGANISM_A, kegg_gene_id="sce:DUP1", gene_id=gene_a)
    candidate_b = _candidate(organism_id=ORGANISM_A, kegg_gene_id="sce:DUP1", gene_id=gene_b)
    identity = GeneIdentity(
        source=SourceType.KEGG, source_identifier="sce:DUP1", kegg_gene_id="sce:DUP1"
    )

    forward = normalize_gene(
        identity, organism_id=ORGANISM_A, lookup=FakeGeneLookup(genes=(candidate_a, candidate_b))
    )
    backward = normalize_gene(
        identity, organism_id=ORGANISM_A, lookup=FakeGeneLookup(genes=(candidate_b, candidate_a))
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_conflicting_candidate_id_order_is_deterministic() -> None:
    gene_a, gene_b = uuid4(), uuid4()
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S1", sgd_id="S1", ncbi_gene_id="N1"
    )

    forward = normalize_gene(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeGeneLookup(
            genes=(
                _candidate(organism_id=ORGANISM_A, sgd_id="S1", gene_id=gene_a),
                _candidate(organism_id=ORGANISM_A, ncbi_gene_id="N1", gene_id=gene_b),
            )
        ),
    )
    backward = normalize_gene(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeGeneLookup(
            genes=(
                _candidate(organism_id=ORGANISM_A, ncbi_gene_id="N1", gene_id=gene_b),
                _candidate(organism_id=ORGANISM_A, sgd_id="S1", gene_id=gene_a),
            )
        ),
    )

    assert (
        forward.candidate_entity_ids
        == backward.candidate_entity_ids
        == tuple(sorted((gene_a, gene_b)))
    )


# --- SGD conversion helper -----------------------------------------------------------


def test_gene_identity_from_sgd_preserves_sgd_id() -> None:
    record = _sgd_record(sgd_id="S000000364")

    identity = gene_identity_from_sgd(record)

    assert identity.source is SourceType.SGD
    assert identity.source_identifier == "S000000364"
    assert identity.sgd_id == "S000000364"


def test_gene_identity_from_sgd_preserves_systematic_name() -> None:
    record = _sgd_record(systematic_name="YBR160W")

    identity = gene_identity_from_sgd(record)

    assert identity.systematic_name == "YBR160W"


def test_gene_identity_from_sgd_maps_standard_name_to_symbol() -> None:
    record = _sgd_record(standard_name="CDC28")

    identity = gene_identity_from_sgd(record)

    assert identity.symbol == "CDC28"


def test_gene_identity_from_sgd_preserves_aliases() -> None:
    record = _sgd_record(aliases=("ALIAS_ONE", "ALIAS_TWO"))

    identity = gene_identity_from_sgd(record)

    assert identity.aliases == ("ALIAS_ONE", "ALIAS_TWO")


def test_gene_identity_from_sgd_preserves_description() -> None:
    record = _sgd_record(description="Some description")

    identity = gene_identity_from_sgd(record)

    assert identity.description == "Some description"


def test_gene_identity_from_sgd_does_not_promote_uniprot_id_into_gene_identity() -> None:
    """SGD's UniProt cross-reference stays on the source record; it is never copied
    into GeneIdentity, which has no uniprot_id field to receive it at all.
    """
    record = _sgd_record(uniprot_id="P00546")

    identity = gene_identity_from_sgd(record)

    assert not hasattr(identity, "uniprot_id")
    # The source record itself still carries it, unmutated, for later Protein
    # normalization -- this function only reads it, never alters or drops it there.
    assert record.uniprot_id == "P00546"


def test_gene_identity_from_sgd_leaves_ncbi_and_kegg_gene_id_none() -> None:
    """SGD's locus record exposes neither as a structured field."""
    record = _sgd_record()

    identity = gene_identity_from_sgd(record)

    assert identity.ncbi_gene_id is None
    assert identity.kegg_gene_id is None


# --- UniProt values never affect outcomes --------------------------------------------


def test_uniprot_values_do_not_affect_matched_outcome() -> None:
    """Two candidates that would only differ by a UniProt accession -- which does not
    exist as a Gene-identity field at all -- must not diverge in outcome.
    """
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, sgd_id="S000000364", gene_id=gene_id),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD, source_identifier="S000000364", sgd_id="S000000364"
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == gene_id


# --- Safety -----------------------------------------------------------------------


def test_gene_lookup_protocol_has_no_ec_number_or_description_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(GeneLookup, inspect.isfunction)}
    assert not any("ec_number" in name for name in method_names)
    assert not any("description" in name for name in method_names)


def test_no_fuzzy_matching_of_symbol() -> None:
    """A near-miss symbol must not match -- exact string comparison only."""
    lookup = FakeGeneLookup(genes=(_candidate(organism_id=ORGANISM_A, symbol="ABC1-like"),))
    identity = GeneIdentity(source=SourceType.OTHER, source_identifier="req-13", symbol="ABC1")

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_description_never_used_for_identity() -> None:
    """Two genes sharing a description must never collide -- description plays no role."""
    lookup = FakeGeneLookup(
        genes=(_candidate(organism_id=ORGANISM_A, sgd_id="S000000999", description="Shared text"),)
    )
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier="S000000364",
        sgd_id="S000000364",
        description="Shared text",
    )

    result = normalize_gene(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
