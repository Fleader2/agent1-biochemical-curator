"""Tests for ``app.review.experiment_execution_workflow``: the execution lifecycle state machine."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.experiment_execution import ExperimentExecution, ExperimentExecutionEvent
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.experiment_execution import persist_experiment_execution
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.review import experiment_execution_workflow as workflow_module
from app.review.errors import InvalidExperimentExecutionTransitionError
from app.review.experiment_execution_history import get_experiment_execution_history
from app.review.experiment_execution_types import (
    ExecutionStatus,
    ExperimentExecutionDecision,
)
from app.review.experiment_execution_workflow import transition_experiment_execution
from app.review.experiment_recommendation_types import (
    ExperimentRecommendationDecision,
    RecommendationLifecycleStatus,
)
from app.review.experiment_recommendation_workflow import transition_experiment_recommendation

pytestmark = pytest.mark.database

_TS_COUNTER = iter(range(1, 100_000))


def _next_timestamp() -> datetime:
    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=next(_TS_COUNTER))


def _accepted_execution(session) -> ExperimentExecution:
    candidate = KnowledgeGapCandidate(
        gap_type=GapType.REACTION_WITHOUT_PARTICIPANTS,
        severity=GapSeverity.HIGH,
        entity_type="reaction",
        entity_id=uuid4(),
        explanation="test-only explanation",
    )
    gap_result = persist_knowledge_gap(candidate, session=session)
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
            reason="accepted for testing",
            timestamp=_next_timestamp(),
        ),
        session=session,
    )
    execution_result = persist_experiment_execution(
        recommendation_id=record.id, execution_identifier="run-1", session=session
    )
    return session.get(ExperimentExecution, execution_result.execution_id)


def _decision(execution_id, new_status, **overrides) -> ExperimentExecutionDecision:
    merged = {
        "execution_id": execution_id,
        "new_status": new_status,
        "actor_id": "tech@example.com",
        "actor_type": "HUMAN",
        "reason": "test-only decision",
        "timestamp": _next_timestamp(),
    } | overrides
    return ExperimentExecutionDecision(**merged)


# --- Lifecycle transitions -------------------------------------------------------------------


def test_planned_to_in_progress(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    assert result.changed is True
    assert result.new_status is ExecutionStatus.IN_PROGRESS
    assert db_session.get(ExperimentExecution, execution.id).status == "IN_PROGRESS"


def test_planned_to_cancelled(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.CANCELLED), session=db_session
    )
    assert result.new_status is ExecutionStatus.CANCELLED


def test_in_progress_to_completed(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.COMPLETED), session=db_session
    )
    assert result.new_status is ExecutionStatus.COMPLETED


def test_in_progress_to_failed(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.FAILED), session=db_session
    )
    assert result.new_status is ExecutionStatus.FAILED


def test_in_progress_to_cancelled(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.CANCELLED), session=db_session
    )
    assert result.new_status is ExecutionStatus.CANCELLED


@pytest.mark.parametrize(
    "terminal_status",
    [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED],
)
@pytest.mark.parametrize(
    "target",
    [ExecutionStatus.IN_PROGRESS, ExecutionStatus.COMPLETED, ExecutionStatus.FAILED],
)
def test_no_transition_out_of_terminal_states(db_session, terminal_status, target):
    if terminal_status == target:
        pytest.skip("same-state is a no-op, not a transition-table rejection")
    execution = _accepted_execution(db_session)
    if terminal_status is not ExecutionStatus.CANCELLED:
        transition_experiment_execution(
            _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
        )
    transition_experiment_execution(
        _decision(execution.id, terminal_status), session=db_session
    )
    with pytest.raises(InvalidExperimentExecutionTransitionError):
        transition_experiment_execution(_decision(execution.id, target), session=db_session)


def test_no_transition_from_planned_to_completed(db_session):
    execution = _accepted_execution(db_session)
    with pytest.raises(InvalidExperimentExecutionTransitionError):
        transition_experiment_execution(
            _decision(execution.id, ExecutionStatus.COMPLETED), session=db_session
        )


def test_no_transition_from_planned_to_failed(db_session):
    execution = _accepted_execution(db_session)
    with pytest.raises(InvalidExperimentExecutionTransitionError):
        transition_experiment_execution(
            _decision(execution.id, ExecutionStatus.FAILED), session=db_session
        )


def test_same_state_request_is_a_noop(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    assert result.changed is False
    assert result.event_id is None

    history = get_experiment_execution_history(db_session, execution.id)
    assert len(history) == 1


# --- Actor policy ---------------------------------------------------------------------------


def test_actor_id_and_type_recorded_verbatim(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(
            execution.id,
            ExecutionStatus.IN_PROGRESS,
            actor_id="robot-arm-7",
            actor_type="AUTOMATED_PIPELINE",
        ),
        session=db_session,
    )
    event = db_session.get(ExperimentExecutionEvent, result.event_id)
    assert event.actor_id == "robot-arm-7"
    assert event.actor_type == "AUTOMATED_PIPELINE"


def test_human_actor_also_accepted(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS, actor_type="HUMAN"),
        session=db_session,
    )
    event = db_session.get(ExperimentExecutionEvent, result.event_id)
    assert event.actor_type == "HUMAN"


def test_decision_requires_non_blank_actor_id():
    with pytest.raises(ValueError):
        _decision(uuid4(), ExecutionStatus.IN_PROGRESS, actor_id="   ")


def test_decision_requires_non_blank_actor_type():
    with pytest.raises(ValueError):
        _decision(uuid4(), ExecutionStatus.IN_PROGRESS, actor_type="   ")


def test_module_does_not_hardcode_a_single_actor_type():
    """Unlike app.review.experiment_recommendation_workflow, this module must
    not hardcode a HUMAN-only actor type anywhere in its source."""
    source = inspect.getsource(workflow_module)
    assert "RecommendationActorType" not in source
    assert '"HUMAN"' not in source
    assert "= HUMAN" not in source


# --- Timestamp stamping ------------------------------------------------------------------------


def test_started_at_set_on_first_in_progress_transition(db_session):
    execution = _accepted_execution(db_session)
    assert execution.started_at is None
    ts = _next_timestamp()
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS, timestamp=ts), session=db_session
    )
    refreshed = db_session.get(ExperimentExecution, execution.id)
    assert refreshed.started_at == ts


def test_completed_at_set_on_terminal_transition(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    ts = _next_timestamp()
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.COMPLETED, timestamp=ts), session=db_session
    )
    refreshed = db_session.get(ExperimentExecution, execution.id)
    assert refreshed.completed_at == ts


def test_completed_at_never_overwritten(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    first_ts = _next_timestamp()
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.CANCELLED, timestamp=first_ts), session=db_session
    )
    refreshed = db_session.get(ExperimentExecution, execution.id)
    assert refreshed.completed_at == first_ts


# --- Audit -----------------------------------------------------------------------------


def test_one_event_per_transition(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.COMPLETED), session=db_session
    )
    history = get_experiment_execution_history(db_session, execution.id)
    assert len(history) == 2


def test_event_previous_and_new_states_correct(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    event = db_session.get(ExperimentExecutionEvent, result.event_id)
    assert event.previous_status == "PLANNED"
    assert event.new_status == "IN_PROGRESS"


def test_event_comment_preserves_reason_and_notes(db_session):
    execution = _accepted_execution(db_session)
    result = transition_experiment_execution(
        _decision(
            execution.id,
            ExecutionStatus.IN_PROGRESS,
            reason="starting the assay",
            notes="see lab notebook page 7",
        ),
        session=db_session,
    )
    event = db_session.get(ExperimentExecutionEvent, result.event_id)
    assert "starting the assay" in event.comment
    assert "see lab notebook page 7" in event.comment


def test_history_ordering_oldest_to_newest(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.FAILED), session=db_session
    )
    history = get_experiment_execution_history(db_session, execution.id)
    assert [event.new_status for event in history] == ["IN_PROGRESS", "FAILED"]


# --- Transactions ---------------------------------------------------------------------


def test_workflow_never_commits_or_rolls_back():
    source = inspect.getsource(workflow_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_workflow_uses_savepoint():
    source = inspect.getsource(workflow_module)
    assert "begin_nested" in source


def test_workflow_never_touches_knowledge_gap_or_recommendation():
    """Checks actual usage patterns, not the bare words (which the module's
    own docstring uses in prose describing what it does *not* do)."""
    source = inspect.getsource(workflow_module)
    assert "KnowledgeGap(" not in source
    assert "app.models.knowledge_gap" not in source
    assert "app.persistence.knowledge_gap" not in source
    assert "app.models.experiment_recommendation" not in source
    assert "ExperimentRecommendationRecord(" not in source


def test_transitioning_execution_does_not_change_recommendation_status(db_session):
    execution = _accepted_execution(db_session)
    transition_experiment_execution(
        _decision(execution.id, ExecutionStatus.IN_PROGRESS), session=db_session
    )
    refreshed = db_session.get(ExperimentRecommendationRecord, execution.recommendation_id)
    assert refreshed.lifecycle_status == "ACCEPTED"
