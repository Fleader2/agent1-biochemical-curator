"""Compound and compound-synonym records.

See ``docs/02_database_schema.md`` ("Table: compound", "Table: compound_synonym").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint, false, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Compound(Base):
    """A normalized chemical species.

    Compounds with different protonation states or chemically distinct
    molecular forms must not be merged merely because they have similar names
    (``docs/02_database_schema.md``). No uniqueness constraint is placed on
    ``chebi_id``/``kegg_compound_id``/``pubchem_cid``/``metacyc_id``/``inchikey``:
    unlike the analogous identifier fields on ``gene`` and ``publication``, the
    specification does not require one here.
    """

    __tablename__ = "compound"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    canonical_name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    formula: Mapped[str | None] = mapped_column(String)
    charge: Mapped[int | None] = mapped_column()
    molecular_weight: Mapped[Decimal | None] = mapped_column(Numeric)

    chebi_id: Mapped[str | None] = mapped_column(String, index=True)
    kegg_compound_id: Mapped[str | None] = mapped_column(String, index=True)
    pubchem_cid: Mapped[str | None] = mapped_column(String)
    metacyc_id: Mapped[str | None] = mapped_column(String)

    inchi: Mapped[str | None] = mapped_column(Text)
    inchikey: Mapped[str | None] = mapped_column(String, index=True)
    smiles: Mapped[str | None] = mapped_column(Text)

    is_generic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    synonyms: Mapped[list[CompoundSynonym]] = relationship(
        back_populates="compound", cascade="all, delete-orphan", passive_deletes=True
    )


class CompoundSynonym(Base):
    """A synonym for a compound.

    Has no independent scientific meaning apart from its compound, so
    ``compound_id`` uses ``ON DELETE CASCADE`` (``docs/02_database_schema.md``,
    "Delete Behavior": named as one of the two tables where cascade is
    permitted).
    """

    __tablename__ = "compound_synonym"
    __table_args__ = (
        UniqueConstraint(
            "compound_id", "synonym", name="uq_compound_synonym_compound_id_synonym"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    compound_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="CASCADE"), nullable=False
    )

    synonym: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String)

    compound: Mapped[Compound] = relationship(back_populates="synonyms")
