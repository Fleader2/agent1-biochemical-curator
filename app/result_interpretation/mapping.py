"""Deterministic mapping rules (Increment 26).

Every function here is a pure function of its inputs: same
``ExperimentResult``/``ExperimentRecommendationRecord`` in, same output,
every time. No randomness, no wall-clock reads, no LLM calls, no database
access. This is the only module in this package that decides
``InterpretationStatus``, ``evidence_type``, ``quoted_support``, or
``curator_summary`` -- ``app.result_interpretation.interpreter`` only
orchestrates calls into this module and ``app.result_interpretation.types``,
it makes none of these decisions itself.

**Result-type classification.** ``ExperimentResult.result_type`` is parsed
against ``app.persistence.experiment_execution_types.ResultType`` --
reused directly, never redefined -- and mapped to an
``InterpretationStatus`` by data shape, not by inventing per-type
biological meaning: a present ``value_numeric`` is a direct observation, an
explicit ``NON_DETECTION`` result type is always ``NON_DETECTION``, and a
result carrying only descriptive text is a qualitative observation. A
``result_type`` string that does not parse as a known ``ResultType`` value
at all yields ``UNSUPPORTED_RESULT_TYPE`` -- see that status's own
docstring in ``app.result_interpretation.types``.

**Evidence-type mapping.** ``EvidenceType`` (Increment 26 instructions,
Step 11: reused verbatim, never a new vocabulary) is derived from the
originating recommendation's own ``experiment_class`` --
Increment 23's own deterministic categorization (``ENZYME_SUBSTRATE_ASSAY``/
``REACTION_VALIDATION``/``PROTEIN_LOCALIZATION_ASSAY``/
``EXPERIMENTAL_CONTEXT_CHARACTERIZATION``/``REPLICATION_EXPERIMENT``), not
invented biological reasoning about the measurement itself. Two of the five
classes (``EXPERIMENTAL_CONTEXT_CHARACTERIZATION``, which characterizes
conditions rather than a specific biochemical/localization phenomenon, and
``REPLICATION_EXPERIMENT``, which replicates a prior claim of unknown
original evidence type) have no single-category match in
``EvidenceType`` and deliberately fall back to ``EvidenceType.OTHER`` --
that is a disclosed limitation of this mapping, not a guess. No
recommendation supplied at all (or a recommendation with no
``experiment_class``, which happens whenever its own
``recommendation_status`` was not ``RECOMMENDED``) also falls back to
``EvidenceType.OTHER``.
"""

from __future__ import annotations

from decimal import Decimal

from app.experiment_recommendation.types import ExperimentClass
from app.models.enums import EvidenceType
from app.models.experiment_execution import ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.persistence.experiment_execution_types import ResultType
from app.result_interpretation.types import InterpretationStatus

_EXPERIMENT_CLASS_TO_EVIDENCE_TYPE: dict[ExperimentClass, EvidenceType] = {
    ExperimentClass.ENZYME_SUBSTRATE_ASSAY: EvidenceType.DIRECT_BIOCHEMICAL,
    ExperimentClass.REACTION_VALIDATION: EvidenceType.DIRECT_BIOCHEMICAL,
    ExperimentClass.PROTEIN_LOCALIZATION_ASSAY: EvidenceType.LOCALIZATION,
    ExperimentClass.EXPERIMENTAL_CONTEXT_CHARACTERIZATION: EvidenceType.OTHER,
    ExperimentClass.REPLICATION_EXPERIMENT: EvidenceType.OTHER,
}


def classify_result(result: ExperimentResult) -> InterpretationStatus:
    """Deterministically classify one result by its recorded shape. See module docstring."""
    try:
        result_type = ResultType(result.result_type)
    except ValueError:
        return InterpretationStatus.UNSUPPORTED_RESULT_TYPE

    if result_type is ResultType.NON_DETECTION:
        return InterpretationStatus.NON_DETECTION

    if result_type in (ResultType.QUANTITATIVE_MEASUREMENT, ResultType.DETECTION):
        if result.value_numeric is not None:
            return InterpretationStatus.DIRECT_OBSERVATION
        if result.value_text is not None:
            return InterpretationStatus.QUALITATIVE_OBSERVATION
        return InterpretationStatus.INSUFFICIENT_INFORMATION

    if result_type in (ResultType.QUALITATIVE_OBSERVATION, ResultType.ASSAY_OUTCOME):
        if result.value_text is not None or result.value_numeric is not None:
            return InterpretationStatus.QUALITATIVE_OBSERVATION
        return InterpretationStatus.INSUFFICIENT_INFORMATION

    return InterpretationStatus.UNSUPPORTED_RESULT_TYPE  # unreachable: ResultType is closed


def evidence_type_for(recommendation: ExperimentRecommendationRecord | None) -> EvidenceType:
    """Deterministic ``EvidenceType`` derived from the recommendation's ``experiment_class``.

    See module docstring for the full mapping table and its disclosed
    ``OTHER`` fallback cases.
    """
    if recommendation is None or recommendation.experiment_class is None:
        return EvidenceType.OTHER
    try:
        experiment_class = ExperimentClass(recommendation.experiment_class)
    except ValueError:
        return EvidenceType.OTHER
    return _EXPERIMENT_CLASS_TO_EVIDENCE_TYPE.get(experiment_class, EvidenceType.OTHER)


def _format_value(value_numeric: Decimal | None, unit: str | None) -> str:
    if unit:
        return f"{value_numeric} {unit}"
    return f"{value_numeric}"


def build_quoted_support(result: ExperimentResult, status: InterpretationStatus) -> str:
    """A deterministic, verbatim-as-possible quotation of the recorded result.

    Increment 26 instructions, Step 14: "Never embellish. Never summarize.
    Never call an LLM." When the caller already supplied descriptive text
    (``value_text``), that text is quoted exactly, unmodified -- the least
    embellished option available. A generated sentence is used only when no
    descriptive text exists to quote.
    """
    name = result.measurement_name

    if status is InterpretationStatus.NON_DETECTION:
        if result.value_text:
            return result.value_text
        return f"No detectable {name} observed."

    if status is InterpretationStatus.DIRECT_OBSERVATION:
        value = _format_value(result.value_numeric, result.unit)
        return f"Measured {name}: {value}."

    if status is InterpretationStatus.QUALITATIVE_OBSERVATION:
        if result.value_text:
            return result.value_text
        value = _format_value(result.value_numeric, result.unit)
        return f"Observed {name}: {value}."

    if status is InterpretationStatus.INSUFFICIENT_INFORMATION:
        return f"Result recorded for {name} with no usable value or description."

    # UNSUPPORTED_RESULT_TYPE
    return f"Result type {result.result_type!r} for {name} is not yet supported for interpretation."


def build_curator_summary(result: ExperimentResult, status: InterpretationStatus) -> str:
    """One deterministic sentence summarizing what was measured/observed. No interpretation."""
    name = result.measurement_name

    if status is InterpretationStatus.NON_DETECTION:
        return f"Experiment found no detectable {name}."

    if status is InterpretationStatus.DIRECT_OBSERVATION:
        value = _format_value(result.value_numeric, result.unit)
        return f"Experiment measured {name} of {value}."

    if status is InterpretationStatus.QUALITATIVE_OBSERVATION:
        if result.value_text:
            return f"Experiment observed {name}: {result.value_text}."
        value = _format_value(result.value_numeric, result.unit)
        return f"Experiment observed {name}: {value}."

    if status is InterpretationStatus.INSUFFICIENT_INFORMATION:
        return (
            f"Experiment recorded a result for {name} with insufficient information "
            "to interpret."
        )

    # UNSUPPORTED_RESULT_TYPE
    return f"Experiment recorded a {name} result of an unsupported result type."


__all__ = [
    "build_curator_summary",
    "build_quoted_support",
    "classify_result",
    "evidence_type_for",
]
