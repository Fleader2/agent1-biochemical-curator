"""The public Result Interpretation API (Increment 26).

``interpret_experiment_result``/``interpret_experiment_execution`` are the
only two entry points. Both are pure functions of already-loaded ORM
objects -- neither accepts a database ``Session``, issues a query, or
writes anything: there is no session parameter anywhere in this module's
public or private functions, which is how "no persistence, no database
writes" (Increment 26 instructions, Step 7) is enforced structurally rather
than only by convention. A caller loads whatever
``ExperimentExecution``/``ExperimentResult``/``ExperimentRecommendationRecord``
rows it needs (for example via ``app.persistence.experiment_execution``'s
own read APIs) and passes the already-loaded objects in here.

Neither function mutates its inputs. Neither calls
``app.experiment_recommendation``, ``app.claim_generation``,
``app.confidence``, ``app.review``, or any LLM client -- verified by
source-level tests (``tests/result_interpretation/``).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.extraction.types import Directness
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.result_interpretation.mapping import (
    build_curator_summary,
    build_quoted_support,
    classify_result,
    evidence_type_for,
)
from app.result_interpretation.types import EvidenceCandidate, ExperimentInterpretationContext
from app.result_interpretation.validation import (
    require_matching_execution,
    require_matching_recommendation,
)


def interpret_experiment_result(
    execution: ExperimentExecution,
    result: ExperimentResult,
    *,
    recommendation: ExperimentRecommendationRecord | None = None,
    context: ExperimentInterpretationContext | None = None,
) -> list[EvidenceCandidate]:
    """Deterministically map one ``ExperimentResult`` to one ``EvidenceCandidate``.

    Always returns a list of exactly one element -- a list, not a single
    object, for a uniform return shape with
    ``interpret_experiment_execution`` (Increment 26 instructions, Step 7).
    Every result produces exactly one candidate, including a result whose
    ``result_type`` this package does not recognize
    (``InterpretationStatus.UNSUPPORTED_RESULT_TYPE``) -- a result is never
    silently dropped.
    """
    if not isinstance(execution, ExperimentExecution):
        raise TypeError(
            f"interpret_experiment_result requires an ExperimentExecution, got {execution!r}"
        )
    if not isinstance(result, ExperimentResult):
        raise TypeError(
            f"interpret_experiment_result requires an ExperimentResult, got {result!r}"
        )
    if recommendation is not None and not isinstance(
        recommendation, ExperimentRecommendationRecord
    ):
        raise TypeError(
            "interpret_experiment_result requires recommendation to be an "
            f"ExperimentRecommendationRecord or None, got {recommendation!r}"
        )
    if context is not None and not isinstance(context, ExperimentInterpretationContext):
        raise TypeError(
            "interpret_experiment_result requires context to be an "
            f"ExperimentInterpretationContext or None, got {context!r}"
        )

    require_matching_execution(execution.id, result.execution_id)
    if recommendation is not None:
        require_matching_recommendation(recommendation.id, execution.recommendation_id)

    resolved_context = context if context is not None else ExperimentInterpretationContext()

    status = classify_result(result)
    evidence_type = evidence_type_for(recommendation)
    quoted_support = build_quoted_support(result, status)
    curator_summary = build_curator_summary(result, status)

    candidate = EvidenceCandidate(
        experiment_execution_id=execution.id,
        experiment_result_id=result.id,
        result_type=result.result_type,
        measurement_name=result.measurement_name,
        value_text=result.value_text,
        value_numeric=result.value_numeric,
        unit=result.unit,
        statistical_support=result.statistical_support,
        sample_identifier=result.sample_identifier,
        replicate_identifier=result.replicate_identifier,
        time_point=result.time_point,
        condition_label=result.condition_label,
        raw_data_reference=result.raw_data_reference,
        observed_at=result.observed_at,
        notes=result.notes,
        candidate_subject_text=resolved_context.candidate_subject_text,
        candidate_predicate_text=resolved_context.candidate_predicate_text,
        candidate_object_text=resolved_context.candidate_object_text,
        evidence_type=evidence_type,
        directness=Directness.AUTHORS_OBSERVED,
        quoted_support=quoted_support,
        curator_summary=curator_summary,
        interpretation_status=status,
    )
    return [candidate]


def interpret_experiment_execution(
    execution: ExperimentExecution,
    results: Sequence[ExperimentResult],
    *,
    recommendation: ExperimentRecommendationRecord | None = None,
    context: ExperimentInterpretationContext | None = None,
) -> list[EvidenceCandidate]:
    """Deterministically map every result of one execution to its own ``EvidenceCandidate``.

    A thin, visible fan-out over ``interpret_experiment_result`` -- one call
    per result, in the exact order ``results`` was supplied, never re-sorted
    or deduplicated. Results are never aggregated or averaged together
    (Increment 25's own "no aggregation" rule carries forward unchanged).
    """
    if not isinstance(execution, ExperimentExecution):
        raise TypeError(
            f"interpret_experiment_execution requires an ExperimentExecution, got {execution!r}"
        )
    if not isinstance(results, Sequence) or isinstance(results, str | bytes):
        raise TypeError(
            f"interpret_experiment_execution requires a Sequence of ExperimentResult, "
            f"got {results!r}"
        )

    candidates: list[EvidenceCandidate] = []
    for result in results:
        candidates.extend(
            interpret_experiment_result(
                execution, result, recommendation=recommendation, context=context
            )
        )
    return candidates


__all__ = ["interpret_experiment_execution", "interpret_experiment_result"]
