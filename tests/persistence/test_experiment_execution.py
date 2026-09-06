"""Tests for ``app.persistence.experiment_execution`` (execution side)."""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.experiment_execution import ExperimentExecution
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence import experiment_execution as experiment_execution_module
from app.persistence.experiment_execution import (
    compute_execution_identity,
    diff_planned_vs_actual_conditions,
    get_experiment_execution,
    list_executions_for_recommendation,
    persist_experiment_execution,
)
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.persistence.types import PersistenceAction
from app.review.experiment_recommendation_types import (
    ExperimentRecommendationDecision,
    RecommendationLifecycleStatus,
)
from app.review.experiment_recommendation_workflow import transition_experiment_recommendation

pytestmark = pytest.mark.database

_TS_COUNTER = iter(range(1, 100_000))


def _next_timestamp() -> datetime:
    from datetime import timedelta

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
    return session.get(ExperimentRecommendationRecord, result.recommendation_id)


def _accept(session, recommendation: ExperimentRecommendationRecord) -> None:
    transition_experiment_recommendation(
        ExperimentRecommendationDecision(
            recommendation_id=recommendation.id,
            new_status=RecommendationLifecycleStatus.ACCEPTED,
            reviewer_id="curator@example.com",
            reason="accepted for testing",
            timestamp=_next_timestamp(),
        ),
        session=session,
    )


def _accepted_recommendation(session, **gap_overrides) -> ExperimentRecommendationRecord:
    gap = _persist_gap(session, **gap_overrides)
    recommendation = _persist_recommendation(session, gap)
    _accept(session, recommendation)
    return recommendation


# --- Schema --------------------------------------------------------------------------------


def test_experiment_execution_table_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    assert "experiment_execution" in inspector.get_table_names()


def test_experiment_execution_event_table_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    assert "experiment_execution_event" in inspector.get_table_names()


def test_recommendation_foreign_key_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    fks = inspector.get_foreign_keys("experiment_execution")
    referred = {fk["referred_table"] for fk in fks}
    assert "experiment_recommendation" in referred


def test_execution_identity_is_unique_at_db_level(db_session):
    recommendation = _accepted_recommendation(db_session)
    row_a = ExperimentExecution(
        recommendation_id=recommendation.id,
        execution_identifier="run-1",
        execution_identity="experiment-exec-v1:dup",
        status="PLANNED",
        planned_conditions_json={},
    )
    db_session.add(row_a)
    db_session.flush()

    row_b = ExperimentExecution(
        recommendation_id=recommendation.id,
        execution_identifier="run-2",
        execution_identity="experiment-exec-v1:dup",
        status="PLANNED",
        planned_conditions_json={},
    )
    db_session.add(row_b)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_status_defaults_to_planned(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    row = db_session.get(ExperimentExecution, result.execution_id)
    assert row.status == "PLANNED"


def test_indexes_present(db_session):
    inspector = sa_inspect(db_session.get_bind())
    index_names = {index["name"] for index in inspector.get_indexes("experiment_execution")}
    assert "uq_experiment_execution_execution_identity" in index_names
    assert "ix_experiment_execution_recommendation_id" in index_names
    assert "ix_experiment_execution_status" in index_names

    event_index_names = {
        index["name"] for index in inspector.get_indexes("experiment_execution_event")
    }
    assert "ix_experiment_execution_event_execution_id" in event_index_names


# --- Accepted-recommendation gate ------------------------------------------------------------


def test_accepted_recommendation_creates_execution(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert result.action is PersistenceAction.CREATED
    assert result.execution_id is not None


@pytest.mark.parametrize(
    "target_status",
    [RecommendationLifecycleStatus.REJECTED, RecommendationLifecycleStatus.DEFERRED],
)
def test_non_accepted_recommendation_blocks_execution(db_session, target_status):
    gap = _persist_gap(db_session)
    recommendation = _persist_recommendation(db_session, gap)
    transition_experiment_recommendation(
        ExperimentRecommendationDecision(
            recommendation_id=recommendation.id,
            new_status=target_status,
            reviewer_id="curator@example.com",
            reason="test",
            timestamp=_next_timestamp(),
        ),
        session=db_session,
    )
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW
    assert result.execution_id is None


def test_proposed_recommendation_blocks_execution(db_session):
    gap = _persist_gap(db_session)
    recommendation = _persist_recommendation(db_session, gap)
    assert recommendation.lifecycle_status == "PROPOSED"
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW


def test_superseded_recommendation_blocks_new_execution(db_session):
    recommendation = _accepted_recommendation(db_session)
    transition_experiment_recommendation(
        ExperimentRecommendationDecision(
            recommendation_id=recommendation.id,
            new_status=RecommendationLifecycleStatus.SUPERSEDED,
            reviewer_id="curator@example.com",
            reason="superseded",
            timestamp=_next_timestamp(),
        ),
        session=db_session,
    )
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW


def test_existing_execution_survives_later_supersession(db_session):
    recommendation = _accepted_recommendation(db_session)
    created = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert created.action is PersistenceAction.CREATED

    transition_experiment_recommendation(
        ExperimentRecommendationDecision(
            recommendation_id=recommendation.id,
            new_status=RecommendationLifecycleStatus.SUPERSEDED,
            reviewer_id="curator@example.com",
            reason="superseded",
            timestamp=_next_timestamp(),
        ),
        session=db_session,
    )
    row = db_session.get(ExperimentExecution, created.execution_id)
    assert row is not None
    assert row.status == "PLANNED"


# --- Multiple executions -------------------------------------------------------------------


def test_multiple_executions_per_recommendation_allowed(db_session):
    recommendation = _accepted_recommendation(db_session)
    first = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    second = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-2", session=db_session
    )
    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.CREATED
    assert first.execution_id != second.execution_id

    rows = list_executions_for_recommendation(db_session, recommendation.id)
    assert len(rows) == 2


# --- Field preservation ----------------------------------------------------------------------


def test_planned_conditions_preserved(db_session):
    recommendation = _accepted_recommendation(db_session)
    planned = {"required_fields": ["organism", "strain", "temperature"]}
    result = persist_experiment_execution(
        recommendation_id=recommendation.id,
        execution_identifier="run-1",
        planned_conditions=planned,
        session=db_session,
    )
    row = db_session.get(ExperimentExecution, result.execution_id)
    assert row.planned_conditions_json == planned


def test_performer_laboratory_protocol_preserved(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id,
        execution_identifier="run-1",
        performed_by="Dr. Test",
        laboratory="Test Lab",
        protocol_reference="protocol-42",
        session=db_session,
    )
    row = db_session.get(ExperimentExecution, result.execution_id)
    assert row.performed_by == "Dr. Test"
    assert row.laboratory == "Test Lab"
    assert row.protocol_reference == "protocol-42"


def test_no_recommendation_mutation(db_session):
    recommendation = _accepted_recommendation(db_session)
    before_status = recommendation.recommendation_status
    persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    refreshed = db_session.get(ExperimentRecommendationRecord, recommendation.id)
    assert refreshed.lifecycle_status == "ACCEPTED"
    assert refreshed.recommendation_status == before_status


def test_no_knowledge_gap_mutation(db_session):
    gap = _persist_gap(db_session)
    recommendation = _persist_recommendation(db_session, gap)
    _accept(db_session, recommendation)
    persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    refreshed = db_session.get(KnowledgeGap, gap.id)
    assert refreshed.status == "OPEN"


# --- Idempotency -------------------------------------------------------------------------------


def test_same_execution_identifier_twice_creates_one_row(db_session):
    recommendation = _accepted_recommendation(db_session)
    first = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    second = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.execution_id == second.execution_id

    rows = (
        db_session.execute(
            select(ExperimentExecution).where(ExperimentExecution.id == first.execution_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_compute_execution_identity_deterministic():
    recommendation_id = uuid4()
    first = compute_execution_identity(recommendation_id, "run-1")
    second = compute_execution_identity(recommendation_id, "run-1")
    assert first == second
    assert first.startswith("experiment-exec-v1:")


def test_different_execution_identifier_yields_different_identity():
    recommendation_id = uuid4()
    a = compute_execution_identity(recommendation_id, "run-1")
    b = compute_execution_identity(recommendation_id, "run-2")
    assert a != b


# --- Concurrency -----------------------------------------------------------------------------


def test_concurrent_persist_yields_one_row(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as setup_connection:
        setup_session = Session(bind=setup_connection)
        try:
            recommendation = _accepted_recommendation(setup_session)
            setup_session.commit()
            recommendation_id = recommendation.id
            knowledge_gap_id = recommendation.knowledge_gap_id
        finally:
            setup_session.close()

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                ready.wait(timeout=5)
                outcomes[label] = persist_experiment_execution(
                    recommendation_id=recommendation_id,
                    execution_identifier="run-1",
                    session=session,
                )
                session.commit()
            except BaseException as exc:
                errors[label] = exc
            finally:
                session.close()

    threads = [threading.Thread(target=attempt, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == {}, f"persist_experiment_execution leaked an exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            count = verify_connection.execute(
                select(ExperimentExecution).where(
                    ExperimentExecution.recommendation_id == recommendation_id
                )
            ).all()
            assert len(count) == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            from app.models.experiment_recommendation import ExperimentRecommendationEvent

            cleanup_connection.execute(
                delete(ExperimentExecution).where(
                    ExperimentExecution.recommendation_id == recommendation_id
                )
            )
            cleanup_connection.execute(
                delete(ExperimentRecommendationEvent).where(
                    ExperimentRecommendationEvent.recommendation_id == recommendation_id
                )
            )
            cleanup_connection.execute(
                delete(ExperimentRecommendationRecord).where(
                    ExperimentRecommendationRecord.id == recommendation_id
                )
            )
            cleanup_connection.execute(
                delete(KnowledgeGap).where(KnowledgeGap.id == knowledge_gap_id)
            )
            cleanup_connection.commit()


# --- Transactions ------------------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(experiment_execution_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_uses_savepoint():
    source = inspect.getsource(experiment_execution_module)
    assert "begin_nested" in source


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    recommendation = _accepted_recommendation(db_session)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    assert result.action is PersistenceAction.FAILED
    assert result.execution_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    recommendation = _accepted_recommendation(db_session)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )

    monkeypatch.undo()
    rows = db_session.execute(select(ExperimentExecution)).scalars().all()
    assert rows == []


# --- Read APIs ----------------------------------------------------------------------------------


def test_get_experiment_execution(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    row = get_experiment_execution(db_session, result.execution_id)
    assert row is not None
    assert row.id == result.execution_id


def test_get_experiment_execution_missing_returns_none(db_session):
    assert get_experiment_execution(db_session, uuid4()) is None


def test_list_executions_for_recommendation(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id, execution_identifier="run-1", session=db_session
    )
    rows = list_executions_for_recommendation(db_session, recommendation.id)
    assert [row.id for row in rows] == [result.execution_id]


# --- Planned vs actual comparison ------------------------------------------------------------


def test_diff_planned_vs_actual_conditions_reports_exact_differences(db_session):
    recommendation = _accepted_recommendation(db_session)
    result = persist_experiment_execution(
        recommendation_id=recommendation.id,
        execution_identifier="run-1",
        planned_conditions={"organism": "yeast", "temperature": "30C"},
        actual_conditions={"organism": "yeast", "temperature": "37C", "ph": "7.0"},
        session=db_session,
    )
    row = db_session.get(ExperimentExecution, result.execution_id)
    diff = diff_planned_vs_actual_conditions(row)
    assert diff["missing_in_actual"] == []
    assert diff["extra_in_actual"] == ["ph"]
    assert diff["differing_keys"] == ["temperature"]


def test_diff_helper_is_read_only():
    source = inspect.getsource(experiment_execution_module.diff_planned_vs_actual_conditions)
    assert "session" not in source


# --- Safety --------------------------------------------------------------------------------------


def test_module_never_imports_claim_or_confidence_machinery():
    """Checks actual import statements, not the bare dotted paths (which the
    module's own docstring uses in prose describing what it does *not*
    import)."""
    source = inspect.getsource(experiment_execution_module)
    for forbidden in (
        "import openai",
        "import anthropic",
        "import httpx",
        "import app.connectors",
        "from app.connectors",
        "import app.confidence",
        "from app.confidence",
        "import app.normalization",
        "from app.normalization",
        "from app.models.claim",
        "from app.models.knowledge_gap",
    ):
        assert forbidden not in source


def test_module_never_writes_interpretive_fields():
    """Checks actual usage/assignment patterns, not the bare words (which the
    module's own docstring uses in prose describing what it does *not* do)."""
    source = inspect.getsource(experiment_execution_module)
    for forbidden in (
        "supports_claim=",
        "refutes_claim=",
        "confidence_delta=",
        "gap_resolved=",
        "hypothesis=",
    ):
        assert forbidden not in source
