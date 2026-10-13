"""Tests for ``app.persistence.modeling_decision``."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import ModelingDecisionResolutionSource, ModelingDecisionStatus, ProjectType
from app.persistence.modeling_decision import (
    add_modeling_decision_option,
    get_modeling_decision,
    persist_modeling_decision,
    resolve_modeling_decision,
)
from app.persistence.project import persist_project
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _make_project_id(db_session) -> uuid.UUID:
    result = persist_project(
        stable_identifier=f"PROJ_{uuid.uuid4().hex[:8]}",
        title="test-only project",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )
    return result.project_id


def test_creates_new_decision(db_session):
    project_id = _make_project_id(db_session)
    result = persist_modeling_decision(
        question="which boundary species should be used?", session=db_session, project_id=project_id
    )

    assert result.action is PersistenceAction.CREATED
    row = get_modeling_decision(db_session, result.decision_id)
    assert row.status is ModelingDecisionStatus.OPEN


def test_reuses_existing_decision_with_the_same_context_and_question(db_session):
    project_id = _make_project_id(db_session)
    first = persist_modeling_decision(
        question="which boundary species should be used?", session=db_session, project_id=project_id
    )
    second = persist_modeling_decision(
        question="which boundary species should be used?", session=db_session, project_id=project_id
    )

    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.decision_id == first.decision_id


def test_requires_at_least_one_context_reference(db_session):
    with pytest.raises(ValueError):
        persist_modeling_decision(question="an orphaned question", session=db_session)


def test_adds_multiple_options(db_session):
    project_id = _make_project_id(db_session)
    decision = persist_modeling_decision(
        question="which carbon source?", session=db_session, project_id=project_id
    )

    option_a = add_modeling_decision_option(
        decision_id=decision.decision_id, label="glucose", session=db_session
    )
    option_b = add_modeling_decision_option(
        decision_id=decision.decision_id, label="ethanol", session=db_session, recommended=True
    )

    assert option_a.action is PersistenceAction.CREATED
    assert option_b.action is PersistenceAction.CREATED
    assert option_a.option_id != option_b.option_id


def test_idempotent_option_persistence(db_session):
    project_id = _make_project_id(db_session)
    decision = persist_modeling_decision(
        question="test-only question", session=db_session, project_id=project_id
    )

    first = add_modeling_decision_option(
        decision_id=decision.decision_id, label="option A", session=db_session
    )
    second = add_modeling_decision_option(
        decision_id=decision.decision_id, label="option A", session=db_session
    )

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.option_id == second.option_id


def test_resolve_modeling_decision_sets_status_and_selected_option(db_session):
    project_id = _make_project_id(db_session)
    decision = persist_modeling_decision(
        question="test-only question", session=db_session, project_id=project_id
    )
    option = add_modeling_decision_option(
        decision_id=decision.decision_id, label="chosen option", session=db_session
    )

    resolved = resolve_modeling_decision(
        decision_id=decision.decision_id,
        selected_option_id=option.option_id,
        resolution_source=ModelingDecisionResolutionSource.USER,
        session=db_session,
        rationale="chosen because of X",
    )

    assert resolved.status is ModelingDecisionStatus.RESOLVED
    assert resolved.selected_option_id == option.option_id
    assert resolved.resolution_source is ModelingDecisionResolutionSource.USER
    assert resolved.rationale == "chosen because of X"


def test_resolve_modeling_decision_rejects_a_cross_decision_option(db_session):
    project_id = _make_project_id(db_session)
    decision_a = persist_modeling_decision(
        question="question A", session=db_session, project_id=project_id
    )
    decision_b = persist_modeling_decision(
        question="question B", session=db_session, project_id=project_id
    )
    option_for_b = add_modeling_decision_option(
        decision_id=decision_b.decision_id, label="option for B", session=db_session
    )

    with pytest.raises(ValueError):
        resolve_modeling_decision(
            decision_id=decision_a.decision_id,
            selected_option_id=option_for_b.option_id,
            resolution_source=ModelingDecisionResolutionSource.USER,
            session=db_session,
        )
