"""Tests for ``app.experiment_recommendation.recommender``: the public API."""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from app.experiment_recommendation import recommender as recommender_module
from app.experiment_recommendation.errors import InvalidRecommendationContextError
from app.experiment_recommendation.recommender import (
    build_context_from_session,
    compute_recommendation_identity,
    recommend_experiment_for_gap,
    recommend_experiment_for_persisted_gap,
)
from app.experiment_recommendation.types import (
    RecommendationStatus,
)
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim
from app.models.enums import ConfidenceClass, EvidenceType
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.knowledge_gap import persist_knowledge_gap
from tests.experiment_recommendation.fixtures import make_candidate
from tests.knowledge_gaps.helpers import accept_claim, make_claim, make_evidence

pytestmark = pytest.mark.database


def test_recommend_for_in_memory_candidate():
    candidate = make_candidate(gap_type=GapType.REACTION_WITHOUT_PARTICIPANTS)
    rec = recommend_experiment_for_gap(candidate)
    assert rec.status is RecommendationStatus.RECOMMENDED
    assert rec.knowledge_gap_identity.startswith("kg-v1:")


def test_recommend_for_gap_rejects_wrong_type():
    with pytest.raises(TypeError):
        recommend_experiment_for_gap("not a candidate")  # type: ignore[arg-type]


def test_recommend_for_gap_rejects_bad_context_type():
    candidate = make_candidate()
    with pytest.raises(InvalidRecommendationContextError):
        recommend_experiment_for_gap(candidate, context="not a context")  # type: ignore[arg-type]


def test_recommend_for_persisted_gap(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)

    candidate = KnowledgeGapCandidate(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        explanation="test-only",
        supporting_claim_ids=(claim.id,),
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)

    rec = recommend_experiment_for_persisted_gap(row)
    assert rec.knowledge_gap_identity == row.identity_key
    assert rec.gap_type is GapType.LOW_CONFIDENCE_CLAIM


def test_recommend_for_persisted_gap_rejects_wrong_type():
    with pytest.raises(TypeError):
        recommend_experiment_for_persisted_gap("not a knowledge gap")  # type: ignore[arg-type]


def test_recommend_for_persisted_gap_without_gap_type_raises(db_session):
    row = KnowledgeGap(subject_type="reaction", missing_information="legacy row")
    db_session.add(row)
    db_session.flush()

    with pytest.raises(InvalidRecommendationContextError):
        recommend_experiment_for_persisted_gap(row)


def test_build_context_from_session_resolves_claim_and_evidence(db_session):
    claim = make_claim(db_session)
    evidence = make_evidence(
        db_session, claim_id=claim.id, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL
    )

    context = build_context_from_session(
        db_session, claim_ids=(claim.id,), evidence_ids=(evidence.id,)
    )
    assert context.claims[claim.id].id == claim.id
    assert context.evidence[evidence.id].id == evidence.id


def test_build_context_from_session_empty_ids_returns_empty_context(db_session):
    context = build_context_from_session(db_session)
    assert context.claims == {}
    assert context.evidence == {}


def test_low_confidence_claim_resolves_via_real_session_context(db_session):
    claim = make_claim(db_session)
    make_evidence(db_session, claim_id=claim.id, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL)

    context = build_context_from_session(db_session, claim_ids=(claim.id,))
    candidate = KnowledgeGapCandidate(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        explanation="test-only",
        supporting_claim_ids=(claim.id,),
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    rec = recommend_experiment_for_gap(candidate, context=context)
    assert rec.status is RecommendationStatus.RECOMMENDED


# --- Recommendation identity / versioning --------------------------------------------------


def test_recommendation_identity_is_stable():
    candidate = make_candidate(entity_id=uuid4())
    rec = recommend_experiment_for_gap(candidate)
    first = compute_recommendation_identity(rec)
    second = compute_recommendation_identity(rec)
    assert first == second
    assert first.startswith("experiment-rec-v1:")


def test_recommendation_identity_depends_on_knowledge_gap_and_template_only():
    """Identity omits rationale by construction: two recommendations with
    identical knowledge_gap_identity/template_id/template_version always
    produce the identical digest regardless of any other field."""
    from dataclasses import replace

    candidate = make_candidate(entity_id=uuid4())
    rec = recommend_experiment_for_gap(candidate)
    identity = compute_recommendation_identity(rec)

    reworded = replace(rec, rationale="a completely different rationale string")
    assert compute_recommendation_identity(reworded) == identity


def test_different_template_id_changes_identity():
    candidate_a = make_candidate(gap_type=GapType.ISOLATED_COMPOUND, entity_type="compound")
    candidate_b = make_candidate(gap_type=GapType.PROTEIN_WITHOUT_REACTION, entity_type="protein")
    rec_a = recommend_experiment_for_gap(candidate_a)
    rec_b = recommend_experiment_for_gap(candidate_b)
    assert compute_recommendation_identity(rec_a) != compute_recommendation_identity(rec_b)


# --- Determinism -----------------------------------------------------------------------------


def test_repeated_recommendation_for_gap_is_identical():
    candidate = make_candidate(entity_id=uuid4())
    first = recommend_experiment_for_gap(candidate)
    second = recommend_experiment_for_gap(candidate)
    assert first == second


# --- Safety -----------------------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(recommender_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_issues_no_write_statements():
    source = inspect.getsource(recommender_module)
    for forbidden in ("session.add(", "session.delete(", "insert(", "update(", "delete("):
        assert forbidden not in source


def test_module_never_writes_suggested_experiment():
    source = inspect.getsource(recommender_module)
    assert "suggested_experiment" not in source


def test_module_never_imports_llm_connector_or_normalization_machinery():
    source = inspect.getsource(recommender_module)
    for forbidden in (
        "import openai",
        "import anthropic",
        "import httpx",
        "app.connectors",
        "app.normalization",
        "app.entity_resolution",
        "app.confidence.aggregation",
        "analyze_knowledge_gaps",
    ):
        assert forbidden not in source


def test_recommendation_does_not_mutate_candidate(db_session):
    candidate = make_candidate(entity_id=uuid4())
    original_reason_codes = candidate.reason_codes
    recommend_experiment_for_gap(candidate)
    assert candidate.reason_codes == original_reason_codes


def test_recommendation_does_not_mutate_persisted_claim(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)

    context = build_context_from_session(db_session, claim_ids=(claim.id,))
    candidate = KnowledgeGapCandidate(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        explanation="test-only",
        supporting_claim_ids=(claim.id,),
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    recommend_experiment_for_gap(candidate, context=context)

    refreshed = db_session.get(Claim, claim.id)
    assert refreshed.confidence_class is ConfidenceClass.LOW
