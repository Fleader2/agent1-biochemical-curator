"""Quantitative observation records (Agent 1.x Increment "Experimental Context
and Quantitative Observation Framework").

See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale. This is the common ingestion model for SGD
reference protein abundance now, and future custom-database observations
(protein/metabolite concentrations, fluxes, cell volumes, time series,
replicates) later -- no connector or Agent 4 logic is implemented here, only
the schema and its deterministic reshaping into the Agent 1 handoff.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import QuantitativeEvidenceClass, SourceType, TimeReferenceBasis

if TYPE_CHECKING:
    from app.models.compound import Compound
    from app.models.experimental_context import ExperimentalContext
    from app.models.organism import Organism
    from app.models.perturbation import Perturbation
    from app.models.protein import Protein
    from app.models.publication import Publication
    from app.models.reaction import Reaction

_QUANTITATIVE_EVIDENCE_CLASS = Enum(QuantitativeEvidenceClass, name="quantitative_evidence_class")
_TIME_REFERENCE_BASIS = Enum(TimeReferenceBasis, name="time_reference_basis")
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class QuantitativeObservation(Base):
    """A single, independently-sourced quantitative biological observation.

    Mirrors ``KineticMeasurement``'s own foundational design choices,
    deliberately: every observation is its own row (no natural-key
    uniqueness/deduplication constraint on any column combination -- two
    observations that appear identical must both be able to persist as
    independent rows); ``value``/``unit`` are always the as-reported
    figures, ``normalized_value``/``normalized_unit`` are the
    ``app.normalization.quantitative_observation.convert_quantitative_unit``
    canonical conversion (``None``/``None`` when unresolved -- never
    fabricated); ``observation_type`` is a deliberately open ``VARCHAR``,
    never a closed enum, so a future observation type never requires a
    migration to add (mirrors ``parameter_type``'s own identical policy);
    identity links (``protein_id``/``compound_id``/``reaction_id``/
    ``organism_id``) are accepted only as already-resolved UUIDs, never
    inferred from fuzzy text -- an observation whose source identity could
    not be deterministically resolved leaves every one of those ``NULL``
    and instead preserves the raw text on
    ``unresolved_identity_kind``/``unresolved_identity_text``, rather than
    silently guessing a link or silently dropping the record.

    A partial unique index on ``(source, source_id)`` (both non-null)
    makes re-ingesting the identical source record idempotent at the
    database layer, mirroring ``kinetic_measurement``'s own identical
    index for the identical reason -- useful the moment a real connector
    (SGD abundance, or a future custom database) is wired up, even though
    this increment adds no connector itself.
    """

    __tablename__ = "quantitative_observation"
    __table_args__ = (
        CheckConstraint(
            "time_reference_basis IS NULL OR time_canonical_s IS NOT NULL "
            "OR time_value IS NOT NULL",
            name=conv("ck_quantitative_observation_time_reference_requires_time"),
        ),
        Index(
            "uq_quantitative_observation_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    observation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    reported_observation_type: Mapped[str | None] = mapped_column(String)

    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    normalized_value: Mapped[Decimal | None] = mapped_column(Numeric)
    normalized_unit: Mapped[str | None] = mapped_column(String)

    uncertainty: Mapped[Decimal | None] = mapped_column(Numeric)
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric)
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric)

    measurement_method: Mapped[str | None] = mapped_column(String)

    evidence_class: Mapped[QuantitativeEvidenceClass] = mapped_column(
        _QUANTITATIVE_EVIDENCE_CLASS, nullable=False
    )

    # --- Time (task Sec 2: "time must be a first-class field") -----------------------------
    # time_reference_basis IS NULL means "no time / a reference value" -- the first of the
    # task's own four listed cases. It is never required, and a CHECK constraint (above)
    # only guards internal consistency (a stated basis must carry an actual time value),
    # never forces every observation to have one.
    time_reference_basis: Mapped[TimeReferenceBasis | None] = mapped_column(
        _TIME_REFERENCE_BASIS
    )
    time_value: Mapped[Decimal | None] = mapped_column(Numeric)
    time_unit: Mapped[str | None] = mapped_column(String)
    time_canonical_s: Mapped[Decimal | None] = mapped_column(Numeric)

    experimental_context_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: this table+column+referred-table combination's
        # naming-convention-derived name (72 chars) exceeds PostgreSQL's 63-character
        # identifier limit -- confirmed directly (IdentifierError) while writing migration
        # 0017_experimental_context_and_quantitative_observation, which uses this exact name.
        ForeignKey(
            "experimental_context.id",
            ondelete="RESTRICT",
            name="fk_qobs_experimental_context_id_experimental_context",
        ),
        index=True,
    )
    # time_reference_basis == PERTURBATION_ONSET implies this is set -- enforced by
    # app.normalization.quantitative_observation.QuantitativeObservationIdentity, not by a
    # database CHECK (mirrors KineticMeasurementIdentity's own paired-field validation
    # convention for original_source/original_source_identifier).
    perturbation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("perturbation.id", ondelete="RESTRICT"), index=True
    )

    # --- Replicates (task Sec 1: never required when the source lacks them) ---------------
    biological_replicate_id: Mapped[str | None] = mapped_column(String)
    technical_replicate_id: Mapped[str | None] = mapped_column(String)

    # --- Identity links (task Sec 3: deterministic only, never fuzzy-inferred) -------------
    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), index=True
    )
    compound_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT"), index=True
    )
    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), index=True
    )
    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), index=True
    )
    #: Free text naming *what kind* of entity ``unresolved_identity_text`` refers to (e.g.
    #: ``"protein"``, ``"compound"``) -- open, never a closed enum, exactly like
    #: ``observation_type``/``perturbation_type``. Set only when the source names a specific
    #: biological identity that could not be deterministically resolved to any of the four
    #: links above -- never set merely because a link happens to be irrelevant for this
    #: observation type (e.g. a growth-rate observation has no compound identity to resolve
    #: at all, and leaves both of these NULL, not "unresolved").
    unresolved_identity_kind: Mapped[str | None] = mapped_column(String)
    unresolved_identity_text: Mapped[str | None] = mapped_column(Text)

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    #: Free-text dataset/experiment identifier (e.g. an SGD dataset accession, a GEO series
    #: id) -- open, never a closed enum or a foreign key to a dataset table this increment
    #: does not add (task Sec 5: "dataset/experiment identity").
    dataset_id: Mapped[str | None] = mapped_column(String)

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

    experimental_context: Mapped[ExperimentalContext | None] = relationship(
        back_populates="quantitative_observations"
    )
    perturbation: Mapped[Perturbation | None] = relationship(
        back_populates="quantitative_observations"
    )
    protein: Mapped[Protein | None] = relationship(back_populates="quantitative_observations")
    compound: Mapped[Compound | None] = relationship(back_populates="quantitative_observations")
    reaction: Mapped[Reaction | None] = relationship(back_populates="quantitative_observations")
    organism: Mapped[Organism | None] = relationship(back_populates="quantitative_observations")
    publication: Mapped[Publication | None] = relationship(
        back_populates="quantitative_observations"
    )
    #: This observation's own dependencies, when it is itself DERIVED (this row is the
    #: "derived_observation" side -- see QuantitativeObservationDependency's own docstring).
    input_dependencies: Mapped[list[QuantitativeObservationDependency]] = relationship(
        back_populates="derived_observation",
        foreign_keys="QuantitativeObservationDependency.derived_observation_id",
    )
    #: Every derived observation that names this row as one of its inputs (this row is the
    #: "input_observation" side).
    dependent_derivations: Mapped[list[QuantitativeObservationDependency]] = relationship(
        back_populates="input_observation",
        foreign_keys="QuantitativeObservationDependency.input_observation_id",
    )


class QuantitativeObservationDependency(Base):
    """Records that one ``DERIVED`` ``QuantitativeObservation`` depends on another,
    already-persisted ``QuantitativeObservation`` as one of its inputs (task Sec 7).

    Example (task's own): an enzyme-concentration observation derived from a
    protein-abundance observation *and* a cell-volume observation would have
    two rows here, both with the same ``derived_observation_id`` and
    different ``input_observation_id``\\ s.

    **This increment represents dependency/provenance only -- it never
    performs the derivation itself** (task's own explicit exclusion,
    Sec 7/12): no code anywhere computes a ``DERIVED`` value's
    ``value``/``normalized_value`` from its declared inputs. A future
    increment that does perform derivations would read this table to know
    which inputs to use and would still write its own, separately-computed
    ``QuantitativeObservation`` row -- this table only ever records which
    rows *were* used and what extra assumption, if any, the derivation
    required.

    ``derived_observation_id`` cascades (this row has no independent
    meaning apart from the derived observation it describes);
    ``input_observation_id`` restricts (an independent scientific
    observation must never be deleted out from under a dependency that
    still names it) -- mirrors ``KineticMeasurementProteinContext``'s own
    identical ``ON DELETE`` split.
    """

    __tablename__ = "quantitative_observation_dependency"
    __table_args__ = (
        UniqueConstraint(
            "derived_observation_id",
            "input_observation_id",
            name="uq_quantitative_observation_dependency_derived_input",
        ),
        CheckConstraint(
            "derived_observation_id != input_observation_id",
            name=conv("ck_quantitative_observation_dependency_not_self"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    # Both FKs below use explicit, shortened names for the same reason
    # ``QuantitativeObservation.experimental_context_id`` does (see that column's own
    # comment) -- the naming-convention-derived names here (86/84 chars) also exceed
    # PostgreSQL's 63-character limit.
    derived_observation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "quantitative_observation.id",
            ondelete="CASCADE",
            name="fk_qo_dependency_derived_observation_id_qo",
        ),
        nullable=False,
        index=True,
    )
    input_observation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "quantitative_observation.id",
            ondelete="RESTRICT",
            name="fk_qo_dependency_input_observation_id_qo",
        ),
        nullable=False,
        index=True,
    )

    #: Open, free-text label of what role this input plays (e.g.
    #: ``"protein_abundance_input"``, ``"cell_volume_input"``) -- never a closed enum, since
    #: the set of possible derivation roles is exactly as open-ended as the set of possible
    #: future derivations.
    role: Mapped[str | None] = mapped_column(String)
    #: Any extra, non-observational assumption the derivation required (e.g. "assumed
    #: reference cell volume of 0.1 pL used in the absence of strain-specific data") --
    #: preserved as plain text since this increment represents, but never validates or
    #: enforces, derivation assumptions.
    assumption_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    derived_observation: Mapped[QuantitativeObservation] = relationship(
        back_populates="input_dependencies",
        foreign_keys=[derived_observation_id],
    )
    input_observation: Mapped[QuantitativeObservation] = relationship(
        back_populates="dependent_derivations",
        foreign_keys=[input_observation_id],
    )
