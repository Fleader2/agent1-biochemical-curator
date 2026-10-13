"""Subproject persistence.

Idempotent on ``(Subproject.project_id, Subproject.title)``, backed by
``uq_subproject_project_id_title``. Transaction handling mirrors
``app.persistence.project``/``app.persistence.knowledge_gap``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import ProjectLifecycleStatus, SubprojectTaskType
from app.models.subproject import Subproject
from app.persistence.subproject_types import SubprojectPersistenceResult
from app.persistence.types import PersistenceAction


def _existing_by_natural_key(session: Session, project_id: UUID, title: str) -> Subproject | None:
    return session.execute(
        select(Subproject).where(Subproject.project_id == project_id, Subproject.title == title)
    ).scalar_one_or_none()


def persist_subproject(
    *,
    project_id: UUID,
    title: str,
    task_type: SubprojectTaskType,
    session: Session,
    objective: str | None = None,
    experimental_context_id: UUID | None = None,
    status: ProjectLifecycleStatus = ProjectLifecycleStatus.ACTIVE,
    notes: str | None = None,
) -> SubprojectPersistenceResult:
    """Persist one ``Subproject``, keyed by ``(project_id, title)``. See module docstring."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("persist_subproject requires a non-empty title")

    existing = _existing_by_natural_key(session, project_id, title)
    if existing is not None:
        return SubprojectPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            subproject_id=existing.id,
            created=False,
            reused_existing=True,
            project_id=project_id,
            title=title,
            reason=(
                f"reused existing subproject {existing.id} with the identical (project_id, title)"
            ),
        )

    try:
        with session.begin_nested():
            row = Subproject(
                project_id=project_id,
                title=title,
                objective=objective,
                task_type=task_type,
                experimental_context_id=experimental_context_id,
                status=status,
                notes=notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_by_natural_key(session, project_id, title)
        if raced_existing is not None:
            return SubprojectPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                subproject_id=raced_existing.id,
                created=False,
                reused_existing=True,
                project_id=project_id,
                title=title,
                reason=(
                    f"reused existing subproject {raced_existing.id} after a concurrent insert won"
                ),
            )
        return SubprojectPersistenceResult(
            action=PersistenceAction.FAILED,
            subproject_id=None,
            created=False,
            reused_existing=False,
            project_id=project_id,
            title=title,
            reason=(
                "subproject creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return SubprojectPersistenceResult(
        action=PersistenceAction.CREATED,
        subproject_id=row.id,
        created=True,
        reused_existing=False,
        project_id=project_id,
        title=title,
        reason="created new subproject",
    )


def get_subproject(session: Session, subproject_id: UUID) -> Subproject | None:
    """One ``Subproject`` row by id, or ``None``. Read-only."""
    return session.get(Subproject, subproject_id)


__all__ = ["get_subproject", "persist_subproject"]
