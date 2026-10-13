"""Project persistence.

Idempotent on ``Project.stable_identifier``: persisting a project whose
``stable_identifier`` already exists reuses that row and never overwrites
its fields (same "reuse never touches existing content" policy as
``app.persistence.network``'s own ``MATCHED`` handling). Transaction
handling mirrors ``app.persistence.knowledge_gap``: one ``SAVEPOINT``
around the insert attempt, an ``IntegrityError`` caught and resolved via a
post-failure recheck, every other exception left to propagate. Never
commits or rolls back the session it is given.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import ProjectLifecycleStatus, ProjectType
from app.models.project import Project, ProjectRegulatoryContext
from app.persistence.project_types import ProjectPersistenceResult
from app.persistence.types import PersistenceAction


def _existing_by_stable_identifier(session: Session, stable_identifier: str) -> Project | None:
    return session.execute(
        select(Project).where(Project.stable_identifier == stable_identifier)
    ).scalar_one_or_none()


def persist_project(
    *,
    stable_identifier: str,
    title: str,
    project_type: ProjectType,
    session: Session,
    description: str | None = None,
    organism_id: UUID | None = None,
    strain_genotype_notes: str | None = None,
    biological_process: str | None = None,
    status: ProjectLifecycleStatus = ProjectLifecycleStatus.ACTIVE,
    notes: str | None = None,
    regulatory_context: dict[str, str | None] | None = None,
) -> ProjectPersistenceResult:
    """Persist one ``Project``, keyed by ``stable_identifier``. See module docstring.

    ``regulatory_context``, when given, is a mapping with any of
    ``disease``/``tissue``/``cell_type``/``notes`` and is only ever written
    on initial creation -- consistent with "reuse never touches existing
    content", an existing project's ``ProjectRegulatoryContext`` is never
    modified by a later call with a different ``regulatory_context``.
    """
    if not isinstance(stable_identifier, str) or not stable_identifier.strip():
        raise ValueError("persist_project requires a non-empty stable_identifier")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("persist_project requires a non-empty title")

    existing = _existing_by_stable_identifier(session, stable_identifier)
    if existing is not None:
        return ProjectPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            project_id=existing.id,
            created=False,
            reused_existing=True,
            stable_identifier=stable_identifier,
            reason=f"reused existing project {existing.id} with the identical stable_identifier",
        )

    try:
        with session.begin_nested():
            row = Project(
                stable_identifier=stable_identifier,
                title=title,
                description=description,
                project_type=project_type,
                organism_id=organism_id,
                strain_genotype_notes=strain_genotype_notes,
                biological_process=biological_process,
                status=status,
                notes=notes,
            )
            session.add(row)
            session.flush()

            if regulatory_context is not None:
                session.add(
                    ProjectRegulatoryContext(
                        project_id=row.id,
                        disease=regulatory_context.get("disease"),
                        tissue=regulatory_context.get("tissue"),
                        cell_type=regulatory_context.get("cell_type"),
                        notes=regulatory_context.get("notes"),
                    )
                )
                session.flush()
    except IntegrityError:
        raced_existing = _existing_by_stable_identifier(session, stable_identifier)
        if raced_existing is not None:
            return ProjectPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                project_id=raced_existing.id,
                created=False,
                reused_existing=True,
                stable_identifier=stable_identifier,
                reason=f"reused existing project {raced_existing.id} after a concurrent insert won",
            )
        return ProjectPersistenceResult(
            action=PersistenceAction.FAILED,
            project_id=None,
            created=False,
            reused_existing=False,
            stable_identifier=stable_identifier,
            reason=(
                "project creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return ProjectPersistenceResult(
        action=PersistenceAction.CREATED,
        project_id=row.id,
        created=True,
        reused_existing=False,
        stable_identifier=stable_identifier,
        reason="created new project",
    )


def get_project(session: Session, project_id: UUID) -> Project | None:
    """One ``Project`` row by id, or ``None``. Read-only."""
    return session.get(Project, project_id)


__all__ = ["get_project", "persist_project"]
