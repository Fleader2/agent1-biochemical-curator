"""Tests for ``app.confidence.types``: self-validation of the data contract."""

from __future__ import annotations

import pytest

from app.confidence.policy import EntityResolutionQuality, OrganismAssessment, ScoringStatus
from app.confidence.types import SingleEvidenceAssessment
from app.extraction.types import Directness
from app.models.enums import EvidenceType


def _assessment(**overrides) -> SingleEvidenceAssessment:
    merged = {
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "evidence_base_score": 45,
        "scoring_status": ScoringStatus.SCORED_BASE,
        "directness": Directness.AUTHORS_OBSERVED,
        "subject_resolution": EntityResolutionQuality.RESOLVED,
        "object_resolution": None,
        "organism_resolution": OrganismAssessment.NOT_STATED,
        "compartment_resolution": None,
    } | overrides
    return SingleEvidenceAssessment(**merged)


def test_rejects_wrong_evidence_type():
    with pytest.raises(TypeError):
        _assessment(evidence_type="DIRECT_BIOCHEMICAL")


def test_rejects_wrong_scoring_status_type():
    with pytest.raises(TypeError):
        _assessment(scoring_status="SCORED_BASE")


def test_rejects_wrong_directness_type():
    with pytest.raises(TypeError):
        _assessment(directness="AUTHORS_OBSERVED")


def test_rejects_wrong_subject_resolution_type():
    with pytest.raises(TypeError):
        _assessment(subject_resolution="RESOLVED")


def test_rejects_wrong_object_resolution_type_when_not_none():
    with pytest.raises(TypeError):
        _assessment(object_resolution="RESOLVED")


def test_rejects_wrong_organism_resolution_type():
    with pytest.raises(TypeError):
        _assessment(organism_resolution="NOT_STATED")


def test_evidence_base_score_none_requires_unscored_status():
    with pytest.raises(ValueError):
        _assessment(evidence_base_score=None, scoring_status=ScoringStatus.SCORED_BASE)


def test_evidence_base_score_populated_requires_scored_status():
    with pytest.raises(ValueError):
        _assessment(evidence_base_score=45, scoring_status=ScoringStatus.UNSCORED_EVIDENCE_TYPE)


def test_unscored_construction_is_accepted():
    result = _assessment(
        evidence_type=EvidenceType.REVIEW,
        evidence_base_score=None,
        scoring_status=ScoringStatus.UNSCORED_EVIDENCE_TYPE,
    )
    assert result.evidence_base_score is None
    assert result.scoring_status is ScoringStatus.UNSCORED_EVIDENCE_TYPE


def test_evidence_base_score_out_of_range_rejected():
    with pytest.raises(ValueError):
        _assessment(evidence_base_score=101)


def test_evidence_base_score_negative_rejected():
    with pytest.raises(ValueError):
        _assessment(evidence_base_score=-1)


def test_object_resolution_none_is_accepted():
    result = _assessment(object_resolution=None)
    assert result.object_resolution is None


def test_object_resolution_not_applicable_is_accepted():
    result = _assessment(object_resolution=EntityResolutionQuality.NOT_APPLICABLE)
    assert result.object_resolution is EntityResolutionQuality.NOT_APPLICABLE


def test_compartment_resolution_none_is_accepted():
    result = _assessment(compartment_resolution=None)
    assert result.compartment_resolution is None


def test_rejects_blank_reason_code_entry():
    with pytest.raises(ValueError):
        _assessment(reason_codes=("EVIDENCE_DIRECT_BIOCHEMICAL", "   "))


def test_is_frozen():
    result = _assessment()
    with pytest.raises(AttributeError):
        result.evidence_base_score = 10  # type: ignore[misc]


def test_valid_construction_defaults():
    result = _assessment()
    assert result.reason_codes == ()
    assert result.explanation == ""


def test_has_no_final_score_or_confidence_class_fields():
    """Structural: Increment 17 produces no final Claim confidence."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(SingleEvidenceAssessment)}
    assert "score" not in field_names
    assert "confidence_class" not in field_names
    assert "base_score" not in field_names
    assert "directness_component" not in field_names
    assert "organism_component" not in field_names
    assert "entity_resolution_component" not in field_names
    assert "penalties" not in field_names
    assert "bonuses" not in field_names
