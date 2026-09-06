"""Tests for ``app.confidence.aggregate_types``: self-validation of the aggregate data contract."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.confidence.aggregate_policy import (
    ConflictSeverity,
    ExperimentalRelevance,
    OrganismRelevance,
)
from app.confidence.aggregate_types import (
    AggregateClaimConfidence,
    ContributionBreakdown,
    EvidenceContribution,
)
from app.confidence.policy import ScoringStatus
from app.confidence.scoring import assess_single_evidence_claim
from app.models.enums import ConfidenceClass, EvidenceType
from tests.confidence.helpers import make_claim


def _contribution(**overrides) -> EvidenceContribution:
    claim = overrides.pop("candidate_claim", None) or make_claim(organism_text=None)
    assessment = overrides.pop("single_evidence_assessment", None) or assess_single_evidence_claim(
        claim
    )
    merged = {
        "candidate_claim": claim,
        "single_evidence_assessment": assessment,
        "publication_identifier": "PMID:1",
        "source_identifier": "PMID:1",
    } | overrides
    return EvidenceContribution(**merged)


def _breakdown(**overrides) -> ContributionBreakdown:
    merged = {
        "source_identifier": "PMID:1",
        "publication_identifier": "PMID:1",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "evidence_base_score": 45,
        "organism_relevance": OrganismRelevance.UNKNOWN,
        "experimental_relevance": ExperimentalRelevance.UNKNOWN,
        "is_duplicate": False,
        "numerically_eligible": True,
        "ineligibility_reasons": (),
        "independence_group": "PMID:1",
        "counted_for_replication": True,
        "conflict_status": ConflictSeverity.NONE,
        "adjusted_contribution": Decimal(45),
        "weight": Decimal(1),
        "weighted_contribution": Decimal(45),
        "cumulative_score": Decimal(45),
    } | overrides
    return ContributionBreakdown(**merged)


def _aggregate(**overrides) -> AggregateClaimConfidence:
    breakdown = overrides.pop("contribution_breakdown", (_breakdown(),))
    merged = {
        "score": 45,
        "confidence_class": ConfidenceClass.LOW,
        "base_evidence_total": 45,
        "replication_bonus": 0,
        "conflict_penalty": 0,
        "organism_modifier_summary": (),
        "experimental_modifier_summary": (),
        "evidence_count": 1,
        "independent_evidence_count": 1,
        "dependent_evidence_count": 0,
        "reason_codes": ("AGGREGATE_BASE_EVIDENCE_APPLIED",),
        "contribution_breakdown": breakdown,
    } | overrides
    return AggregateClaimConfidence(**merged)


# --- EvidenceContribution --------------------------------------------------------


def test_contribution_rejects_wrong_claim_type():
    with pytest.raises(TypeError):
        _contribution(candidate_claim="not a claim")


def test_contribution_rejects_mismatched_evidence_type():
    claim = make_claim(evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, organism_text=None)
    other_claim = make_claim(evidence_type=EvidenceType.GENETIC, organism_text=None)
    mismatched_assessment = assess_single_evidence_claim(other_claim)
    with pytest.raises(ValueError, match="evidence_type"):
        _contribution(candidate_claim=claim, single_evidence_assessment=mismatched_assessment)


def test_contribution_rejects_blank_publication_identifier():
    with pytest.raises(ValueError):
        _contribution(publication_identifier="   ")


def test_contribution_rejects_wrong_conflict_status_type():
    with pytest.raises(TypeError):
        _contribution(conflict_status="NONE")


def test_contribution_valid_construction_defaults():
    contribution = _contribution()
    assert contribution.organism_relevance is OrganismRelevance.UNKNOWN
    assert contribution.experimental_relevance is ExperimentalRelevance.UNKNOWN
    assert contribution.conflict_status is ConflictSeverity.NONE
    assert contribution.independence_group is None


# --- ContributionBreakdown --------------------------------------------------------


def test_breakdown_ineligible_requires_reasons():
    with pytest.raises(ValueError):
        _breakdown(numerically_eligible=False, ineligibility_reasons=())


def test_breakdown_eligible_rejects_reasons():
    with pytest.raises(ValueError):
        _breakdown(numerically_eligible=True, ineligibility_reasons=("EVIDENCE_TYPE_UNSCORED",))


def test_breakdown_ineligible_rejects_adjusted_contribution():
    with pytest.raises(ValueError):
        _breakdown(
            numerically_eligible=False,
            ineligibility_reasons=("EVIDENCE_TYPE_UNSCORED",),
            adjusted_contribution=45,
        )


def _ineligible_breakdown(**overrides) -> ContributionBreakdown:
    merged = {
        "numerically_eligible": False,
        "ineligibility_reasons": ("EVIDENCE_TYPE_UNSCORED",),
        "adjusted_contribution": None,
        "weight": None,
        "weighted_contribution": None,
        "cumulative_score": None,
        "evidence_base_score": None,
    } | overrides
    return _breakdown(**merged)


def test_breakdown_valid_ineligible_construction():
    breakdown = _ineligible_breakdown()
    assert breakdown.adjusted_contribution is None
    assert breakdown.weight is None
    assert breakdown.weighted_contribution is None
    assert breakdown.cumulative_score is None


def test_breakdown_weighting_fields_must_all_be_none_or_all_populated():
    with pytest.raises(ValueError):
        _breakdown(weight=None)  # adjusted_contribution/weighted/cumulative still populated


def test_breakdown_negative_adjusted_contribution_rejected():
    with pytest.raises(ValueError):
        _breakdown(adjusted_contribution=Decimal(-1))


def test_breakdown_weight_out_of_range_rejected():
    with pytest.raises(ValueError):
        _breakdown(weight=Decimal("1.5"))


def test_breakdown_zero_weight_rejected():
    """Weight must be in (0, 1] -- a counted contribution always has a positive weight."""
    with pytest.raises(ValueError):
        _breakdown(weight=Decimal(0))


# --- AggregateClaimConfidence -----------------------------------------------------


def test_aggregate_none_score_requires_unknown_class():
    with pytest.raises(ValueError):
        _aggregate(score=None, confidence_class=ConfidenceClass.LOW)


def test_aggregate_none_score_with_unknown_class_accepted():
    result = _aggregate(
        score=None,
        confidence_class=ConfidenceClass.UNKNOWN,
        base_evidence_total=None,
        reason_codes=("AGGREGATE_NO_ELIGIBLE_EVIDENCE",),
        contribution_breakdown=(_ineligible_breakdown(),),
    )
    assert result.score is None
    assert result.confidence_class is ConfidenceClass.UNKNOWN


def test_aggregate_score_must_match_computed_class():
    with pytest.raises(ValueError):
        _aggregate(score=45, confidence_class=ConfidenceClass.HIGH)


def test_aggregate_score_none_requires_base_evidence_total_none():
    with pytest.raises(ValueError):
        _aggregate(score=None, confidence_class=ConfidenceClass.UNKNOWN, base_evidence_total=45)


def test_aggregate_replication_bonus_exceeds_cap_rejected():
    with pytest.raises(ValueError):
        _aggregate(replication_bonus=15)


def test_aggregate_negative_replication_bonus_rejected():
    with pytest.raises(ValueError):
        _aggregate(replication_bonus=-1)


def test_aggregate_negative_conflict_penalty_rejected():
    with pytest.raises(ValueError):
        _aggregate(conflict_penalty=-1)


def test_aggregate_counts_must_not_exceed_evidence_count():
    with pytest.raises(ValueError):
        _aggregate(evidence_count=1, independent_evidence_count=2, dependent_evidence_count=0)


def test_aggregate_contribution_breakdown_length_must_match_evidence_count():
    with pytest.raises(ValueError):
        _aggregate(evidence_count=2, contribution_breakdown=(_breakdown(),))


def test_aggregate_rejects_non_breakdown_entries():
    with pytest.raises(TypeError):
        _aggregate(contribution_breakdown=("not a breakdown",))


def test_aggregate_is_frozen():
    result = _aggregate()
    with pytest.raises(AttributeError):
        result.score = 10  # type: ignore[misc]


def test_aggregate_algorithm_version_defaults_to_confidence_v1():
    result = _aggregate()
    assert result.algorithm_version == "confidence-v1"


def test_aggregate_algorithm_version_rejects_blank():
    with pytest.raises(ValueError):
        _aggregate(algorithm_version="   ")


def test_aggregate_scoring_status_not_used_directly():
    """Sanity: ScoringStatus remains an Increment 17 concept, not duplicated here."""
    result = _aggregate()
    assert not hasattr(result, "scoring_status")
    assert ScoringStatus.SCORED_BASE  # still importable/usable elsewhere
