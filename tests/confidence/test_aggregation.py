"""Tests for ``app.confidence.aggregation``."""

from __future__ import annotations

import random
from decimal import Decimal
from uuid import uuid4

import pytest

from app.claim_generation.types import EntityKind
from app.confidence.aggregate_policy import ConflictSeverity
from app.confidence.aggregate_types import EvidenceContribution
from app.confidence.aggregation import aggregate_claim_confidence, build_evidence_contribution
from app.confidence.errors import IncompatibleClaimsError
from app.confidence.scoring import assess_single_evidence_claim
from app.extraction.types import Directness
from app.models.enums import ConfidenceClass, EvidenceType
from tests.confidence.helpers import make_claim, make_matched_result, make_reference


def _resolved_subject(gene_id=None):
    gene_id = gene_id or uuid4()
    return make_reference(
        normalization_result=make_matched_result(matched_entity_id=gene_id), normalized_id=gene_id
    )


def _contribution_for(
    *,
    source_identifier: str,
    subject=None,
    evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
    directness=Directness.AUTHORS_OBSERVED,
    conflict_status=ConflictSeverity.NONE,
    experiment_identifier=None,
    independence_group=None,
    **claim_overrides,
) -> EvidenceContribution:
    subject = subject if subject is not None else _resolved_subject()
    claim_overrides.setdefault("organism_text", None)
    claim_overrides.setdefault("object_text", None)
    claim = make_claim(
        subject=subject,
        evidence_type=evidence_type,
        directness=directness,
        source_identifier=source_identifier,
        **claim_overrides,
    )
    assessment = assess_single_evidence_claim(claim)
    return build_evidence_contribution(
        claim,
        assessment,
        publication_identifier=source_identifier,
        source_identifier=source_identifier,
        experiment_identifier=experiment_identifier,
        independence_group=independence_group,
        conflict_status=conflict_status,
    )


# --- Claim compatibility (Step 4 / 34) --------------------------------------------


def test_compatible_evidence_accepted():
    gene_id = uuid4()
    subject = _resolved_subject(gene_id)
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    assert result.evidence_count == 2


def test_different_subjects_rejected():
    c1 = _contribution_for(source_identifier="PMID:1", subject=_resolved_subject())
    c2 = _contribution_for(source_identifier="PMID:2", subject=_resolved_subject())
    with pytest.raises(IncompatibleClaimsError):
        aggregate_claim_confidence([c1, c2])


def test_different_predicates_rejected():
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject, predicate="activates")
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject, predicate="inhibits")
    with pytest.raises(IncompatibleClaimsError):
        aggregate_claim_confidence([c1, c2])


def test_different_entity_objects_rejected():
    subject = _resolved_subject()
    compound_id_a, compound_id_b = uuid4(), uuid4()
    object_a = make_reference(
        original_text="acyl-CoA",
        normalization_result=make_matched_result(matched_entity_id=compound_id_a),
        normalized_id=compound_id_a,
    )
    object_b = make_reference(
        original_text="acyl-CoA",
        normalization_result=make_matched_result(matched_entity_id=compound_id_b),
        normalized_id=compound_id_b,
    )
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, object_=object_a, object_text="acyl-CoA"
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, object_=object_b, object_text="acyl-CoA"
    )
    with pytest.raises(IncompatibleClaimsError):
        aggregate_claim_confidence([c1, c2])


def test_incompatible_literal_values_rejected():
    """One claim carries a literal value, the other an entity object -- a shape mismatch."""
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1",
        subject=subject,
        object_text=None,
        value_text="0.42",
        value_numeric=Decimal("0.42"),
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, object_text=None, value_text=None
    )
    with pytest.raises(IncompatibleClaimsError):
        aggregate_claim_confidence([c1, c2])


def test_different_organism_contexts_rejected_when_both_explicit():
    subject = _resolved_subject()
    organism_a = make_reference(original_text="Escherichia coli", entity_kind=EntityKind.ORGANISM)
    organism_b = make_reference(
        original_text="Saccharomyces cerevisiae", entity_kind=EntityKind.ORGANISM
    )
    c1 = _contribution_for(
        source_identifier="PMID:1",
        subject=subject,
        organism=organism_a,
        organism_text="Escherichia coli",
    )
    c2 = _contribution_for(
        source_identifier="PMID:2",
        subject=subject,
        organism=organism_b,
        organism_text="Saccharomyces cerevisiae",
    )
    with pytest.raises(IncompatibleClaimsError):
        aggregate_claim_confidence([c1, c2])


def test_organism_stated_on_one_side_only_is_compatible():
    """Not stated is a wildcard -- never a mismatch."""
    subject = _resolved_subject()
    organism = make_reference(original_text="Escherichia coli", entity_kind=EntityKind.ORGANISM)
    c1 = _contribution_for(
        source_identifier="PMID:1",
        subject=subject,
        organism=organism,
        organism_text="Escherichia coli",
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, organism=None, organism_text=None
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.evidence_count == 2


# --- Evidence base scores (Step 5 / 35) -------------------------------------------


@pytest.mark.parametrize(
    ("evidence_type", "expected_score"),
    [
        (EvidenceType.DIRECT_BIOCHEMICAL, 45),
        (EvidenceType.DIRECT_IN_VIVO, 40),
        (EvidenceType.GENETIC, 25),
        (EvidenceType.CURATED_DATABASE, 20),
        (EvidenceType.HOMOLOGY, 5),
        (EvidenceType.AUTHOR_HYPOTHESIS, 0),
    ],
)
def test_single_evidence_no_bonus(evidence_type, expected_score):
    c1 = _contribution_for(source_identifier="PMID:1", evidence_type=evidence_type)
    result = aggregate_claim_confidence([c1])
    assert result.base_evidence_total == expected_score
    assert result.replication_bonus == 0
    assert result.score == expected_score


def test_computational_evidence_applies_computational_only_experimental_relevance():
    """EvidenceType.COMPUTATIONAL is the one deterministic experimental-relevance

    mapping this increment implements (Step 8): base 10 x COMPUTATIONAL_ONLY
    (40%) = 4, not the raw base score.
    """
    c1 = _contribution_for(source_identifier="PMID:1", evidence_type=EvidenceType.COMPUTATIONAL)
    result = aggregate_claim_confidence([c1])
    assert result.contribution_breakdown[0].experimental_relevance.value == "COMPUTATIONAL_ONLY"
    assert result.base_evidence_total == 4
    assert result.score == 4


def test_unscored_evidence_preserved_but_not_invented():
    c1 = _contribution_for(source_identifier="PMID:1", evidence_type=EvidenceType.REVIEW)
    result = aggregate_claim_confidence([c1])
    assert result.score is None
    assert result.confidence_class is ConfidenceClass.UNKNOWN
    assert result.contribution_breakdown[0].evidence_type is EvidenceType.REVIEW
    assert "EVIDENCE_TYPE_UNSCORED" in result.contribution_breakdown[0].ineligibility_reasons


# --- Replication (Step 9 / required tests) ----------------------------------------


def test_no_bonus_for_one_evidence_item():
    c1 = _contribution_for(source_identifier="PMID:1")
    result = aggregate_claim_confidence([c1])
    assert result.replication_bonus == 0


def test_bonus_for_first_independent_replication():
    """Two independent DIRECT_BIOCHEMICAL (45) contributions: weighted sum

    45*1 + 45*0.5 = 67.5, rounded (half-up) to 68, +5 replication = 73.
    """
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    assert result.replication_bonus == 5
    assert result.base_evidence_total == 68
    assert result.score == 73


def test_bonus_capped_at_ten():
    """Five independent DIRECT_BIOCHEMICAL (45) contributions: weighted sum

    45*(1+0.5+0.25+0.125+0.0625)=87.1875, rounded to 87, +10 (capped) = 97.
    """
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 6)
    ]
    result = aggregate_claim_confidence(contributions)
    assert result.replication_bonus == 10
    assert result.base_evidence_total == 87
    assert result.score == 97


def test_no_same_paper_automatic_replication():
    """Two contributions from the same publication, no experiment_identifier, count as one group."""
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1a", subject=subject, independence_group="PMID:1"
    )
    c2 = _contribution_for(
        source_identifier="PMID:1b", subject=subject, independence_group="PMID:1"
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.replication_bonus == 0
    assert result.independent_evidence_count == 1


def test_explicit_experiment_identifiers_allow_same_paper_independence():
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1a", subject=subject, experiment_identifier="exp-1"
    )
    c2 = _contribution_for(
        source_identifier="PMID:1b", subject=subject, experiment_identifier="exp-2"
    )
    # both default to publication_identifier "PMID:1a"/"PMID:1b" respectively unless overridden;
    # simulate same-paper by giving them the same publication_identifier explicitly.
    c1 = build_evidence_contribution(
        c1.candidate_claim,
        c1.single_evidence_assessment,
        publication_identifier="PMID:1",
        source_identifier="PMID:1a",
        experiment_identifier="exp-1",
    )
    c2 = build_evidence_contribution(
        c2.candidate_claim,
        c2.single_evidence_assessment,
        publication_identifier="PMID:1",
        source_identifier="PMID:1b",
        experiment_identifier="exp-2",
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.independent_evidence_count == 2
    assert result.replication_bonus == 5


def test_derivative_review_does_not_create_independent_replication():
    subject = _resolved_subject()
    primary = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    review = _contribution_for(
        source_identifier="PMID:2",
        subject=subject,
        evidence_type=EvidenceType.CURATED_DATABASE,
        directness=Directness.DATABASE_ANNOTATES,
    )
    result = aggregate_claim_confidence([primary, review])
    # The DB annotation still contributes its own (weighted) base score --
    # heterogeneous evidence combines under Increment 18A -- but it never
    # counts as a new independent replication source.
    assert result.independent_evidence_count == 1
    assert result.replication_bonus == 0
    assert result.score is not None


def test_derivative_directness_alone_excluded_from_replication_even_when_scored():
    """Same EvidenceType, but one is DATABASE_ANNOTATES -- must not count as independent.

    Weighted sum: 20*1 + 20*0.5 = 30, rounded to 30, no replication bonus.
    """
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1",
        subject=subject,
        evidence_type=EvidenceType.CURATED_DATABASE,
        directness=Directness.AUTHORS_OBSERVED,
    )
    c2 = _contribution_for(
        source_identifier="PMID:2",
        subject=subject,
        evidence_type=EvidenceType.CURATED_DATABASE,
        directness=Directness.DATABASE_ANNOTATES,
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.independent_evidence_count == 1
    assert result.replication_bonus == 0
    assert result.base_evidence_total == 30
    assert result.score == 30


# --- Conflict (Step 14/16, required tests) ----------------------------------------


def test_no_conflict_no_penalty():
    c1 = _contribution_for(source_identifier="PMID:1", conflict_status=ConflictSeverity.NONE)
    result = aggregate_claim_confidence([c1])
    assert result.conflict_penalty == 0


def test_minor_conflict_penalty_exact():
    c1 = _contribution_for(source_identifier="PMID:1", conflict_status=ConflictSeverity.MINOR)
    result = aggregate_claim_confidence([c1])
    assert result.conflict_penalty == 10
    assert result.score == 35  # 45 - 10


def test_major_conflict_penalty_exact():
    c1 = _contribution_for(source_identifier="PMID:1", conflict_status=ConflictSeverity.MAJOR)
    result = aggregate_claim_confidence([c1])
    assert result.conflict_penalty == 25
    assert result.score == 20  # 45 - 25


def test_multiple_conflicts_use_worst_not_stacked():
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, conflict_status=ConflictSeverity.MINOR
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, conflict_status=ConflictSeverity.MAJOR
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.conflict_penalty == 25  # worst only, not 10+25


def test_negative_predicate_does_not_automatically_conflict():
    c1 = _contribution_for(source_identifier="PMID:1", predicate="did not bind")
    result = aggregate_claim_confidence([c1])
    assert result.conflict_penalty == 0


# --- Duplicate evidence (Step 31) -------------------------------------------------


def test_exact_duplicate_does_not_increase_score():
    subject = _resolved_subject()
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    c1 = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    c2 = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.score == 45
    assert result.replication_bonus == 0
    duplicates = [b for b in result.contribution_breakdown if b.is_duplicate]
    assert len(duplicates) == 1


# --- Entity-resolution eligibility (required tests) --------------------------------


def test_resolved_subject_eligible():
    c1 = _contribution_for(source_identifier="PMID:1")
    result = aggregate_claim_confidence([c1])
    assert result.contribution_breakdown[0].numerically_eligible is True


def test_ambiguous_subject_ineligible():
    from tests.confidence.helpers import make_ambiguous_result

    subject = make_reference(normalization_result=make_ambiguous_result())
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    contribution = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([contribution])
    assert result.contribution_breakdown[0].numerically_eligible is False
    assert "SUBJECT_NOT_RESOLVED" in result.contribution_breakdown[0].ineligibility_reasons


def test_conflicted_subject_ineligible():
    from tests.confidence.helpers import make_conflicted_result

    subject = make_reference(normalization_result=make_conflicted_result())
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    contribution = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([contribution])
    assert result.contribution_breakdown[0].numerically_eligible is False


def test_source_failure_not_penalized_as_counter_evidence():
    from app.entity_resolution.types import MentionResolutionStatus
    from app.models.enums import SourceType
    from tests.confidence.helpers import make_mention_result

    mention_result = make_mention_result(
        status=MentionResolutionStatus.SOURCE_FAILURE,
        failed_source=SourceType.SGD,
        error_category="ConnectorNetworkError",
    )
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    contribution = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([contribution])
    breakdown = result.contribution_breakdown[0]
    assert breakdown.numerically_eligible is False
    assert breakdown.evidence_base_score == 45  # evidence strength itself untouched
    assert result.score is None  # ineligible -- no numerically eligible evidence
    assert result.confidence_class is ConfidenceClass.UNKNOWN


def test_no_candidate_not_penalized_as_counter_evidence():
    from app.entity_resolution.types import MentionResolutionStatus
    from tests.confidence.helpers import make_mention_result

    mention_result = make_mention_result(status=MentionResolutionStatus.NO_CANDIDATE, candidates=())
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    contribution = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([contribution])
    assert result.contribution_breakdown[0].numerically_eligible is False
    assert result.contribution_breakdown[0].evidence_base_score == 45


def test_verified_new_eligible():
    from tests.confidence.helpers import make_new_result

    subject = make_reference(normalization_result=make_new_result())
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    assessment = assess_single_evidence_claim(claim)
    contribution = build_evidence_contribution(
        claim, assessment, publication_identifier="PMID:1", source_identifier="PMID:1"
    )
    result = aggregate_claim_confidence([contribution])
    assert result.contribution_breakdown[0].numerically_eligible is True
    assert result.score == 45


# --- Canonical aggregation mathematics (Increment 18A) -----------------------------


def test_heterogeneous_evidence_no_longer_blocks_final_score():
    """The Increment 18 blocking behavior is gone: differing eligible EvidenceTypes

    now combine via the diminishing-return weighting, never UNKNOWN merely
    for being heterogeneous.
    """
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.GENETIC
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.score is not None
    assert result.confidence_class is not ConfidenceClass.UNKNOWN
    assert "AGGREGATE_BASE_COMBINATION_UNSPECIFIED" not in result.reason_codes
    # 45*1 + 25*0.5 = 57.5 -> round-half-up -> 58, +5 replication (2 independent sources)
    assert result.base_evidence_total == 58
    assert result.replication_bonus == 5
    assert result.score == 63
    assert len(result.contribution_breakdown) == 2
    assert all(b.numerically_eligible for b in result.contribution_breakdown)


def test_identical_adjusted_values_combine_cleanly():
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    result = aggregate_claim_confidence([c1, c2])
    assert result.score is not None
    assert "AGGREGATE_BASE_COMBINATION_UNSPECIFIED" not in result.reason_codes


# --- Weighting -----------------------------------------------------------------------


def test_weighting_one_contribution_has_weight_one():
    c1 = _contribution_for(source_identifier="PMID:1")
    result = aggregate_claim_confidence([c1])
    [entry] = result.contribution_breakdown
    assert entry.weight == Decimal(1)
    assert entry.weighted_contribution == entry.adjusted_contribution
    assert entry.cumulative_score == entry.adjusted_contribution


def test_weighting_two_contributions_second_is_half_weight():
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    weights = sorted((b.weight for b in result.contribution_breakdown), reverse=True)
    assert weights == [Decimal(1), Decimal("0.5")]


def test_weighting_three_contributions_exact_sequence():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 4)
    ]
    result = aggregate_claim_confidence(contributions)
    weights = sorted((b.weight for b in result.contribution_breakdown), reverse=True)
    assert weights == [Decimal(1), Decimal("0.5"), Decimal("0.25")]


def test_weighting_descending_order_independence():
    """The same contribution set in any input order assigns the same weight

    to the same specific contribution.
    """
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.GENETIC
    )
    result_a = aggregate_claim_confidence([c1, c2])
    result_b = aggregate_claim_confidence([c2, c1])
    assert result_a == result_b
    weight_by_source_a = {b.source_identifier: b.weight for b in result_a.contribution_breakdown}
    weight_by_source_b = {b.source_identifier: b.weight for b in result_b.contribution_breakdown}
    assert weight_by_source_a == weight_by_source_b
    assert weight_by_source_a["PMID:1"] == Decimal(1)  # the stronger (45) evidence ranks first
    assert weight_by_source_a["PMID:2"] == Decimal("0.5")


def test_weighting_heterogeneous_evidence_ranked_by_adjusted_score():
    subject = _resolved_subject()
    weak = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.HOMOLOGY
    )
    strong = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    result = aggregate_claim_confidence([weak, strong])
    by_source = {b.source_identifier: b for b in result.contribution_breakdown}
    assert by_source["PMID:2"].weight == Decimal(1)  # DIRECT_BIOCHEMICAL (45) ranks first
    assert by_source["PMID:1"].weight == Decimal("0.5")  # HOMOLOGY (5) ranks second


# --- Diminishing returns -------------------------------------------------------------


def test_diminishing_returns_not_a_naive_sum():
    """45 + 40 + 25 must not become 110 -- it follows the weighting formula exactly."""
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.DIRECT_IN_VIVO
    )
    c3 = _contribution_for(
        source_identifier="PMID:3", subject=subject, evidence_type=EvidenceType.GENETIC
    )
    result = aggregate_claim_confidence([c1, c2, c3])
    assert result.base_evidence_total != 110
    # 45*1 + 40*0.5 + 25*0.25 = 45 + 20 + 6.25 = 71.25 -> round-half-up -> 71
    assert result.base_evidence_total == 71


def test_strongest_evidence_dominates_many_weak_observations():
    """45 + 5 + 5 + 5 remains close to the strong evidence, not a large sum."""
    subject = _resolved_subject()
    strong = _contribution_for(
        source_identifier="PMID:1", subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )
    weak_contribs = [
        _contribution_for(
            source_identifier=f"PMID:{i}", subject=subject, evidence_type=EvidenceType.HOMOLOGY
        )
        for i in range(2, 5)
    ]
    result = aggregate_claim_confidence([strong, *weak_contribs])
    # 45*1 + 5*0.5 + 5*0.25 + 5*0.125 = 45 + 2.5 + 1.25 + 0.625 = 49.375 -> 49
    assert result.base_evidence_total == 49
    assert result.base_evidence_total < 45 + 5 + 5 + 5  # nowhere near a naive sum (60)
    assert result.base_evidence_total - 45 < 5  # dominated by the strong contribution


def test_many_weak_evidence_increases_confidence_gradually():
    """10 + 10 + 10 + 10 + 10 (via HOMOLOGY-style base 5 x5) increases gradually, not linearly."""
    subject = _resolved_subject()
    contributions = [
        _contribution_for(
            source_identifier=f"PMID:{i}", subject=subject, evidence_type=EvidenceType.HOMOLOGY
        )
        for i in range(1, 6)
    ]
    result = aggregate_claim_confidence(contributions)
    # 5*(1+0.5+0.25+0.125+0.0625) = 5*1.9375 = 9.6875 -> round-half-up -> 10
    assert result.base_evidence_total == 10
    assert result.base_evidence_total < 5 * 5  # nowhere near a naive sum (25)


# --- Ordering of operations: replication after weighting, conflict after replication --


def test_replication_bonus_applied_after_weighted_aggregation():
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    # base_evidence_total (68) is the weighted aggregate BEFORE the +5 bonus.
    assert result.base_evidence_total == 68
    assert result.score == result.base_evidence_total + result.replication_bonus


def test_conflict_penalty_applied_after_replication():
    subject = _resolved_subject()
    c1 = _contribution_for(
        source_identifier="PMID:1", subject=subject, conflict_status=ConflictSeverity.MINOR
    )
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    assert (
        result.score
        == result.base_evidence_total + result.replication_bonus - result.conflict_penalty
    )


# --- Algorithm version -----------------------------------------------------------------


def test_algorithm_version_is_confidence_v1():
    c1 = _contribution_for(source_identifier="PMID:1")
    result = aggregate_claim_confidence([c1])
    assert result.algorithm_version == "confidence-v1"


# --- Audit trail: weighted contributions reported correctly -------------------------


def test_audit_trail_reports_every_weighted_contribution():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 4)
    ]
    result = aggregate_claim_confidence(contributions)
    for entry in result.contribution_breakdown:
        assert entry.adjusted_contribution == Decimal(45)
        assert entry.weighted_contribution == entry.adjusted_contribution * entry.weight
    cumulative_values = sorted(b.cumulative_score for b in result.contribution_breakdown)
    assert cumulative_values[-1] == Decimal("78.75")  # 45 + 22.5 + 11.25


# --- ConfidenceClass thresholds through the public API -----------------------------


def test_confidence_class_moderate_reached_via_replication():
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(source_identifier="PMID:2", subject=subject)
    result = aggregate_claim_confidence([c1, c2])
    assert result.score == 73
    assert result.confidence_class is ConfidenceClass.MODERATE


# --- Score clamp -------------------------------------------------------------------


def test_score_clamped_at_zero():
    c1 = _contribution_for(
        source_identifier="PMID:1",
        evidence_type=EvidenceType.AUTHOR_HYPOTHESIS,
        conflict_status=ConflictSeverity.MAJOR,
    )
    result = aggregate_claim_confidence([c1])
    assert result.score == 0
    assert result.confidence_class is ConfidenceClass.LOW


def test_score_clamped_at_one_hundred():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(
            source_identifier=f"PMID:{i}",
            subject=subject,
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        )
        for i in range(1, 6)
    ]
    result = aggregate_claim_confidence(contributions)
    assert result.score <= 100


# --- Order independence (Step 30) --------------------------------------------------


def test_order_independence_shuffled_evidence_set():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 5)
    ]
    result_a = aggregate_claim_confidence(contributions)
    shuffled = list(contributions)
    random.Random(42).shuffle(shuffled)
    result_b = aggregate_claim_confidence(shuffled)
    assert result_a == result_b


def test_order_independence_reversed():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 4)
    ]
    result_a = aggregate_claim_confidence(contributions)
    result_b = aggregate_claim_confidence(list(reversed(contributions)))
    assert result_a == result_b


# --- Auditability -------------------------------------------------------------------


def test_contribution_breakdown_includes_all_evidence_items():
    subject = _resolved_subject()
    c1 = _contribution_for(source_identifier="PMID:1", subject=subject)
    c2 = _contribution_for(
        source_identifier="PMID:2", subject=subject, evidence_type=EvidenceType.REVIEW
    )
    result = aggregate_claim_confidence([c1, c2])
    assert len(result.contribution_breakdown) == 2
    source_ids = {b.source_identifier for b in result.contribution_breakdown}
    assert source_ids == {"PMID:1", "PMID:2"}


def test_ineligible_items_retained_not_deleted():
    from tests.confidence.helpers import make_ambiguous_result

    subject = make_reference(normalization_result=make_ambiguous_result())
    claim = make_claim(
        subject=subject, organism_text=None, object_text=None, source_identifier="PMID:1"
    )
    contribution = build_evidence_contribution(
        claim,
        assess_single_evidence_claim(claim),
        publication_identifier="PMID:1",
        source_identifier="PMID:1",
    )
    result = aggregate_claim_confidence([contribution])
    assert len(result.contribution_breakdown) == 1
    assert result.contribution_breakdown[0].numerically_eligible is False


# --- Determinism --------------------------------------------------------------------


def test_repeated_aggregation_is_identical():
    subject = _resolved_subject()
    contributions = [
        _contribution_for(source_identifier=f"PMID:{i}", subject=subject) for i in range(1, 3)
    ]
    first = aggregate_claim_confidence(contributions)
    second = aggregate_claim_confidence(contributions)
    assert first == second


# --- Input immutability (Step 29) --------------------------------------------------


def test_aggregation_does_not_mutate_claim():
    c1 = _contribution_for(source_identifier="PMID:1")
    before = repr(c1.candidate_claim)
    aggregate_claim_confidence([c1])
    assert repr(c1.candidate_claim) == before


def test_aggregation_does_not_mutate_assessment():
    c1 = _contribution_for(source_identifier="PMID:1")
    before = repr(c1.single_evidence_assessment)
    aggregate_claim_confidence([c1])
    assert repr(c1.single_evidence_assessment) == before


# --- Safety --------------------------------------------------------------------------


def test_empty_contributions_rejected():
    with pytest.raises(ValueError):
        aggregate_claim_confidence([])


def test_rejects_non_contribution_input():
    with pytest.raises(TypeError):
        aggregate_claim_confidence(["not a contribution"])  # type: ignore[list-item]


def test_aggregation_module_imports_no_database_session_or_connector():
    import inspect

    import app.confidence.aggregation as aggregation_module

    source = inspect.getsource(aggregation_module)
    for forbidden in ("Session", "httpx", "ConnectorHttpClient", "requests.", "import connectors"):
        assert forbidden not in source


def test_aggregation_module_never_canonicalizes_predicate():
    """Structural: predicate compatibility uses exact string equality, never rewritten."""
    import inspect

    import app.confidence.aggregation as aggregation_module

    source = inspect.getsource(aggregation_module._are_compatible)
    assert "a.predicate != b.predicate" in source
    assert ".lower()" not in source
    assert ".strip()" not in source


def test_aggregation_module_never_discovers_conflict_from_predicate_text():
    """Structural: conflict severity always comes from EvidenceContribution.conflict_status,

    never from comparing predicate text.
    """
    import inspect

    import app.confidence.aggregation as aggregation_module

    source = inspect.getsource(aggregation_module)
    assert "predicate ==" not in source
    assert "contradiction" not in source.lower()
