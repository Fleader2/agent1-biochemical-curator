"""Tests for ``app.review.history``."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import ConfidenceClass, CurationState
from app.review.history import get_review_history
from app.review.types import ReviewDecision
from app.review.workflow import human_review_claim, machine_review_claim
from tests.review.conftest import make_claim, make_confidence


def test_empty_history_for_unreviewed_claim(db_session):
    claim = make_claim(db_session)
    assert get_review_history(db_session, entity_type="claim", entity_id=claim.id) == ()


def test_history_ordered_oldest_to_newest(db_session):
    claim = make_claim(db_session)
    machine_review_claim(claim, make_confidence(ConfidenceClass.MODERATE), session=db_session)
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.HUMAN_ACCEPTED,
            reviewer="alice",
            reason="looks correct",
            # Deliberately far in the future: machine_review_claim's own
            # ReviewEvent gets its created_at from the database's
            # server-side now(), which is the real wall-clock time the
            # test runs -- this must sort strictly after that.
            timestamp=datetime(2999, 1, 1, tzinfo=UTC),
        ),
        claim=claim,
        session=db_session,
    )

    history = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    assert len(history) == 2
    assert history[0].new_state is CurationState.MACHINE_REVIEWED
    assert history[1].new_state is CurationState.HUMAN_ACCEPTED
    assert history[0].created_at <= history[1].created_at


def test_history_scoped_to_entity_id(db_session):
    claim_a = make_claim(db_session)
    claim_b = make_claim(db_session)
    machine_review_claim(claim_a, make_confidence(ConfidenceClass.HIGH), session=db_session)
    machine_review_claim(claim_b, make_confidence(ConfidenceClass.HIGH), session=db_session)

    history_a = get_review_history(db_session, entity_type="claim", entity_id=claim_a.id)
    history_b = get_review_history(db_session, entity_type="claim", entity_id=claim_b.id)
    assert len(history_a) == 1
    assert len(history_b) == 1
    assert history_a[0].id != history_b[0].id


def test_history_scoped_to_entity_type(db_session):
    claim = make_claim(db_session)
    machine_review_claim(claim, make_confidence(ConfidenceClass.HIGH), session=db_session)

    other_type_history = get_review_history(db_session, entity_type="reaction", entity_id=claim.id)
    assert other_type_history == ()


def test_history_is_read_only_and_does_not_mutate_rows(db_session):
    claim = make_claim(db_session)
    machine_review_claim(claim, make_confidence(ConfidenceClass.HIGH), session=db_session)

    before = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    after = get_review_history(db_session, entity_type="claim", entity_id=claim.id)
    assert before[0].id == after[0].id
    assert before[0].new_state == after[0].new_state
