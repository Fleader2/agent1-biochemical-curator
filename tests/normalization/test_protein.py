"""Tests for protein identity normalization (Phase 4, Increment 5).

Pure unit tests: no database, no HTTP, no live UniProt/SGD/BRENDA access.
``FakeProteinLookup`` is an in-memory, read-only stand-in for
``app.normalization.protein.ProteinLookup`` -- there is no SQLAlchemy adapter
in this increment, consistent with ``app.normalization.protein``'s own module
docstring. It mirrors the real lookup contract exactly: ``by_uniprot_id``
searches **globally** (matching ``Protein.uniprot_id``'s lack of a uniqueness
constraint -- candidates can legitimately span organisms), while ``by_name``
is **organism-scoped**. Every test uses one of two fixed, distinct organism
UUIDs so no test can pass by accidentally assuming a single/default/global
organism.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.models.enums import SourceType
from app.normalization.protein import (
    ProteinCandidate,
    ProteinIdentity,
    ProteinLookup,
    normalize_protein,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit

ORGANISM_A = UUID("11111111-1111-1111-1111-111111111111")
ORGANISM_B = UUID("22222222-2222-2222-2222-222222222222")


@dataclass(frozen=True, slots=True)
class FakeProteinLookup:
    """In-memory ``ProteinLookup``: global exact UniProt lookup, organism-scoped name lookup."""

    proteins: Sequence[ProteinCandidate] = ()

    def by_uniprot_id(self, uniprot_id: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.uniprot_id == uniprot_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.organism_id == organism_id and p.name == name]


class LeakyNameProteinLookup:
    """A ``ProteinLookup`` whose ``by_name`` ignores organism scope entirely.

    Used only to prove ``normalize_protein`` rejects cross-organism leakage
    from ``by_name``, which is contractually required to filter by
    ``organism_id`` itself. ``by_uniprot_id`` is legitimately global, so it
    is implemented correctly here -- only ``by_name`` is broken.
    """

    def __init__(self, proteins: Sequence[ProteinCandidate]) -> None:
        self.proteins = proteins

    def by_uniprot_id(self, uniprot_id: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.uniprot_id == uniprot_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.name == name]


def _candidate(
    *,
    organism_id: UUID,
    uniprot_id: str | None = None,
    name: str = "Test Protein",
    gene_id: UUID | None = None,
    ec_number: str | None = None,
    protein_id: UUID | None = None,
) -> ProteinCandidate:
    return ProteinCandidate(
        id=protein_id or uuid4(),
        organism_id=organism_id,
        uniprot_id=uniprot_id,
        name=name,
        gene_id=gene_id,
        ec_number=ec_number,
    )


# --- ProteinIdentity construction / validation ------------------------------------


def test_protein_identity_requires_at_least_one_identity_signal() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ProteinIdentity(source=SourceType.UNIPROT, source_identifier="req-1")


def test_protein_identity_gene_id_and_ec_number_alone_are_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ProteinIdentity(
            source=SourceType.UNIPROT,
            source_identifier="req-2",
            gene_id=uuid4(),
            ec_number="1.1.1.1",
        )


def test_protein_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        ProteinIdentity(source=SourceType.UNIPROT, source_identifier="   ", uniprot_id="P12345")


def test_protein_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P12345",
        uniprot_id="  P12345  ",
        name="   ",
    )
    assert identity.uniprot_id == "P12345"
    assert identity.name is None


def test_protein_identity_preserves_isoform_suffix_literally() -> None:
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345-2", uniprot_id="P12345-2"
    )
    assert identity.uniprot_id == "P12345-2"


def test_protein_identity_has_no_aliases_field() -> None:
    """Protein has no aliases_json/synonym column -- ProteinIdentity has no such field."""
    assert "aliases" not in inspect.signature(ProteinIdentity).parameters


def test_lookup_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )
    with pytest.raises(TypeError):
        normalize_protein(identity, organism_id=ORGANISM_A)  # type: ignore[call-arg]


def test_organism_id_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )
    lookup = FakeProteinLookup(proteins=())
    with pytest.raises(TypeError):
        normalize_protein(identity, lookup=lookup)  # type: ignore[call-arg]


def test_normalize_protein_organism_id_is_keyword_only_with_no_default() -> None:
    parameters = inspect.signature(normalize_protein).parameters
    assert parameters["organism_id"].default is inspect.Parameter.empty
    assert parameters["organism_id"].kind is inspect.Parameter.KEYWORD_ONLY


# --- ProteinLookup API shape: global uniprot_id, organism-scoped name --------------


def test_uniprot_id_lookup_does_not_accept_organism_id() -> None:
    params = list(inspect.signature(ProteinLookup.by_uniprot_id).parameters)
    assert "organism_id" not in params


def test_name_lookup_requires_organism_id_first() -> None:
    params = list(inspect.signature(ProteinLookup.by_name).parameters)
    assert params[1] == "organism_id"


def test_protein_lookup_has_no_by_ec_number_method() -> None:
    assert not hasattr(ProteinLookup, "by_ec_number")


def test_protein_lookup_has_no_by_gene_id_method() -> None:
    assert not hasattr(ProteinLookup, "by_gene_id")


def test_protein_lookup_has_no_by_alias_method() -> None:
    assert not hasattr(ProteinLookup, "by_alias")


# --- Organism scoping (name lookup only) --------------------------------------------


def test_same_protein_name_in_two_organisms_does_not_collide() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_A, name="Kinase X", protein_id=protein_a),
            _candidate(organism_id=ORGANISM_B, name="Kinase X", protein_id=protein_b),
        )
    )
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-3", name="Kinase X")

    result_a = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)
    result_b = normalize_protein(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result_a.candidate_entity_ids == (protein_a,)
    assert result_b.candidate_entity_ids == (protein_b,)


def test_cross_organism_leakage_from_name_lookup_is_rejected() -> None:
    other_org_protein = _candidate(organism_id=ORGANISM_B, name="Kinase X")
    lookup = LeakyNameProteinLookup(proteins=[other_org_protein])
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-4", name="Kinase X")

    with pytest.raises(ValueError, match="ProteinLookup returned candidate"):
        normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)


# --- Exact UniProt match -------------------------------------------------------------


def test_uniprot_id_single_candidate_in_requested_organism_matched() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_A, uniprot_id="P12345", protein_id=protein_id),)
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == protein_id
    assert result.organism_id == ORGANISM_A


def test_exact_isoform_accession_single_candidate_matched() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_A, uniprot_id="P12345-2", protein_id=protein_id),)
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345-2", uniprot_id="P12345-2"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == protein_id


def test_base_accession_does_not_match_isoform_accession() -> None:
    """P12345 must not match a candidate whose uniprot_id is P12345-2, or vice versa."""
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_A, uniprot_id="P12345-2"),)
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is not NormalizationStatus.MATCHED
    assert result.matched_entity_id is None


def test_isoform_accession_does_not_match_base_accession() -> None:
    lookup = FakeProteinLookup(proteins=(_candidate(organism_id=ORGANISM_A, uniprot_id="P12345"),))
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345-2", uniprot_id="P12345-2"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is not NormalizationStatus.MATCHED
    assert result.matched_entity_id is None


# --- Cross-organism UniProt conflict -------------------------------------------------


def test_uniprot_id_already_attached_to_protein_in_different_organism_is_conflicted() -> None:
    foreign_protein = uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_B, uniprot_id="P12345", protein_id=foreign_protein),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert foreign_protein in result.candidate_entity_ids
    assert result.organism_id == ORGANISM_A


def test_cross_organism_uniprot_conflict_cannot_become_new_even_with_full_metadata() -> None:
    foreign_protein = uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_B, uniprot_id="P12345", protein_id=foreign_protein),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P12345",
        uniprot_id="P12345",
        name="Kinase X",
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW


# --- Multiple exact candidates (Protein.uniprot_id is not DB-unique) ----------------


def test_duplicate_candidate_ids_do_not_manufacture_ambiguity() -> None:
    protein_id = uuid4()
    candidate = _candidate(organism_id=ORGANISM_A, uniprot_id="P12345", protein_id=protein_id)
    lookup = FakeProteinLookup(proteins=(candidate, candidate))
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == protein_id


def test_same_accession_on_two_rows_in_requested_organism_is_ambiguous() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_A, uniprot_id="P12345", protein_id=protein_a),
            _candidate(organism_id=ORGANISM_A, uniprot_id="P12345", protein_id=protein_b),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {protein_a, protein_b}


def test_same_accession_across_different_organisms_is_conflicted() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_A, uniprot_id="P12345", protein_id=protein_a),
            _candidate(organism_id=ORGANISM_B, uniprot_id="P12345", protein_id=protein_b),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert set(result.candidate_entity_ids) == {protein_a, protein_b}


def test_same_accession_on_two_rows_both_outside_requested_organism_is_conflicted() -> None:
    """Every candidate is in ORGANISM_B (not the requested ORGANISM_A) -- still CONFLICTED,
    not silently ignored, since the requested organism has no rightful claim to it.
    """
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_B, uniprot_id="P12345", protein_id=protein_a),
            _candidate(organism_id=ORGANISM_B, uniprot_id="P12345", protein_id=protein_b),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P12345", uniprot_id="P12345"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert set(result.candidate_entity_ids) == {protein_a, protein_b}


# --- Weak (name) candidate generation ------------------------------------------------


def test_name_only_one_candidate_is_ambiguous_never_matched() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_A, name="Kinase X", protein_id=protein_id),)
    )
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-5", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (protein_id,)
    assert result.matched_entity_id is None


def test_multiple_name_candidates_is_ambiguous_with_all_ids() -> None:
    """Two distinct rows can legitimately share one name (name carries no uniqueness
    constraint at all).
    """
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(organism_id=ORGANISM_A, name="Kinase X", protein_id=protein_a),
            _candidate(organism_id=ORGANISM_A, name="Kinase X", protein_id=protein_b),
        )
    )
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-6", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {protein_a, protein_b}


def test_weak_only_zero_candidates_is_unresolved_never_new() -> None:
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-7", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


# --- NEW / collision guard -----------------------------------------------------------


def test_unmatched_uniprot_id_with_name_no_weak_collision_is_new() -> None:
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P99999", uniprot_id="P99999", name="Kinase X"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


def test_unmatched_uniprot_id_with_exact_same_organism_name_collision_is_ambiguous_not_new() -> (
    None
):
    """Collision guard: a same-organism name candidate must block NEW."""
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_A, name="Kinase X", protein_id=protein_id),)
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P99999", uniprot_id="P99999", name="Kinase X"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == (protein_id,)


def test_unmatched_uniprot_id_without_name_is_unresolved() -> None:
    """Incomplete creation data: uniprot_id unmatched, no name supplied at all."""
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P99999", uniprot_id="P99999"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


# --- Gene relationship: never identity-relevant --------------------------------------


def test_gene_id_alone_cannot_match_a_protein() -> None:
    """Because gene_id is not part of ProteinIdentity's anchor requirement, supplying it
    alone with no uniprot_id/name is a construction-time validation error, not a lookup.
    """
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ProteinIdentity(source=SourceType.OTHER, source_identifier="req-8", gene_id=uuid4())


def test_gene_id_alone_cannot_make_a_protein_new() -> None:
    gene_id = uuid4()
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P99999", uniprot_id="P99999", gene_id=gene_id
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    # name is absent, so creation completeness (name-only rule) fails --
    # gene_id being present does not substitute for it.
    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_one_gene_id_may_appear_on_multiple_distinct_protein_identities_without_conflict() -> None:
    """A single Gene may correspond to more than one Protein -- carrying the same
    gene_id on two ProteinIdentity values with different UniProt IDs must not itself
    create any conflict, since gene_id is never compared.
    """
    shared_gene_id = uuid4()
    lookup = FakeProteinLookup(proteins=())
    identity_one = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P00001",
        uniprot_id="P00001",
        name="Isoform A",
        gene_id=shared_gene_id,
    )
    identity_two = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P00002",
        uniprot_id="P00002",
        name="Isoform B",
        gene_id=shared_gene_id,
    )

    result_one = normalize_protein(identity_one, organism_id=ORGANISM_A, lookup=lookup)
    result_two = normalize_protein(identity_two, organism_id=ORGANISM_A, lookup=lookup)

    assert result_one.status is NormalizationStatus.NEW
    assert result_two.status is NormalizationStatus.NEW


def test_candidate_with_different_gene_id_does_not_prevent_matched() -> None:
    """An existing candidate's gene_id disagreeing with the incoming gene_id is never
    an identity conflict -- gene_id is inert relationship metadata only.
    """
    protein_id = uuid4()
    candidate_gene = uuid4()
    incoming_gene = uuid4()
    assert candidate_gene != incoming_gene
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(
                organism_id=ORGANISM_A,
                uniprot_id="P12345",
                gene_id=candidate_gene,
                protein_id=protein_id,
            ),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P12345",
        uniprot_id="P12345",
        gene_id=incoming_gene,
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == protein_id


# --- EC number: never identity-relevant -----------------------------------------------


def test_ec_number_alone_cannot_construct_an_identity() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ProteinIdentity(source=SourceType.OTHER, source_identifier="req-9", ec_number="1.1.1.1")


def test_two_candidates_may_share_ec_number_without_being_considered_same_protein() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(
                organism_id=ORGANISM_A,
                uniprot_id="P00001",
                ec_number="1.1.1.1",
                protein_id=protein_a,
            ),
            _candidate(
                organism_id=ORGANISM_A,
                uniprot_id="P00002",
                ec_number="1.1.1.1",
                protein_id=protein_b,
            ),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P00001", uniprot_id="P00001"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == protein_a


def test_ec_number_mismatch_does_not_independently_create_conflict() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(
            _candidate(
                organism_id=ORGANISM_A,
                uniprot_id="P12345",
                ec_number="1.1.1.1",
                protein_id=protein_id,
            ),
        )
    )
    identity = ProteinIdentity(
        source=SourceType.UNIPROT,
        source_identifier="P12345",
        uniprot_id="P12345",
        ec_number="2.2.2.2",
    )

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == protein_id


# --- Result semantics: organism_id ---------------------------------------------------


def test_organism_id_matches_supplied_value_for_matched() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_B, uniprot_id="P1", protein_id=protein_id),)
    )
    identity = ProteinIdentity(source=SourceType.UNIPROT, source_identifier="P1", uniprot_id="P1")

    result = normalize_protein(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_new() -> None:
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(
        source=SourceType.UNIPROT, source_identifier="P1", uniprot_id="P1", name="X"
    )

    result = normalize_protein(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_ambiguous() -> None:
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_B, name="Kinase X", protein_id=protein_id),)
    )
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-10", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_unresolved() -> None:
    lookup = FakeProteinLookup(proteins=())
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-11", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.organism_id == ORGANISM_B


def test_organism_id_matches_supplied_value_for_cross_organism_conflicted() -> None:
    foreign_protein = uuid4()
    lookup = FakeProteinLookup(
        proteins=(_candidate(organism_id=ORGANISM_B, uniprot_id="P1", protein_id=foreign_protein),)
    )
    identity = ProteinIdentity(source=SourceType.UNIPROT, source_identifier="P1", uniprot_id="P1")

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.organism_id == ORGANISM_A


# --- Determinism ---------------------------------------------------------------------


def test_same_candidate_set_different_lookup_order_same_result() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    candidate_a = _candidate(organism_id=ORGANISM_A, uniprot_id="P1", protein_id=protein_a)
    candidate_b = _candidate(organism_id=ORGANISM_A, uniprot_id="P1", protein_id=protein_b)
    identity = ProteinIdentity(source=SourceType.UNIPROT, source_identifier="P1", uniprot_id="P1")

    forward = normalize_protein(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeProteinLookup(proteins=(candidate_a, candidate_b)),
    )
    backward = normalize_protein(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeProteinLookup(proteins=(candidate_b, candidate_a)),
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_conflicting_candidate_id_order_is_deterministic() -> None:
    protein_a, protein_b = uuid4(), uuid4()
    identity = ProteinIdentity(source=SourceType.UNIPROT, source_identifier="P1", uniprot_id="P1")

    forward = normalize_protein(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeProteinLookup(
            proteins=(
                _candidate(organism_id=ORGANISM_A, uniprot_id="P1", protein_id=protein_a),
                _candidate(organism_id=ORGANISM_B, uniprot_id="P1", protein_id=protein_b),
            )
        ),
    )
    backward = normalize_protein(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeProteinLookup(
            proteins=(
                _candidate(organism_id=ORGANISM_B, uniprot_id="P1", protein_id=protein_b),
                _candidate(organism_id=ORGANISM_A, uniprot_id="P1", protein_id=protein_a),
            )
        ),
    )

    assert (
        forward.candidate_entity_ids
        == backward.candidate_entity_ids
        == tuple(sorted((protein_a, protein_b)))
    )


# --- Safety -----------------------------------------------------------------------


def test_no_fuzzy_matching_of_name() -> None:
    """A near-miss name must not match -- exact string comparison only."""
    lookup = FakeProteinLookup(proteins=(_candidate(organism_id=ORGANISM_A, name="Kinase X-like"),))
    identity = ProteinIdentity(source=SourceType.OTHER, source_identifier="req-12", name="Kinase X")

    result = normalize_protein(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
