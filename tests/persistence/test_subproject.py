"""Tests for ``app.persistence.subproject``."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import ProjectType, SubprojectTaskType
from app.persistence.project import persist_project
from app.persistence.subproject import get_subproject, persist_subproject
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_experimental_context

pytestmark = pytest.mark.database


def _make_project_id(db_session) -> uuid.UUID:
    result = persist_project(
        stable_identifier=f"PROJ_{uuid.uuid4().hex[:8]}",
        title="test-only project",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )
    return result.project_id


def test_creates_new_subproject(db_session):
    project_id = _make_project_id(db_session)
    result = persist_subproject(
        project_id=project_id,
        title="build the core metabolic model",
        task_type=SubprojectTaskType.BUILD_MODEL,
        session=db_session,
    )

    assert result.action is PersistenceAction.CREATED
    row = get_subproject(db_session, result.subproject_id)
    assert row.project_id == project_id
    assert row.task_type is SubprojectTaskType.BUILD_MODEL


def test_reuses_existing_subproject_by_project_id_and_title(db_session):
    project_id = _make_project_id(db_session)
    first = persist_subproject(
        project_id=project_id,
        title="calibrate the model",
        task_type=SubprojectTaskType.CALIBRATE_MODEL,
        session=db_session,
    )

    second = persist_subproject(
        project_id=project_id,
        title="calibrate the model",
        task_type=SubprojectTaskType.TEST_MODEL,
        session=db_session,
    )

    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.subproject_id == first.subproject_id
    row = get_subproject(db_session, first.subproject_id)
    assert row.task_type is SubprojectTaskType.CALIBRATE_MODEL


def test_two_subprojects_with_different_titles_under_the_same_project(db_session):
    project_id = _make_project_id(db_session)
    build = persist_subproject(
        project_id=project_id,
        title="build",
        task_type=SubprojectTaskType.BUILD_MODEL,
        session=db_session,
    )
    calibrate = persist_subproject(
        project_id=project_id,
        title="calibrate",
        task_type=SubprojectTaskType.CALIBRATE_MODEL,
        session=db_session,
    )

    assert build.subproject_id != calibrate.subproject_id


@pytest.mark.parametrize(
    "task_type",
    [
        SubprojectTaskType.BUILD_MODEL,
        SubprojectTaskType.IMPORT_MODEL,
        SubprojectTaskType.TEST_MODEL,
        SubprojectTaskType.CALIBRATE_MODEL,
        SubprojectTaskType.EXTEND_MODEL,
    ],
)
def test_every_task_type_is_persistable(db_session, task_type):
    project_id = _make_project_id(db_session)
    result = persist_subproject(
        project_id=project_id,
        title=f"test-only {task_type.value}",
        task_type=task_type,
        session=db_session,
    )
    row = get_subproject(db_session, result.subproject_id)
    assert row.task_type is task_type


def test_reuses_experimental_context_rather_than_duplicating_it(db_session):
    project_id = _make_project_id(db_session)
    context = make_experimental_context(db_session)
    result = persist_subproject(
        project_id=project_id,
        title="test-only",
        task_type=SubprojectTaskType.BUILD_MODEL,
        session=db_session,
        experimental_context_id=context.id,
    )

    row = get_subproject(db_session, result.subproject_id)
    assert row.experimental_context_id == context.id
    assert db_session.query(type(context)).filter_by(id=context.id).count() == 1
