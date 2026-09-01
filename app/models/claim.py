"""Claim, evidence, and evidence-condition records.

See ``docs/02_database_schema.md``: "Table: claim", "Table: evidence",
"Table: evidence_condition".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType

if TYPE_CHECKING:
    from app.models.experimental_condition import ExperimentalCondition
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.organism import Organism
    from app.models.publication import Publication

# Type creation is owned exclusively by migration 0005_claim_evidence
# (create_type=False): these ORM-level Enum instances only describe the
# column type, they never issue CREATE TYPE themselves.
_CLAIM_STATUS = Enum(ClaimStatus, name="claim_status", create_type=False)
_CONFIDENCE_CLASS = Enum(ConfidenceClass, name="confidence_class", create_type=False)
_EVIDENCE_TYPE = Enum(EvidenceType, name="evidence_type", create_type=False)
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class Claim(Base):
    """A single scientific assertion.

    ``subject_type``/``subject_id`` and ``object_type``/``object_id`` are
    polymorphic references with no foreign key: the referenced table varies
    by ``*_type``, which the specification does not constrain to a closed
    enumeration. This is a known referential-integrity limitation, deferred
    to the validation layer rather than approximated here with an invented
    enum, multi-table FK, or trigger.
    """

    __tablename__ = "claim"
    __table_args__ = (
        # conv() marks the name as already final — see the identical note in
        # app/models/reaction.py for why this is required to avoid the "ck"
        # naming convention mangling an already-explicit name.
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_claim_confidence_score_range"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    subject_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subject_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)

    predicate: Mapped[str] = mapped_column(String, nullable=False, index=True)

    object_type: Mapped[str | None] = mapped_column(String)
    object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    value_text: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    strain: Mapped[str | None] = mapped_column(String)

    claim_category: Mapped[str | None] = mapped_column(String)

    status: Mapped[ClaimStatus] = mapped_column(
        _CLAIM_STATUS, nullable=False, default=ClaimStatus.UNKNOWN
    )

    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric)
    confidence_class: Mapped[ConfidenceClass | None] = mapped_column(_CONFIDENCE_CLASS)

    created_by: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    organism: Mapped[Organism | None] = relationship(back_populates="claims")
    evidence_records: Mapped[list[Evidence]] = relationship(back_populates="claim")


class Evidence(Base):
    """A scientific evidence record supporting a claim.

    Only ``created_at`` is defined: the specification gives this table no
    ``updated_at`` column, consistent with it being part of the append-only
    provenance trail. ``organism``/``strain`` here are free text describing
    the evidence's experimental origin, distinct from ``claim.organism_id``
    (a foreign key) — the specification defines them differently on each
    table and neither is inferred from the other.
    """

    __tablename__ = "evidence"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    claim_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("claim.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )

    source_type: Mapped[SourceType] = mapped_column(_SOURCE_TYPE, nullable=False, index=True)

    source_id: Mapped[str | None] = mapped_column(String)

    database_name: Mapped[str | None] = mapped_column(String)
    database_accession: Mapped[str | None] = mapped_column(String)

    evidence_type: Mapped[EvidenceType] = mapped_column(_EVIDENCE_TYPE, nullable=False)

    organism: Mapped[str | None] = mapped_column(String)
    strain: Mapped[str | None] = mapped_column(String)

    experimental_system: Mapped[str | None] = mapped_column(Text)
    assay_type: Mapped[str | None] = mapped_column(Text)

    directness: Mapped[str | None] = mapped_column(String)

    quoted_support: Mapped[str | None] = mapped_column(Text)
    curator_summary: Mapped[str] = mapped_column(Text, nullable=False)

    page: Mapped[str | None] = mapped_column(String)
    figure: Mapped[str | None] = mapped_column(String)
    table_reference: Mapped[str | None] = mapped_column(String)

    date_accessed: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="evidence_records")
    publication: Mapped[Publication | None] = relationship(back_populates="evidence_records")
    conditions: Mapped[list[EvidenceCondition]] = relationship(back_populates="evidence")
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="evidence"
    )


class EvidenceCondition(Base):
    """Associates an evidence record with an experimental condition."""

    __tablename__ = "evidence_condition"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id",
            "experimental_condition_id",
            name="uq_evidence_condition_evidence_id_experimental_condition_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    evidence_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False
    )
    # name= is explicit and shortened: the formulaic
    # fk_<table>_<column>_<referred_table> name here would be 70 characters,
    # exceeding PostgreSQL's 63-byte identifier limit (the column name
    # already embeds the full referred-table name, making the suffix
    # redundant). Matches the identical shortened name in the migration.
    experimental_condition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "experimental_condition.id",
            ondelete="RESTRICT",
            name="fk_evidence_condition_experimental_condition_id",
        ),
        nullable=False,
    )

    evidence: Mapped[Evidence] = relationship(back_populates="conditions")
    experimental_condition: Mapped[ExperimentalCondition] = relationship(
        back_populates="evidence_conditions"
    )
