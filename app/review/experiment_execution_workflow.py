"""The Experiment Execution lifecycle state machine (Increment 25).

**State machine.**

    PLANNED     -> IN_PROGRESS, CANCELLED
    IN_PROGRESS -> COMPLETED, FAILED, CANCELLED
    COMPLETED   -> (terminal)
    FAILED      -> (terminal)
    CANCELLED   -> (terminal)

No reopening transition exists out of any terminal state -- Increment 25
instructions, Step 6: "Do not allow reopening terminal executions unless
explicitly justified." Nothing here justifies it.

**Human/machine actor policy.** Unlike
``app.review.experiment_recommendation_workflow`` (which hardcodes every
event's ``actor_type`` to ``HUMAN``), this module makes no assumption about
who or what may transition an execution: ``actor_id``/``actor_type`` are
always taken verbatim from the caller-supplied
``ExperimentExecutionDecision`` (both required, non-blank, by that type's
own validation) and stamped onto ``ExperimentExecutionEvent`` unchanged.
Increment 25 instructions are explicit: "Do not assume execution
transitions are human-only unless explicitly designed" and "Do not invent
authorization semantics" -- this module invents none; it is a pure,
unauthenticated recorder of whatever actor identity/type its caller
supplies, the same trust boundary ``review_event.reviewer_type`` already
documents.

**Timestamp stamping.** When a transition's ``new_status`` is
``IN_PROGRESS`` and ``started_at`` is not already set, ``started_at`` is set
to ``decision.timestamp``. When ``new_status`` is one of the terminal
statuses (``COMPLETED``/``FAILED``/``CANCELLED``) and ``completed_at`` is
not already set, ``completed_at`` is set to ``decision.timestamp``. Neither
is ever overwritten once set, and neither is ever read from the wall clock
-- always the caller-supplied decision timestamp.

**Transaction handling.** Exactly mirrors
``app.review.experiment_recommendation_workflow``: one ``SAVEPOINT`` per
call, an ``IntegrityError`` inside converted to a conservative "nothing
changed" result, every other exception (including
``InvalidExperimentExecutionTransitionError``, raised *before* any
``SAVEPOINT`` is opened) left to propagate. Never commits or rolls back the
session it is given, and never touches ``ExperimentRecommendationRecord``/
``KnowledgeGap`` in any way -- transitioning an execution's status never
mutates the recommendation it descends from or the gap beyond that.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.experiment_execution import ExperimentExecution, ExperimentExecutionEvent
from app.review.errors import InvalidExperimentExecutionTransitionError
from app.review.experiment_execution_types import (
    ExecutionStatus,
    ExperimentExecutionDecision,
    ExperimentExecutionLifecycleResult,
)

_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.PLANNED: frozenset(
        {ExecutionStatus.IN_PROGRESS, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.IN_PROGRESS: frozenset(
        {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
}

_TERMINAL_STATUSES = frozenset(
    {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
)


def _current_status(execution: ExperimentExecution) -> ExecutionStatus:
    return ExecutionStatus(execution.status)


def _validate_table_edge(old_status: ExecutionStatus, new_status: ExecutionStatus) -> None:
    if new_status not in _TRANSITIONS.get(old_status, frozenset()):
        raise InvalidExperimentExecutionTransitionError(
            f"no transition from {old_status.value} to {new_status.value} exists in the "
            "experiment execution lifecycle state machine"
        )


def transition_experiment_execution(
    decision: ExperimentExecutionDecision,
    *,
    session: Session,
) -> ExperimentExecutionLifecycleResult:
    """Apply one authorized ``ExperimentExecutionDecision``.

    Every transition is validated against the state machine above -- no
    transition bypasses validation. A same-state request (the execution is
    already in ``decision.new_status``) is a harmless no-op: no event is
    created, ``changed=False``.
    """
    if not isinstance(decision, ExperimentExecutionDecision):
        raise TypeError(
            f"transition_experiment_execution requires an ExperimentExecutionDecision, "
            f"got {decision!r}"
        )

    execution = session.get(ExperimentExecution, decision.execution_id)
    if execution is None:
        raise ValueError(f"no ExperimentExecution exists with id {decision.execution_id!r}")

    old_status = _current_status(execution)
    new_status = decision.new_status

    if new_status == old_status:
        return ExperimentExecutionLifecycleResult(
            execution_id=execution.id,
            old_status=old_status,
            new_status=old_status,
            changed=False,
            event_id=None,
            reason=f"execution is already {old_status.value}; no state change recorded",
        )

    _validate_table_edge(old_status, new_status)

    comment = (
        decision.reason if decision.notes is None else f"{decision.reason}\n\n{decision.notes}"
    )
    try:
        with session.begin_nested():
            event = ExperimentExecutionEvent(
                execution_id=execution.id,
                previous_status=old_status.value,
                new_status=new_status.value,
                actor_type=decision.actor_type,
                actor_id=decision.actor_id,
                comment=comment,
                created_at=decision.timestamp,
            )
            session.add(event)
            execution.status = new_status.value
            if new_status is ExecutionStatus.IN_PROGRESS and execution.started_at is None:
                execution.started_at = decision.timestamp
            if new_status in _TERMINAL_STATUSES and execution.completed_at is None:
                execution.completed_at = decision.timestamp
            session.flush()
    except IntegrityError as exc:
        return ExperimentExecutionLifecycleResult(
            execution_id=execution.id,
            old_status=old_status,
            new_status=old_status,
            changed=False,
            event_id=None,
            reason=(
                "experiment execution lifecycle transition rolled back due to a database "
                f"integrity violation: {exc.orig}"
            ),
        )

    return ExperimentExecutionLifecycleResult(
        execution_id=execution.id,
        old_status=old_status,
        new_status=new_status,
        changed=True,
        event_id=event.id,
        reason=f"transitioned {old_status.value} -> {new_status.value}",
    )


__all__ = ["transition_experiment_execution"]
