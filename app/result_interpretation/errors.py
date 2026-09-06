"""Result-interpretation exceptions.

Mirrors ``app.persistence.errors``/``app.review.errors``'s own philosophy:
raised only for a condition a caller should never have reached at all -- a
genuine mismatch between the objects passed to this package's public API
(for example, an ``ExperimentResult`` that does not belong to the
``ExperimentExecution`` it was passed alongside). An ordinary "this result
type has no specific interpretation rule yet" outcome is represented as
data (``app.result_interpretation.types.InterpretationStatus
.UNSUPPORTED_RESULT_TYPE``), never raised -- see
``UnsupportedResultTypeError``'s own docstring below.
"""

from __future__ import annotations


class ResultInterpretationError(Exception):
    """Base class for ``app.result_interpretation`` exceptions."""


class InterpretationValidationError(ResultInterpretationError):
    """A caller-supplied combination of objects is internally inconsistent.

    Raised when, for example, ``result.execution_id`` does not equal the
    ``execution.id`` it was passed alongside, or a supplied
    ``ExperimentRecommendationRecord`` does not match
    ``execution.recommendation_id`` -- always a caller bug (the wrong
    objects were routed together), never an ordinary data condition.
    """


class UnsupportedResultTypeError(ResultInterpretationError):
    """Reserved for a future caller that wants a hard failure instead of a structured result.

    ``app.result_interpretation.interpreter`` never raises this today: an
    ``ExperimentResult.result_type`` value this package does not recognize
    (for example, a future addition to the database's
    ``RESULT_TYPE_VALUES`` this package has not yet been updated to
    understand) is represented as data instead --
    ``EvidenceCandidate.interpretation_status ==
    InterpretationStatus.UNSUPPORTED_RESULT_TYPE`` -- the same
    conservative-result philosophy
    ``app.persistence.errors.TerminalKnowledgeGapError`` already
    established: an unrecognized result type is not this package's fault
    and does not mean interpretation as a whole failed, so a candidate is
    still produced, flagged for human attention, rather than silently
    dropped or a raised exception aborting an entire batch. This class
    exists for API completeness and for a future override-flow that might
    want to fail loudly instead.
    """


__all__ = [
    "InterpretationValidationError",
    "ResultInterpretationError",
    "UnsupportedResultTypeError",
]
