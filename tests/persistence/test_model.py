"""Tests for ``app.persistence.model``."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import ModelSourceType, ModelStatus, ProjectType, SubprojectTaskType
from app.persistence.model import get_model, persist_model
from app.persistence.project import persist_project
from app.persistence.subproject import persist_subproject
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _make_subproject_id(db_session) -> uuid.UUID:
    project = persist_project(
        stable_identifier=f"PROJ_{uuid.uuid4().hex[:8]}",
        title="test-only project",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )
    subproject = persist_subproject(
        project_id=project.project_id,
        title=f"test-only subproject {uuid.uuid4().hex[:8]}",
        task_type=SubprojectTaskType.BUILD_MODEL,
        session=db_session,
    )
    return subproject.subproject_id


def test_creates_new_model_with_allocated_identifier(db_session):
    subproject_id = _make_subproject_id(db_session)
    result = persist_model(
        subproject_id=subproject_id,
        name="core metabolic model v1",
        source_type=ModelSourceType.GENERATED,
        session=db_session,
    )

    assert result.action is PersistenceAction.CREATED
    assert result.stable_model_identifier.startswith("MODEL")
    row = get_model(db_session, result.model_id)
    assert row.stable_model_identifier == result.stable_model_identifier


def test_allocated_identifiers_are_unique_across_calls(db_session):
    subproject_id = _make_subproject_id(db_session)
    first = persist_model(
        subproject_id=subproject_id,
        name="model a",
        source_type=ModelSourceType.GENERATED,
        session=db_session,
    )
    second = persist_model(
        subproject_id=subproject_id,
        name="model b",
        source_type=ModelSourceType.GENERATED,
        session=db_session,
    )
    assert first.stable_model_identifier != second.stable_model_identifier


def test_reuses_existing_model_by_caller_supplied_identifier(db_session):
    subproject_id = _make_subproject_id(db_session)
    identifier = f"MODEL_{uuid.uuid4().hex[:8]}"
    first = persist_model(
        subproject_id=subproject_id,
        name="first name",
        source_type=ModelSourceType.GENERATED,
        stable_model_identifier=identifier,
        session=db_session,
    )

    second = persist_model(
        subproject_id=subproject_id,
        name="a different name entirely",
        source_type=ModelSourceType.IMPORTED,
        stable_model_identifier=identifier,
        session=db_session,
    )

    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.model_id == first.model_id
    row = get_model(db_session, first.model_id)
    assert row.name == "first name"
    assert row.source_type is ModelSourceType.GENERATED


@pytest.mark.parametrize(
    "source_type", [ModelSourceType.GENERATED, ModelSourceType.IMPORTED, ModelSourceType.DERIVED]
)
def test_every_source_type_is_persistable(db_session, source_type):
    subproject_id = _make_subproject_id(db_session)
    result = persist_model(
        subproject_id=subproject_id,
        name="test-only",
        source_type=source_type,
        session=db_session,
    )
    row = get_model(db_session, result.model_id)
    assert row.source_type is source_type


def test_calibration_creates_a_new_version_linked_by_parent_model_id(db_session):
    subproject_id = _make_subproject_id(db_session)
    draft = persist_model(
        subproject_id=subproject_id,
        name="draft model",
        source_type=ModelSourceType.GENERATED,
        session=db_session,
    )

    calibrated = persist_model(
        subproject_id=subproject_id,
        name="calibrated model",
        source_type=ModelSourceType.DERIVED,
        session=db_session,
        parent_model_id=draft.model_id,
        status=ModelStatus.ACTIVE,
    )

    assert calibrated.model_id != draft.model_id
    calibrated_row = get_model(db_session, calibrated.model_id)
    assert calibrated_row.parent_model_id == draft.model_id
    # The prior version is never overwritten -- it still exists, untouched.
    draft_row = get_model(db_session, draft.model_id)
    assert draft_row.name == "draft model"
