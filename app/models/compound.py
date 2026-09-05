"""Compound and compound-synonym records.

See ``docs/02_database_schema.md`` ("Table: compound", "Table: compound_synonym").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.reaction import ReactionParticipant


class Compound(Base):
    """A normalized chemical species.

    Compounds with different protonation states or chemically distinct
    molecular forms must not be merged merely because they have similar names
    (``docs/02_database_schema.md``). No uniqueness constraint is placed on
    ``chebi_id``/``kegg_compound_id``/``pubchem_cid``/``metacyc_id``/``inchikey``:
    unlike the analogous identifier fields on ``gene`` and ``publication``, the
    specification does not require one here, and ``app.normalization.compound``
    treats a duplicate row sharing any one of these identifiers as a live,
    expected ``AMBIGUOUS`` outcome, not a defensive edge case -- schema
    hardening (migration ``0009_persistence_hardening``) deliberately leaves
    this alone. ``pubchem_cid``/``metacyc_id`` are indexed as of that same
    migration (matching ``chebi_id``/``kegg_compound_id``/``inchikey``'s
    existing indexes) purely to support ``app.persistence.compound``'s
    freshness-recheck lookups -- indexing is not a uniqueness decision.
    """

    __tablename__ = "compound"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    canonical_name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    formula: Mapped[str | None] = mapped_column(String)
    charge: Mapped[int | None] = mapped_column()
    molecular_weight: Mapped[Decimal | None] = mapped_column(Numeric)

    chebi_id: Mapped[str | None] = mapped_column(String, index=True)
    kegg_compound_id: Mapped[str | None] = mapped_column(String, index=True)
    pubchem_cid: Mapped[str | None] = mapped_column(String, index=True)
    metacyc_id: Mapped[str | None] = mapped_column(String, index=True)

    inchi: Mapped[str | None] = mapped_column(Text)
    inchikey: Mapped[str | None] = mapped_column(String, index=True)
    smiles: Mapped[str | None] = mapped_column(Text)

    is_generic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
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

    synonyms: Mapped[list[CompoundSynonym]] = relationship(
        back_populates="compound", cascade="all, delete-orphan", passive_deletes=True
    )
    reaction_participants: Mapped[list[ReactionParticipant]] = relationship(
        back_populates="compound"
    )
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="substrate"
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
