"""Subproject records: one specific modeling effort within a ``Project``.

See ``docs/02_database_schema.md`` ("Table: subproject") and
``docs/30_project_subproject_model_foundation.md``.

A ``Project`` may contain many ``Subproject`` rows; nothing in this schema
assumes a project has only one model-building effort.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ProjectLifecycleStatus, SubprojectTaskType
from app.models.project import Project

if TYPE_CHECKING:
    from app.models.model import Model
    from app.models.model_specification import ModelSpecification

_SUBPROJECT_TASK_TYPE = Enum(SubprojectTaskType, name="subproject_task_type", create_type=False)
_PROJECT_LIFECYCLE_STATUS = Enum(
    ProjectLifecycleStatus, name="project_lifecycle_status", create_type=False
)


class Subproject(Base):
    """One specific modeling effort/objective belonging to a ``Project``.

    ``experimental_context_id`` links to the existing, more general
    ``ExperimentalContext`` table -- never the narrower, Claim/Evidence-
    scoped ``ExperimentalCondition`` table, a different, unrelated structure
    (see ``app.models.experimental_context``'s own docstring for why the two
    are never merged).
    """

    __tablename__ = "subproject"
    __table_args__ = (
        UniqueConstraint("project_id", "title", name="uq_subproject_project_id_title"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    objective: Mapped[str | None] = mapped_column(Text)

    task_type: Mapped[SubprojectTaskType] = mapped_column(_SUBPROJECT_TASK_TYPE, nullable=False)

    experimental_context_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experimental_context.id", ondelete="RESTRICT"),
        index=True,
    )

    status: Mapped[ProjectLifecycleStatus] = mapped_column(
        _PROJECT_LIFECYCLE_STATUS,
        nullable=False,
        default=ProjectLifecycleStatus.ACTIVE,
        server_default=ProjectLifecycleStatus.ACTIVE.value,
    )

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    project: Mapped[Project] = relationship(back_populates="subprojects")
    specification: Mapped[ModelSpecification | None] = relationship(
        back_populates="subproject", cascade="all, delete-orphan", passive_deletes=True
    )
    models: Mapped[list[Model]] = relationship(back_populates="subproject")


__all__ = ["Subproject"]
