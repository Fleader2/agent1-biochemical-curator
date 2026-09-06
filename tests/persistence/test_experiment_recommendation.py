"""Tests for ``app.persistence.experiment_recommendation``."""

from __future__ import annotations

import inspect
import threading
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.experiment_recommendation.types import ExperimentRecommendation
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence import experiment_recommendation as experiment_recommendation_module
from app.persistence.errors import RecommendationIdentityConflictError
from app.persistence.experiment_recommendation import (
    get_experiment_recommendation,
    list_experiment_recommendations_for_gap,
    list_open_experiment_recommendations,
    persist_experiment_recommendation,
    recommend_and_persist_for_gap,
)
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _gap_candidate(**overrides) -> KnowledgeGapCandidate:
    merged = {
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "severity": GapSeverity.HIGH,
        "entity_type": "reaction",
        "entity_id": uuid4(),
        "explanation": "test-only explanation",
    } | overrides
    return KnowledgeGapCandidate(**merged)


def _persist_gap(session, **overrides) -> KnowledgeGap:
    candidate = _gap_candidate(**overrides)
    result = persist_knowledge_gap(candidate, session=session)
    row = session.get(KnowledgeGap, result.knowledge_gap_id)
    return row


def _recommendation_for(knowledge_gap: KnowledgeGap) -> ExperimentRecommendation:
    from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap

    return recommend_experiment_for_persisted_gap(knowledge_gap)


# --- Schema --------------------------------------------------------------------------------


def test_experiment_recommendation_table_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    assert "experiment_recommendation" in inspector.get_table_names()


def test_experiment_recommendation_event_table_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    assert "experiment_recommendation_event" in inspector.get_table_names()


def test_knowledge_gap_foreign_key_exists(db_session):
    inspector = sa_inspect(db_session.get_bind())
    fks = inspector.get_foreign_keys("experiment_recommendation")
    referred = {fk["referred_table"] for fk in fks}
    assert "knowledge_gap" in referred


def test_recommendation_identity_is_unique_at_db_level(db_session):
    gap = _persist_gap(db_session)
    row_a = ExperimentRecommendationRecord(
        knowledge_gap_id=gap.id,
        recommendation_identity="experiment-rec-v1:dup",
        gap_type="REACTION_WITHOUT_PARTICIPANTS",
        gap_severity="HIGH",
        recommendation_status="RECOMMENDED",
        objective="x",
        experimental_context_requirements_json=[],
        rationale="x",
        supporting_claim_ids_json=[],
        supporting_evidence_ids_json=[],
        reason_codes_json=["X"],
        template_id="t",
        template_version="v1",
        experiment_class="REACTION_VALIDATION",
        success_criterion="x",
    )
    db_session.add(row_a)
    db_session.flush()

    row_b = ExperimentRecommendationRecord(
        knowledge_gap_id=gap.id,
        recommendation_identity="experiment-rec-v1:dup",
        gap_type="REACTION_WITHOUT_PARTICIPANTS",
        gap_severity="HIGH",
        recommendation_status="RECOMMENDED",
        objective="y",
        experimental_context_requirements_json=[],
        rationale="y",
        supporting_claim_ids_json=[],
        supporting_evidence_ids_json=[],
        reason_codes_json=["X"],
        template_id="t",
        template_version="v1",
        experiment_class="REACTION_VALIDATION",
        success_criterion="y",
    )
    db_session.add(row_b)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_lifecycle_status_field_defaults_to_proposed(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    row = db_session.get(ExperimentRecommendationRecord, result.recommendation_id)
    assert row.lifecycle_status == "PROPOSED"


def test_json_fields_persist_lists(db_session):
    claim_id = uuid4()
    gap = _persist_gap(
        db_session,
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim_id,
        supporting_claim_ids=(claim_id,),
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    row = db_session.get(ExperimentRecommendationRecord, result.recommendation_id)
    assert isinstance(row.reason_codes_json, list)
    assert isinstance(row.supporting_claim_ids_json, list)
    assert isinstance(row.experimental_context_requirements_json, list)


def test_indexes_present(db_session):
    inspector = sa_inspect(db_session.get_bind())
    index_names = {index["name"] for index in inspector.get_indexes("experiment_recommendation")}
    assert "uq_experiment_recommendation_recommendation_identity" in index_names
    assert "ix_experiment_recommendation_knowledge_gap_id" in index_names
    assert "ix_experiment_recommendation_lifecycle_status" in index_names
    assert "ix_experiment_recommendation_recommendation_status" in index_names
    assert "ix_experiment_recommendation_template_id_template_version" in index_names

    event_index_names = {
        index["name"] for index in inspector.get_indexes("experiment_recommendation_event")
    }
    assert "ix_experiment_recommendation_event_recommendation_id" in event_index_names


# --- Persistence -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gap_type,entity_type,severity",
    [
        (GapType.REACTION_WITHOUT_PARTICIPANTS, "reaction", GapSeverity.HIGH),
        (GapType.ISOLATED_COMPOUND, "compound", GapSeverity.LOW),
        (GapType.PROTEIN_WITHOUT_REACTION, "protein", GapSeverity.LOW),
    ],
)
def test_every_generation_status_persists(db_session, gap_type, entity_type, severity):
    gap = _persist_gap(db_session, gap_type=gap_type, severity=severity, entity_type=entity_type)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(ExperimentRecommendationRecord, result.recommendation_id)
    assert row.recommendation_status == recommendation.status.value


def test_insufficient_information_persists(db_session):
    claim_id = uuid4()
    gap = _persist_gap(
        db_session,
        gap_type=GapType.LOW_CONFIDENCE_CLAIM,
        severity=GapSeverity.MODERATE,
        entity_type="claim",
        entity_id=claim_id,
        supporting_claim_ids=(claim_id,),
        reason_codes=("CONFIDENCE_CLASS_LOW",),
    )
    recommendation = _recommendation_for(gap)  # no context supplied -> INSUFFICIENT_INFORMATION
    assert recommendation.status.value == "INSUFFICIENT_INFORMATION"
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(ExperimentRecommendationRecord, result.recommendation_id)
    assert row.recommendation_status == "INSUFFICIENT_INFORMATION"
    assert row.experiment_class is None


def test_exact_field_preservation(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    row = db_session.get(ExperimentRecommendationRecord, result.recommendation_id)

    assert row.objective == recommendation.objective
    assert row.rationale == recommendation.rationale
    assert row.success_criterion == recommendation.success_criterion
    assert row.template_id == recommendation.template_id
    assert row.template_version == recommendation.template_version
    assert row.knowledge_gap_id == gap.id
    assert row.target_entity_type == recommendation.target_entity_type
    assert row.target_entity_id == recommendation.target_entity_id


def test_no_suggested_experiment_write(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    persist_experiment_recommendation(recommendation, knowledge_gap_id=gap.id, session=db_session)
    refreshed = db_session.get(KnowledgeGap, gap.id)
    assert refreshed.suggested_experiment is None


def test_no_model_impact_write(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    persist_experiment_recommendation(recommendation, knowledge_gap_id=gap.id, session=db_session)
    refreshed = db_session.get(KnowledgeGap, gap.id)
    assert refreshed.model_impact is None


# --- Idempotency -------------------------------------------------------------------------------


def test_same_recommendation_twice_creates_one_row(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    first = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    second = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.recommendation_id == second.recommendation_id

    rows = db_session.execute(
        select(ExperimentRecommendationRecord).where(
            ExperimentRecommendationRecord.id == first.recommendation_id
        )
    ).scalars().all()
    assert len(rows) == 1


def test_same_identity_differing_content_raises_conflict(db_session, monkeypatch):
    """Force two structurally different recommendations to collide on the
    same identity (simulating a template-registry bug that changed content
    without bumping its version) -- persistence must refuse, not silently
    reuse or overwrite."""
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)

    monkeypatch.setattr(
        experiment_recommendation_module,
        "compute_recommendation_identity",
        lambda rec: "experiment-rec-v1:forced-collision",
    )

    persist_experiment_recommendation(recommendation, knowledge_gap_id=gap.id, session=db_session)

    other_gap = _persist_gap(db_session, gap_type=GapType.ISOLATED_COMPOUND, entity_type="compound")
    other_recommendation = _recommendation_for(other_gap)
    with pytest.raises(RecommendationIdentityConflictError):
        persist_experiment_recommendation(
            other_recommendation, knowledge_gap_id=other_gap.id, session=db_session
        )


def test_different_template_version_is_distinct(db_session, monkeypatch):
    """Simulate a template-version bump by forging two ExperimentRecommendation
    objects with different template_version -- their identities must differ."""
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    from dataclasses import replace

    bumped = replace(recommendation, template_version="v2")

    first = persist_experiment_recommendation(

        recommendation, knowledge_gap_id=gap.id, session=db_session

    )
    second = persist_experiment_recommendation(
        bumped, knowledge_gap_id=gap.id, session=db_session
    )
    assert first.recommendation_id != second.recommendation_id
    assert second.action is PersistenceAction.CREATED


# --- Terminal gap --------------------------------------------------------------------------------


def test_open_gap_can_receive_recommendation(db_session):
    gap = _persist_gap(db_session)
    assert gap.status == "OPEN"
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.CREATED


def test_resolved_gap_conservative_behavior(db_session):
    gap = _persist_gap(db_session)
    gap.status = "RESOLVED"
    db_session.flush()
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW
    assert result.recommendation_id is None


def test_dismissed_gap_conservative_behavior(db_session):
    gap = _persist_gap(db_session)
    gap.status = "DISMISSED"
    db_session.flush()
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.REQUIRES_REVIEW


def test_reuse_still_allowed_after_gap_becomes_terminal(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    created = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert created.action is PersistenceAction.CREATED

    gap.status = "RESOLVED"
    db_session.flush()

    reused = persist_experiment_recommendation(

        recommendation, knowledge_gap_id=gap.id, session=db_session

    )
    assert reused.action is PersistenceAction.REUSED_EXISTING


# --- Concurrency -----------------------------------------------------------------------------


def test_concurrent_persist_yields_one_row(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as setup_connection:
        setup_session = Session(bind=setup_connection)
        try:
            gap = _persist_gap(setup_session)
            setup_session.commit()
            gap_id = gap.id
        finally:
            setup_session.close()

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                fresh_gap = session.get(KnowledgeGap, gap_id)
                rec = _recommendation_for(fresh_gap)
                ready.wait(timeout=5)
                outcomes[label] = persist_experiment_recommendation(
                    rec, knowledge_gap_id=gap_id, session=session
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
        assert errors == {}, f"persist_experiment_recommendation leaked an exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            count = verify_connection.execute(
                select(ExperimentRecommendationRecord).where(
                    ExperimentRecommendationRecord.knowledge_gap_id == gap_id
                )
            ).all()
            assert len(count) == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                delete(ExperimentRecommendationRecord).where(
                    ExperimentRecommendationRecord.knowledge_gap_id == gap_id
                )
            )
            cleanup_connection.execute(delete(KnowledgeGap).where(KnowledgeGap.id == gap_id))
            cleanup_connection.commit()


# --- Transactions ------------------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(experiment_recommendation_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_uses_savepoint():
    source = inspect.getsource(experiment_recommendation_module)
    assert "begin_nested" in source


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    """Make the next ``session.flush()`` raise ``IntegrityError``.

    An instance-level patch (not a class patch) so the pre-insert
    ``_existing_by_identity`` recheck -- a plain
    ``session.execute(select(ExperimentRecommendationRecord)...)`` -- is
    unaffected; only the ``session.add(row); session.flush()`` inside the
    SAVEPOINT fails, simulating a real database-level integrity violation.
    """

    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    assert result.action is PersistenceAction.FAILED
    assert result.recommendation_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    persist_experiment_recommendation(recommendation, knowledge_gap_id=gap.id, session=db_session)

    monkeypatch.undo()  # restore real flush before querying
    rows = db_session.execute(select(ExperimentRecommendationRecord)).scalars().all()
    assert rows == []


# --- Read APIs ----------------------------------------------------------------------------------


def test_get_experiment_recommendation(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    row = get_experiment_recommendation(db_session, result.recommendation_id)
    assert row is not None
    assert row.id == result.recommendation_id


def test_get_experiment_recommendation_missing_returns_none(db_session):
    assert get_experiment_recommendation(db_session, uuid4()) is None


def test_list_experiment_recommendations_for_gap(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    rows = list_experiment_recommendations_for_gap(db_session, gap.id)
    assert [row.id for row in rows] == [result.recommendation_id]


def test_list_open_experiment_recommendations(db_session):
    gap = _persist_gap(db_session)
    recommendation = _recommendation_for(gap)
    result = persist_experiment_recommendation(
        recommendation, knowledge_gap_id=gap.id, session=db_session
    )
    open_rows = list_open_experiment_recommendations(db_session)
    assert result.recommendation_id in {row.id for row in open_rows}


def test_recommend_and_persist_for_gap(db_session):
    gap = _persist_gap(db_session)
    result = recommend_and_persist_for_gap(gap, session=db_session)
    assert result.action is PersistenceAction.CREATED


# --- Safety --------------------------------------------------------------------------------------


def test_module_never_imports_llm_connector_or_confidence_machinery():
    source = inspect.getsource(experiment_recommendation_module)
    for forbidden in (
        "import openai",
        "import anthropic",
        "import httpx",
        "app.connectors",
        "app.confidence",
        "app.normalization",
    ):
        assert forbidden not in source


def test_module_never_imports_claim_or_evidence_models():
    """This module only ever reads ``KnowledgeGap``/writes
    ``ExperimentRecommendationRecord`` -- it has no reason to import, and
    therefore no ability to mutate, ``Claim``/``Evidence`` ORM rows."""
    source = inspect.getsource(experiment_recommendation_module)
    assert "app.models.claim" not in source


def test_module_never_writes_confidence_or_status_fields():
    source = inspect.getsource(experiment_recommendation_module)
    for forbidden in ("confidence_score", "confidence_class", ".status ="):
        assert forbidden not in source
