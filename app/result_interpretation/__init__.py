"""Experiment Result Interpretation and Evidence Candidate Generation (Increment 26).

Transforms a completed ``ExperimentExecution`` and its ``ExperimentResult``
rows into deterministic ``EvidenceCandidate`` objects, structurally
compatible with (but never merged into) the existing Evidence Extraction ->
Claim Generation pipeline. See
``docs/22_experiment_result_interpretation_contract.md`` for the full
contract.

This package performs no normalization, no claim generation, no
persistence, no confidence scoring, no review, no hypothesis generation,
and no biological reasoning of any kind -- it is a purely deterministic
mapping layer, the final stage of Agent 1 Version 1's experimental
subsystem:

    KnowledgeGap -> ExperimentRecommendation -> Recommendation Lifecycle
        -> ExperimentExecution -> ExperimentResult -> EvidenceCandidate
"""

from app.result_interpretation.errors import (
    InterpretationValidationError,
    ResultInterpretationError,
    UnsupportedResultTypeError,
)
from app.result_interpretation.interpreter import (
    interpret_experiment_execution,
    interpret_experiment_result,
)
from app.result_interpretation.types import (
    EvidenceCandidate,
    ExperimentInterpretationContext,
    InterpretationStatus,
)

__all__ = [
    "EvidenceCandidate",
    "ExperimentInterpretationContext",
    "InterpretationStatus",
    "InterpretationValidationError",
    "ResultInterpretationError",
    "UnsupportedResultTypeError",
    "interpret_experiment_execution",
    "interpret_experiment_result",
]
