"""Organism and strain records.

See ``docs/02_database_schema.md`` ("Table: organism").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.claim import Claim
    from app.models.compartment import Compartment
    from app.models.enzyme_complex import EnzymeComplex
    from app.models.gene import Gene
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.protein import Protein
    from app.models.reaction import Reaction


class Organism(Base):
    """A specific organism, optionally scoped to a strain.

    Multiple strain-specific rows for the same species are expected
    (``docs/02_database_schema.md``: "Multiple strain-specific records for the
    same species are permitted and expected"). ``(scientific_name, strain)``
    is therefore only unique when ``strain`` is present; rows with
    ``strain IS NULL`` are not deduplicated by the schema.
    """

    __tablename__ = "organism"
    __table_args__ = (
        Index(
            "uq_organism_scientific_name_strain",
            "scientific_name",
            "strain",
            unique=True,
            postgresql_where=text("strain IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    scientific_name: Mapped[str] = mapped_column(String, nullable=False)
    common_name: Mapped[str | None] = mapped_column(String)
    ncbi_taxonomy_id: Mapped[int | None] = mapped_column(index=True)
    kegg_code: Mapped[str | None] = mapped_column(String)
    biocyc_id: Mapped[str | None] = mapped_column(String)

    strain: Mapped[str | None] = mapped_column(String)
    strain_parent: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    compartments: Mapped[list[Compartment]] = relationship(back_populates="organism")
    genes: Mapped[list[Gene]] = relationship(back_populates="organism")
    proteins: Mapped[list[Protein]] = relationship(back_populates="organism")
    enzyme_complexes: Mapped[list[EnzymeComplex]] = relationship(back_populates="organism")
    reactions: Mapped[list[Reaction]] = relationship(back_populates="organism")
    claims: Mapped[list[Claim]] = relationship(back_populates="organism")
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="organism"
    )
