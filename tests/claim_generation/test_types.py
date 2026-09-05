"""Tests for ``app.claim_generation.types``: self-validation of the data contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.claim_generation.errors import ClaimValidationError
from app.claim_generation.types import CandidateClaim, CandidateEntityReference, EntityKind
from app.extraction.types import Directness
from app.models.enums import EvidenceType, SourceType
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from tests.claim_generation.fixtures import ACTIVATION_EXTRACTION


def _matched_result(**overrides) -> NormalizationResult:
    merged = {
        "status": NormalizationStatus.MATCHED,
        "source": SourceType.PUBMED,
        "source_identifier": "PMID:1",
        "entity_type": "gene",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "matched_entity_id": uuid4(),
    } | overrides
    return NormalizationResult(**merged)


def _reference(**overrides) -> CandidateEntityReference:
    merged = {"original_text": "FadD", "entity_kind": EntityKind.GENE} | overrides
    return CandidateEntityReference(**merged)


def _claim(**overrides) -> CandidateClaim:
    merged = {
        "source": ACTIVATION_EXTRACTION.source,
        "source_identifier": ACTIVATION_EXTRACTION.source_identifier,
        "evidence_extraction": ACTIVATION_EXTRACTION,
        "subject": _reference(),
        "predicate": "activates",
        "supporting_span": ACTIVATION_EXTRACTION.span,
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": Directness.AUTHORS_OBSERVED,
    } | overrides
    return CandidateClaim(**merged)


# --- CandidateEntityReference -------------------------------------------------


def test_reference_requires_non_empty_text():
    with pytest.raises(ClaimValidationError):
        _reference(original_text="")


def test_reference_rejects_wrong_entity_kind_type():
    with pytest.raises(TypeError):
        _reference(entity_kind="GENE")


def test_reference_rejects_normalized_id_without_result():
    with pytest.raises(ClaimValidationError):
        _reference(normalized_id=uuid4())


def test_reference_rejects_normalized_id_mismatched_with_matched_result():
    result = _matched_result()
    with pytest.raises(ClaimValidationError):
        _reference(normalization_result=result, normalized_id=uuid4())


def test_reference_accepts_normalized_id_matching_matched_result():
    matched_id = uuid4()
    result = _matched_result(matched_entity_id=matched_id)
    reference = _reference(normalization_result=result, normalized_id=matched_id)
    assert reference.normalized_id == matched_id


def test_reference_rejects_normalized_id_when_status_is_not_matched():
    unresolved = NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        entity_type="gene",
        match_method=MatchMethod.NONE,
    )
    with pytest.raises(ClaimValidationError):
        _reference(normalization_result=unresolved, normalized_id=uuid4())


def test_reference_allows_unresolved_result_with_no_id():
    unresolved = NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        entity_type="gene",
        match_method=MatchMethod.NONE,
    )
    reference = _reference(normalization_result=unresolved)
    assert reference.normalized_id is None


# --- CandidateClaim ------------------------------------------------------------


def test_claim_requires_predicate():
    with pytest.raises(ClaimValidationError):
        _claim(predicate="")


def test_claim_requires_source_matches_extraction():
    with pytest.raises(ValueError, match="source"):
        _claim(source=SourceType.KEGG)


def test_claim_requires_source_identifier_matches_extraction():
    with pytest.raises(ValueError, match="source_identifier"):
        _claim(source_identifier="PMID:999")


def test_claim_requires_supporting_span_matches_extraction():
    from app.extraction.types import SourceSpan

    other_span = SourceSpan(
        document_identifier=ACTIVATION_EXTRACTION.source_identifier,
        character_start=0,
        character_end=4,
        quoted_text="FadR",
    )
    with pytest.raises(ValueError, match="supporting_span"):
        _claim(supporting_span=other_span)


def test_claim_rejects_non_reference_subject():
    with pytest.raises(TypeError):
        _claim(subject="not a reference")


def test_claim_organism_must_be_typed_as_organism():
    bad_organism = _reference(entity_kind=EntityKind.GENE)
    with pytest.raises(ValueError, match="entity_kind"):
        _claim(organism=bad_organism)


def test_claim_compartment_must_be_typed_as_compartment():
    bad_compartment = _reference(entity_kind=EntityKind.GENE)
    with pytest.raises(ValueError, match="entity_kind"):
        _claim(compartment=bad_compartment)


def test_claim_organism_typed_as_organism_is_accepted():
    organism_ref = _reference(original_text="Escherichia coli", entity_kind=EntityKind.ORGANISM)
    claim = _claim(organism=organism_ref)
    assert claim.organism is organism_ref


def test_claim_rejects_value_numeric_without_value_text():
    from decimal import Decimal

    with pytest.raises(ClaimValidationError):
        _claim(value_numeric=Decimal("5.4"), value_text=None)


def test_claim_rejects_value_unit_without_any_value():
    with pytest.raises(ClaimValidationError):
        _claim(value_unit="mM")


def test_claim_allows_value_text_and_numeric_together():
    from decimal import Decimal

    claim = _claim(value_text="5.4", value_numeric=Decimal("5.4"), value_unit="fold")
    assert claim.value_numeric == Decimal("5.4")
    assert claim.value_text == "5.4"


def test_claim_rejects_blank_qualifier():
    with pytest.raises(ValueError, match="qualifiers"):
        _claim(qualifiers=("   ",))


def test_claim_is_frozen():
    claim = _claim()
    with pytest.raises(AttributeError):
        claim.predicate = "something else"  # type: ignore[misc]


def test_claim_strain_and_category_blank_becomes_none():
    claim = _claim(strain="  ", claim_category="   ")
    assert claim.strain is None
    assert claim.claim_category is None
