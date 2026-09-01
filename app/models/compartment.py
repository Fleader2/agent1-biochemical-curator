"""Cellular compartment records.

See ``docs/02_database_schema.md`` ("Table: compartment").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organism import Organism


class Compartment(Base):
    """A cellular compartment, optionally scoped to an organism.

    ``organism_id`` is nullable. The standard compartment seed rows created by
    migration ``0002_reference_data`` intentionally leave it ``NULL``, since no
    organism seed row is specified in ``docs/02_database_schema.md``.
    """

    __tablename__ = "compartment"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String)
    ontology_id: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    organism: Mapped[Organism | None] = relationship(back_populates="compartments")
