"""Tests for the shared entity-normalization result types.

No database, no HTTP, no fixtures -- these are plain Python types.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from app.models.enums import SourceType
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

pytestmark = pytest.mark.unit

_EXPECTED_STATUS_MEMBERS = {"MATCHED", "NEW", "AMBIGUOUS", "CONFLICTED", "UNRESOLVED"}
_EXPECTED_MATCH_METHOD_MEMBERS = {
    "EXACT_IDENTIFIER",
    "EXPLICIT_CROSS_REFERENCE",
    "CANDIDATE_SYNONYM",
    "NONE",
}


def test_normalization_status_has_exactly_the_expected_members() -> None:
    assert {member.value for member in NormalizationStatus} == _EXPECTED_STATUS_MEMBERS


def test_match_method_has_exactly_the_expected_members() -> None:
    assert {member.value for member in MatchMethod} == _EXPECTED_MATCH_METHOD_MEMBERS


# --- Valid construction, one per status ---------------------------------------


def test_matched_result_requires_matched_entity_id_and_no_candidates() -> None:
    entity_id = uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.MATCHED,
        source=SourceType.SGD,
        source_identifier="S000000364",
        entity_type="gene",
        match_method=MatchMethod.EXACT_IDENTIFIER,
        matched_entity_id=entity_id,
    )
    assert result.matched_entity_id == entity_id
    assert result.candidate_entity_ids == ()


def test_new_result_carries_neither_matched_entity_id_nor_candidates() -> None:
    result = NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.PUBMED,
        source_identifier="90000001",
        entity_type="publication",
        match_method=MatchMethod.NONE,
    )
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()


def test_unresolved_result_carries_neither_matched_entity_id_nor_candidates() -> None:
    result = NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=SourceType.BRENDA,
        source_identifier="1.1.1.1",
        entity_type="protein",
        match_method=MatchMethod.NONE,
        reason="EC number alone does not identify a unique protein",
    )
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()


def test_ambiguous_result_with_multiple_candidates_and_no_match() -> None:
    candidates = (uuid4(), uuid4())
    result = NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=SourceType.KEGG,
        source_identifier="D-Glucose",
        entity_type="compound",
        match_method=MatchMethod.CANDIDATE_SYNONYM,
        candidate_entity_ids=candidates,
    )
    assert result.candidate_entity_ids == candidates
    assert result.matched_entity_id is None


def test_conflicted_with_matched_entity_id_only_is_valid() -> None:
    """One exact identifier anchor resolves a row, but incoming metadata disagrees with it."""
    entity_id = uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.CONFLICTED,
        source=SourceType.SGD,
        source_identifier="S000000364",
        entity_type="gene",
        match_method=MatchMethod.EXACT_IDENTIFIER,
        matched_entity_id=entity_id,
        reason="systematic name disagrees with the existing Gene row",
    )
    assert result.matched_entity_id == entity_id
    assert result.candidate_entity_ids == ()


def test_conflicted_with_multiple_candidates_and_no_match_is_valid() -> None:
    """Two authoritative identifiers on one incoming record resolve to different entities.

    E.g. a PubMed record whose PMID resolves to Publication A but whose DOI
    already belongs to a different existing Publication B.
    """
    publication_a = uuid4()
    publication_b = uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.CONFLICTED,
        source=SourceType.PUBMED,
        source_identifier="90000001",
        entity_type="publication",
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=(publication_a, publication_b),
        reason="PMID resolves to one Publication but DOI already belongs to another",
    )
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == (publication_a, publication_b)


# --- organism_id: explicit, optional, never defaulted from global state --------


def test_organism_id_defaults_to_none() -> None:
    result = NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.PUBMED,
        source_identifier="90000001",
        entity_type="publication",
        match_method=MatchMethod.NONE,
    )
    assert result.organism_id is None


def test_organism_id_can_be_supplied_explicitly() -> None:
    organism_id = uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.SGD,
        source_identifier="S000099999",
        entity_type="gene",
        match_method=MatchMethod.NONE,
        organism_id=organism_id,
    )
    assert result.organism_id == organism_id


# --- Invalid construction: __post_init__ invariants ----------------------------


def test_matched_without_matched_entity_id_raises() -> None:
    with pytest.raises(ValueError, match="MATCHED requires matched_entity_id"):
        NormalizationResult(
            status=NormalizationStatus.MATCHED,
            source=SourceType.SGD,
            source_identifier="S000000364",
            entity_type="gene",
            match_method=MatchMethod.EXACT_IDENTIFIER,
        )


def test_matched_with_candidates_raises() -> None:
    with pytest.raises(ValueError, match="must not carry candidate_entity_ids"):
        NormalizationResult(
            status=NormalizationStatus.MATCHED,
            source=SourceType.SGD,
            source_identifier="S000000364",
            entity_type="gene",
            match_method=MatchMethod.EXACT_IDENTIFIER,
            matched_entity_id=uuid4(),
            candidate_entity_ids=(uuid4(),),
        )


def test_candidate_synonym_paired_with_matched_raises() -> None:
    """A Level-3 synonym match is inherently too weak to ever justify MATCHED."""
    with pytest.raises(ValueError, match="CANDIDATE_SYNONYM must never be paired with MATCHED"):
        NormalizationResult(
            status=NormalizationStatus.MATCHED,
            source=SourceType.KEGG,
            source_identifier="D-Glucose",
            entity_type="compound",
            match_method=MatchMethod.CANDIDATE_SYNONYM,
            matched_entity_id=uuid4(),
        )


def test_conflicted_with_neither_matched_entity_id_nor_candidates_raises() -> None:
    """CONFLICTED requires *some* entity context -- neither is not enough."""
    with pytest.raises(ValueError, match="CONFLICTED requires matched_entity_id or"):
        NormalizationResult(
            status=NormalizationStatus.CONFLICTED,
            source=SourceType.PUBMED,
            source_identifier="10.1000/xyz",
            entity_type="publication",
            match_method=MatchMethod.EXACT_IDENTIFIER,
        )


def test_ambiguous_with_matched_entity_id_raises() -> None:
    with pytest.raises(ValueError, match="AMBIGUOUS must not carry matched_entity_id"):
        NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=SourceType.KEGG,
            source_identifier="D-Glucose",
            entity_type="compound",
            match_method=MatchMethod.CANDIDATE_SYNONYM,
            matched_entity_id=uuid4(),
            candidate_entity_ids=(uuid4(), uuid4()),
        )


def test_ambiguous_with_zero_candidates_raises() -> None:
    with pytest.raises(ValueError, match="requires at least one candidate_entity_id"):
        NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=SourceType.KEGG,
            source_identifier="D-Glucose",
            entity_type="compound",
            match_method=MatchMethod.CANDIDATE_SYNONYM,
        )


def test_ambiguous_with_exactly_one_candidate_is_valid() -> None:
    """NormalizationStatus.AMBIGUOUS is a scientific-identity verdict, not a
    candidate-count fact: a Level-3 synonym/name lookup can find exactly one
    candidate that is still insufficient evidence for MATCHED. This is
    deliberately looser than identifiers.CandidateSetState.AMBIGUOUS (2+ by
    count alone).
    """
    candidate = uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=SourceType.KEGG,
        source_identifier="D-Glucose",
        entity_type="compound",
        match_method=MatchMethod.CANDIDATE_SYNONYM,
        candidate_entity_ids=(candidate,),
    )
    assert result.candidate_entity_ids == (candidate,)
    assert result.matched_entity_id is None


@pytest.mark.parametrize("status", [NormalizationStatus.NEW, NormalizationStatus.UNRESOLVED])
def test_new_and_unresolved_with_matched_entity_id_raises(status: NormalizationStatus) -> None:
    with pytest.raises(ValueError, match="must not carry matched_entity_id"):
        NormalizationResult(
            status=status,
            source=SourceType.PUBMED,
            source_identifier="90000001",
            entity_type="publication",
            match_method=MatchMethod.NONE,
            matched_entity_id=uuid4(),
        )


@pytest.mark.parametrize("status", [NormalizationStatus.NEW, NormalizationStatus.UNRESOLVED])
def test_new_and_unresolved_with_candidates_raises(status: NormalizationStatus) -> None:
    with pytest.raises(ValueError, match="must not carry candidate_entity_ids"):
        NormalizationResult(
            status=status,
            source=SourceType.PUBMED,
            source_identifier="90000001",
            entity_type="publication",
            match_method=MatchMethod.NONE,
            candidate_entity_ids=(uuid4(),),
        )


def test_empty_source_identifier_raises() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        NormalizationResult(
            status=NormalizationStatus.NEW,
            source=SourceType.PUBMED,
            source_identifier="   ",
            entity_type="publication",
            match_method=MatchMethod.NONE,
        )


def test_empty_entity_type_raises() -> None:
    with pytest.raises(ValueError, match="entity_type must not be empty"):
        NormalizationResult(
            status=NormalizationStatus.NEW,
            source=SourceType.PUBMED,
            source_identifier="90000001",
            entity_type="",
            match_method=MatchMethod.NONE,
        )


def test_result_is_frozen() -> None:
    result = NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.PUBMED,
        source_identifier="90000001",
        entity_type="publication",
        match_method=MatchMethod.NONE,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.reason = "attempted mutation"  # type: ignore[misc]
