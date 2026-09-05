"""Cellular compartment records.

See ``docs/02_database_schema.md`` ("Table: compartment").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organism import Organism
    from app.models.reaction import ReactionParticipant


class Compartment(Base):
    """A cellular compartment, optionally scoped to an organism.

    ``organism_id`` is nullable. The standard compartment seed rows created by
    migration ``0002_reference_data`` intentionally leave it ``NULL``, since no
    organism seed row is specified in ``docs/02_database_schema.md``.

    As of migration ``0009_persistence_hardening``, ``ontology_id`` is
    indexed (global, matching ``app.normalization.compartment``'s own global
    ``by_ontology_id`` lookup) and ``(organism_id, name)``/
    ``(organism_id, abbreviation)`` are each indexed as composites (matching
    the organism-scoped ``by_name``/``by_abbreviation`` lookups). **None of
    these three is a uniqueness constraint** -- reference rows
    (``organism_id IS NULL``) and organism-specific rows may legitimately
    coexist and even share a ``name``/``abbreviation``/``ontology_id``
    (Open Question F/G/H, ``docs/07_normalization_design.md``), and this
    increment does not collapse that distinction. These are lookup-path
    indexes only, added because ``Compartment`` previously had no index of
    any kind beyond its primary key.
    """

    __tablename__ = "compartment"
    __table_args__ = (
        Index("ix_compartment_organism_id_name", "organism_id", "name"),
        Index("ix_compartment_organism_id_abbreviation", "organism_id", "abbreviation"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String)
    ontology_id: Mapped[str | None] = mapped_column(String, index=True)

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

    organism: Mapped[Organism | None] = relationship(back_populates="compartments")
    reaction_participants: Mapped[list[ReactionParticipant]] = relationship(
        back_populates="compartment"
    )
