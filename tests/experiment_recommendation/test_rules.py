"""Tests for ``app.experiment_recommendation.rules``: the GapType policy.

Exercises ``build_recommendation``/``_GapView`` directly -- the internal
bridge shape both public entry points convert into -- for full control
over reason codes and supporting ids per sub-case, independent of how a
real ``KnowledgeGapCandidate`` happens to be constructed upstream.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.experiment_recommendation.errors import UnsupportedGapTypeError
from app.experiment_recommendation.rules import _GapView, build_recommendation
from app.experiment_recommendation.types import (
    ExperimentClass,
    ExperimentRecommendationContext,
    RecommendationStatus,
)
from app.knowledge_gaps.types import GapSeverity, GapType
from app.models.enums import EvidenceType
from tests.experiment_recommendation.fixtures import make_claim, make_context, make_evidence


def _view(**overrides) -> _GapView:
    merged = {
        "knowledge_gap_identity": "kg-v1:test",
        "gap_type": GapType.ISOLATED_COMPOUND,
        "severity": GapSeverity.LOW,
        "entity_type": "compound",
        "entity_id": uuid4(),
        "supporting_claim_ids": (),
        "supporting_evidence_ids": (),
        "reason_codes": (),
    } | overrides
    return _GapView(**merged)


# --- CONFLICTING_CLAIMS --------------------------------------------------------------


def test_conflicting_claims_value_disagreement_recommends_replication_without_bias():
    claim_ids = (uuid4(), uuid4())
    view = _view(
        gap_type=GapType.CONFLICTING_CLAIMS,
        severity=GapSeverity.HIGH,
        entity_type="claim",
        entity_id=claim_ids[0],
        supporting_claim_ids=claim_ids,
        reason_codes=("LITERAL_VALUE_DISAGREEMENT",),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.REPLICATION_EXPERIMENT
    assert "discriminate" in rec.objective.lower()
    assert "claim a" not in rec.objective.lower()
    assert "prove" not in rec.objective.lower()
    assert set(rec.supporting_claim_ids) == set(claim_ids)


def test_conflicting_claims_status_only_requires_human_design():
    view = _view(
        gap_type=GapType.CONFLICTING_CLAIMS,
        severity=GapSeverity.HIGH,
        entity_type="claim",
        entity_id=uuid4(),
        supporting_claim_ids=(uuid4(),),
        reason_codes=("CLAIM_STATUS_CONFLICTED",),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.REQUIRES_HUMAN_DESIGN
    assert rec.experiment_class is None


# --- LOW_CONFIDENCE_CLAIM --------------------------------------------------------------


def test_low_confidence_claim_primary_evidence_recommends_replication():
    claim = make_claim()
    make_evidence(
        claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, directness="AUTHORS_OBSERVED"
    )
    context = make_context(claims=(claim,))
    view = _view(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.REPLICATION_EXPERIMENT


def test_low_confidence_claim_computational_only_compartment_object_recommends_localization():
    claim = make_claim(object_type="COMPARTMENT", object_id=uuid4())
    make_evidence(claim, evidence_type=EvidenceType.COMPUTATIONAL, directness="AUTHORS_PROPOSED")
    context = make_context(claims=(claim,))
    view = _view(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.PROTEIN_LOCALIZATION_ASSAY


def test_low_confidence_claim_ambiguous_semantics_requires_human_design():
    claim = make_claim(object_type=None)
    make_evidence(claim, evidence_type=EvidenceType.COMPUTATIONAL, directness="AUTHORS_PROPOSED")
    context = make_context(claims=(claim,))
    view = _view(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.REQUIRES_HUMAN_DESIGN


def test_low_confidence_claim_missing_from_context_is_insufficient_information():
    view = _view(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=uuid4(),
        supporting_claim_ids=(uuid4(),),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.INSUFFICIENT_INFORMATION


# --- SINGLE_SOURCE_SUPPORT --------------------------------------------------------------


def test_single_source_primary_experimental_support_recommends_replication():
    claim = make_claim()
    evidence = make_evidence(
        claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, directness="AUTHORS_OBSERVED"
    )
    context = make_context(claims=(claim,), evidence=(evidence,))
    view = _view(
        gap_type=GapType.SINGLE_SOURCE_SUPPORT,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
        supporting_evidence_ids=(evidence.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.REPLICATION_EXPERIMENT


def test_single_source_review_only_support_does_not_become_replication():
    claim = make_claim()
    evidence = make_evidence(
        claim, evidence_type=EvidenceType.REVIEW, directness="REVIEW_SUMMARIZES"
    )
    context = make_context(claims=(claim,), evidence=(evidence,))
    view = _view(
        gap_type=GapType.SINGLE_SOURCE_SUPPORT,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
        supporting_evidence_ids=(evidence.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.experiment_class is not ExperimentClass.REPLICATION_EXPERIMENT
    assert rec.status is RecommendationStatus.REQUIRES_HUMAN_DESIGN


# --- NO_PRIMARY_EXPERIMENTAL_EVIDENCE --------------------------------------------------


def test_no_primary_evidence_compartment_object_maps_deterministically():
    claim = make_claim(object_type="COMPARTMENT", object_id=uuid4())
    context = make_context(claims=(claim,))
    view = _view(
        gap_type=GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.PROTEIN_LOCALIZATION_ASSAY


def test_no_primary_evidence_unsupported_literal_predicate_requires_human_design():
    claim = make_claim(object_type=None, predicate="is somehow related to")
    context = make_context(claims=(claim,))
    view = _view(
        gap_type=GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.REQUIRES_HUMAN_DESIGN


def test_no_primary_evidence_no_fuzzy_verb_interpretation():
    """Two different free-text predicates must never produce different
    experiment classes -- only the closed object_type signal may."""
    claim_a = make_claim(object_type=None, predicate="upregulates")
    claim_b = make_claim(object_type=None, predicate="downregulates")
    context = make_context(claims=(claim_a, claim_b))
    rec_a = build_recommendation(
        _view(
            gap_type=GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,
            entity_type="claim",
            entity_id=claim_a.id,
            supporting_claim_ids=(claim_a.id,),
        ),
        context,
    )
    rec_b = build_recommendation(
        _view(
            gap_type=GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,
            entity_type="claim",
            entity_id=claim_b.id,
            supporting_claim_ids=(claim_b.id,),
        ),
        context,
    )
    assert rec_a.status == rec_b.status == RecommendationStatus.REQUIRES_HUMAN_DESIGN
    assert rec_a.template_id == rec_b.template_id


# --- MISSING_PUBLICATION ------------------------------------------------------------------


def test_missing_publication_is_not_applicable_experimental_recommendation():
    view = _view(
        gap_type=GapType.MISSING_PUBLICATION,
        severity=GapSeverity.LOW,
        entity_type="claim",
        entity_id=uuid4(),
        supporting_claim_ids=(uuid4(),),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.NOT_APPLICABLE
    assert rec.experiment_class is None
    assert "provenance" in rec.objective.lower()
    assert "laboratory" not in rec.objective.lower()
    assert "experiment" not in rec.objective.lower()


# --- MISSING_EXPERIMENTAL_CONTEXT ----------------------------------------------------------


def test_missing_experimental_context_asks_for_characterization_without_inventing_values():
    view = _view(
        gap_type=GapType.MISSING_EXPERIMENTAL_CONTEXT,
        severity=GapSeverity.LOW,
        entity_type="claim",
        entity_id=uuid4(),
        supporting_claim_ids=(uuid4(),),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.EXPERIMENTAL_CONTEXT_CHARACTERIZATION
    assert rec.experimental_context_requirements
    for requirement in rec.experimental_context_requirements:
        assert requirement.islower()  # field names, not invented values like "30 C"


# --- REACTION_WITHOUT_ENZYME ----------------------------------------------------------------


def test_reaction_without_enzyme_no_candidate_requires_human_design():
    view = _view(
        gap_type=GapType.REACTION_WITHOUT_ENZYME,
        severity=GapSeverity.LOW,
        entity_type="reaction",
        entity_id=uuid4(),
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.REQUIRES_HUMAN_DESIGN
    assert rec.experiment_class is None


def test_reaction_without_enzyme_with_structured_candidates_permits_assay():
    reaction_id = uuid4()
    protein_id = uuid4()
    context = ExperimentRecommendationContext(
        reaction_candidate_protein_ids={reaction_id: (protein_id,)}
    )
    view = _view(
        gap_type=GapType.REACTION_WITHOUT_ENZYME,
        severity=GapSeverity.LOW,
        entity_type="reaction",
        entity_id=reaction_id,
    )
    rec = build_recommendation(view, context)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.ENZYME_SUBSTRATE_ASSAY
    # No protein id is invented into the recommendation text.
    assert str(protein_id) not in rec.objective


def test_reaction_without_enzyme_no_invented_protein_id_in_recommendation():
    view = _view(
        gap_type=GapType.REACTION_WITHOUT_ENZYME, entity_type="reaction", entity_id=uuid4()
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.target_entity_type == "reaction"
    assert not rec.supporting_evidence_ids  # nothing fabricated


# --- PROTEIN_WITHOUT_REACTION ----------------------------------------------------------------


def test_protein_without_reaction_does_not_infer_catalytic_function():
    view = _view(
        gap_type=GapType.PROTEIN_WITHOUT_REACTION, entity_type="protein", entity_id=uuid4()
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.NOT_APPLICABLE
    assert rec.experiment_class is None


# --- GENE_WITHOUT_PROTEIN ----------------------------------------------------------------------


def test_gene_without_protein_does_not_equate_missing_row_with_biological_absence():
    view = _view(gap_type=GapType.GENE_WITHOUT_PROTEIN, entity_type="gene", entity_id=uuid4())
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.NOT_APPLICABLE
    assert "proteomics" not in rec.objective.lower()


# --- REACTION_WITHOUT_PARTICIPANTS ------------------------------------------------------------


def test_reaction_without_participants_recommends_structural_characterization_only():
    view = _view(
        gap_type=GapType.REACTION_WITHOUT_PARTICIPANTS, entity_type="reaction", entity_id=uuid4()
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.experiment_class is ExperimentClass.REACTION_VALIDATION
    assert not rec.supporting_evidence_ids
    assert not rec.supporting_claim_ids  # no substrate/product ever fabricated


# --- ISOLATED_COMPOUND -------------------------------------------------------------------------


def test_isolated_compound_does_not_automatically_recommend_metabolomics():
    view = _view(gap_type=GapType.ISOLATED_COMPOUND, entity_type="compound", entity_id=uuid4())
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.status is RecommendationStatus.NOT_APPLICABLE
    assert rec.experiment_class is None


# --- Severity preserved, no invented priority ---------------------------------------------------


def test_severity_preserved_verbatim():
    view = _view(
        gap_type=GapType.ISOLATED_COMPOUND, severity=GapSeverity.CRITICAL, entity_id=uuid4()
    )
    rec = build_recommendation(view, ExperimentRecommendationContext())
    assert rec.gap_severity is GapSeverity.CRITICAL


def test_no_priority_field_exists_on_recommendation():
    assert "priority" not in ExperimentRecommendationContext.__dataclass_fields__


# --- Success criteria: deterministic, no invented thresholds -------------------------------------


def test_success_criteria_contain_no_numeric_thresholds():
    import re

    from app.experiment_recommendation.templates import TEMPLATE_REGISTRY

    for template in TEMPLATE_REGISTRY.values():
        if template.success_criterion is not None:
            assert not re.search(r"\d", template.success_criterion)


# --- Determinism ---------------------------------------------------------------------


def test_repeated_recommendation_is_identical():
    view = _view(gap_type=GapType.ISOLATED_COMPOUND, entity_id=uuid4())
    first = build_recommendation(view, ExperimentRecommendationContext())
    second = build_recommendation(view, ExperimentRecommendationContext())
    assert first == second


# --- Unsupported gap type -------------------------------------------------------------


def test_unsupported_gap_type_raises(monkeypatch):
    """``build_recommendation`` itself must raise, not silently fall back to
    an arbitrary template, when a GapType has no registered handler."""
    from app.experiment_recommendation import rules as rules_module

    monkeypatch.delitem(rules_module._GAP_TYPE_HANDLERS, GapType.ISOLATED_COMPOUND)
    view = _view(gap_type=GapType.ISOLATED_COMPOUND, entity_id=uuid4())
    with pytest.raises(UnsupportedGapTypeError):
        build_recommendation(view, ExperimentRecommendationContext())
