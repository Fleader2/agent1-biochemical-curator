"""Tests for ``app.experiment_recommendation.types``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.experiment_recommendation.types import (
    ExperimentClass,
    ExperimentRecommendation,
    ExperimentRecommendationContext,
    RecommendationStatus,
)
from app.knowledge_gaps.types import GapSeverity, GapType


def _recommendation(**overrides) -> ExperimentRecommendation:
    merged = {
        "knowledge_gap_identity": "kg-v1:abc",
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "gap_severity": GapSeverity.HIGH,
        "status": RecommendationStatus.RECOMMENDED,
        "objective": "Establish the chemical participants and stoichiometry of this reaction.",
        "rationale": (
            "REACTION_WITHOUT_PARTICIPANTS -> kg-exp-reaction-validation-v1 -> RECOMMENDED"
        ),
        "template_id": "kg-exp-reaction-validation-v1",
        "template_version": "v1",
        "experiment_class": ExperimentClass.REACTION_VALIDATION,
        "success_criterion": (
            "Reactants, products, and stoichiometry are experimentally established."
        ),
        "reason_codes": ("REACTION_HAS_ZERO_PARTICIPANTS",),
    } | overrides
    return ExperimentRecommendation(**merged)


def test_valid_recommendation():
    rec = _recommendation()
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.REACTION_VALIDATION


def test_recommended_without_experiment_class_rejected():
    with pytest.raises(ValueError):
        _recommendation(experiment_class=None)


def test_recommended_without_success_criterion_rejected():
    with pytest.raises(ValueError):
        _recommendation(success_criterion=None)


def test_not_applicable_may_omit_experiment_class():
    rec = _recommendation(
        status=RecommendationStatus.NOT_APPLICABLE,
        experiment_class=None,
        success_criterion=None,
        reason_codes=("PROVENANCE_GAP_NOT_AN_EXPERIMENTAL_GAP",),
    )
    assert rec.experiment_class is None
    assert rec.success_criterion is None


def test_not_applicable_with_experiment_class_rejected():
    with pytest.raises(ValueError):
        _recommendation(status=RecommendationStatus.NOT_APPLICABLE, success_criterion=None)


def test_requires_human_design_may_omit_experiment_class():
    rec = _recommendation(
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        experiment_class=None,
        success_criterion=None,
        reason_codes=("NO_STRUCTURED_CANDIDATE_PROTEINS",),
    )
    assert rec.experiment_class is None


def test_insufficient_information_may_omit_experiment_class():
    rec = _recommendation(
        status=RecommendationStatus.INSUFFICIENT_INFORMATION,
        experiment_class=None,
        success_criterion=None,
        reason_codes=("INSUFFICIENT_CONTEXT",),
    )
    assert rec.experiment_class is None


def test_template_id_required():
    with pytest.raises(ValueError):
        _recommendation(template_id="   ")


def test_template_version_required():
    with pytest.raises(ValueError):
        _recommendation(template_version="")


def test_knowledge_gap_identity_required():
    with pytest.raises(ValueError):
        _recommendation(knowledge_gap_identity="")


def test_objective_required():
    with pytest.raises(ValueError):
        _recommendation(objective="  ")


def test_rationale_required():
    with pytest.raises(ValueError):
        _recommendation(rationale="")


def test_reason_codes_must_not_be_empty():
    with pytest.raises(ValueError):
        _recommendation(reason_codes=())


def test_reason_codes_are_deterministic_tuple():
    codes = ("REACTION_HAS_ZERO_PARTICIPANTS",)
    rec = _recommendation(reason_codes=codes)
    assert rec.reason_codes == codes
    assert isinstance(rec.reason_codes, tuple)


def test_rejects_wrong_gap_type():
    with pytest.raises(TypeError):
        _recommendation(gap_type="REACTION_WITHOUT_PARTICIPANTS")


def test_rejects_wrong_severity():
    with pytest.raises(TypeError):
        _recommendation(gap_severity="HIGH")


def test_rejects_wrong_status():
    with pytest.raises(TypeError):
        _recommendation(status="RECOMMENDED")


def test_rejects_wrong_experiment_class():
    with pytest.raises(TypeError):
        _recommendation(experiment_class="REACTION_VALIDATION")


def test_rejects_non_uuid_target_entity_id():
    with pytest.raises(TypeError):
        _recommendation(target_entity_id="not-a-uuid")


def test_rejects_non_uuid_in_supporting_ids():
    with pytest.raises(TypeError):
        _recommendation(supporting_claim_ids=("not-a-uuid",))


def test_blank_optional_text_becomes_none():
    rec = _recommendation(target_entity_type="  ", required_measurement="  ")
    assert rec.target_entity_type is None
    assert rec.required_measurement is None


def test_is_frozen():
    rec = _recommendation()
    with pytest.raises(AttributeError):
        rec.status = RecommendationStatus.NOT_APPLICABLE  # type: ignore[misc]


def test_no_forbidden_fields_exist():
    """The dataclass must not carry a cost/schedule/vendor/protocol field."""
    field_names = set(ExperimentRecommendation.__dataclass_fields__)
    forbidden = {"cost", "schedule", "vendor", "protocol_steps", "reagents", "hypothesis"}
    assert field_names.isdisjoint(forbidden)


# --- ExperimentRecommendationContext ------------------------------------------------


def test_context_defaults_to_empty():
    context = ExperimentRecommendationContext()
    assert context.claims == {}
    assert context.evidence == {}
    assert context.reaction_candidate_protein_ids == {}


def test_context_is_immutable_mapping():
    context = ExperimentRecommendationContext(claims={uuid4(): object()})
    with pytest.raises(TypeError):
        context.claims[uuid4()] = object()  # type: ignore[index]


def test_context_rejects_non_mapping():
    with pytest.raises(TypeError):
        ExperimentRecommendationContext(claims=[])  # type: ignore[arg-type]


def test_context_is_frozen():
    context = ExperimentRecommendationContext()
    with pytest.raises(AttributeError):
        context.claims = {}  # type: ignore[misc]
