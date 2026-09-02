"""Kinetic measurement records.

See ``docs/02_database_schema.md`` ("Table: kinetic_measurement").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import ConfidenceClass

if TYPE_CHECKING:
    from app.models.claim import Evidence
    from app.models.compound import Compound
    from app.models.enzyme_complex import EnzymeComplex
    from app.models.organism import Organism
    from app.models.protein import Protein
    from app.models.publication import Publication
    from app.models.reaction import Reaction

# Type creation is owned exclusively by migration 0005_claim_evidence, not
# this table's migration (0006_kinetic_measurement reuses this type rather
# than recreating it): create_type=False means this ORM-level Enum instance
# only describes the column type, it never issues CREATE TYPE itself.
_CONFIDENCE_CLASS = Enum(ConfidenceClass, name="confidence_class", create_type=False)


class KineticMeasurement(Base):
    """A single, independently-sourced kinetic measurement.

    Every measurement is its own row. This table must never contain averaged
    values derived from multiple papers unless explicitly marked as derived
    data (``docs/02_database_schema.md``), so no natural-key uniqueness or
    deduplication constraint is placed on any combination of its columns:
    two measurements that appear identical (same reaction, protein, compound,
    parameter type, value, publication, and evidence) must both be able to
    persist as independent rows.

    ``original_value``/``original_unit`` are preserved independently of
    ``normalized_value``/``normalized_unit``; normalization never overwrites
    the original measurement. ``parameter_type`` is a plain, unconstrained
    ``VARCHAR`` — not an enum and not CHECK-restricted — so that future
    parameter types can be recorded without a schema migration.
    """

    __tablename__ = "kinetic_measurement"
    __table_args__ = (
        # conv() marks the name as already final — see the identical note in
        # app/models/reaction.py and app/models/claim.py for why this is
        # required to avoid the "ck" naming convention mangling an
        # already-explicit name.
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_kinetic_measurement_confidence_score_range"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), index=True
    )

    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), index=True
    )
    complex_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="RESTRICT")
    )

    parameter_type: Mapped[str] = mapped_column(String, nullable=False, index=True)

    parameter_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)

    original_value: Mapped[Decimal | None] = mapped_column(Numeric)
    original_unit: Mapped[str | None] = mapped_column(String)

    normalized_value: Mapped[Decimal | None] = mapped_column(Numeric)
    normalized_unit: Mapped[str | None] = mapped_column(String)

    substrate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT")
    )

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    strain: Mapped[str | None] = mapped_column(String)

    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric)
    ph: Mapped[Decimal | None] = mapped_column(Numeric)
    ionic_strength: Mapped[Decimal | None] = mapped_column(Numeric)
    ionic_strength_unit: Mapped[str | None] = mapped_column(String)

    buffer: Mapped[str | None] = mapped_column(Text)

    enzyme_concentration: Mapped[Decimal | None] = mapped_column(Numeric)
    enzyme_concentration_unit: Mapped[str | None] = mapped_column(String)

    substrate_concentrations_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSONB
    )

    protein_form: Mapped[str | None] = mapped_column(String)
    purification_state: Mapped[str | None] = mapped_column(String)

    assay_type: Mapped[str | None] = mapped_column(String)

    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT")
    )

    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric)
    confidence_class: Mapped[ConfidenceClass | None] = mapped_column(_CONFIDENCE_CLASS)

    model_applicability_score: Mapped[Decimal | None] = mapped_column(Numeric)

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

    reaction: Mapped[Reaction | None] = relationship(back_populates="kinetic_measurements")
    protein: Mapped[Protein | None] = relationship(back_populates="kinetic_measurements")
    complex: Mapped[EnzymeComplex | None] = relationship(back_populates="kinetic_measurements")
    substrate: Mapped[Compound | None] = relationship(back_populates="kinetic_measurements")
    organism: Mapped[Organism | None] = relationship(back_populates="kinetic_measurements")
    publication: Mapped[Publication | None] = relationship(back_populates="kinetic_measurements")
    evidence: Mapped[Evidence | None] = relationship(back_populates="kinetic_measurements")
