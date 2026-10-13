"""Model records: one concrete model artifact/version.

See ``docs/02_database_schema.md`` ("Table: model") and
``docs/30_project_subproject_model_foundation.md``.

A ``Subproject`` may contain multiple ``Model`` rows/versions; a later
calibration or modification never overwrites a prior model -- it creates a
new row, linked via ``parent_model_id``, so model lineage is always
reproducible. This table intentionally does **not** yet carry any column
for an executable reaction/species/parameter representation -- that is a
future ``ModelNetworkRepresentation`` table, attachable to ``Model`` without
altering this table's own shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ModelSourceType, ModelStatus
from app.models.subproject import Subproject

if TYPE_CHECKING:
    pass

_MODEL_SOURCE_TYPE = Enum(ModelSourceType, name="model_source_type", create_type=False)
_MODEL_STATUS = Enum(ModelStatus, name="model_status", create_type=False)


class Model(Base):
    """One concrete model artifact/version belonging to a ``Subproject``.

    ``stable_model_identifier`` is globally unique, allocated the same way
    ``Reaction.internal_id`` is (a dedicated PostgreSQL sequence,
    ``model_stable_identifier_seq``, via
    ``app.persistence.model_id_allocator``) when the caller does not supply
    one. ``executable_format`` is a deliberately open ``VARCHAR``, not a
    closed enum -- the specification's own "initially including ANTIMONY"
    wording signals this vocabulary will grow, the same policy already
    applied to ``kinetic_measurement.parameter_type``.
    """

    __tablename__ = "model"
    __table_args__ = (
        UniqueConstraint("stable_model_identifier", name="uq_model_stable_model_identifier"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    subproject_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subproject.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    stable_model_identifier: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    source_type: Mapped[ModelSourceType] = mapped_column(_MODEL_SOURCE_TYPE, nullable=False)
    status: Mapped[ModelStatus] = mapped_column(
        _MODEL_STATUS,
        nullable=False,
        default=ModelStatus.DRAFT,
        server_default=ModelStatus.DRAFT.value,
    )

    parent_model_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("model.id", ondelete="RESTRICT"), index=True
    )
    version_label: Mapped[str | None] = mapped_column(String)

    executable_format: Mapped[str | None] = mapped_column(String)

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

    subproject: Mapped[Subproject] = relationship(back_populates="models")
    parent_model: Mapped[Model | None] = relationship(remote_side="Model.id")


__all__ = ["Model"]
