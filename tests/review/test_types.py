"""Tests for ``app.review.types``: self-validation of the review data contract."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.enums import CurationState
from app.review.types import ReviewDecision, ReviewerType, ReviewWorkflowResult

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _decision(**overrides) -> ReviewDecision:
    merged = {
        "claim_id": uuid4(),
        "decision": CurationState.HUMAN_ACCEPTED,
        "reviewer": "alice@example.com",
        "reason": "evidence is direct and unambiguous",
        "timestamp": _NOW,
    } | overrides
    return ReviewDecision(**merged)


# --- ReviewDecision ----------------------------------------------------------------


def test_valid_decision_construction():
    decision = _decision()
    assert decision.decision is CurationState.HUMAN_ACCEPTED
    assert decision.notes is None
    assert decision.created_by is None


def test_decision_rejects_non_uuid_claim_id():
    with pytest.raises(TypeError):
        _decision(claim_id="not-a-uuid")


def test_decision_rejects_non_curation_state_decision():
    with pytest.raises(TypeError):
        _decision(decision="HUMAN_ACCEPTED")


def test_decision_rejects_blank_reviewer():
    with pytest.raises(ValueError):
        _decision(reviewer="   ")


def test_decision_rejects_blank_reason():
    with pytest.raises(ValueError):
        _decision(reason="")


def test_decision_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError):
        _decision(timestamp=datetime(2024, 1, 1))  # naive


def test_decision_rejects_non_datetime_timestamp():
    with pytest.raises(TypeError):
        _decision(timestamp="2024-01-01")


def test_decision_blank_notes_becomes_none():
    decision = _decision(notes="   ")
    assert decision.notes is None


def test_decision_blank_created_by_becomes_none():
    decision = _decision(created_by="  ")
    assert decision.created_by is None


def test_decision_preserves_notes_and_created_by():
    decision = _decision(notes="see figure 2", created_by="curator-service")
    assert decision.notes == "see figure 2"
    assert decision.created_by == "curator-service"


def test_decision_is_frozen():
    decision = _decision()
    with pytest.raises(AttributeError):
        decision.reason = "changed"  # type: ignore[misc]


# --- ReviewerType --------------------------------------------------------------------


def test_reviewer_type_values_match_docs():
    assert ReviewerType.HUMAN.value == "HUMAN"
    assert ReviewerType.AI_CRITIC.value == "AI_CRITIC"
    assert ReviewerType.DETERMINISTIC_VALIDATOR.value == "DETERMINISTIC_VALIDATOR"


# --- ReviewWorkflowResult -------------------------------------------------------------


def _result(**overrides) -> ReviewWorkflowResult:
    merged = {
        "old_state": CurationState.PROPOSED,
        "new_state": CurationState.MACHINE_REVIEWED,
        "review_event_id": uuid4(),
        "changed": True,
        "reason": "transitioned",
    } | overrides
    return ReviewWorkflowResult(**merged)


def test_valid_result_construction():
    result = _result()
    assert result.changed is True
    assert result.review_event_id is not None


def test_result_rejects_non_curation_state_old_state():
    with pytest.raises(TypeError):
        _result(old_state="PROPOSED")


def test_result_rejects_non_curation_state_new_state():
    with pytest.raises(TypeError):
        _result(new_state="MACHINE_REVIEWED")


def test_result_changed_true_requires_review_event_id():
    with pytest.raises(ValueError):
        _result(changed=True, review_event_id=None)


def test_result_changed_false_rejects_review_event_id():
    with pytest.raises(ValueError):
        _result(changed=False, review_event_id=uuid4())


def test_result_changed_false_with_none_event_id_accepted():
    result = _result(
        old_state=CurationState.PROPOSED,
        new_state=CurationState.PROPOSED,
        changed=False,
        review_event_id=None,
        reason="no-op",
    )
    assert result.changed is False
    assert result.review_event_id is None


def test_result_rejects_blank_reason():
    with pytest.raises(ValueError):
        _result(reason="")


def test_result_is_frozen():
    result = _result()
    with pytest.raises(AttributeError):
        result.changed = False  # type: ignore[misc]
