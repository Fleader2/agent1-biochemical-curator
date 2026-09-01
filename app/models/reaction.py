"""Reaction, reaction-participant, and reaction-enzyme records.

See ``docs/02_database_schema.md``: "Table: reaction", "Table:
reaction_participant", "Table: reaction_enzyme".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import CurationState, ReactionParticipantRole

if TYPE_CHECKING:
    from app.models.compartment import Compartment
    from app.models.compound import Compound
    from app.models.enzyme_complex import EnzymeComplex
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.organism import Organism
    from app.models.protein import Protein

# Type creation is owned exclusively by migration 0004_reaction
# (create_type=False): these ORM-level Enum instances only describe the
# column type, they never issue CREATE TYPE themselves.
_CURATION_STATE = Enum(CurationState, name="curation_state", create_type=False)
_REACTION_PARTICIPANT_ROLE = Enum(
    ReactionParticipantRole, name="reaction_participant_role", create_type=False
)


class Reaction(Base):
    """A biochemical reaction.

    Stoichiometry is never encoded here as a free-text equation; it lives
    entirely in ``reaction_participant`` (``docs/02_database_schema.md``).
    ``status`` (free text) and ``curation_state`` (the ``CurationState`` enum)
    are two independent columns — the specification defines both with no
    stated relationship between them, so none is inferred or enforced here.
    """

    __tablename__ = "reaction"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    internal_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    reversible: Mapped[bool | None] = mapped_column(Boolean)

    reaction_type: Mapped[str | None] = mapped_column(String)
    ec_number: Mapped[str | None] = mapped_column(String, index=True)

    kegg_reaction_id: Mapped[str | None] = mapped_column(String, index=True)
    metacyc_reaction_id: Mapped[str | None] = mapped_column(String)
    rhea_id: Mapped[str | None] = mapped_column(String, index=True)

    balanced_mass: Mapped[bool | None] = mapped_column(Boolean)
    balanced_charge: Mapped[bool | None] = mapped_column(Boolean)

    status: Mapped[str | None] = mapped_column(String)
    curation_state: Mapped[CurationState] = mapped_column(
        _CURATION_STATE, nullable=False, default=CurationState.PROPOSED
    )

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    organism: Mapped[Organism | None] = orm_relationship(back_populates="reactions")
    participants: Mapped[list[ReactionParticipant]] = orm_relationship(back_populates="reaction")
    enzymes: Mapped[list[ReactionEnzyme]] = orm_relationship(back_populates="reaction")
    kinetic_measurements: Mapped[list[KineticMeasurement]] = orm_relationship(
        back_populates="reaction"
    )


class ReactionParticipant(Base):
    """A reactant, product, or modifier of a reaction.

    ``stoichiometry`` must always be positive (``docs/02_database_schema.md``:
    "Do not encode negative stoichiometric values"), enforced with a database
    CHECK constraint. No timestamp columns: the specification defines none
    for this table.
    """

    __tablename__ = "reaction_participant"
    __table_args__ = (
        # conv() marks the name as already final: the "ck" naming convention
        # in app/db/base.py includes a %(constraint_name)s token, so without
        # conv() SQLAlchemy would reprocess this explicit name through that
        # template and mangle it (observed: a doubled, hash-truncated name).
        CheckConstraint(
            "stoichiometry > 0",
            name=conv("ck_reaction_participant_stoichiometry_positive"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    reaction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), nullable=False
    )
    compound_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT"), nullable=False
    )
    compartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compartment.id", ondelete="RESTRICT")
    )

    role: Mapped[ReactionParticipantRole] = mapped_column(
        _REACTION_PARTICIPANT_ROLE, nullable=False
    )

    stoichiometry: Mapped[Decimal] = mapped_column(Numeric, nullable=False)

    reaction: Mapped[Reaction] = orm_relationship(back_populates="participants")
    compound: Mapped[Compound] = orm_relationship(back_populates="reaction_participants")
    compartment: Mapped[Compartment | None] = orm_relationship(
        back_populates="reaction_participants"
    )


class ReactionEnzyme(Base):
    """Associates a reaction with a catalytic protein or enzyme complex.

    The specification states that exactly one of ``protein_id``/``complex_id``
    "should normally" be populated — soft language, not "must" — so no CHECK
    constraint is added here, consistent with how other soft-worded rules in
    the specification (e.g. ``knowledge_gap.priority``) are left unconstrained.
    No timestamp columns: the specification defines none for this table.
    """

    __tablename__ = "reaction_enzyme"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    reaction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), nullable=False
    )
    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT")
    )
    complex_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="RESTRICT")
    )

    relationship: Mapped[str] = mapped_column(String, nullable=False)

    confidence_summary: Mapped[Decimal | None] = mapped_column(Numeric)

    notes: Mapped[str | None] = mapped_column(Text)

    reaction: Mapped[Reaction] = orm_relationship(back_populates="enzymes")
    protein: Mapped[Protein | None] = orm_relationship(back_populates="reaction_enzymes")
    complex: Mapped[EnzymeComplex | None] = orm_relationship(back_populates="reaction_enzymes")
