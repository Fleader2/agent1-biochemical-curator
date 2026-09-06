"""Tests for ``app.review.workflow``: the review state machine."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.enums import ClaimStatus, ConfidenceClass, CurationState
from app.models.review_event import ReviewEvent
from app.review import workflow
from app.review.errors import InvalidReviewTransitionError
from app.review.history import get_review_history
from app.review.types import ReviewDecision
from app.review.workflow import human_review_claim, machine_review_claim
from tests.review.conftest import make_claim, make_confidence

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _human_decision(claim_id, decision, *, reviewer="alice", reason="reviewed", notes=None):
    return ReviewDecision(
        claim_id=claim_id, decision=decision, reviewer=reviewer, reason=reason, notes=notes,
        timestamp=_TS,
    )


# --- Machine review: legal paths ------------------------------------------------------


@pytest.mark.parametrize(
    "confidence_class,expected_state",
    [
        (ConfidenceClass.UNKNOWN, CurationState.NEEDS_REVIEW),
        (ConfidenceClass.LOW, CurationState.NEEDS_REVIEW),
        (ConfidenceClass.MODERATE, CurationState.MACHINE_REVIEWED),
        (ConfidenceClass.HIGH, CurationState.MACHINE_REVIEWED),
        (ConfidenceClass.VERY_HIGH, CurationState.MACHINE_REVIEWED),
    ],
)
def test_machine_review_from_proposed(db_session, confidence_class, expected_state):
    claim = make_claim(db_session)
    result = machine_review_claim(claim, make_confidence(confidence_class), session=db_session)

    assert result.changed is True
    assert result.old_state is CurationState.PROPOSED
    assert result.new_state is expected_state
    assert result.review_event_id is not None


def test_machine_review_never_reaches_human_accepted():
    assert CurationState.HUMAN_ACCEPTED not in workflow._MACHINE_ALLOWED_TARGETS
    for confidence_class in ConfidenceClass:
        target = workflow._MACHINE_REVIEW_TARGET_BY_CONFIDENCE_CLASS.get(confidence_class)
        assert target is not CurationState.HUMAN_ACCEPTED


def test_machine_review_never_produces_rejected_by_default_policy():
    for target in workflow._MACHINE_REVIEW_TARGET_BY_CONFIDENCE_CLASS.values():
        assert target is not CurationState.REJECTED


def test_machine_review_leaves_claim_status_unknown(db_session):
    claim = make_claim(db_session)
    machine_review_claim(claim, make_confidence(ConfidenceClass.MODERATE), session=db_session)
    assert claim.status is ClaimStatus.UNKNOWN


def test_machine_review_is_noop_once_past_proposed(db_session):
    claim = make_claim(db_session)
    first = machine_review_claim(
        claim, make_confidence(ConfidenceClass.MODERATE), session=db_session
    )
    assert first.changed is True

    second = machine_review_claim(
        claim, make_confidence(ConfidenceClass.LOW), session=db_session
    )
    assert second.changed is False
    assert second.review_event_id is None
    assert second.old_state is CurationState.MACHINE_REVIEWED
    assert second.new_state is CurationState.MACHINE_REVIEWED

    history = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    assert len(history) == 1


def test_machine_review_rejects_wrong_claim_type(db_session):
    with pytest.raises(TypeError):
        machine_review_claim(
            "not a claim", make_confidence(ConfidenceClass.HIGH), session=db_session
        )


def test_machine_review_rejects_wrong_confidence_type(db_session):
    claim = make_claim(db_session)
    with pytest.raises(TypeError):
        machine_review_claim(claim, "not confidence", session=db_session)


# --- Human review: legal paths ---------------------------------------------------------


@pytest.mark.parametrize(
    "start_state",
    [CurationState.PROPOSED, CurationState.MACHINE_REVIEWED, CurationState.NEEDS_REVIEW],
)
@pytest.mark.parametrize(
    "target_state",
    [CurationState.HUMAN_ACCEPTED, CurationState.NEEDS_REVIEW, CurationState.REJECTED],
)
def test_every_legal_human_transition(db_session, start_state, target_state):
    if target_state not in workflow._TRANSITIONS[start_state]:
        pytest.skip(f"{start_state} -> {target_state} is not a legal edge")
    if target_state == start_state:
        pytest.skip("same-state request is a no-op, tested separately")

    claim = make_claim(db_session)
    _drive_to_state(db_session, claim, start_state)

    result = human_review_claim(
        _human_decision(claim.id, target_state), claim=claim, session=db_session
    )
    assert result.changed is True
    assert result.old_state is start_state
    assert result.new_state is target_state
    assert result.review_event_id is not None


def _drive_to_state(session, claim, state: CurationState) -> None:
    if state is CurationState.PROPOSED:
        return
    if state is CurationState.MACHINE_REVIEWED:
        machine_review_claim(claim, make_confidence(ConfidenceClass.HIGH), session=session)
        return
    if state is CurationState.NEEDS_REVIEW:
        machine_review_claim(claim, make_confidence(ConfidenceClass.LOW), session=session)
        return
    raise AssertionError(f"no driver for {state}")


def test_human_acceptance(db_session):
    claim = make_claim(db_session)
    result = human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED, reason="two independent papers"),
        claim=claim,
        session=db_session,
    )
    assert result.new_state is CurationState.HUMAN_ACCEPTED
    assert claim.status is ClaimStatus.UNKNOWN  # HUMAN_ACCEPTED has no ClaimStatus mapping


def test_human_rejection_updates_claim_status(db_session):
    claim = make_claim(db_session)
    result = human_review_claim(
        _human_decision(
            claim.id, CurationState.REJECTED, reason="contradicted by stronger evidence"
        ),
        claim=claim,
        session=db_session,
    )
    assert result.new_state is CurationState.REJECTED
    assert claim.status is ClaimStatus.REJECTED


# --- Illegal transitions -----------------------------------------------------------------


def test_human_review_cannot_target_proposed(db_session):
    claim = make_claim(db_session)
    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, CurationState.PROPOSED), claim=claim, session=db_session
        )


def test_human_review_cannot_target_machine_reviewed(db_session):
    claim = make_claim(db_session)
    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, CurationState.MACHINE_REVIEWED),
            claim=claim,
            session=db_session,
        )


@pytest.mark.parametrize("target_state", [CurationState.NEEDS_REVIEW, CurationState.REJECTED])
def test_no_transition_out_of_human_accepted(db_session, target_state):
    claim = make_claim(db_session)
    human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
    )
    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, target_state), claim=claim, session=db_session
        )


@pytest.mark.parametrize("target_state", [CurationState.NEEDS_REVIEW, CurationState.HUMAN_ACCEPTED])
def test_no_transition_out_of_rejected(db_session, target_state):
    claim = make_claim(db_session)
    human_review_claim(
        _human_decision(claim.id, CurationState.REJECTED), claim=claim, session=db_session
    )
    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, target_state), claim=claim, session=db_session
        )


def test_needs_review_cannot_go_back_to_machine_reviewed(db_session):
    claim = make_claim(db_session)
    _drive_to_state(db_session, claim, CurationState.NEEDS_REVIEW)
    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, CurationState.MACHINE_REVIEWED),
            claim=claim,
            session=db_session,
        )


def test_illegal_transition_raises_before_writing_anything(db_session):
    claim = make_claim(db_session)
    human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
    )
    before = len(get_review_history(db_session, entity_type="claim", entity_id=claim.id))

    with pytest.raises(InvalidReviewTransitionError):
        human_review_claim(
            _human_decision(claim.id, CurationState.REJECTED), claim=claim, session=db_session
        )

    after = len(get_review_history(db_session, entity_type="claim", entity_id=claim.id))
    assert before == after


def test_human_review_rejects_mismatched_claim_id(db_session):
    claim = make_claim(db_session)
    with pytest.raises(ValueError):
        human_review_claim(
            _human_decision(uuid4(), CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
        )


def test_human_review_rejects_wrong_decision_type(db_session):
    claim = make_claim(db_session)
    with pytest.raises(TypeError):
        human_review_claim("not a decision", claim=claim, session=db_session)


def test_human_review_rejects_wrong_claim_type():
    with pytest.raises(TypeError):
        human_review_claim(
            _human_decision(uuid4(), CurationState.HUMAN_ACCEPTED),
            claim="not a claim",
            session=None,
        )


# --- Duplicate transitions ---------------------------------------------------------------


def test_duplicate_human_decision_is_a_noop(db_session):
    claim = make_claim(db_session)
    first = human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
    )
    second = human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
    )

    assert first.changed is True
    assert second.changed is False
    assert second.review_event_id is None
    history = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    assert len(history) == 1


# --- ReviewEvent creation and audit fields ------------------------------------------------


def test_review_event_created_with_full_audit_fields(db_session):
    claim = make_claim(db_session)
    result = human_review_claim(
        _human_decision(
            claim.id,
            CurationState.NEEDS_REVIEW,
            reviewer="bob@example.com",
            reason="conflicting evidence",
            notes="see reviewer thread #42",
        ),
        claim=claim,
        session=db_session,
    )

    event = db_session.get(ReviewEvent, result.review_event_id)
    assert event.entity_type == "claim"
    assert event.entity_id == claim.id
    assert event.previous_state is CurationState.PROPOSED
    assert event.new_state is CurationState.NEEDS_REVIEW
    assert event.reviewer_type == "HUMAN"
    assert event.reviewer_id == "bob@example.com"
    assert "conflicting evidence" in event.comment
    assert "see reviewer thread #42" in event.comment
    assert event.created_at == _TS


def test_machine_review_event_has_deterministic_validator_reviewer_type(db_session):
    claim = make_claim(db_session)
    result = machine_review_claim(claim, make_confidence(ConfidenceClass.HIGH), session=db_session)
    event = db_session.get(ReviewEvent, result.review_event_id)
    assert event.reviewer_type == "DETERMINISTIC_VALIDATOR"
    assert event.reviewer_id is None


def test_exactly_one_review_event_per_transition(db_session):
    claim = make_claim(db_session)
    machine_review_claim(claim, make_confidence(ConfidenceClass.HIGH), session=db_session)
    human_review_claim(
        _human_decision(claim.id, CurationState.HUMAN_ACCEPTED), claim=claim, session=db_session
    )
    history = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    assert len(history) == 2


# --- Transaction / SAVEPOINT / IntegrityError behavior ---------------------------------------


def test_integrity_error_rolls_back_review_event_as_a_unit(db_session):
    """``ReviewEvent.id`` is a Python-side ``default=uuid4`` captured by
    reference when the ORM class was defined -- patching the ``uuid4`` name
    anywhere after that point cannot affect it. To force a genuine primary-
    key collision (and so exercise the real ``IntegrityError`` -> ``FAILED``
    conversion path) this patches ``app.review.workflow.ReviewEvent``
    itself: that name *is* looked up fresh from the module's globals every
    time ``_create_review_event`` calls it, so the replacement -- which
    still constructs a real row, just pinned to an already-occupied id --
    is exactly what ``_create_review_event`` ends up inserting."""
    claim = make_claim(db_session)
    poisoned_id = uuid4()
    db_session.add(
        ReviewEvent(
            id=poisoned_id,
            entity_type="claim",
            entity_id=uuid4(),
            new_state=CurationState.NEEDS_REVIEW,
            reviewer_type="HUMAN",
        )
    )
    db_session.flush()

    def _pinned_review_event(**kwargs):
        kwargs["id"] = poisoned_id
        return ReviewEvent(**kwargs)

    with patch("app.review.workflow.ReviewEvent", side_effect=_pinned_review_event):
        result = machine_review_claim(
            claim, make_confidence(ConfidenceClass.MODERATE), session=db_session
        )

    assert result.changed is False
    assert result.review_event_id is None
    assert "integrity" in result.reason.lower()
    assert get_review_history(db_session, entity_type="claim", entity_id=claim.id) == ()
    assert claim.status is ClaimStatus.UNKNOWN

    # The caller's own transaction must still be usable afterward.
    another_claim = make_claim(db_session)
    assert another_claim.id is not None


def test_workflow_module_never_commits_or_rolls_back_the_session():
    source = inspect.getsource(workflow)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_workflow_module_uses_savepoint():
    source = inspect.getsource(workflow)
    assert "begin_nested" in source


def test_workflow_module_never_constructs_a_claim_row():
    """No duplicated persistence logic: this package only reads/updates an
    already-persisted Claim, never creates one (Increment 19's own
    app.persistence.claim owns Claim creation)."""
    source = inspect.getsource(workflow)
    assert "Claim(" not in source


# --- Determinism ---------------------------------------------------------------------------


def test_machine_review_is_deterministic_across_equivalent_calls(db_session):
    claim_one = make_claim(db_session)
    claim_two = make_claim(db_session)
    result_one = machine_review_claim(
        claim_one, make_confidence(ConfidenceClass.MODERATE), session=db_session
    )
    result_two = machine_review_claim(
        claim_two, make_confidence(ConfidenceClass.MODERATE), session=db_session
    )
    assert result_one.new_state == result_two.new_state
    assert result_one.old_state == result_two.old_state

    event_one = db_session.get(ReviewEvent, result_one.review_event_id)
    event_two = db_session.get(ReviewEvent, result_two.review_event_id)
    assert event_one.comment == event_two.comment
