"""Tests for ``app.result_interpretation``."""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.experiment_recommendation.recommender import recommend_experiment_for_persisted_gap
from app.extraction.types import Directness
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim, Evidence
from app.models.enums import EvidenceType
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.experiment_execution import (
    persist_experiment_execution,
    record_experiment_result,
)
from app.persistence.experiment_execution_types import ExperimentResultInput, ResultType
from app.persistence.experiment_recommendation import persist_experiment_recommendation
from app.persistence.knowledge_gap import persist_knowledge_gap
from app.result_interpretation import interpreter as interpreter_module
from app.result_interpretation import mapping as mapping_module
from app.result_interpretation.errors import InterpretationValidationError
from app.result_interpretation.interpreter import (
    interpret_experiment_execution,
    interpret_experiment_result,
)
from app.result_interpretation.types import (
    EvidenceCandidate,
    ExperimentInterpretationContext,
    InterpretationStatus,
)
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


def _in_progress_execution(session, **gap_overrides) -> ExperimentExecution:
    gap = _persist_gap(session, **gap_overrides)
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
    session.flush()
    return session.get(ExperimentExecution, execution.id)


def _record(session, execution, **overrides) -> ExperimentResult:
    merged = {
        "result_identifier": "obs-1",
        "result_type": ResultType.QUANTITATIVE_MEASUREMENT,
        "measurement_name": "enzyme activity",
        "value_numeric": Decimal("5.2"),
        "unit": "umol/min/mg",
    } | overrides
    result = record_experiment_result(
        execution_id=execution.id, result=ExperimentResultInput(**merged), session=session
    )
    return session.get(ExperimentResult, result.result_id)


def _recommendation_for(session, execution) -> ExperimentRecommendationRecord:
    return session.get(ExperimentRecommendationRecord, execution.recommendation_id)


# --- Quantitative measurements -----------------------------------------------------------------


def test_quantitative_measurement_produces_direct_observation(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidates = interpret_experiment_result(execution, result)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.interpretation_status is InterpretationStatus.DIRECT_OBSERVATION
    assert candidate.value_numeric == Decimal("5.2")
    assert candidate.unit == "umol/min/mg"
    assert candidate.measurement_name == "enzyme activity"
    assert candidate.quoted_support == "Measured enzyme activity: 5.2 umol/min/mg."
    assert candidate.curator_summary == "Experiment measured enzyme activity of 5.2 umol/min/mg."


def test_quantitative_measurement_preserves_exact_decimal(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution, value_numeric=Decimal("0.00100"))
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.value_numeric == Decimal("0.00100")
    assert "0.00100" in candidate.quoted_support


def test_directness_is_always_authors_observed(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.directness is Directness.AUTHORS_OBSERVED


def test_evidence_type_reused_from_existing_enum(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result)[0]
    assert isinstance(candidate.evidence_type, EvidenceType)


# --- Qualitative observations -----------------------------------------------------------------


def test_qualitative_observation_status(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(
        db_session,
        execution,
        result_identifier="obs-qual",
        result_type=ResultType.QUALITATIVE_OBSERVATION,
        measurement_name="colony_morphology",
        value_numeric=None,
        unit=None,
        value_text="smooth, white colonies",
    )
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.interpretation_status is InterpretationStatus.QUALITATIVE_OBSERVATION
    assert candidate.quoted_support == "smooth, white colonies"
    assert candidate.curator_summary == (
        "Experiment observed colony_morphology: smooth, white colonies."
    )


def test_assay_outcome_treated_as_qualitative(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(
        db_session,
        execution,
        result_identifier="obs-assay",
        result_type=ResultType.ASSAY_OUTCOME,
        measurement_name="growth_assay",
        value_numeric=None,
        unit=None,
        value_text="growth observed",
    )
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.interpretation_status is InterpretationStatus.QUALITATIVE_OBSERVATION


# --- Non-detection -----------------------------------------------------------------------------


def test_non_detection_status_and_verbatim_quote(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(
        db_session,
        execution,
        result_identifier="obs-nd",
        result_type=ResultType.NON_DETECTION,
        measurement_name="activity",
        value_numeric=None,
        unit=None,
        value_text="No activity detected",
    )
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.interpretation_status is InterpretationStatus.NON_DETECTION
    assert candidate.quoted_support == "No activity detected"
    assert candidate.curator_summary == "Experiment found no detectable activity."


def test_non_detection_never_rewritten_into_interpretation(db_session):
    """Increment 26 instructions, Step 9: never rewrite into absence, negative
    biology, failure, or any interpretation."""
    execution = _in_progress_execution(db_session)
    result = _record(
        db_session,
        execution,
        result_identifier="obs-nd2",
        result_type=ResultType.NON_DETECTION,
        measurement_name="activity",
        value_numeric=None,
        unit=None,
        value_text="No activity detected",
    )
    candidate = interpret_experiment_result(execution, result)[0]
    for forbidden in ("absence", "negative biology", "failure", "mechanism"):
        assert forbidden not in candidate.quoted_support.lower()
        assert forbidden not in candidate.curator_summary.lower()


# --- Unsupported result types --------------------------------------------------------------------


def test_unsupported_result_type_still_produces_candidate(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    # Force an unrecognized result_type value on the in-memory object only
    # (never flushed -- the database CHECK constraint would reject it) --
    # simulating a future schema value this package does not yet know about.
    result.result_type = "SOME_FUTURE_RESULT_TYPE"

    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.interpretation_status is InterpretationStatus.UNSUPPORTED_RESULT_TYPE
    assert candidate.result_type == "SOME_FUTURE_RESULT_TYPE"


def test_unsupported_result_type_error_never_raised_by_default(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    result.result_type = "SOME_FUTURE_RESULT_TYPE"
    # Must not raise -- a candidate is always produced instead.
    interpret_experiment_result(execution, result)


# --- Multiple results ----------------------------------------------------------------------------


def test_interpret_experiment_execution_maps_every_result(db_session):
    execution = _in_progress_execution(db_session)
    results = [
        _record(db_session, execution, result_identifier="obs-a"),
        _record(db_session, execution, result_identifier="obs-b", value_numeric=Decimal("7.0")),
        _record(
            db_session,
            execution,
            result_identifier="obs-c",
            result_type=ResultType.NON_DETECTION,
            value_numeric=None,
            unit=None,
            value_text="No activity detected",
        ),
    ]
    candidates = interpret_experiment_execution(execution, results)
    assert len(candidates) == 3
    assert [c.experiment_result_id for c in candidates] == [r.id for r in results]


def test_interpret_experiment_execution_preserves_order(db_session):
    execution = _in_progress_execution(db_session)
    results = [
        _record(db_session, execution, result_identifier=f"obs-{i}", value_numeric=Decimal(i))
        for i in range(5)
    ]
    candidates = interpret_experiment_execution(execution, results)
    assert [c.value_numeric for c in candidates] == [Decimal(i) for i in range(5)]


def test_results_never_aggregated(db_session):
    execution = _in_progress_execution(db_session)
    results = [
        _record(db_session, execution, result_identifier="obs-r1", replicate_identifier="rep-1"),
        _record(db_session, execution, result_identifier="obs-r2", replicate_identifier="rep-2"),
    ]
    candidates = interpret_experiment_execution(execution, results)
    assert {c.replicate_identifier for c in candidates} == {"rep-1", "rep-2"}


# --- Deterministic output --------------------------------------------------------------------


def test_interpretation_is_deterministic(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    first = interpret_experiment_result(execution, result)[0]
    second = interpret_experiment_result(execution, result)[0]
    assert dataclasses.asdict(first) == dataclasses.asdict(second)


# --- Recommendation context / subject-predicate-object ------------------------------------------


def test_no_recommendation_supplied_yields_other_evidence_type(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.evidence_type is EvidenceType.OTHER


def test_subject_predicate_object_null_without_context(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result)[0]
    assert candidate.candidate_subject_text is None
    assert candidate.candidate_predicate_text is None
    assert candidate.candidate_object_text is None


def test_context_supplied_is_copied_verbatim(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    context = ExperimentInterpretationContext(
        candidate_subject_text="FadD",
        candidate_predicate_text="catalyzes",
        candidate_object_text="oleoyl-CoA formation",
    )
    candidate = interpret_experiment_result(execution, result, context=context)[0]
    assert candidate.candidate_subject_text == "FadD"
    assert candidate.candidate_predicate_text == "catalyzes"
    assert candidate.candidate_object_text == "oleoyl-CoA formation"


def test_recommendation_experiment_class_drives_evidence_type(db_session):
    execution = _in_progress_execution(db_session)
    recommendation = _recommendation_for(db_session, execution)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result, recommendation=recommendation)[0]
    # Whatever the deterministic mapping table produces, it must be a real EvidenceType.
    assert isinstance(candidate.evidence_type, EvidenceType)


# --- Validation failures --------------------------------------------------------------------


def test_mismatched_execution_raises(db_session):
    execution_a = _in_progress_execution(db_session)
    execution_b = _in_progress_execution(db_session)
    result = _record(db_session, execution_a)
    with pytest.raises(InterpretationValidationError):
        interpret_experiment_result(execution_b, result)


def test_mismatched_recommendation_raises(db_session):
    execution = _in_progress_execution(db_session)
    other_execution = _in_progress_execution(db_session)
    other_recommendation = _recommendation_for(db_session, other_execution)
    result = _record(db_session, execution)
    with pytest.raises(InterpretationValidationError):
        interpret_experiment_result(execution, result, recommendation=other_recommendation)


def test_interpret_experiment_result_type_checks_execution():
    with pytest.raises(TypeError):
        interpret_experiment_result("not-an-execution", "not-a-result")


def test_interpret_experiment_execution_type_checks_results(db_session):
    execution = _in_progress_execution(db_session)
    with pytest.raises(TypeError):
        interpret_experiment_execution(execution, "not-a-sequence")


def test_evidence_candidate_requires_at_least_one_value():
    with pytest.raises(ValueError):
        EvidenceCandidate(
            experiment_execution_id=uuid4(),
            experiment_result_id=uuid4(),
            result_type="QUANTITATIVE_MEASUREMENT",
            measurement_name="x",
            value_text=None,
            value_numeric=None,
            unit=None,
            statistical_support=None,
            sample_identifier=None,
            replicate_identifier=None,
            time_point=None,
            condition_label=None,
            raw_data_reference=None,
            observed_at=None,
            notes=None,
            candidate_subject_text=None,
            candidate_predicate_text=None,
            candidate_object_text=None,
            evidence_type=EvidenceType.OTHER,
            directness=Directness.AUTHORS_OBSERVED,
            quoted_support="x",
            curator_summary="x",
            interpretation_status=InterpretationStatus.INSUFFICIENT_INFORMATION,
        )


def test_evidence_candidate_rejects_float_value_numeric():
    with pytest.raises(TypeError):
        EvidenceCandidate(
            experiment_execution_id=uuid4(),
            experiment_result_id=uuid4(),
            result_type="QUANTITATIVE_MEASUREMENT",
            measurement_name="x",
            value_text=None,
            value_numeric=1.23,
            unit=None,
            statistical_support=None,
            sample_identifier=None,
            replicate_identifier=None,
            time_point=None,
            condition_label=None,
            raw_data_reference=None,
            observed_at=None,
            notes=None,
            candidate_subject_text=None,
            candidate_predicate_text=None,
            candidate_object_text=None,
            evidence_type=EvidenceType.OTHER,
            directness=Directness.AUTHORS_OBSERVED,
            quoted_support="x",
            curator_summary="x",
            interpretation_status=InterpretationStatus.DIRECT_OBSERVATION,
        )


def test_evidence_candidate_rejects_invalid_evidence_type():
    with pytest.raises(TypeError):
        EvidenceCandidate(
            experiment_execution_id=uuid4(),
            experiment_result_id=uuid4(),
            result_type="QUANTITATIVE_MEASUREMENT",
            measurement_name="x",
            value_text="observed",
            value_numeric=None,
            unit=None,
            statistical_support=None,
            sample_identifier=None,
            replicate_identifier=None,
            time_point=None,
            condition_label=None,
            raw_data_reference=None,
            observed_at=None,
            notes=None,
            candidate_subject_text=None,
            candidate_predicate_text=None,
            candidate_object_text=None,
            evidence_type="NOT_A_REAL_TYPE",
            directness=Directness.AUTHORS_OBSERVED,
            quoted_support="x",
            curator_summary="x",
            interpretation_status=InterpretationStatus.QUALITATIVE_OBSERVATION,
        )


def test_evidence_candidate_rejects_naive_observed_at():
    with pytest.raises(ValueError):
        EvidenceCandidate(
            experiment_execution_id=uuid4(),
            experiment_result_id=uuid4(),
            result_type="QUANTITATIVE_MEASUREMENT",
            measurement_name="x",
            value_text="observed",
            value_numeric=None,
            unit=None,
            statistical_support=None,
            sample_identifier=None,
            replicate_identifier=None,
            time_point=None,
            condition_label=None,
            raw_data_reference=None,
            observed_at=datetime(2024, 1, 1),  # naive
            notes=None,
            candidate_subject_text=None,
            candidate_predicate_text=None,
            candidate_object_text=None,
            evidence_type=EvidenceType.OTHER,
            directness=Directness.AUTHORS_OBSERVED,
            quoted_support="x",
            curator_summary="x",
            interpretation_status=InterpretationStatus.QUALITATIVE_OBSERVATION,
        )


def test_evidence_candidate_requires_non_empty_quoted_support():
    with pytest.raises(ValueError):
        EvidenceCandidate(
            experiment_execution_id=uuid4(),
            experiment_result_id=uuid4(),
            result_type="QUANTITATIVE_MEASUREMENT",
            measurement_name="x",
            value_text="observed",
            value_numeric=None,
            unit=None,
            statistical_support=None,
            sample_identifier=None,
            replicate_identifier=None,
            time_point=None,
            condition_label=None,
            raw_data_reference=None,
            observed_at=None,
            notes=None,
            candidate_subject_text=None,
            candidate_predicate_text=None,
            candidate_object_text=None,
            evidence_type=EvidenceType.OTHER,
            directness=Directness.AUTHORS_OBSERVED,
            quoted_support="   ",
            curator_summary="x",
            interpretation_status=InterpretationStatus.QUALITATIVE_OBSERVATION,
        )


# --- Immutable outputs -----------------------------------------------------------------------


def test_evidence_candidate_is_frozen(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    candidate = interpret_experiment_result(execution, result)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.value_numeric = Decimal("999")


def test_context_is_frozen():
    context = ExperimentInterpretationContext(candidate_subject_text="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.candidate_subject_text = "y"


# --- No persistence / no DB writes -------------------------------------------------------------


def test_interpreter_module_never_imports_sqlalchemy_session():
    """Checks the actual import, not the bare word (which this module's own
    docstring uses in prose describing what it does *not* accept)."""
    source = inspect.getsource(interpreter_module)
    assert "import Session" not in source
    assert "sqlalchemy.orm" not in source


def test_interpreter_module_never_commits_or_adds():
    source = inspect.getsource(interpreter_module)
    for forbidden in ("session.add(", "session.commit(", "session.flush(", ".add(", "INSERT INTO"):
        assert forbidden not in source


def test_mapping_module_never_imports_sqlalchemy_session():
    source = inspect.getsource(mapping_module)
    assert "import Session" not in source
    assert "sqlalchemy.orm" not in source


# --- No mutation of Claim/Evidence/KnowledgeGap/confidence --------------------------------------


def test_no_claim_created(db_session):
    before = len(db_session.execute(select(Claim)).scalars().all())
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    interpret_experiment_result(execution, result)
    after = len(db_session.execute(select(Claim)).scalars().all())
    assert before == after


def test_no_evidence_created(db_session):
    before = len(db_session.execute(select(Evidence)).scalars().all())
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    interpret_experiment_result(execution, result)
    after = len(db_session.execute(select(Evidence)).scalars().all())
    assert before == after


def test_no_knowledge_gap_mutation(db_session):
    execution = _in_progress_execution(db_session)
    recommendation = _recommendation_for(db_session, execution)
    knowledge_gap = db_session.get(KnowledgeGap, recommendation.knowledge_gap_id)
    status_before = knowledge_gap.status
    result = _record(db_session, execution)
    interpret_experiment_result(execution, result, recommendation=recommendation)
    refreshed = db_session.get(KnowledgeGap, recommendation.knowledge_gap_id)
    assert refreshed.status == status_before


def test_no_experiment_result_mutation(db_session):
    execution = _in_progress_execution(db_session)
    result = _record(db_session, execution)
    value_before = result.value_numeric
    interpret_experiment_result(execution, result)
    refreshed = db_session.get(ExperimentResult, result.id)
    assert refreshed.value_numeric == value_before


def test_module_never_imports_confidence_or_review():
    """Checks actual import statements, not the bare dotted paths (which this
    module's own docstring uses in prose describing what it does *not*
    call)."""
    source = inspect.getsource(interpreter_module)
    for forbidden in (
        "import app.confidence",
        "from app.confidence",
        "import app.review.workflow",
        "from app.review.workflow",
        "import app.claim_generation",
        "from app.claim_generation",
        "import openai",
        "import anthropic",
    ):
        assert forbidden not in source


def test_module_never_writes_claim_or_evidence_models():
    source = inspect.getsource(interpreter_module)
    assert "from app.models.claim" not in source
