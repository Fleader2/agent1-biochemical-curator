"""Project records: the long-lived scientific project being studied.

See ``docs/02_database_schema.md`` ("Table: project", "Table:
project_regulatory_context") and
``docs/30_project_subproject_model_foundation.md`` for the full design
rationale.

A ``Project`` describes *what biological system is being studied* -- never
model calibration data, and never the same thing as a ``Subproject`` (what
modeling task is being performed on that system). See
``app.models.subproject`` and the design doc's "Important architectural
boundaries" section.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ProjectLifecycleStatus, ProjectType

if TYPE_CHECKING:
    from app.models.subproject import Subproject

_PROJECT_TYPE = Enum(ProjectType, name="project_type", create_type=False)
_PROJECT_LIFECYCLE_STATUS = Enum(
    ProjectLifecycleStatus, name="project_lifecycle_status", create_type=False
)


class Project(Base):
    """One long-lived scientific project.

    ``organism_id`` links to an existing, already-curated ``Organism`` row --
    never a new one fabricated for the project. ``strain_genotype_notes`` is
    a free-text supplement for project-specific framing beyond what the
    linked ``Organism`` row (itself already strain-scoped) captures; it is
    never a substitute for resolving ``organism_id`` when a suitable row
    exists. ``biological_process`` is free text: Agent 1 has no ``Pathway``
    entity to link to instead (the same gap ``NetworkPathway`` already
    discloses).
    """

    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("stable_identifier", name="uq_project_stable_identifier"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    stable_identifier: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    project_type: Mapped[ProjectType] = mapped_column(_PROJECT_TYPE, nullable=False)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), index=True
    )
    strain_genotype_notes: Mapped[str | None] = mapped_column(Text)
    biological_process: Mapped[str | None] = mapped_column(Text)

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

    regulatory_context: Mapped[ProjectRegulatoryContext | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    subprojects: Mapped[list[Subproject]] = relationship(back_populates="project")


class ProjectRegulatoryContext(Base):
    """A one-to-one extension of ``Project``, holding fields relevant only to
    ``ProjectType.REGULATORY_NETWORK`` projects.

    Kept out of ``Project`` itself so that table never becomes the "one
    oversized table" the design doc's own instructions warn against. Agent 1
    has no ``disease``/``tissue``/``cell_type`` entity, so all three are
    free text -- the same "no entity to link to yet" precedent as
    ``Project.biological_process``.
    """

    __tablename__ = "project_regulatory_context"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    disease: Mapped[str | None] = mapped_column(String)
    tissue: Mapped[str | None] = mapped_column(String)
    cell_type: Mapped[str | None] = mapped_column(String)

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

    project: Mapped[Project] = relationship(back_populates="regulatory_context")


__all__ = ["Project", "ProjectRegulatoryContext"]
