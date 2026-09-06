"""End-to-end Agent 1 contract tests (Increment 27).

Demonstrates the complete, persisted Agent 1 v1 capability chain:

    Claim (HUMAN_ACCEPTED) -> KnowledgeGap -> ExperimentRecommendation
        -> ACCEPTED -> ExperimentExecution -> ExperimentResult

and verifies the final ``Agent1KnowledgePackage`` exposes every link.
Also covers the curated-state policy (Step 22), provenance (Step 23),
regulation/cofactor disclosure (Steps 25-26), and the "no automatic
result interpretation" boundary (Step 27). This module does not re-run
any upstream algorithm from raw literature -- every row is constructed
directly against already-tested persistence/review/knowledge-gap/
experiment-recommendation/experiment-execution APIs.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.agent1 import (
    get_agent1_knowledge_package,
)
from app.agent1 import service as agent1_service_module
from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim, Evidence
from app.models.enums import (
    ClaimStatus,
    CurationState,
    EvidenceType,
    ReactionParticipantRole,
    RegulatoryEffect,
    SourceType,
)
from app.models.experiment_execution import ExperimentExecution
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.models.reaction import ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.persistence.experiment_execution import (
    persist_experiment_execution,
    record_experiment_result,
)
from app.persistence.experiment_execution_types import ExperimentResultInput, ResultType
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.review.experiment_execution_types import ExecutionStatus, ExperimentExecutionDecision
from app.review.experiment_execution_workflow import transition_experiment_execution
from app.review.experiment_recommendation_types import (
    ExperimentRecommendationDecision,
    RecommendationLifecycleStatus,
)
from app.review.experiment_recommendation_workflow import transition_experiment_recommendation
from app.review.types import ReviewDecision
from app.review.workflow import human_review_claim
from tests.persistence.conftest import make_organism, make_protein, make_reaction

pytestmark = pytest.mark.database

_TS_COUNTER = iter(range(1, 100_000))


def _next_timestamp() -> datetime:
    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=next(_TS_COUNTER))


def _build_curated_claim(session, *, organism):
    protein = make_protein(session, organism_id=organism.id)
    reaction = make_reaction(session, organism_id=organism.id)
    claim = Claim(
        subject_type="protein",
        subject_id=protein.id,
        predicate="catalyzes",
        object_type="reaction",
        object_id=reaction.id,
        organism_id=organism.id,
        status=ClaimStatus.UNKNOWN,
    )
    session.add(claim)
    session.flush()
    evidence = Evidence(
        claim_id=claim.id,
        source_type=SourceType.PUBMED,
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        quoted_support="The protein catalyzed the reaction.",
        curator_summary="Test-only curator summary.",
    )
    session.add(evidence)
    session.flush()
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.HUMAN_ACCEPTED,
            reviewer="curator@example.com",
            reason="accepted for end-to-end demonstration",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=session,
    )
    return claim, evidence, reaction


def _full_chain(session):
    """Build the complete Claim -> ... -> ExperimentResult chain. Returns every row."""
    organism = make_organism(session)
    claim, evidence, reaction = _build_curated_claim(session, organism=organism)

    gap_candidate = KnowledgeGapCandidate(
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim.id,
        supporting_claim_ids=(claim.id,),
        explanation="test-only: demonstrating the full Agent 1 chain",
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    gap_result = persist_knowledge_gap(gap_candidate, session=session)
    gap = session.get(KnowledgeGap, gap_result.knowledge_gap_id)

    recommendation = recommend_experiment_for_persisted_gap(gap)
    persisted = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=session
    )
    record = session.get(ExperimentRecommendationRecord, persisted.recommendation_id)

    transition_experiment_recommendation(
        ExperimentRecommendationDecision(
            recommendation_id=record.id,
            new_status=RecommendationLifecycleStatus.ACCEPTED,
            reviewer_id="curator@example.com",
            reason="accepted for end-to-end demonstration",
            timestamp=_next_timestamp(),
        ),
        session=session,
    )

    execution_result = persist_experiment_execution(
        recommendation_id=record.id, execution_identifier="run-1", session=session
    )
    execution = session.get(ExperimentExecution, execution_result.execution_id)
    transition_experiment_execution(
        ExperimentExecutionDecision(
            execution_id=execution.id,
            new_status=ExecutionStatus.IN_PROGRESS,
            actor_id="tech@example.com",
            actor_type="HUMAN",
            reason="starting",
            timestamp=_next_timestamp(),
        ),
        session=session,
    )

    result_outcome = record_experiment_result(
        execution_id=execution.id,
        result=ExperimentResultInput(
            result_identifier="obs-1",
            result_type=ResultType.QUANTITATIVE_MEASUREMENT,
            measurement_name="reaction_rate",
            value_numeric=Decimal("3.4"),
            unit="umol/min/mg",
        ),
        session=session,
    )
    session.flush()

    return {
        "organism": organism,
        "claim": claim,
        "evidence": evidence,
        "reaction": reaction,
        "knowledge_gap": gap,
        "recommendation": record,
        "execution": execution,
        "result_id": result_outcome.result_id,
    }


# --- End-to-end demonstration (Step 24) ---------------------------------------------------------


def test_full_chain_is_exposed_by_the_knowledge_package(db_session):
    chain = _full_chain(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=chain["organism"].id)

    assert [c.id for c in package.claims] == [chain["claim"].id]
    assert chain["knowledge_gap"].id in {g.id for g in package.knowledge_gaps}
    assert chain["recommendation"].id in {
        r.id for r in package.experiment_recommendations
    }
    assert chain["execution"].id in {e.id for e in package.experiment_executions}
    assert chain["result_id"] in {r.id for r in package.experiment_results}

    review_state = next(r for r in package.review_states if r.claim_id == chain["claim"].id)
    assert review_state.curation_state is CurationState.HUMAN_ACCEPTED
    assert len(review_state.history) == 1

    recommendation = next(
        r for r in package.experiment_recommendations if r.id == chain["recommendation"].id
    )
    assert recommendation.lifecycle_status == "ACCEPTED"


# --- Curated-state tests (Step 22) --------------------------------------------------------------


def test_human_accepted_claim_appears_in_curated_output(db_session):
    from app.agent1.service import curated_claims

    chain = _full_chain(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=chain["organism"].id)
    assert chain["claim"].id in {c.id for c in curated_claims(package)}


def test_rejected_claim_does_not_appear_in_curated_output(db_session):
    from app.agent1.service import curated_claims, rejected_claims

    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    claim = Claim(
        subject_type="protein",
        subject_id=protein.id,
        predicate="catalyzes",
        organism_id=organism.id,
        status=ClaimStatus.UNKNOWN,
    )
    db_session.add(claim)
    db_session.flush()
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.REJECTED,
            reviewer="curator@example.com",
            reason="not supported",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=db_session,
    )
    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    assert claim.id not in {c.id for c in curated_claims(package)}
    assert claim.id in {c.id for c in rejected_claims(package)}


def test_needs_review_claim_is_segregated_as_non_curated(db_session):
    from app.agent1.service import curated_claims, non_curated_claims

    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    claim = Claim(
        subject_type="protein",
        subject_id=protein.id,
        predicate="catalyzes",
        organism_id=organism.id,
        status=ClaimStatus.UNKNOWN,
    )
    db_session.add(claim)
    db_session.flush()
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.NEEDS_REVIEW,
            reviewer="curator@example.com",
            reason="needs a second opinion",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=db_session,
    )
    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    assert claim.id not in {c.id for c in curated_claims(package)}
    assert claim.id in {c.id for c in non_curated_claims(package)}


def test_review_history_remains_visible_for_every_claim(db_session):
    chain = _full_chain(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=chain["organism"].id)
    review_state = next(r for r in package.review_states if r.claim_id == chain["claim"].id)
    assert review_state.history[0].new_state is CurationState.HUMAN_ACCEPTED
    assert review_state.history[0].reviewer_id == "curator@example.com"


# --- Provenance tests (Step 23) ------------------------------------------------------------------


def test_curated_claim_exposes_evidence_source_confidence_and_review_state(db_session):
    chain = _full_chain(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=chain["organism"].id)

    evidence = [e for e in package.evidence if e.claim_id == chain["claim"].id]
    assert len(evidence) == 1
    assert evidence[0].source_type == SourceType.PUBMED
    assert evidence[0].quoted_support == "The protein catalyzed the reaction."

    confidence = next(
        s for s in package.confidence_summaries if s.claim_id == chain["claim"].id
    )
    assert confidence.status == ClaimStatus.UNKNOWN  # exposed verbatim, never recomputed

    review_state = next(r for r in package.review_states if r.claim_id == chain["claim"].id)
    assert review_state.curation_state is CurationState.HUMAN_ACCEPTED


def test_provenance_never_fabricated_for_claim_without_evidence(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    claim = Claim(
        subject_type="protein",
        subject_id=protein.id,
        predicate="catalyzes",
        organism_id=organism.id,
        status=ClaimStatus.UNKNOWN,
    )
    db_session.add(claim)
    db_session.flush()
    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    evidence_for_claim = [e for e in package.evidence if e.claim_id == claim.id]
    assert evidence_for_claim == []
    assert package.provenance_summary.claims_without_evidence >= 1


# --- Regulation acceptance test (Step 25) -------------------------------------------------------


def test_regulation_is_exposed_when_present_but_flagged_as_a_limitation(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    reaction = make_reaction(db_session, organism_id=organism.id)
    regulatory_interaction = RegulatoryInteraction(
        regulator_type="protein",
        regulator_id=protein.id,
        target_type="reaction",
        target_id=reaction.id,
        effect=RegulatoryEffect.INHIBITION,
        organism_id=organism.id,
    )
    db_session.add(regulatory_interaction)
    db_session.flush()

    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    assert regulatory_interaction.id in {r.id for r in package.regulatory_interactions}
    # The limitation must be disclosed regardless of whether a row exists.
    assert any("egulation" in item for item in package.limitations)


def test_no_regulation_normalization_or_persistence_module_exists():
    """Verifies the documented finding: no normalization/persistence pipeline
    writes RegulatoryInteraction rows today."""
    from pathlib import Path

    import app.normalization as normalization_package
    import app.persistence as persistence_package

    for package in (normalization_package, persistence_package):
        package_dir = Path(package.__file__).parent
        assert not (package_dir / "regulatory_interaction.py").exists()


# --- Cofactor acceptance test (Step 26) -----------------------------------------------------------


def test_cofactor_represented_as_ordinary_reaction_participant(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    from tests.persistence.conftest import make_compartment, make_compound

    atp = make_compound(db_session, suffix="ATP")
    compartment = make_compartment(db_session, organism_id=organism.id)
    participant = ReactionParticipant(
        reaction_id=reaction.id,
        compound_id=atp.id,
        compartment_id=compartment.id,
        role=ReactionParticipantRole.MODIFIER,
        stoichiometry=Decimal("1"),
    )
    db_session.add(participant)
    db_session.flush()

    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    exposed = next(p for p in package.reaction_participants if p.id == participant.id)
    assert exposed.compound_id == atp.id
    # No separate cofactor classification exists anywhere on the row or the schema.
    assert not hasattr(exposed, "is_cofactor")
    assert any("ofactor" in item for item in package.limitations)


# --- No automatic experiment-result interpretation (Step 27) -------------------------------------


def test_service_module_never_creates_claim_or_evidence():
    source = inspect.getsource(agent1_service_module)
    assert "Claim(" not in source
    assert "Evidence(" not in source


def test_service_module_never_writes_confidence_or_resolves_gaps():
    source = inspect.getsource(agent1_service_module)
    for forbidden in (
        "confidence_score =",
        "confidence_class =",
        ".status = KnowledgeGapStatus",
        "gap.status =",
    ):
        assert forbidden not in source


def test_service_module_never_commits_or_writes():
    source = inspect.getsource(agent1_service_module)
    for forbidden in ("session.add(", "session.commit(", "session.flush(", "session.rollback("):
        assert forbidden not in source


def test_service_module_never_calls_a_connector():
    source = inspect.getsource(agent1_service_module)
    assert "app.connectors" not in source


def test_recording_a_result_does_not_auto_generate_claim_or_evidence(db_session):
    """The experimental subsystem's own boundary (Increment 26), reasserted at
    the Agent 1 package level: a fresh ExperimentResult never appears as a
    new Claim/Evidence row anywhere in the package."""
    chain = _full_chain(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=chain["organism"].id)
    # Exactly the one claim this fixture explicitly created -- recording the
    # experiment result did not add another.
    assert len(package.claims) == 1
    assert len(package.evidence) == 1


def test_recording_a_result_does_not_resolve_the_knowledge_gap(db_session):
    chain = _full_chain(db_session)
    refreshed_gap = db_session.get(KnowledgeGap, chain["knowledge_gap"].id)
    assert refreshed_gap.status == "OPEN"
