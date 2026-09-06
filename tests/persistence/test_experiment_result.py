"""Tests for ``app.persistence.experiment_execution`` (result side)."""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence import experiment_execution as experiment_execution_module
from app.persistence.errors import ExperimentResultIdentityConflictError
from app.persistence.experiment_execution import (
    get_experiment_result,
    list_results_for_execution,
    persist_experiment_execution,
    record_experiment_result,
)
from app.persistence.experiment_execution_types import ExperimentResultInput, ResultType
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.persistence.types import PersistenceAction
from app.review.experiment_execution_types import ExecutionStatus, ExperimentExecutionDecision
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


def _accepted_execution(session, *, status: str = "IN_PROGRESS") -> ExperimentExecution:
    gap = _persist_gap(session)
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
    execution = session.get(ExperimentExecution, execution_result.execution_id)

    if status != "PLANNED":
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
    if status in ("COMPLETED", "FAILED", "CANCELLED"):
        transition_experiment_execution(
            ExperimentExecutionDecision(
                execution_id=execution.id,
                new_status=ExecutionStatus(status),
                actor_id="tech@example.com",
                actor_type="HUMAN",
                reason="finishing",
                timestamp=_next_timestamp(),
            ),
            session=session,
        )
    session.flush()
    return session.get(ExperimentExecution, execution.id)


def _quantitative(**overrides) -> ExperimentResultInput:
    merged = {
        "result_identifier": "obs-1",
        "result_type": ResultType.QUANTITATIVE_MEASUREMENT,
        "measurement_name": "reaction_rate",
        "value_numeric": Decimal("1.23"),
        "unit": "umol/min",
    } | overrides
    return ExperimentResultInput(**merged)


# --- Schema --------------------------------------------------------------------------------


def test_experiment_result_table_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    assert "experiment_result" in inspector.get_table_names()


def test_execution_foreign_key_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    fks = inspector.get_foreign_keys("experiment_result")
    referred = {fk["referred_table"] for fk in fks}
    assert "experiment_execution" in referred


def test_result_identity_is_unique_at_db_level(db_session):
    execution = _accepted_execution(db_session)
    row_a = ExperimentResult(
        execution_id=execution.id,
        result_identity="experiment-result-v1:dup",
        result_type="QUANTITATIVE_MEASUREMENT",
        measurement_name="rate",
        value_numeric=Decimal("1"),
    )
    db_session.add(row_a)
    db_session.flush()

    row_b = ExperimentResult(
        execution_id=execution.id,
        result_identity="experiment-result-v1:dup",
        result_type="QUANTITATIVE_MEASUREMENT",
        measurement_name="rate",
        value_numeric=Decimal("2"),
    )
    db_session.add(row_b)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_indexes_present(db_session):
    inspector = sa_inspect(db_session.get_bind())
    index_names = {index["name"] for index in inspector.get_indexes("experiment_result")}
    assert "uq_experiment_result_result_identity" in index_names
    assert "ix_experiment_result_execution_id" in index_names
    assert "ix_experiment_result_measurement_name" in index_names


def test_result_table_has_no_updated_at_column(db_session):
    inspector = sa_inspect(db_session.get_bind())
    columns = {column["name"] for column in inspector.get_columns("experiment_result")}
    assert "updated_at" not in columns


# --- Recording results -----------------------------------------------------------------------


def test_quantitative_result_recorded(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.value_numeric == Decimal("1.23")
    assert row.unit == "umol/min"


def test_qualitative_result_recorded(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=ExperimentResultInput(
            result_identifier="obs-2",
            result_type=ResultType.QUALITATIVE_OBSERVATION,
            measurement_name="colony_morphology",
            value_text="smooth, white colonies",
        ),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.value_text == "smooth, white colonies"
    assert row.value_numeric is None


def test_non_detection_result_recorded(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=ExperimentResultInput(
            result_identifier="obs-3",
            result_type=ResultType.NON_DETECTION,
            measurement_name="enzyme_activity",
            value_text="No activity detected",
        ),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.result_type == "NON_DETECTION"
    assert row.value_text == "No activity detected"


def test_exact_decimal_preservation(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(value_numeric=Decimal("0.00100")),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.value_numeric == Decimal("0.00100")


def test_exact_unit_preservation(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(unit="nmol s^-1 mg^-1"),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.unit == "nmol s^-1 mg^-1"


def test_statistical_support_preserved(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(statistical_support="n=3, SD=0.05, p<0.01"),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.statistical_support == "n=3, SD=0.05, p<0.01"


def test_raw_data_reference_preserved(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(raw_data_reference="s3://bucket/run1/plate3.csv"),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.raw_data_reference == "s3://bucket/run1/plate3.csv"


def test_replicate_identifiers_preserved(db_session):
    execution = _accepted_execution(db_session)
    first = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(result_identifier="obs-r1", replicate_identifier="rep-1"),
        session=db_session,
    )
    second = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(result_identifier="obs-r2", replicate_identifier="rep-2"),
        session=db_session,
    )
    assert first.result_id != second.result_id
    rows = list_results_for_execution(db_session, execution.id)
    assert {row.replicate_identifier for row in rows} == {"rep-1", "rep-2"}


def test_time_points_preserved_exactly(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(time_point="24 h"),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.time_point == "24 h"


def test_condition_label_preserved(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(condition_label="anaerobic, 30C"),
        session=db_session,
    )
    row = db_session.get(ExperimentResult, result.result_id)
    assert row.condition_label == "anaerobic, 30C"


# --- Result gating -----------------------------------------------------------------------------


def test_planned_execution_blocks_result(db_session):
    execution = _accepted_execution(db_session, status="PLANNED")
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW
    assert result.result_id is None


def test_cancelled_execution_blocks_result(db_session):
    execution = _accepted_execution(db_session, status="PLANNED")
    transition_experiment_execution(
        ExperimentExecutionDecision(
            execution_id=execution.id,
            new_status=ExecutionStatus.CANCELLED,
            actor_id="tech@example.com",
            actor_type="HUMAN",
            reason="cancelled",
            timestamp=_next_timestamp(),
        ),
        session=db_session,
    )
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW


def test_in_progress_execution_allows_result(db_session):
    execution = _accepted_execution(db_session, status="IN_PROGRESS")
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.CREATED


def test_completed_execution_allows_result(db_session):
    execution = _accepted_execution(db_session, status="COMPLETED")
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.CREATED


def test_failed_execution_allows_result(db_session):
    """Increment 25 instructions, Step 31: a failed run's partial, scientifically
    valid measurements must not be discarded."""
    execution = _accepted_execution(db_session, status="FAILED")
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.CREATED


def test_completed_with_zero_results_is_allowed(db_session):
    execution = _accepted_execution(db_session, status="COMPLETED")
    rows = list_results_for_execution(db_session, execution.id)
    assert rows == ()


# --- Idempotency and conflicts -----------------------------------------------------------------


def test_same_result_replay_identical_is_reused(db_session):
    execution = _accepted_execution(db_session)
    first = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    second = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.result_id == second.result_id

    rows = (
        db_session.execute(select(ExperimentResult).where(ExperimentResult.id == first.result_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_same_identity_different_value_raises_conflict(db_session):
    execution = _accepted_execution(db_session)
    record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(value_numeric=Decimal("1.0")),
        session=db_session,
    )
    with pytest.raises(ExperimentResultIdentityConflictError):
        record_experiment_result(
            execution_id=execution.id,
            result=_quantitative(value_numeric=Decimal("2.0")),
            session=db_session,
        )


def test_different_result_identifier_is_distinct_row(db_session):
    execution = _accepted_execution(db_session)
    first = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(result_identifier="obs-a"),
        session=db_session,
    )
    second = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(result_identifier="obs-b"),
        session=db_session,
    )
    assert first.result_id != second.result_id


# --- Multiple results ----------------------------------------------------------------------------


def test_many_results_per_execution(db_session):
    execution = _accepted_execution(db_session)
    for i in range(5):
        record_experiment_result(
            execution_id=execution.id,
            result=_quantitative(result_identifier=f"obs-{i}"),
            session=db_session,
        )
    rows = list_results_for_execution(db_session, execution.id)
    assert len(rows) == 5


def test_multiple_time_points(db_session):
    execution = _accepted_execution(db_session)
    for tp in ("0 h", "12 h", "24 h"):
        record_experiment_result(
            execution_id=execution.id,
            result=_quantitative(result_identifier=f"obs-{tp}", time_point=tp),
            session=db_session,
        )
    rows = list_results_for_execution(db_session, execution.id)
    assert {row.time_point for row in rows} == {"0 h", "12 h", "24 h"}


# --- Concurrency -----------------------------------------------------------------------------


def test_concurrent_result_insert_yields_one_row(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as setup_connection:
        setup_session = Session(bind=setup_connection)
        try:
            execution = _accepted_execution(setup_session)
            setup_session.commit()
            execution_id = execution.id
            recommendation_id = execution.recommendation_id
            recommendation = setup_session.get(
                ExperimentRecommendationRecord, recommendation_id
            )
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
                outcomes[label] = record_experiment_result(
                    execution_id=execution_id, result=_quantitative(), session=session
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
        assert errors == {}, f"record_experiment_result leaked an exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            count = verify_connection.execute(
                select(ExperimentResult).where(ExperimentResult.execution_id == execution_id)
            ).all()
            assert len(count) == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            from app.models.experiment_execution import ExperimentExecutionEvent
            from app.models.experiment_recommendation import ExperimentRecommendationEvent

            cleanup_connection.execute(
                delete(ExperimentResult).where(ExperimentResult.execution_id == execution_id)
            )
            cleanup_connection.execute(
                delete(ExperimentExecutionEvent).where(
                    ExperimentExecutionEvent.execution_id == execution_id
                )
            )
            cleanup_connection.execute(
                delete(ExperimentExecution).where(ExperimentExecution.id == execution_id)
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


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    execution = _accepted_execution(db_session)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    assert result.action is PersistenceAction.FAILED
    assert result.result_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    execution = _accepted_execution(db_session)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    record_experiment_result(execution_id=execution.id, result=_quantitative(), session=db_session)

    monkeypatch.undo()
    rows = db_session.execute(select(ExperimentResult)).scalars().all()
    assert rows == []


# --- Read APIs ----------------------------------------------------------------------------------


def test_get_experiment_result(db_session):
    execution = _accepted_execution(db_session)
    result = record_experiment_result(
        execution_id=execution.id, result=_quantitative(), session=db_session
    )
    row = get_experiment_result(db_session, result.result_id)
    assert row is not None
    assert row.id == result.result_id


def test_get_experiment_result_missing_returns_none(db_session):
    assert get_experiment_result(db_session, uuid4()) is None


def test_list_results_for_execution_ordering(db_session):
    execution = _accepted_execution(db_session)
    first = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(
            result_identifier="obs-first", observed_at=datetime(2024, 1, 1, tzinfo=UTC)
        ),
        session=db_session,
    )
    second = record_experiment_result(
        execution_id=execution.id,
        result=_quantitative(
            result_identifier="obs-second", observed_at=datetime(2024, 1, 2, tzinfo=UTC)
        ),
        session=db_session,
    )
    rows = list_results_for_execution(db_session, execution.id)
    assert [row.id for row in rows] == [first.result_id, second.result_id]


# --- Input validation ----------------------------------------------------------------------------


def test_result_input_requires_at_least_one_value():
    with pytest.raises(ValueError):
        ExperimentResultInput(
            result_identifier="obs-1",
            result_type=ResultType.QUALITATIVE_OBSERVATION,
            measurement_name="x",
        )


def test_quantitative_requires_value_numeric():
    with pytest.raises(ValueError):
        ExperimentResultInput(
            result_identifier="obs-1",
            result_type=ResultType.QUANTITATIVE_MEASUREMENT,
            measurement_name="x",
            value_text="1.23",
        )


def test_value_numeric_rejects_float():
    with pytest.raises(TypeError):
        ExperimentResultInput(
            result_identifier="obs-1",
            result_type=ResultType.QUANTITATIVE_MEASUREMENT,
            measurement_name="x",
            value_numeric=1.23,
        )


# --- Safety --------------------------------------------------------------------------------------


def test_module_never_writes_interpretive_result_fields():
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
