"""Kinetic measurement records.

See ``docs/02_database_schema.md`` ("Table: kinetic_measurement").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import ConfidenceClass, SourceType

if TYPE_CHECKING:
    from app.models.claim import Evidence
    from app.models.compound import Compound
    from app.models.enzyme_complex import EnzymeComplex
    from app.models.enzyme_state import EnzymeState
    from app.models.organism import Organism
    from app.models.protein import Protein
    from app.models.publication import Publication
    from app.models.reaction import Reaction

# Type creation is owned exclusively by migration 0005_claim_evidence, not
# this table's migration (0006_kinetic_measurement reuses this type rather
# than recreating it): create_type=False means this ORM-level Enum instance
# only describes the column type, it never issues CREATE TYPE itself.
_CONFIDENCE_CLASS = Enum(ConfidenceClass, name="confidence_class", create_type=False)
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


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

    **Added in Agent 1.x Increment A** (migration
    ``0013_kinetic_measurement_sources``,
    ``docs/24_kinetic_data_curation_and_handoff.md``): ``source``/
    ``source_id`` identify which connector-ingested source *created* this
    row (its primary retrieval source) -- the same ``source_type``/
    ``source_id`` shape ``Evidence`` already uses, deliberately not routed
    through ``SourceCrossReference`` (which has no concept of "primary"
    among several cross-references, see
    ``app.persistence.kinetic_measurement``'s own docstring). A partial
    unique index on ``(source, source_id)`` (both non-null) makes
    re-ingesting the identical source record idempotent at the database
    layer. Additional, non-primary source lineage (e.g. an Open Enzyme
    Database record whose original source is SABIO-RK) is instead recorded
    as an ordinary ``SourceCrossReference`` row for
    ``entity_type="kinetic_measurement"`` -- that table's own "0-to-many
    external identifiers for one entity" shape already fits a measurement
    being *also* known via another source. ``reported_rate_law``/
    ``reported_parameter_type`` preserve a source's own free-text framing
    (the exact rate-law equation text and the exact parameter label as the
    source itself wrote it) independently of this table's own controlled
    ``parameter_type`` value.

    **Added in Agent 1.x Increment B** (migration
    ``0014_enzyme_regulatory_states``,
    ``docs/25_enzyme_regulatory_states_contract.md``): ``enzyme_state_id``
    optionally attributes a measurement to one specific, defined
    ``EnzymeState`` rather than only to the ``protein_id``/``complex_id``
    generally -- e.g. a ``kcat`` reported specifically for the
    phosphorylated form of a protein. Never required: a measurement not
    tied to any particular state leaves this ``NULL``, exactly as before.
    A state-specific measurement applies *only* to that state -- it is
    never treated as applicable to the parent protein/complex generally,
    or to any other state of it (see
    ``docs/25_enzyme_regulatory_states_contract.md`` §15).
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
        Index(
            "uq_kinetic_measurement_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), index=True
    )

    # LEGACY CONVENIENCE FIELD, NOT AUTHORITATIVE (Agent 1.x Increment C.6): records
    # only whichever protein's own persist_kinetic_measurement call happened to
    # reach this (source, source_id) row first -- an accident of processing order
    # (e.g. random UUID sort order), not a scientific judgment that this protein is
    # the "real" or "preferred" one. Kept unmodified, forever, purely so an
    # already-existing single-protein-context caller/query needs no change. The
    # authoritative, complete, order-independent record of every protein this
    # measurement is applicable to is `protein_contexts`
    # (`kinetic_measurement_protein_context`, see that model's own docstring) --
    # always query that (or Agent1KnowledgePackage.kinetic_measurement_protein_
    # contexts / CuratedKineticMeasurement.protein_ids downstream) to answer "which
    # protein(s) is this measurement applicable to," never this column alone.
    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), index=True
    )
    complex_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="RESTRICT")
    )
    enzyme_state_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_state.id", ondelete="RESTRICT"), index=True
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

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)

    reported_rate_law: Mapped[str | None] = mapped_column(Text)
    reported_parameter_type: Mapped[str | None] = mapped_column(String)

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
    enzyme_state: Mapped[EnzymeState | None] = relationship(back_populates="kinetic_measurements")
    protein_contexts: Mapped[list[KineticMeasurementProteinContext]] = relationship(
        back_populates="kinetic_measurement"
    )


#: The sole basis value populated today (Agent 1.x Increment C.6): a protein
#: context was established because a query issued *for that protein*
#: independently discovered this exact source record. This is deliberately a
#: plain string, not a closed enum (mirrors ``KineticMeasurement.parameter_type``'s
#: own "never a closed enum" policy, ``app/models/enums.py``'s own docstring) --
#: a future increment able to derive protein applicability directly from a
#: source's own explicitly-reported protein/accession evidence (see
#: ``app.connectors.sabiork.SabioKineticRecord.uniprot_ids``) would record that
#: as a new, distinct basis value here, e.g. ``"EXPLICIT_SOURCE_EVIDENCE"`` --
#: never silently reusing this one to mean something stronger than it does.
KINETIC_MEASUREMENT_PROTEIN_CONTEXT_BASIS_QUERY = "QUERY_CONTEXT"


class KineticMeasurementProteinContext(Base):
    """Records that one persisted ``KineticMeasurement`` is biologically
    applicable to one ``Protein`` (Agent 1.x Increment C.6).

    **This table -- not ``KineticMeasurement.protein_id`` -- is the
    authoritative representation of protein applicability.**
    ``KineticMeasurement.protein_id`` records only the *first* protein
    context ever established for a given ``(source, source_id)`` record, an
    accident of processing order; it is kept unmodified, forever, purely as
    a legacy convenience field so an already-existing single-protein-context
    caller/query needs no change (see that column's own comment in
    ``KineticMeasurement``). This table is the general, complete,
    order-independent record of *every* protein context a measurement is
    actually applicable to, including that first one (``app.persistence
    .kinetic_measurement.persist_kinetic_measurement`` always attaches a row
    here, on both the create and the reuse path) -- any code that needs to
    answer "which protein(s) is this measurement applicable to" must query
    this table (or its downstream reshapings,
    ``Agent1KnowledgePackage.kinetic_measurement_protein_contexts`` /
    ``CuratedKineticMeasurement.protein_ids``), never ``protein_id`` alone.

    This exists because SABIO-RK's own search is EC-scoped, not
    protein-scoped: two distinct proteins sharing one EC number (e.g. yeast's
    real FAS1/FAS2 heterodimer) can each independently, legitimately
    discover the identical external source record. Before this table
    existed, the second protein's own successful query found the first
    protein's already-persisted row via the ``(source, source_id)`` unique
    index and was silently absorbed into it with no trace of its own,
    equally valid protein context -- confirmed live, Real Integration Pilot
    1 Run 7. This table lets both contexts survive, regardless of which
    protein's query happened to run first.

    Never merges or infers biological identity: FAS1 and FAS2 remain two
    distinct ``Protein`` rows, each with their own row here pointing at the
    *same* ``KineticMeasurement`` -- this table is additive evidence about
    applicability, never a claim that the two proteins are the same enzyme
    or form an inferred complex.

    Has no independent scientific meaning apart from its own measurement
    (mirrors ``EnzymeComplexMember``'s own identical ``ON DELETE`` split):
    ``kinetic_measurement_id`` cascades, ``protein_id`` (an independent
    scientific record) restricts.
    """

    __tablename__ = "kinetic_measurement_protein_context"
    __table_args__ = (
        UniqueConstraint(
            "kinetic_measurement_id",
            "protein_id",
            name="uq_kinetic_measurement_protein_context_km_id_protein_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    kinetic_measurement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("kinetic_measurement.id", ondelete="CASCADE"),
        nullable=False,
    )
    protein_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), nullable=False
    )

    basis: Mapped[str] = mapped_column(
        String, nullable=False, default=KINETIC_MEASUREMENT_PROTEIN_CONTEXT_BASIS_QUERY
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    kinetic_measurement: Mapped[KineticMeasurement] = relationship(
        back_populates="protein_contexts"
    )
    protein: Mapped[Protein] = relationship(back_populates="kinetic_measurement_contexts")
