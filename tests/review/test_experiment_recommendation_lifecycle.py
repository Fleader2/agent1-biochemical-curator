"""Tests for ``app.review.experiment_recommendation_workflow``: the lifecycle state machine."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.experiment_recommendation import (
    ExperimentRecommendationEvent,
    ExperimentRecommendationRecord,
)
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.persistence.types import PersistenceAction
from app.review import experiment_recommendation_workflow as workflow_module
from app.review.errors import InvalidRecommendationTransitionError
from app.review.experiment_recommendation_history import get_experiment_recommendation_history
from app.review.experiment_recommendation_types import (
    ExperimentRecommendationDecision,
    RecommendationLifecycleStatus,
)
from app.review.experiment_recommendation_workflow import transition_experiment_recommendation

pytestmark = pytest.mark.database

_TS_COUNTER = iter(range(1, 100_000))


def _next_timestamp() -> datetime:
    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=next(_TS_COUNTER))


def _persist_gap(session, **overrides) -> KnowledgeGap:
    merged = {
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "severity": GapSeverity.HIGH,
        "entity_type": "reaction",
        "entity_id": uuid4(),
        "explanation": "test-only explanation",
    } | overrides
    candidate = KnowledgeGapCandidate(**merged)
    result = persist_knowledge_gap(candidate, session=session)
    return session.get(KnowledgeGap, result.knowledge_gap_id)


def _persist_recommendation(session, gap: KnowledgeGap) -> ExperimentRecommendationRecord:
    recommendation = recommend_experiment_for_persisted_gap(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=session
    )
    assert result.action is PersistenceAction.CREATED
    return session.get(ExperimentRecommendationRecord, result.recommendation_id)


def _decision(recommendation_id, new_status, **overrides) -> ExperimentRecommendationDecision:
    merged = {
        "recommendation_id": recommendation_id,
        "new_status": new_status,
        "reviewer_id": "curator@example.com",
        "reason": "test-only decision",
        "timestamp": _next_timestamp(),
    } | overrides
    return ExperimentRecommendationDecision(**merged)


# --- Lifecycle transitions -------------------------------------------------------------------


def test_proposed_to_accepted(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    assert result.changed is True
    assert result.new_status is RecommendationLifecycleStatus.ACCEPTED
    assert db_session.get(ExperimentRecommendationRecord, record.id).lifecycle_status == "ACCEPTED"


def test_proposed_to_rejected(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
    )
    assert result.new_status is RecommendationLifecycleStatus.REJECTED


def test_proposed_to_deferred_then_accepted(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    deferred = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.DEFERRED), session=db_session
    )
    assert deferred.new_status is RecommendationLifecycleStatus.DEFERRED

    accepted = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    assert accepted.old_status is RecommendationLifecycleStatus.DEFERRED
    assert accepted.new_status is RecommendationLifecycleStatus.ACCEPTED


def test_deferred_to_rejected(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.DEFERRED), session=db_session
    )
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
    )
    assert result.new_status is RecommendationLifecycleStatus.REJECTED


def test_accepted_to_superseded(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.SUPERSEDED), session=db_session
    )
    assert result.new_status is RecommendationLifecycleStatus.SUPERSEDED


def test_proposed_to_superseded_directly(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.SUPERSEDED), session=db_session
    )
    assert result.new_status is RecommendationLifecycleStatus.SUPERSEDED


@pytest.mark.parametrize(
    "target",
    [RecommendationLifecycleStatus.ACCEPTED, RecommendationLifecycleStatus.DEFERRED],
)
def test_no_transition_out_of_rejected(db_session, target):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
    )
    with pytest.raises(InvalidRecommendationTransitionError):
        transition_experiment_recommendation(_decision(record.id, target), session=db_session)


@pytest.mark.parametrize(
    "target",
    [
        RecommendationLifecycleStatus.ACCEPTED,
        RecommendationLifecycleStatus.DEFERRED,
        RecommendationLifecycleStatus.REJECTED,
    ],
)
def test_no_transition_out_of_superseded(db_session, target):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.SUPERSEDED), session=db_session
    )
    with pytest.raises(InvalidRecommendationTransitionError):
        transition_experiment_recommendation(_decision(record.id, target), session=db_session)


def test_no_transition_from_accepted_to_deferred(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    with pytest.raises(InvalidRecommendationTransitionError):
        transition_experiment_recommendation(
            _decision(record.id, RecommendationLifecycleStatus.DEFERRED), session=db_session
        )


def test_no_transition_from_accepted_to_rejected(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    with pytest.raises(InvalidRecommendationTransitionError):
        transition_experiment_recommendation(
            _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
        )


def test_same_state_request_is_a_noop(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    assert result.changed is False
    assert result.event_id is None

    history = get_experiment_recommendation_history(db_session, record.id)
    assert len(history) == 1


# --- Human-only acceptance ---------------------------------------------------------------------


def test_human_can_accept(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED, reviewer_id="alice"),
        session=db_session,
    )
    event = db_session.get(ExperimentRecommendationEvent, result.event_id)
    assert event.actor_type == "HUMAN"
    assert event.actor_id == "alice"


def test_no_machine_transition_function_exists():
    """Structural proof: this module defines exactly one public function of
    its own (imported names, classes, and dataclasses are excluded), and it
    always requires an ExperimentRecommendationDecision (which itself
    always requires a non-blank human reviewer_id)."""
    own_functions = [
        name
        for name, obj in inspect.getmembers(workflow_module, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == workflow_module.__name__
    ]
    assert own_functions == ["transition_experiment_recommendation"]


def test_recommender_module_never_constructs_lifecycle_event():
    from app.experiment_recommendation import recommender as recommender_module

    source = inspect.getsource(recommender_module)
    assert "ExperimentRecommendationEvent" not in source


def test_persistence_module_never_constructs_lifecycle_event():
    from app.persistence import experiment_recommendation as persistence_module

    source = inspect.getsource(persistence_module)
    assert "ExperimentRecommendationEvent" not in source


def test_workflow_module_only_writes_human_actor_type():
    source = inspect.getsource(workflow_module)
    assert 'actor_type=RecommendationActorType.HUMAN.value' in source
    assert "RecommendationActorType.DETERMINISTIC" not in source


def test_decision_requires_non_blank_reviewer():
    with pytest.raises(ValueError):
        _decision(uuid4(), RecommendationLifecycleStatus.ACCEPTED, reviewer_id="   ")


# --- Rejection -----------------------------------------------------------------------------------


def test_rejection_is_terminal(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
    )
    row = db_session.get(ExperimentRecommendationRecord, record.id)
    assert row.lifecycle_status == "REJECTED"


# --- Supersession --------------------------------------------------------------------------------


def test_supersede_via_transition_api(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(
            record.id,
            RecommendationLifecycleStatus.SUPERSEDED,
            reason="newer template version exists",
        ),
        session=db_session,
    )
    assert result.new_status is RecommendationLifecycleStatus.SUPERSEDED

    history = get_experiment_recommendation_history(db_session, record.id)
    assert len(history) == 1
    assert history[0].comment.startswith("newer template version exists")


def test_persisting_new_recommendation_never_auto_supersedes_old_one(db_session):
    """Increment 24 instructions, Step 9/25: persisting a new recommendation
    for the same gap must never automatically supersede an older row."""
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)

    from dataclasses import replace

    newer = replace(recommend_experiment_for_persisted_gap(gap), template_version="v2")
    persist_experiment_recommendation(newer, knowledge_gap_id=gap.id, session=db_session)

    unchanged = db_session.get(ExperimentRecommendationRecord, record.id)
    assert unchanged.lifecycle_status == "PROPOSED"


# --- Audit -----------------------------------------------------------------------------


def test_one_event_per_transition(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.DEFERRED), session=db_session
    )
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    history = get_experiment_recommendation_history(db_session, record.id)
    assert len(history) == 2


def test_event_previous_and_new_states_correct(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    event = db_session.get(ExperimentRecommendationEvent, result.event_id)
    assert event.previous_status == "PROPOSED"
    assert event.new_status == "ACCEPTED"


def test_event_reviewer_and_timestamp_preserved(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    ts = _next_timestamp()
    result = transition_experiment_recommendation(
        _decision(
            record.id, RecommendationLifecycleStatus.ACCEPTED, reviewer_id="bob", timestamp=ts
        ),
        session=db_session,
    )
    event = db_session.get(ExperimentRecommendationEvent, result.event_id)
    assert event.actor_id == "bob"
    assert event.created_at == ts


def test_event_comment_preserves_reason_and_notes(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    result = transition_experiment_recommendation(
        _decision(
            record.id,
            RecommendationLifecycleStatus.ACCEPTED,
            reason="strong replication candidate",
            notes="see lab notebook page 42",
        ),
        session=db_session,
    )
    event = db_session.get(ExperimentRecommendationEvent, result.event_id)
    assert "strong replication candidate" in event.comment
    assert "see lab notebook page 42" in event.comment


def test_history_ordering_oldest_to_newest(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.DEFERRED), session=db_session
    )
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.REJECTED), session=db_session
    )
    history = get_experiment_recommendation_history(db_session, record.id)
    assert [event.new_status for event in history] == ["DEFERRED", "REJECTED"]


# --- Transactions ---------------------------------------------------------------------


def test_workflow_never_commits_or_rolls_back():
    source = inspect.getsource(workflow_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_workflow_uses_savepoint():
    source = inspect.getsource(workflow_module)
    assert "begin_nested" in source


def test_workflow_never_touches_knowledge_gap():
    """Checks actual usage patterns, not the bare word (which the module's
    own docstring uses in prose describing what it does *not* do)."""
    source = inspect.getsource(workflow_module)
    assert "KnowledgeGap(" not in source
    assert "app.models.knowledge_gap" not in source
    assert "app.persistence.knowledge_gap" not in source


def test_accepting_recommendation_does_not_change_knowledge_gap_status(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    refreshed = db_session.get(KnowledgeGap, gap.id)
    assert refreshed.status == "OPEN"


def test_accepting_recommendation_does_not_touch_suggested_experiment(db_session):
    gap = _persist_gap(db_session)
    record = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        _decision(record.id, RecommendationLifecycleStatus.ACCEPTED), session=db_session
    )
    refreshed = db_session.get(KnowledgeGap, gap.id)
    assert refreshed.suggested_experiment is None
