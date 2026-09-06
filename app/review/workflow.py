"""The Review Workflow state machine: ``machine_review_claim``/``human_review_claim``.

**The central schema finding this module is built around.** ``Claim`` has
**no ``curation_state`` column** -- verified directly against
``app/models/claim.py`` (``Reaction``/``RegulatoryInteraction`` have one;
``Claim`` does not). ``ReviewEvent.previous_state``/``new_state`` are typed
``CurationState`` (``PROPOSED``/``MACHINE_REVIEWED``/``NEEDS_REVIEW``/
``HUMAN_ACCEPTED``/``REJECTED``); ``Claim.status`` is typed ``ClaimStatus``
(``SUPPORTED``/``CONFLICTED``/``UNRESOLVED``/``REJECTED``/``UNKNOWN``) -- an
entirely different, non-overlapping vocabulary except for the one word both
happen to share, ``REJECTED``.

This module therefore does **not** invent a ``Claim.curation_state``
column. A claim's current curation state is derived entirely from its own
``ReviewEvent`` history (the most recent row's ``new_state``, or
``CurationState.PROPOSED`` when no ``ReviewEvent`` exists yet for it --
the same implicit "not yet reviewed" starting point ``Reaction``/
``RegulatoryInteraction`` already default their own real ``curation_state``
column to). ``Claim.status`` is updated only where a transition's
``CurationState`` target has a defensible, non-invented ``ClaimStatus``
meaning: **only ``CurationState.REJECTED`` -> ``ClaimStatus.REJECTED``**.
Every other transition (``MACHINE_REVIEWED``, ``NEEDS_REVIEW``,
``HUMAN_ACCEPTED``) leaves ``Claim.status`` untouched -- ``HUMAN_ACCEPTED``
means "a human has signed off on this curation record," not "the evidence
supports this claim" (``ClaimStatus.SUPPORTED`` would require duplicate/
contradiction analysis this increment does not perform). See
``docs/16_review_workflow_contract.md`` for the full disclosure.

**State machine.** Built from ``docs/03_agent_behavior.md``'s "Human Review
Behavior" section (`"Curation states should progress through: PROPOSED,
MACHINE_REVIEWED, NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED"` plus `"The system
must never automatically mark a record HUMAN_ACCEPTED"`) and Step 6's own
grant ("Machine review may: request human review, reject, leave
unchanged"). ``HUMAN_ACCEPTED``/``REJECTED`` are terminal -- nothing in the
schema or documentation represents reopening either, so no reopening
transition is implemented (Increment 20 instructions, Step 5: "Include
reopening transitions only if they already exist.").

    PROPOSED         -> MACHINE_REVIEWED, NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED
    MACHINE_REVIEWED -> NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED
    NEEDS_REVIEW     -> HUMAN_ACCEPTED, REJECTED
    HUMAN_ACCEPTED   -> (terminal)
    REJECTED         -> (terminal)

Each actor is further restricted to the subset of this table it is allowed
to target: machine review may only ever target ``MACHINE_REVIEWED``/
``NEEDS_REVIEW`` (never ``HUMAN_ACCEPTED`` -- absolutely, per Step 6);
human review may only ever target ``HUMAN_ACCEPTED``/``NEEDS_REVIEW``/
``REJECTED`` (a human never re-labels a claim ``PROPOSED``/
``MACHINE_REVIEWED`` -- those are not human decisions).

**Machine review's own policy is deliberately narrow.** It only ever acts
on a claim still in ``PROPOSED`` (its own first-pass triage role) --
calling it again once a claim has moved past ``PROPOSED``, by machine or
human action, is a harmless no-op (Step 6: "leave unchanged"), never a
re-litigation of an already-reached state. Its confidence -> target mapping
uses only ``AggregateClaimConfidence.confidence_class`` (the one
authoritative aggregate signal that already exists):
``UNKNOWN``/``LOW`` -> ``NEEDS_REVIEW`` (weak or unscored evidence needs a
human, never automatic rejection -- "unknown" is never treated as
"false"); ``MODERATE``/``HIGH``/``VERY_HIGH`` -> ``MACHINE_REVIEWED``. This
policy **never** selects ``REJECTED``: although the state machine's table
permits it (Step 6 grants machine review that capability in principle), no
authoritative, deterministic signal exists today in
``AggregateClaimConfidence`` to justify automatic rejection without
performing conflict/contradiction resolution, which this increment is
explicitly forbidden from implementing. This is a disclosed policy
restriction, not a schema limitation.

**Every successful transition creates exactly one ``ReviewEvent``** (Step
9); a no-op (requested state already equals the current state, or machine
review declining to act past ``PROPOSED``) creates none.

**Transaction handling.** Exactly mirrors ``app.persistence.reaction``:
one ``SAVEPOINT`` (``session.begin_nested()``) per call, an
``IntegrityError`` inside converted to a conservative "nothing changed"
result, every other exception (including
``app.review.errors.InvalidReviewTransitionError``, raised *before* any
``SAVEPOINT`` is opened) left to propagate. This module never commits or
rolls back the session it is given, and creates no
``SourceCrossReference``/``ExternalRecord`` of its own -- there is nothing
here for those helpers to attach to that Increment 19's own persistence
call has not already recorded.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.confidence import AggregateClaimConfidence
from app.models.claim import Claim
from app.models.enums import ClaimStatus, ConfidenceClass, CurationState
from app.models.review_event import ReviewEvent
from app.review.errors import InvalidReviewTransitionError
from app.review.history import get_review_history
from app.review.types import ReviewDecision, ReviewerType, ReviewWorkflowResult

_ENTITY_TYPE = "claim"

_TRANSITIONS: dict[CurationState, frozenset[CurationState]] = {
    CurationState.PROPOSED: frozenset(
        {
            CurationState.MACHINE_REVIEWED,
            CurationState.NEEDS_REVIEW,
            CurationState.HUMAN_ACCEPTED,
            CurationState.REJECTED,
        }
    ),
    CurationState.MACHINE_REVIEWED: frozenset(
        {CurationState.NEEDS_REVIEW, CurationState.HUMAN_ACCEPTED, CurationState.REJECTED}
    ),
    CurationState.NEEDS_REVIEW: frozenset(
        {CurationState.HUMAN_ACCEPTED, CurationState.REJECTED}
    ),
    CurationState.HUMAN_ACCEPTED: frozenset(),
    CurationState.REJECTED: frozenset(),
}

_MACHINE_ALLOWED_TARGETS = frozenset({CurationState.MACHINE_REVIEWED, CurationState.NEEDS_REVIEW})
_HUMAN_ALLOWED_TARGETS = frozenset(
    {CurationState.HUMAN_ACCEPTED, CurationState.NEEDS_REVIEW, CurationState.REJECTED}
)

# The only defensible CurationState -> ClaimStatus mapping. See module docstring.
_CLAIM_STATUS_BY_CURATION_STATE: dict[CurationState, ClaimStatus] = {
    CurationState.REJECTED: ClaimStatus.REJECTED,
}

_MACHINE_REVIEW_TARGET_BY_CONFIDENCE_CLASS: dict[ConfidenceClass, CurationState] = {
    ConfidenceClass.UNKNOWN: CurationState.NEEDS_REVIEW,
    ConfidenceClass.LOW: CurationState.NEEDS_REVIEW,
    ConfidenceClass.MODERATE: CurationState.MACHINE_REVIEWED,
    ConfidenceClass.HIGH: CurationState.MACHINE_REVIEWED,
    ConfidenceClass.VERY_HIGH: CurationState.MACHINE_REVIEWED,
}


def _current_curation_state(session: Session, entity_id: UUID) -> CurationState:
    """A claim's curation state, derived from its own ``ReviewEvent`` history.

    ``CurationState.PROPOSED`` when no ``ReviewEvent`` exists yet -- the
    implicit starting point, not an invented default (see module
    docstring).
    """
    history = get_review_history(session, entity_type=_ENTITY_TYPE, entity_id=entity_id)
    return history[-1].new_state if history else CurationState.PROPOSED


def _validate_actor_target(
    new_state: CurationState, *, allowed_targets: frozenset, actor: str
) -> None:
    """Reject a target the calling actor may never reach, regardless of the current state.

    Checked independently of, and before, the current-state edge check
    (``_validate_table_edge``) and any same-state no-op short-circuit --
    ``CurationState.PROPOSED``/``MACHINE_REVIEWED`` are never legal human
    decisions even when a claim happens to already be in that state.
    """
    if new_state not in allowed_targets:
        raise InvalidReviewTransitionError(
            f"{actor} may not target {new_state.value} "
            f"(allowed targets: {sorted(t.value for t in allowed_targets)})"
        )


def _validate_table_edge(old_state: CurationState, new_state: CurationState) -> None:
    if new_state not in _TRANSITIONS.get(old_state, frozenset()):
        raise InvalidReviewTransitionError(
            f"no transition from {old_state.value} to {new_state.value} exists in the "
            "review state machine"
        )


def _create_review_event(
    session: Session,
    *,
    entity_id: UUID,
    previous_state: CurationState,
    new_state: CurationState,
    reviewer_type: ReviewerType,
    reviewer_id: str | None,
    comment: str,
    created_at=None,
) -> ReviewEvent:
    kwargs = {
        "entity_type": _ENTITY_TYPE,
        "entity_id": entity_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "reviewer_type": reviewer_type.value,
        "reviewer_id": reviewer_id,
        "comment": comment,
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    row = ReviewEvent(**kwargs)
    session.add(row)
    session.flush()
    return row


def machine_review_claim(
    claim: Claim, confidence: AggregateClaimConfidence, *, session: Session
) -> ReviewWorkflowResult:
    """Deterministic, confidence-driven first-pass triage of a ``PROPOSED`` claim.

    Never sets ``CurationState.HUMAN_ACCEPTED`` under any circumstance --
    that value is not even in ``_MACHINE_ALLOWED_TARGETS``, so this is a
    structural guarantee, not merely a runtime check. See module docstring
    for the full policy and its deliberate limitations.
    """
    if not isinstance(claim, Claim):
        raise TypeError(f"machine_review_claim requires a Claim, got {claim!r}")
    if not isinstance(confidence, AggregateClaimConfidence):
        raise TypeError(
            f"machine_review_claim requires an AggregateClaimConfidence, got {confidence!r}"
        )

    old_state = _current_curation_state(session, claim.id)
    if old_state is not CurationState.PROPOSED:
        return ReviewWorkflowResult(
            old_state=old_state,
            new_state=old_state,
            review_event_id=None,
            changed=False,
            reason=(
                "machine review only acts on PROPOSED claims; current curation state is "
                f"already {old_state.value}"
            ),
        )

    target_state = _MACHINE_REVIEW_TARGET_BY_CONFIDENCE_CLASS[confidence.confidence_class]
    _validate_actor_target(
        target_state, allowed_targets=_MACHINE_ALLOWED_TARGETS, actor="machine review"
    )
    _validate_table_edge(old_state, target_state)

    comment = (
        f"Deterministic machine review: confidence class is "
        f"{confidence.confidence_class.value} (score={confidence.score!r})."
    )
    try:
        with session.begin_nested():
            event = _create_review_event(
                session,
                entity_id=claim.id,
                previous_state=old_state,
                new_state=target_state,
                reviewer_type=ReviewerType.DETERMINISTIC_VALIDATOR,
                reviewer_id=None,
                comment=comment,
            )
            new_status = _CLAIM_STATUS_BY_CURATION_STATE.get(target_state)
            if new_status is not None:
                claim.status = new_status
                session.flush()
    except IntegrityError as exc:
        return ReviewWorkflowResult(
            old_state=old_state,
            new_state=old_state,
            review_event_id=None,
            changed=False,
            reason=(
                "machine review rolled back due to a database integrity violation: "
                f"{exc.orig}"
            ),
        )

    return ReviewWorkflowResult(
        old_state=old_state,
        new_state=target_state,
        review_event_id=event.id,
        changed=True,
        reason=f"machine review transitioned {old_state.value} -> {target_state.value}",
    )


def human_review_claim(
    decision: ReviewDecision, *, claim: Claim, session: Session
) -> ReviewWorkflowResult:
    """Apply one authorized human ``ReviewDecision`` to one ``Claim``.

    ``decision.timestamp`` is written verbatim to ``ReviewEvent.created_at``
    (see ``ReviewDecision``'s own docstring for why). Every transition is
    validated against the same state machine ``machine_review_claim`` uses
    -- no transition bypasses validation.
    """
    if not isinstance(decision, ReviewDecision):
        raise TypeError(f"human_review_claim requires a ReviewDecision, got {decision!r}")
    if not isinstance(claim, Claim):
        raise TypeError(f"human_review_claim requires a Claim, got {claim!r}")
    if decision.claim_id != claim.id:
        raise ValueError(
            f"ReviewDecision.claim_id ({decision.claim_id!r}) does not match "
            f"claim.id ({claim.id!r})"
        )

    old_state = _current_curation_state(session, claim.id)
    new_state = decision.decision

    _validate_actor_target(new_state, allowed_targets=_HUMAN_ALLOWED_TARGETS, actor="human review")

    if new_state == old_state:
        return ReviewWorkflowResult(
            old_state=old_state,
            new_state=old_state,
            review_event_id=None,
            changed=False,
            reason=f"claim is already {old_state.value}; no state change recorded",
        )

    _validate_table_edge(old_state, new_state)

    comment = (
        decision.reason
        if decision.notes is None
        else f"{decision.reason}\n\n{decision.notes}"
    )
    try:
        with session.begin_nested():
            event = _create_review_event(
                session,
                entity_id=claim.id,
                previous_state=old_state,
                new_state=new_state,
                reviewer_type=ReviewerType.HUMAN,
                reviewer_id=decision.reviewer,
                comment=comment,
                created_at=decision.timestamp,
            )
            new_status = _CLAIM_STATUS_BY_CURATION_STATE.get(new_state)
            if new_status is not None:
                claim.status = new_status
                session.flush()
    except IntegrityError as exc:
        return ReviewWorkflowResult(
            old_state=old_state,
            new_state=old_state,
            review_event_id=None,
            changed=False,
            reason=f"human review rolled back due to a database integrity violation: {exc.orig}",
        )

    return ReviewWorkflowResult(
        old_state=old_state,
        new_state=new_state,
        review_event_id=event.id,
        changed=True,
        reason=f"human review transitioned {old_state.value} -> {new_state.value}",
    )


__all__ = ["human_review_claim", "machine_review_claim"]
