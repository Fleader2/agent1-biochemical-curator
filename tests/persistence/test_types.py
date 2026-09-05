"""Invariant tests for ``app.persistence.types``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.normalization.types import NormalizationStatus
from app.persistence.types import PersistenceAction, PersistenceResult


def _kwargs(**overrides):
    return {
        "normalization_status": NormalizationStatus.MATCHED,
        "action": PersistenceAction.REUSED_EXISTING,
        "entity_type": "organism",
        "entity_id": uuid4(),
        "reused": True,
    } | overrides


def test_created_requires_entity_id():
    with pytest.raises(ValueError, match="CREATED requires entity_id"):
        PersistenceResult(
            **_kwargs(action=PersistenceAction.CREATED, entity_id=None, reused=False)
        )


def test_created_requires_created_flag_true():
    with pytest.raises(ValueError, match="CREATED requires created=True"):
        PersistenceResult(
            **_kwargs(action=PersistenceAction.CREATED, created=False, reused=False)
        )


def test_created_rejects_reused_flag():
    with pytest.raises(ValueError, match="CREATED requires created=True and reused=False"):
        PersistenceResult(
            **_kwargs(action=PersistenceAction.CREATED, created=True, reused=True)
        )


def test_created_valid():
    result = PersistenceResult(
        **_kwargs(action=PersistenceAction.CREATED, created=True, reused=False)
    )
    assert result.entity_id is not None
    assert result.created is True


def test_reused_existing_requires_entity_id():
    with pytest.raises(ValueError, match="REUSED_EXISTING requires entity_id"):
        PersistenceResult(**_kwargs(entity_id=None))


def test_reused_existing_requires_reused_flag_true():
    with pytest.raises(ValueError, match="REUSED_EXISTING requires reused=True"):
        PersistenceResult(**_kwargs(reused=False))


def test_reused_existing_rejects_created_flag():
    with pytest.raises(ValueError, match="REUSED_EXISTING requires reused=True and created=False"):
        PersistenceResult(**_kwargs(created=True))


@pytest.mark.parametrize(
    "action",
    [PersistenceAction.NO_ACTION, PersistenceAction.REQUIRES_REVIEW, PersistenceAction.FAILED],
)
def test_non_write_actions_reject_entity_id(action):
    with pytest.raises(ValueError, match="must not carry entity_id"):
        PersistenceResult(
            normalization_status=NormalizationStatus.UNRESOLVED,
            action=action,
            entity_type="organism",
            entity_id=uuid4(),
            review_required=(action is PersistenceAction.REQUIRES_REVIEW),
        )


@pytest.mark.parametrize(
    "action",
    [PersistenceAction.NO_ACTION, PersistenceAction.REQUIRES_REVIEW, PersistenceAction.FAILED],
)
def test_non_write_actions_reject_created_or_reused(action):
    with pytest.raises(ValueError, match="must not set created/reused"):
        PersistenceResult(
            normalization_status=NormalizationStatus.UNRESOLVED,
            action=action,
            entity_type="organism",
            created=True,
            review_required=(action is PersistenceAction.REQUIRES_REVIEW),
        )


def test_requires_review_requires_review_required_flag():
    with pytest.raises(ValueError, match="REQUIRES_REVIEW requires review_required=True"):
        PersistenceResult(
            normalization_status=NormalizationStatus.AMBIGUOUS,
            action=PersistenceAction.REQUIRES_REVIEW,
            entity_type="organism",
            review_required=False,
        )


def test_no_action_and_failed_are_valid_with_no_entity_context():
    no_action = PersistenceResult(
        normalization_status=NormalizationStatus.UNRESOLVED,
        action=PersistenceAction.NO_ACTION,
        entity_type="organism",
    )
    assert no_action.entity_id is None

    failed = PersistenceResult(
        normalization_status=NormalizationStatus.NEW,
        action=PersistenceAction.FAILED,
        entity_type="organism",
        reason="missing required field",
    )
    assert failed.entity_id is None


def test_entity_type_must_not_be_empty():
    with pytest.raises(ValueError, match="entity_type must not be empty"):
        PersistenceResult(
            normalization_status=NormalizationStatus.UNRESOLVED,
            action=PersistenceAction.NO_ACTION,
            entity_type="   ",
        )
