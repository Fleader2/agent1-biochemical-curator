"""Protein records.

See ``docs/02_database_schema.md`` ("Table: protein").
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
    from app.models.enzyme_complex import EnzymeComplexMember
    from app.models.gene import Gene
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.organism import Organism
    from app.models.reaction import ReactionEnzyme


class Protein(Base):
    """A protein product, optionally associated with a gene.

    A protein record must not assume that every gene corresponds to exactly
    one active enzyme (``docs/02_database_schema.md``), so ``gene_id`` is
    nullable. ``uniprot_id`` is indexed but not unique here: it is a distinct
    column from ``gene.uniprot_id``, and only the identifier columns on
    ``gene`` carry a uniqueness requirement in the specification.
    """

    __tablename__ = "protein"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    gene_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("gene.id", ondelete="RESTRICT")
    )
    organism_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    uniprot_id: Mapped[str | None] = mapped_column(String, index=True)
    ec_number: Mapped[str | None] = mapped_column(String, index=True)

    subunit_state: Mapped[str | None] = mapped_column(String)
    localization_consensus: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    gene: Mapped[Gene | None] = relationship(back_populates="proteins")
    organism: Mapped[Organism] = relationship(back_populates="proteins")
    complex_memberships: Mapped[list[EnzymeComplexMember]] = relationship(back_populates="protein")
    reaction_enzymes: Mapped[list[ReactionEnzyme]] = relationship(back_populates="protein")
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="protein"
    )
