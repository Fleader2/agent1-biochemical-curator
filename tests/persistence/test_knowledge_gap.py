"""Tests for ``app.persistence.knowledge_gap``."""

from __future__ import annotations

import inspect
import threading
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.knowledge_gaps.types import (
    GapSeverity,
    GapType,
    KnowledgeGapAnalysisResult,
    KnowledgeGapCandidate,
)
from app.models.knowledge_gap import KnowledgeGap
from app.persistence import knowledge_gap as knowledge_gap_module
from app.persistence.knowledge_gap import (
    compute_identity_key,
    get_knowledge_gap,
    list_open_knowledge_gaps,
    persist_knowledge_gap,
    persist_knowledge_gap_analysis,
)
from app.persistence.knowledge_gap_types import KnowledgeGapPersistenceResult, KnowledgeGapStatus
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _candidate(**overrides) -> KnowledgeGapCandidate:
    merged = {
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "severity": GapSeverity.HIGH,
        "entity_type": "reaction",
        "entity_id": uuid4(),
        "explanation": "Reaction X has no associated ReactionParticipant rows.",
    } | overrides
    return KnowledgeGapCandidate(**merged)


def _claim_candidate(**overrides) -> KnowledgeGapCandidate:
    merged = {
        "gap_type": GapType.LOW_CONFIDENCE_CLAIM,
        "severity": GapSeverity.MODERATE,
        "entity_type": "claim",
        "entity_id": uuid4(),
        "explanation": "Claim X has persisted confidence_class LOW.",
        "predicate": "activates",
        "supporting_claim_ids": (uuid4(),),
        "supporting_evidence_ids": (uuid4(), uuid4()),
        "reason_codes": ("CONFIDENCE_CLASS_LOW",),
    } | overrides
    return KnowledgeGapCandidate(**merged)


# --- Schema ------------------------------------------------------------------------------


def test_gap_type_and_severity_columns_exist(db_session):
    candidate = _candidate()
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.gap_type == "REACTION_WITHOUT_PARTICIPANTS"
    assert row.severity == "HIGH"


def test_reason_codes_persisted_as_json_list(db_session):
    candidate = _claim_candidate(reason_codes=("CONFIDENCE_CLASS_LOW", "SOME_OTHER_CODE"))
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.reason_codes_json == ["CONFIDENCE_CLASS_LOW", "SOME_OTHER_CODE"]


def test_supporting_claim_ids_persisted(db_session):
    claim_id = uuid4()
    candidate = _claim_candidate(supporting_claim_ids=(claim_id,))
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.supporting_claim_ids_json == [str(claim_id)]


def test_supporting_evidence_ids_persisted_independently(db_session):
    claim_id = uuid4()
    evidence_ids = (uuid4(), uuid4())
    candidate = _claim_candidate(
        supporting_claim_ids=(claim_id,), supporting_evidence_ids=evidence_ids
    )
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.supporting_claim_ids_json == [str(claim_id)]
    assert row.supporting_evidence_ids_json == [str(i) for i in evidence_ids]
    assert row.supporting_claim_ids_json != row.supporting_evidence_ids_json


def test_subject_mapping(db_session):
    entity_id = uuid4()
    candidate = _candidate(entity_type="reaction", entity_id=entity_id)
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.subject_type == "reaction"
    assert row.subject_id == entity_id


def test_status_defaults_to_open(db_session):
    result = persist_knowledge_gap(_candidate(), session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.status == "OPEN"


def test_identity_key_unique_constraint_exists_at_db_level(db_session):
    candidate = _candidate()
    identity_key = compute_identity_key(candidate)
    db_session.add(
        KnowledgeGap(subject_type="reaction", missing_information="x", identity_key=identity_key)
    )
    db_session.flush()
    db_session.add(
        KnowledgeGap(subject_type="reaction", missing_information="y", identity_key=identity_key)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- Creation ------------------------------------------------------------------------------


def test_valid_candidate_creates_row(db_session):
    result = persist_knowledge_gap(_candidate(), session=db_session)
    assert result.action is PersistenceAction.CREATED
    assert result.created is True
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row is not None


def test_explanation_maps_verbatim(db_session):
    candidate = _candidate(
        explanation="Reaction R00042 has no associated ReactionParticipant rows."
    )
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    expected = "Reaction R00042 has no associated ReactionParticipant rows."
    assert row.missing_information == expected


def test_severity_and_gap_type_preserved(db_session):
    candidate = _candidate(
        gap_type=GapType.ISOLATED_COMPOUND, severity=GapSeverity.LOW, entity_type="compound"
    )
    result = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.gap_type == "ISOLATED_COMPOUND"
    assert row.severity == "LOW"


def test_suggested_experiment_remains_null(db_session):
    result = persist_knowledge_gap(_candidate(), session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.suggested_experiment is None


def test_model_impact_remains_null_unless_supplied(db_session):
    without = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    assert db_session.get(KnowledgeGap, without.knowledge_gap_id).model_impact is None

    with_impact = persist_knowledge_gap(
        _candidate(entity_id=uuid4()),
        session=db_session,
        model_impact="Blocks stoichiometric balancing.",
    )
    row = db_session.get(KnowledgeGap, with_impact.knowledge_gap_id)
    assert row.model_impact == "Blocks stoichiometric balancing."


def test_priority_remains_null_unless_supplied(db_session):
    without = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    assert db_session.get(KnowledgeGap, without.knowledge_gap_id).priority is None

    with_priority = persist_knowledge_gap(
        _candidate(entity_id=uuid4()), session=db_session, priority=2
    )
    assert db_session.get(KnowledgeGap, with_priority.knowledge_gap_id).priority == 2


def test_importance_never_populated_automatically(db_session):
    result = persist_knowledge_gap(_candidate(), session=db_session)
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.importance is None


# --- Idempotency -----------------------------------------------------------------------------


def test_same_candidate_twice_creates_one_row(db_session):
    candidate = _candidate(entity_id=uuid4())
    first = persist_knowledge_gap(candidate, session=db_session)
    second = persist_knowledge_gap(candidate, session=db_session)

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.knowledge_gap_id == second.knowledge_gap_id

    rows = db_session.execute(
        select(KnowledgeGap).where(KnowledgeGap.id == first.knowledge_gap_id)
    ).scalars().all()
    assert len(rows) == 1


def test_supporting_id_order_does_not_change_identity(db_session):
    a, b = uuid4(), uuid4()
    first = _claim_candidate(supporting_claim_ids=(a, b))
    second = _claim_candidate(entity_id=first.entity_id, supporting_claim_ids=(b, a))

    result_one = persist_knowledge_gap(first, session=db_session)
    result_two = persist_knowledge_gap(second, session=db_session)

    assert result_one.action is PersistenceAction.CREATED
    assert result_two.action is PersistenceAction.REUSED_EXISTING
    assert result_one.knowledge_gap_id == result_two.knowledge_gap_id


def test_explanation_text_does_not_define_identity(db_session):
    entity_id = uuid4()
    first = _candidate(entity_id=entity_id, explanation="First explanation.")
    second = _candidate(entity_id=entity_id, explanation="A completely different explanation.")

    result_one = persist_knowledge_gap(first, session=db_session)
    result_two = persist_knowledge_gap(second, session=db_session)

    assert result_two.action is PersistenceAction.REUSED_EXISTING
    assert result_one.knowledge_gap_id == result_two.knowledge_gap_id
    # The second call's differing explanation must never overwrite the first.
    row = db_session.get(KnowledgeGap, result_one.knowledge_gap_id)
    assert row.missing_information == "First explanation."


# --- Different gaps ----------------------------------------------------------------------------


def test_same_entity_different_gap_type_is_distinct(db_session):
    entity_id = uuid4()
    first = persist_knowledge_gap(
        _candidate(entity_id=entity_id, gap_type=GapType.REACTION_WITHOUT_PARTICIPANTS),
        session=db_session,
    )
    second = persist_knowledge_gap(
        _candidate(entity_id=entity_id, gap_type=GapType.REACTION_WITHOUT_ENZYME),
        session=db_session,
    )
    assert first.knowledge_gap_id != second.knowledge_gap_id
    assert second.action is PersistenceAction.CREATED


def test_same_gap_type_different_entity_is_distinct(db_session):
    first = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    second = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    assert first.knowledge_gap_id != second.knowledge_gap_id


def test_same_gap_type_entity_different_supporting_claims_is_distinct(db_session):
    entity_id = uuid4()
    first = persist_knowledge_gap(
        _claim_candidate(entity_id=entity_id, supporting_claim_ids=(uuid4(),)), session=db_session
    )
    second = persist_knowledge_gap(
        _claim_candidate(entity_id=entity_id, supporting_claim_ids=(uuid4(),)), session=db_session
    )
    assert first.knowledge_gap_id != second.knowledge_gap_id


# --- Status ------------------------------------------------------------------------------------


def test_open_creation_explicit(db_session):
    result = persist_knowledge_gap(
        _candidate(), session=db_session, status=KnowledgeGapStatus.OPEN.value
    )
    row = db_session.get(KnowledgeGap, result.knowledge_gap_id)
    assert row.status == "OPEN"


def test_unknown_status_rejected(db_session):
    with pytest.raises(ValueError):
        persist_knowledge_gap(_candidate(), session=db_session, status="whatever")


def test_resolved_gap_returns_requires_review_on_recurrence(db_session):
    candidate = _candidate(entity_id=uuid4())
    created = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, created.knowledge_gap_id)
    row.status = "RESOLVED"
    db_session.flush()

    recurrence = persist_knowledge_gap(candidate, session=db_session)
    assert recurrence.action is PersistenceAction.REQUIRES_REVIEW
    assert recurrence.review_required is True
    assert recurrence.knowledge_gap_id == created.knowledge_gap_id

    # No duplicate row was created.
    rows = db_session.execute(
        select(KnowledgeGap).where(KnowledgeGap.identity_key == created.identity_key)
    ).scalars().all()
    assert len(rows) == 1


def test_dismissed_gap_returns_requires_review_on_recurrence(db_session):
    candidate = _candidate(entity_id=uuid4())
    created = persist_knowledge_gap(candidate, session=db_session)
    row = db_session.get(KnowledgeGap, created.knowledge_gap_id)
    row.status = "DISMISSED"
    db_session.flush()

    recurrence = persist_knowledge_gap(candidate, session=db_session)
    assert recurrence.action is PersistenceAction.REQUIRES_REVIEW


# --- Concurrency ---------------------------------------------------------------------------------


def test_concurrent_same_gap_creation_yields_one_row(migrated_engine: Engine) -> None:
    entity_id = uuid4()
    candidate = _candidate(entity_id=entity_id)
    identity_key = compute_identity_key(candidate)

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                ready.wait(timeout=5)
                outcomes[label] = persist_knowledge_gap(candidate, session=session)
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
        assert errors == {}, f"persist_knowledge_gap leaked an unhandled exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            count = verify_connection.execute(
                select(KnowledgeGap).where(KnowledgeGap.identity_key == identity_key)
            ).all()
            assert len(count) == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                delete(KnowledgeGap).where(KnowledgeGap.identity_key == identity_key)
            )
            cleanup_connection.commit()


# --- Transactions --------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(knowledge_gap_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_uses_savepoint():
    source = inspect.getsource(knowledge_gap_module)
    assert "begin_nested" in source


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    """Make the next ``session.flush()`` raise ``IntegrityError``.

    An instance-level patch (not a ``KnowledgeGap`` class patch) so the
    pre-insert ``_existing_by_identity_key`` recheck -- a plain
    ``session.execute(select(KnowledgeGap)...)`` -- is unaffected; only the
    ``session.add(row); session.flush()`` inside the SAVEPOINT fails,
    exactly simulating a real database-level integrity violation unrelated
    to the identity-key race path.
    """

    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    """An IntegrityError from an unrelated cause (not the identity-key race
    path) must still yield the conservative FAILED result when no matching
    row is found on recheck."""
    candidate = _candidate(entity_id=uuid4())
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = persist_knowledge_gap(candidate, session=db_session)
    assert result.action is PersistenceAction.FAILED
    assert result.knowledge_gap_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    candidate = _candidate(entity_id=uuid4())
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    persist_knowledge_gap(candidate, session=db_session)

    monkeypatch.undo()  # restore real flush before querying
    rows = db_session.execute(select(KnowledgeGap)).scalars().all()
    assert rows == []


# --- Batch ----------------------------------------------------------------------------


def _analysis_result(*candidates: KnowledgeGapCandidate) -> KnowledgeGapAnalysisResult:
    return KnowledgeGapAnalysisResult(
        gaps=tuple(candidates),
        analyzed_claim_count=0,
        analyzed_evidence_count=0,
        analyzed_entity_count=len(candidates),
        summary_statistics={},
    )


def test_batch_result_count_matches_candidate_count(db_session):
    candidates = [_candidate(entity_id=uuid4()) for _ in range(3)]
    results = persist_knowledge_gap_analysis(_analysis_result(*candidates), session=db_session)
    assert len(results) == 3
    assert all(r.action is PersistenceAction.CREATED for r in results)


def test_batch_result_ordering_matches_input_ordering(db_session):
    candidates = [_candidate(entity_id=uuid4()) for _ in range(3)]
    results = persist_knowledge_gap_analysis(_analysis_result(*candidates), session=db_session)
    expected_keys = [compute_identity_key(c) for c in candidates]
    actual_keys = [r.identity_key for r in results]
    assert actual_keys == expected_keys


def test_batch_duplicate_candidates_within_one_batch_idempotent(db_session):
    candidate = _candidate(entity_id=uuid4())
    results = persist_knowledge_gap_analysis(
        _analysis_result(candidate, candidate), session=db_session
    )
    assert results[0].action is PersistenceAction.CREATED
    assert results[1].action is PersistenceAction.REUSED_EXISTING
    assert results[0].knowledge_gap_id == results[1].knowledge_gap_id


# --- Read API -------------------------------------------------------------------------


def test_get_knowledge_gap_returns_row(db_session):
    result = persist_knowledge_gap(_candidate(), session=db_session)
    row = get_knowledge_gap(db_session, result.knowledge_gap_id)
    assert row is not None
    assert row.id == result.knowledge_gap_id


def test_get_knowledge_gap_returns_none_for_missing_id(db_session):
    assert get_knowledge_gap(db_session, uuid4()) is None


def test_list_open_knowledge_gaps_excludes_resolved(db_session):
    open_result = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    resolved_result = persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    resolved_row = db_session.get(KnowledgeGap, resolved_result.knowledge_gap_id)
    resolved_row.status = "RESOLVED"
    db_session.flush()

    open_gaps = list_open_knowledge_gaps(db_session)
    ids = {gap.id for gap in open_gaps}
    assert open_result.knowledge_gap_id in ids
    assert resolved_result.knowledge_gap_id not in ids


def test_list_open_knowledge_gaps_deterministic_ordering(db_session):
    """Same DB state must produce identical ordering on repeated calls.

    Both rows are created inside the same test transaction, so
    ``created_at`` (PostgreSQL's ``now()``, fixed for the whole transaction)
    legitimately ties between them -- this asserts repeatability, not an
    assumed insertion order (see ``list_open_knowledge_gaps``'s own
    docstring for the identical, disclosed tiebreak caveat
    ``app.review.history.get_review_history`` already documents)."""
    persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)
    persist_knowledge_gap(_candidate(entity_id=uuid4()), session=db_session)

    first_call = [gap.id for gap in list_open_knowledge_gaps(db_session)]
    second_call = [gap.id for gap in list_open_knowledge_gaps(db_session)]
    assert first_call == second_call


# --- Safety ---------------------------------------------------------------------------


def test_module_never_calls_analyze_knowledge_gaps():
    """Checks the actual call pattern (open paren), not the module's own
    docstring, which mentions the name in prose describing what this module
    does *not* do."""
    source = inspect.getsource(knowledge_gap_module)
    assert "analyze_knowledge_gaps(" not in source


def test_module_never_imports_llm_connector_or_confidence_machinery():
    """Checks actual import statements, not bare substrings -- the module's
    own docstring uses the word "confidence" in prose."""
    source = inspect.getsource(knowledge_gap_module)
    for forbidden in (
        "import openai",
        "import anthropic",
        "import httpx",
        "app.connectors",
        "app.confidence",
        "app.normalization",
        "app.entity_resolution",
    ):
        assert forbidden not in source


def test_persist_does_not_mutate_candidate(db_session):
    candidate = _claim_candidate(entity_id=uuid4())
    original_reason_codes = candidate.reason_codes
    persist_knowledge_gap(candidate, session=db_session)
    assert candidate.reason_codes == original_reason_codes


def test_rejects_non_candidate_type(db_session):
    with pytest.raises(TypeError):
        persist_knowledge_gap("not a candidate", session=db_session)


def test_result_type_is_frozen():
    result = KnowledgeGapPersistenceResult(
        action=PersistenceAction.CREATED,
        knowledge_gap_id=uuid4(),
        created=True,
        reused_existing=False,
        identity_key="kg-v1:abc",
    )
    with pytest.raises(AttributeError):
        result.created = False  # type: ignore[misc]
