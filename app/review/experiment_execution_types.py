"""Data contract for experiment-execution lifecycle (Increment 25).

Three types:

1. ``ExecutionStatus`` -- the workflow state of one persisted
   ``ExperimentExecution`` row. Transcribed verbatim as
   ``app.models.experiment_execution.EXECUTION_STATUS_VALUES`` for the
   models layer, the same "vocabulary exists in code, transcribed as plain
   strings in the schema" pattern
   ``app.review.experiment_recommendation_types.RecommendationLifecycleStatus``
   already established.
2. ``ExperimentExecutionDecision`` -- one actor's request to transition one
   execution's status. Unlike
   ``app.review.experiment_recommendation_types.ExperimentRecommendationDecision``
   (always human, hardcoded at the workflow layer), Increment 25
   instructions explicitly decline to assume execution transitions are
   human-only -- both ``actor_id`` *and* ``actor_type`` are required,
   caller-supplied fields here, with no default and no workflow-level
   hardcoding of either.
3. ``ExperimentExecutionLifecycleResult`` -- the outcome of one
   ``app.review.experiment_execution_workflow.transition_experiment_execution``
   call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.review.validation import clean_optional, require_non_empty_str, require_timezone_aware


class ExecutionStatus(StrEnum):
    """The workflow state of one persisted ``ExperimentExecution`` row.

    ``PLANNED`` is the state every newly persisted execution starts in.
    ``COMPLETED``/``FAILED``/``CANCELLED`` are all terminal (Increment 25
    instructions, Step 6: "Do not allow reopening terminal executions
    unless explicitly justified" -- nothing here justifies it).
    ``PARTIALLY_COMPLETED`` is deliberately not introduced: Increment 25
    instructions, Step 6 permits it only "with clear semantics", and no
    requirement in this increment gives it one that ``FAILED``
    (operationally did not finish) plus recorded partial
    ``ExperimentResult`` rows (Step 31: preserved regardless) cannot
    already represent -- see
    ``docs/21_experiment_execution_result_contract.md``'s open architecture
    questions.
    """

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ExperimentExecutionDecision:
    """One actor's request to transition one execution's status.

    ``actor_id``/``actor_type`` are both required, non-blank, and
    caller-supplied -- this module never assumes a human actor and never
    invents authorization semantics (Increment 25 instructions, "Human/
    machine actor policy"). ``timestamp`` is required, with no default --
    this module never reads the wall clock itself, the same discipline
    ``app.review.types.ReviewDecision``/``app.review
    .experiment_recommendation_types.ExperimentRecommendationDecision``
    already establish.
    """

    execution_id: UUID
    new_status: ExecutionStatus
    actor_id: str
    actor_type: str
    reason: str
    timestamp: datetime
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, UUID):
            raise TypeError(
                f"ExperimentExecutionDecision.execution_id must be a UUID, "
                f"got {self.execution_id!r}"
            )
        if not isinstance(self.new_status, ExecutionStatus):
            raise TypeError(
                "ExperimentExecutionDecision.new_status must be an ExecutionStatus, "
                f"got {self.new_status!r}"
            )
        object.__setattr__(
            self, "actor_id", require_non_empty_str(self.actor_id, field_name="actor_id")
        )
        object.__setattr__(
            self, "actor_type", require_non_empty_str(self.actor_type, field_name="actor_type")
        )
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))
        object.__setattr__(
            self, "timestamp", require_timezone_aware(self.timestamp, field_name="timestamp")
        )
        object.__setattr__(self, "notes", clean_optional(self.notes))


@dataclass(frozen=True, slots=True)
class ExperimentExecutionLifecycleResult:
    """The outcome of one ``transition_experiment_execution`` call.

    ``event_id`` is populated if and only if ``changed`` is ``True`` --
    exactly one ``ExperimentExecutionEvent`` was created only when a real
    transition occurred.
    """

    execution_id: UUID
    old_status: ExecutionStatus
    new_status: ExecutionStatus
    changed: bool
    event_id: UUID | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, UUID):
            raise TypeError(
                f"ExperimentExecutionLifecycleResult.execution_id must be a UUID, "
                f"got {self.execution_id!r}"
            )
        if not isinstance(self.old_status, ExecutionStatus):
            raise TypeError(
                "ExperimentExecutionLifecycleResult.old_status must be an ExecutionStatus, "
                f"got {self.old_status!r}"
            )
        if not isinstance(self.new_status, ExecutionStatus):
            raise TypeError(
                "ExperimentExecutionLifecycleResult.new_status must be an ExecutionStatus, "
                f"got {self.new_status!r}"
            )
        if self.changed and self.event_id is None:
            raise ValueError(
                "ExperimentExecutionLifecycleResult: changed=True requires event_id"
            )
        if not self.changed and self.event_id is not None:
            raise ValueError(
                "ExperimentExecutionLifecycleResult: changed=False must not carry event_id"
            )
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))


__all__ = [
    "ExecutionStatus",
    "ExperimentExecutionDecision",
    "ExperimentExecutionLifecycleResult",
]
