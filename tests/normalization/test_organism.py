"""Tests for organism identity normalization (Phase 4, Increment 2).

Pure unit tests: no database, no HTTP. ``FakeOrganismLookup`` is an
in-memory, read-only stand-in for ``app.normalization.organism.OrganismLookup``
-- there is no SQLAlchemy adapter in this increment (deferred to a later
persistence increment), consistent with ``app.normalization.organism``'s own
module docstring. Fixtures deliberately span two unrelated microbial species
(yeast and a bacterium) so no test can pass by accidentally assuming
Saccharomyces cerevisiae is a default or globally unique organism.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.models.enums import SourceType
from app.normalization.organism import (
    OrganismCandidate,
    OrganismIdentity,
    OrganismLookup,
    normalize_organism,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class FakeOrganismLookup:
    """In-memory ``OrganismLookup``: exact-match filtering over a fixed candidate list.

    Mirrors the real query semantics the schema implies -- in particular,
    ``by_scientific_name_without_strain`` only ever returns rows where
    ``strain is None``, matching ``Organism``'s partial-unique-index
    semantics (strain-less rows are never deduplicated by the database, so
    more than one can legitimately exist).
    """

    organisms: Sequence[OrganismCandidate] = ()

    def by_ncbi_taxonomy_id(self, ncbi_taxonomy_id: int) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.ncbi_taxonomy_id == ncbi_taxonomy_id]

    def by_kegg_code(self, kegg_code: str) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.kegg_code == kegg_code]

    def by_biocyc_id(self, biocyc_id: str) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.biocyc_id == biocyc_id]

    def by_scientific_name_and_strain(
        self, scientific_name: str, strain: str
    ) -> Sequence[OrganismCandidate]:
        return [
            o for o in self.organisms if o.scientific_name == scientific_name and o.strain == strain
        ]

    def by_scientific_name_without_strain(
        self, scientific_name: str
    ) -> Sequence[OrganismCandidate]:
        return [
            o for o in self.organisms if o.scientific_name == scientific_name and o.strain is None
        ]


def _candidate(
    *,
    scientific_name: str,
    strain: str | None = None,
    ncbi_taxonomy_id: int | None = None,
    kegg_code: str | None = None,
    biocyc_id: str | None = None,
    organism_id: UUID | None = None,
) -> OrganismCandidate:
    return OrganismCandidate(
        id=organism_id or uuid4(),
        scientific_name=scientific_name,
        strain=strain,
        ncbi_taxonomy_id=ncbi_taxonomy_id,
        kegg_code=kegg_code,
        biocyc_id=biocyc_id,
    )


# --- OrganismIdentity construction / validation ----------------------------------


def test_organism_identity_requires_at_least_one_anchor() -> None:
    with pytest.raises(ValueError, match="requires at least one organism identity anchor"):
        OrganismIdentity(source=SourceType.KEGG, source_identifier="eco")


def test_organism_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        OrganismIdentity(
            source=SourceType.KEGG, source_identifier="   ", scientific_name="Escherichia coli"
        )


def test_organism_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = OrganismIdentity(
        source=SourceType.KEGG,
        source_identifier="eco",
        scientific_name="  Escherichia coli  ",
        strain="   ",
    )
    assert identity.scientific_name == "Escherichia coli"
    assert identity.strain is None


def test_lookup_has_no_default_and_must_be_supplied_explicitly() -> None:
    """No global/default lookup exists -- ``lookup`` is a mandatory keyword argument."""
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-0", scientific_name="Escherichia coli"
    )
    with pytest.raises(TypeError):
        normalize_organism(identity)  # type: ignore[call-arg]


# --- Exact identity: single strong-anchor match ----------------------------------


def test_ncbi_taxonomy_id_single_candidate_matched() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli",
                strain="K-12 MG1655",
                ncbi_taxonomy_id=511145,
                organism_id=organism_id,
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI, source_identifier="511145", ncbi_taxonomy_id=511145
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == organism_id
    assert result.organism_id == organism_id


def test_kegg_code_single_candidate_matched() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli", kegg_code="eco", organism_id=organism_id
            ),
        )
    )
    identity = OrganismIdentity(source=SourceType.KEGG, source_identifier="eco", kegg_code="eco")

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == organism_id
    assert result.organism_id == organism_id


def test_biocyc_id_single_candidate_matched() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli", biocyc_id="ECOLI", organism_id=organism_id
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.BIOCYC, source_identifier="ECOLI", biocyc_id="ECOLI"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == organism_id
    assert result.organism_id == organism_id


def test_scientific_name_and_strain_single_candidate_matched() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Saccharomyces cerevisiae", strain="BY4741", organism_id=organism_id
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.SGD,
        source_identifier="BY4741-batch",
        scientific_name="Saccharomyces cerevisiae",
        strain="BY4741",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == organism_id
    assert result.organism_id == organism_id


# --- Generality: arbitrary microbes, distinct strains ----------------------------


def test_same_scientific_name_different_strains_are_distinct() -> None:
    s288c_id, by4741_id = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Saccharomyces cerevisiae", strain="S288C", organism_id=s288c_id
            ),
            _candidate(
                scientific_name="Saccharomyces cerevisiae", strain="BY4741", organism_id=by4741_id
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.SGD,
        source_identifier="req-1",
        scientific_name="Saccharomyces cerevisiae",
        strain="BY4741",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == by4741_id
    assert result.matched_entity_id != s288c_id


def test_two_different_microbial_species_resolved_independently() -> None:
    """The normalizer carries no yeast-specific or single-organism assumption."""
    yeast_id, ecoli_id = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Saccharomyces cerevisiae",
                ncbi_taxonomy_id=559292,
                organism_id=yeast_id,
            ),
            _candidate(
                scientific_name="Escherichia coli", ncbi_taxonomy_id=511145, organism_id=ecoli_id
            ),
        )
    )

    yeast_result = normalize_organism(
        OrganismIdentity(
            source=SourceType.NCBI, source_identifier="559292", ncbi_taxonomy_id=559292
        ),
        lookup=lookup,
    )
    ecoli_result = normalize_organism(
        OrganismIdentity(
            source=SourceType.NCBI, source_identifier="511145", ncbi_taxonomy_id=511145
        ),
        lookup=lookup,
    )

    assert yeast_result.matched_entity_id == yeast_id
    assert ecoli_result.matched_entity_id == ecoli_id
    assert yeast_result.matched_entity_id != ecoli_result.matched_entity_id


# --- Ambiguity --------------------------------------------------------------------


def test_same_external_id_on_two_rows_is_ambiguous() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(scientific_name="Escherichia coli", kegg_code="eco", organism_id=id_a),
            _candidate(
                scientific_name="Escherichia coli",
                strain="O157:H7",
                kegg_code="eco",
                organism_id=id_b,
            ),
        )
    )
    identity = OrganismIdentity(source=SourceType.KEGG, source_identifier="eco", kegg_code="eco")

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


def test_multiple_strainless_rows_same_scientific_name_is_ambiguous() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(scientific_name="Escherichia coli", organism_id=id_a),
            _candidate(scientific_name="Escherichia coli", organism_id=id_b),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-2", scientific_name="Escherichia coli"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


# --- Conflicts ----------------------------------------------------------------------


def test_ncbi_taxonomy_id_resolves_a_kegg_code_resolves_b_is_conflicted() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli", ncbi_taxonomy_id=511145, organism_id=id_a
            ),
            _candidate(scientific_name="Escherichia coli", kegg_code="eco", organism_id=id_b),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="511145",
        ncbi_taxonomy_id=511145,
        kegg_code="eco",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


def test_exact_id_resolves_but_supplied_strain_disagrees_is_conflicted() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Saccharomyces cerevisiae",
                strain="S288C",
                ncbi_taxonomy_id=559292,
                organism_id=organism_id,
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="559292",
        ncbi_taxonomy_id=559292,
        strain="BY4741",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == organism_id
    assert result.organism_id == organism_id
    assert "strain" in result.reason


def test_scientific_name_strain_resolves_a_other_strong_id_resolves_b_is_conflicted() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Saccharomyces cerevisiae", strain="BY4741", organism_id=id_a
            ),
            _candidate(
                scientific_name="Saccharomyces cerevisiae", biocyc_id="YEAST", organism_id=id_b
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.BIOCYC,
        source_identifier="YEAST",
        scientific_name="Saccharomyces cerevisiae",
        strain="BY4741",
        biocyc_id="YEAST",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


# --- New / Unresolved ---------------------------------------------------------------


def test_strong_scientific_name_and_strain_with_no_existing_candidate_is_new() -> None:
    lookup = FakeOrganismLookup(organisms=())
    identity = OrganismIdentity(
        source=SourceType.SGD,
        source_identifier="req-3",
        scientific_name="Saccharomyces cerevisiae",
        strain="CEN.PK",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_strong_external_identifier_with_no_candidate_and_scientific_name_is_new() -> None:
    lookup = FakeOrganismLookup(organisms=())
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_scientific_name_only_with_no_candidate_is_unresolved_not_new() -> None:
    """A bare scientific_name with no strain and no other anchor is the weak path.

    Increment instructions, decision rule 9: weak/insufficient text must
    produce UNRESOLVED, not NEW, since no documented rule decides when a
    species name alone is "specific enough" to safely create a row.
    """
    lookup = FakeOrganismLookup(organisms=())
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-4", scientific_name="Escherichia coli"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_scientific_name_only_single_candidate_is_matched_unchanged_weak_path() -> None:
    """The true weak path (no strong external identifier supplied at all) is unaffected
    by the collision-guard correction below: a single strain-less name match still
    becomes MATCHED there, since nothing else was claimed for it to fail to
    corroborate.
    """
    existing_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(_candidate(scientific_name="Escherichia coli", organism_id=existing_id),)
    )
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-8", scientific_name="Escherichia coli"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == existing_id
    assert result.organism_id == existing_id


# --- Collision guard: strong ID absent from DB, but scientific_name is not ------------
#
# A strong external identifier (ncbi_taxonomy_id/kegg_code/biocyc_id) matching zero
# existing organisms must not become NEW if an existing strain-less organism already
# carries this exact scientific_name -- that organism may simply be missing the
# supplied identifier, and persistence must not be left to discover the overlap.


def test_strong_id_no_match_and_zero_name_candidates_is_new() -> None:
    lookup = FakeOrganismLookup(organisms=())
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_strong_id_no_match_but_one_name_candidate_is_ambiguous_not_new_not_matched() -> None:
    existing_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(_candidate(scientific_name="Escherichia coli", organism_id=existing_id),)
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == (existing_id,)


def test_strong_id_no_match_and_multiple_name_candidates_is_ambiguous_with_all_ids() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(scientific_name="Escherichia coli", organism_id=id_a),
            _candidate(scientific_name="Escherichia coli", organism_id=id_b),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.KEGG,
        source_identifier="eco2",
        kegg_code="eco2",
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {id_a, id_b}


def test_collision_guard_duplicate_candidate_ids_do_not_inflate_ambiguity() -> None:
    """A lookup returning the same strain-less row twice must not manufacture
    a larger candidate set than the canonical one.
    """
    existing_id = uuid4()
    candidate = _candidate(scientific_name="Escherichia coli", organism_id=existing_id)
    lookup = FakeOrganismLookup(organisms=(candidate, candidate))
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (existing_id,)


def test_collision_guard_ambiguous_results_leave_organism_id_none() -> None:
    existing_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(_candidate(scientific_name="Escherichia coli", organism_id=existing_id),)
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.organism_id is None
    assert result.matched_entity_id is None


def test_collision_guard_uses_candidate_synonym_match_method() -> None:
    """Name-based evidence alone is a Level-3 signal here (never MATCHED on its own)."""
    existing_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(_candidate(scientific_name="Escherichia coli", organism_id=existing_id),)
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM


def test_collision_guard_reports_but_does_not_silently_resolve_explicit_id_mismatch() -> None:
    """An existing candidate carrying a different (not merely absent) value for the
    same strong identifier must not be silently resolved either way.

    This asserts only the permanent safety invariant: the incoming record must
    not become NEW (risking an avoidable duplicate) and must not be silently
    MATCHED to a candidate whose own identifier actually disagrees -- the
    candidate must stay visible in ``candidate_entity_ids`` regardless.

    It deliberately does *not* assert a specific status (AMBIGUOUS today):
    ``_resolve_new_vs_name_collision``'s general conflict-detection logic
    (``_describe_metadata_disagreement``) only ever compares
    ``scientific_name``/``strain``, never external-identifier fields, so it does
    not currently distinguish "field is absent" from "field holds a different
    value" for a name-only collision candidate. A later Phase 4 conflict-
    integration increment, once the fuller cross-reference/conflict machinery
    exists, may reasonably decide this exact-disagreement case should instead
    become CONFLICTED -- this test must not block that refinement.
    """
    existing_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli", ncbi_taxonomy_id=456, organism_id=existing_id
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="123",
        ncbi_taxonomy_id=123,
        scientific_name="Escherichia coli",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is not NormalizationStatus.NEW
    assert result.status is not NormalizationStatus.MATCHED
    assert existing_id in result.candidate_entity_ids


def test_collision_guard_does_not_fire_when_strain_is_supplied() -> None:
    """scientific_name + strain keeps using the existing (scientific_name, strain)
    strong-anchor reconciliation, unaffected by this correction: an unrelated
    strain-less row sharing the scientific_name must not turn a clean NEW into an
    AMBIGUOUS collision here, since nothing here claims to be that strain-less row.
    """
    lookup = FakeOrganismLookup(
        organisms=(_candidate(scientific_name="Escherichia coli", organism_id=uuid4()),)
    )
    identity = OrganismIdentity(
        source=SourceType.NCBI,
        source_identifier="99999",
        ncbi_taxonomy_id=99999,
        scientific_name="Escherichia coli",
        strain="K-12 MG1655",
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


# --- Determinism ----------------------------------------------------------------------


def test_same_candidate_set_different_lookup_order_same_result() -> None:
    id_a, id_b = uuid4(), uuid4()
    candidate_a = _candidate(scientific_name="Escherichia coli", kegg_code="eco", organism_id=id_a)
    candidate_b = _candidate(
        scientific_name="Escherichia coli", strain="O157:H7", kegg_code="eco", organism_id=id_b
    )
    identity = OrganismIdentity(source=SourceType.KEGG, source_identifier="eco", kegg_code="eco")

    forward = normalize_organism(
        identity, lookup=FakeOrganismLookup(organisms=(candidate_a, candidate_b))
    )
    backward = normalize_organism(
        identity, lookup=FakeOrganismLookup(organisms=(candidate_b, candidate_a))
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_duplicate_candidate_ids_do_not_manufacture_ambiguity() -> None:
    """A lookup returning the same row twice (e.g. from a join) must not become AMBIGUOUS."""
    organism_id = uuid4()
    candidate = _candidate(
        scientific_name="Escherichia coli", kegg_code="eco", organism_id=organism_id
    )
    lookup = FakeOrganismLookup(organisms=(candidate, candidate))
    identity = OrganismIdentity(source=SourceType.KEGG, source_identifier="eco", kegg_code="eco")

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == organism_id


# --- Safety -------------------------------------------------------------------------


def test_no_fuzzy_matching_of_scientific_name() -> None:
    """A near-miss scientific_name must not match -- exact string comparison only."""
    lookup = FakeOrganismLookup(organisms=(_candidate(scientific_name="Escherichia coli K-12"),))
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-5", scientific_name="Escherichia coli"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_lookup_candidates_are_never_mutated() -> None:
    candidate = _candidate(scientific_name="Escherichia coli", kegg_code="eco")
    organisms = (candidate,)
    lookup = FakeOrganismLookup(organisms=organisms)
    identity = OrganismIdentity(source=SourceType.KEGG, source_identifier="eco", kegg_code="eco")

    normalize_organism(identity, lookup=lookup)

    assert lookup.organisms == organisms


def test_fake_lookup_satisfies_organism_lookup_protocol() -> None:
    assert isinstance(FakeOrganismLookup(organisms=()), OrganismLookup)


# --- Result semantics ------------------------------------------------------------------


def test_matched_organism_id_equals_matched_entity_id() -> None:
    organism_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=(
            _candidate(
                scientific_name="Escherichia coli", biocyc_id="ECOLI", organism_id=organism_id
            ),
        )
    )
    identity = OrganismIdentity(
        source=SourceType.BIOCYC, source_identifier="ECOLI", biocyc_id="ECOLI"
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.organism_id == result.matched_entity_id == organism_id


@pytest.mark.parametrize(
    "identity_kwargs",
    [
        {"scientific_name": "Escherichia coli", "strain": "K-12"},
        {"scientific_name": "Escherichia coli"},
    ],
    ids=["scientific_name_and_strain_new", "scientific_name_only_unresolved"],
)
def test_new_and_unresolved_leave_organism_id_none(identity_kwargs: dict[str, str]) -> None:
    lookup = FakeOrganismLookup(organisms=())
    identity = OrganismIdentity(
        source=SourceType.OTHER, source_identifier="req-6", **identity_kwargs
    )

    result = normalize_organism(identity, lookup=lookup)

    assert result.status in (NormalizationStatus.NEW, NormalizationStatus.UNRESOLVED)
    assert result.organism_id is None
    assert result.matched_entity_id is None
