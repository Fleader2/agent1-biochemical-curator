"""Enzyme complex and enzyme-complex-membership records.

See ``docs/02_database_schema.md`` ("Table: enzyme_complex",
"Table: enzyme_complex_member").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organism import Organism
    from app.models.protein import Protein
    from app.models.reaction import ReactionEnzyme


class EnzymeComplex(Base):
    """A multi-subunit enzyme complex."""

    __tablename__ = "enzyme_complex"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    organism_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    stoichiometry_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    organism: Mapped[Organism] = relationship(back_populates="enzyme_complexes")
    members: Mapped[list[EnzymeComplexMember]] = relationship(
        back_populates="complex", cascade="all, delete-orphan", passive_deletes=True
    )
    reaction_enzymes: Mapped[list[ReactionEnzyme]] = relationship(back_populates="complex")


class EnzymeComplexMember(Base):
    """Associates a protein with an enzyme complex.

    Has no independent scientific meaning apart from its complex, so
    ``complex_id`` uses ``ON DELETE CASCADE`` (``docs/02_database_schema.md``,
    "Delete Behavior": named as one of the two tables where cascade is
    permitted). ``protein_id`` uses ``ON DELETE RESTRICT``: a protein is an
    independent scientific record. No timestamp columns: the specification
    defines none for this table.
    """

    __tablename__ = "enzyme_complex_member"
    __table_args__ = (
        UniqueConstraint(
            "complex_id", "protein_id", name="uq_enzyme_complex_member_complex_id_protein_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    complex_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="CASCADE"), nullable=False
    )
    protein_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), nullable=False
    )

    stoichiometry: Mapped[Decimal | None] = mapped_column(Numeric)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    complex: Mapped[EnzymeComplex] = relationship(back_populates="members")
    protein: Mapped[Protein] = relationship(back_populates="complex_memberships")
