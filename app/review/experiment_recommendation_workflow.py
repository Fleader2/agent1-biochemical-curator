"""The Experiment Recommendation lifecycle state machine (Increment 24).

**Generation status vs. lifecycle status -- never conflated.**
Increment 23's ``RecommendationStatus`` (``RECOMMENDED``/
``NOT_APPLICABLE``/``REQUIRES_HUMAN_DESIGN``/``INSUFFICIENT_INFORMATION``)
is the deterministic engine's own output, persisted immutably as
``ExperimentRecommendationRecord.recommendation_status`` -- this module
never reads or writes that column. ``RecommendationLifecycleStatus``
(``PROPOSED``/``ACCEPTED``/``REJECTED``/``DEFERRED``/``SUPERSEDED``) is a
separate, human-workflow concept this module owns exclusively via
``ExperimentRecommendationRecord.lifecycle_status``. A ``RECOMMENDED``
generation status does not imply ``ACCEPTED`` lifecycle status: a human
must explicitly accept it, and a ``NOT_APPLICABLE``/
``REQUIRES_HUMAN_DESIGN``/``INSUFFICIENT_INFORMATION`` row is equally
trackable through this same lifecycle (even "no experiment recommended"
is an auditable conclusion someone may want to formally accept-as-is or
dismiss).

**State machine.**

    PROPOSED  -> ACCEPTED, REJECTED, DEFERRED, SUPERSEDED
    DEFERRED  -> ACCEPTED, REJECTED, SUPERSEDED
    ACCEPTED  -> SUPERSEDED
    REJECTED  -> (terminal)
    SUPERSEDED -> (terminal)

No reopening transition exists (``ACCEPTED``/``REJECTED``/``SUPERSEDED``
are all terminal) -- Increment 24 instructions, Step 5: "Do not implement
reopening unless explicitly justified." ``ACCEPTED -> SUPERSEDED`` is the
one transition out of an otherwise-terminal-feeling state, deliberately
kept: an accepted recommendation can still be superseded later by a newer
template version's recommendation for the same gap (Increment 24
instructions, Step 9) without that being a "reopening" of the original
decision.

**Human-only acceptance, structurally enforced.** There is exactly one
public transition function, ``transition_experiment_recommendation``, and
it always requires an ``ExperimentRecommendationDecision`` -- a human
identity (``reviewer_id``) is mandatory by that type's own validation.
Every ``ExperimentRecommendationEvent`` this function creates is stamped
``actor_type=RecommendationActorType.HUMAN.value`` unconditionally; there
is no code path, parameter, or second function anywhere in this module
that could write any other ``actor_type``. Neither
``app.experiment_recommendation.recommender`` (Increment 23's
deterministic engine) nor ``app.persistence.experiment_recommendation``
(this increment's own persistence layer) ever calls this module or
constructs an ``ExperimentRecommendationEvent`` themselves -- acceptance
can only ever originate from an explicit, externally-supplied human
decision. Increment 24 instructions, Step 24's "preferred simplest
policy" (no machine lifecycle transitions at all) is implemented exactly:
there is no machine transition function in this module, not even a
narrowly-scoped one.

**Transaction handling.** Exactly mirrors ``app.review.workflow``: one
``SAVEPOINT`` per call, an ``IntegrityError`` inside converted to a
conservative "nothing changed" result, every other exception (including
``InvalidRecommendationTransitionError``, raised *before* any ``SAVEPOINT``
is opened) left to propagate. Never commits or rolls back the session it
is given, and never touches ``KnowledgeGap`` in any way -- accepting a
recommendation never resolves, dismisses, or otherwise mutates the
originating gap (Increment 24 instructions, Step 34).
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.experiment_recommendation import (
    ExperimentRecommendationEvent,
    ExperimentRecommendationRecord,
)
from app.review.errors import InvalidRecommendationTransitionError
from app.review.experiment_recommendation_types import (
    ExperimentRecommendationDecision,
    ExperimentRecommendationLifecycleResult,
    RecommendationActorType,
    RecommendationLifecycleStatus,
)

_TRANSITIONS: dict[RecommendationLifecycleStatus, frozenset[RecommendationLifecycleStatus]] = {
    RecommendationLifecycleStatus.PROPOSED: frozenset(
        {
            RecommendationLifecycleStatus.ACCEPTED,
            RecommendationLifecycleStatus.REJECTED,
            RecommendationLifecycleStatus.DEFERRED,
            RecommendationLifecycleStatus.SUPERSEDED,
        }
    ),
    RecommendationLifecycleStatus.DEFERRED: frozenset(
        {
            RecommendationLifecycleStatus.ACCEPTED,
            RecommendationLifecycleStatus.REJECTED,
            RecommendationLifecycleStatus.SUPERSEDED,
        }
    ),
    RecommendationLifecycleStatus.ACCEPTED: frozenset({RecommendationLifecycleStatus.SUPERSEDED}),
    RecommendationLifecycleStatus.REJECTED: frozenset(),
    RecommendationLifecycleStatus.SUPERSEDED: frozenset(),
}


def _current_lifecycle_status(
    record: ExperimentRecommendationRecord,
) -> RecommendationLifecycleStatus:
    return RecommendationLifecycleStatus(record.lifecycle_status)


def _validate_table_edge(
    old_status: RecommendationLifecycleStatus, new_status: RecommendationLifecycleStatus
) -> None:
    if new_status not in _TRANSITIONS.get(old_status, frozenset()):
        raise InvalidRecommendationTransitionError(
            f"no transition from {old_status.value} to {new_status.value} exists in the "
            "experiment recommendation lifecycle state machine"
        )


def transition_experiment_recommendation(
    decision: ExperimentRecommendationDecision,
    *,
    session: Session,
) -> ExperimentRecommendationLifecycleResult:
    """Apply one authorized human ``ExperimentRecommendationDecision``.

    Every transition is validated against the state machine above -- no
    transition bypasses validation. A same-state request (the recommendation
    is already in ``decision.new_status``) is a harmless no-op: no event is
    created, ``changed=False``.
    """
    if not isinstance(decision, ExperimentRecommendationDecision):
        raise TypeError(
            "transition_experiment_recommendation requires an "
            f"ExperimentRecommendationDecision, got {decision!r}"
        )

    record = session.get(ExperimentRecommendationRecord, decision.recommendation_id)
    if record is None:
        raise ValueError(
            f"no ExperimentRecommendationRecord exists with id {decision.recommendation_id!r}"
        )

    old_status = _current_lifecycle_status(record)
    new_status = decision.new_status

    if new_status == old_status:
        return ExperimentRecommendationLifecycleResult(
            recommendation_id=record.id,
            old_status=old_status,
            new_status=old_status,
            changed=False,
            event_id=None,
            reason=f"recommendation is already {old_status.value}; no state change recorded",
        )

    _validate_table_edge(old_status, new_status)

    comment = (
        decision.reason
        if decision.notes is None
        else f"{decision.reason}\n\n{decision.notes}"
    )
    try:
        with session.begin_nested():
            event = ExperimentRecommendationEvent(
                recommendation_id=record.id,
                previous_status=old_status.value,
                new_status=new_status.value,
                actor_type=RecommendationActorType.HUMAN.value,
                actor_id=decision.reviewer_id,
                comment=comment,
                created_at=decision.timestamp,
            )
            session.add(event)
            record.lifecycle_status = new_status.value
            session.flush()
    except IntegrityError as exc:
        return ExperimentRecommendationLifecycleResult(
            recommendation_id=record.id,
            old_status=old_status,
            new_status=old_status,
            changed=False,
            event_id=None,
            reason=(
                "experiment recommendation lifecycle transition rolled back due to a "
                f"database integrity violation: {exc.orig}"
            ),
        )

    return ExperimentRecommendationLifecycleResult(
        recommendation_id=record.id,
        old_status=old_status,
        new_status=new_status,
        changed=True,
        event_id=event.id,
        reason=f"transitioned {old_status.value} -> {new_status.value}",
    )


__all__ = ["transition_experiment_recommendation"]
