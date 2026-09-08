"""Enzyme regulatory state records (Agent 1.x Increment B).

See ``docs/25_enzyme_regulatory_states_contract.md`` for the full
contract. Four tables, all new in migration
``0014_enzyme_regulatory_states``:

* ``EnzymeState`` -- a distinct biochemical/catalytic state of a
  ``Protein`` or ``EnzymeComplex`` (never a separate row for the
  underlying macromolecule itself: ``Protein``/``EnzymeComplex`` identity
  is unchanged and unconflated with state identity).
* ``EnzymeModification`` -- one covalent/post-translational modification
  belonging to one ``EnzymeState``.
* ``AllostericInteraction`` -- one allosteric ligand-binding relationship
  belonging to one ``EnzymeState``.
* ``EnzymeStateTransition`` -- one transition between two ``EnzymeState``
  rows.

**Identity, not measurement.** Unlike ``KineticMeasurement`` (where every
row is independently retained, never deduplicated by content), every table
here has a real scientific identity: the same curated state, modification,
allosteric interaction, or transition re-ingested from a second source
must resolve to the same row, never a duplicate. Each table therefore
carries its own ``identity_key`` -- a deterministic SHA-256 digest of its
own canonical, sorted-keys JSON encoding (never Python's built-in
``hash()``), computed by the corresponding ``app.normalization.enzyme_state``
function and enforced here by a real, non-nullable, globally unique
column. This mirrors ``knowledge_gap.identity_key``'s own precedent
exactly, extended to four tables instead of one.

**Provenance.** Every table also carries the same ``source``/``source_id``
(connector-record idempotency, mirroring ``kinetic_measurement``) plus
``publication_id``/``evidence_id`` (mirroring ``kinetic_measurement``'s own
identical pair) -- both optional, since a row may be curated from either a
connector or a literature claim, or (rarely) neither if provenance is
still being resolved. See ``docs/25_enzyme_regulatory_states_contract.md``
§20 for the full provenance policy.

**Deletion behavior.** ``EnzymeModification``/``AllostericInteraction``
use ``ON DELETE CASCADE`` on their ``enzyme_state_id`` foreign key -- they
have no independent scientific meaning apart from the state they describe,
mirroring ``enzyme_complex_member.complex_id``'s identical precedent.
``EnzymeStateTransition.from_state_id``/``.to_state_id`` use
``ON DELETE RESTRICT`` instead: a transition names two independent, peer
states, not an owning parent, so deleting either state must not silently
cascade-delete transition history.
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
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
    SourceType,
)

if TYPE_CHECKING:
    from app.models.claim import Evidence
    from app.models.compartment import Compartment
    from app.models.compound import Compound
    from app.models.enzyme_complex import EnzymeComplex
    from app.models.kinetic_measurement import KineticMeasurement
    from app.models.protein import Protein
    from app.models.publication import Publication
    from app.models.reaction import Reaction

_ENZYME_STATE_TYPE = Enum(EnzymeStateType, name="enzyme_state_type", create_type=False)
_MODIFICATION_TYPE = Enum(ModificationType, name="modification_type", create_type=False)
_ALLOSTERIC_EFFECT = Enum(AllostericEffect, name="allosteric_effect", create_type=False)
_ENZYME_STATE_TRANSITION_TYPE = Enum(
    EnzymeStateTransitionType, name="enzyme_state_transition_type", create_type=False
)
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class EnzymeState(Base):
    """A distinct biochemical/catalytic state of a ``Protein`` or ``EnzymeComplex``.

    Exactly one of ``protein_id``/``complex_id`` must be set -- a state
    always describes one specific macromolecule, never both and never
    neither (``ck_enzyme_state_exactly_one_target``, mirroring
    ``reaction_enzyme``'s identical XOR precedent). ``identity_key`` is a
    deterministic signature over the parent, ``state_type``, compartment,
    and the *complete* modification/allosteric-ligand set the state was
    created with (``app.normalization.enzyme_state.compute_enzyme_state_identity_key``)
    -- it is fixed at creation time and is never recomputed automatically
    when a later ``EnzymeModification``/``AllostericInteraction`` row is
    attached to an already-existing state (a documented policy, see
    ``docs/25_enzyme_regulatory_states_contract.md`` §16). ``state_label``
    is free-text, human-readable only -- never part of identity.
    """

    __tablename__ = "enzyme_state"
    __table_args__ = (
        CheckConstraint(
            "(protein_id IS NOT NULL AND complex_id IS NULL) "
            "OR (protein_id IS NULL AND complex_id IS NOT NULL)",
            name=conv("ck_enzyme_state_exactly_one_target"),
        ),
        Index(
            "uq_enzyme_state_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT")
    )
    complex_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="RESTRICT")
    )

    state_type: Mapped[EnzymeStateType] = mapped_column(_ENZYME_STATE_TYPE, nullable=False)
    state_label: Mapped[str | None] = mapped_column(String)

    compartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compartment.id", ondelete="RESTRICT")
    )

    active_state: Mapped[bool | None] = mapped_column()

    identity_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT")
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

    protein: Mapped[Protein | None] = relationship(back_populates="enzyme_states")
    complex: Mapped[EnzymeComplex | None] = relationship(back_populates="enzyme_states")
    compartment: Mapped[Compartment | None] = relationship()
    publication: Mapped[Publication | None] = relationship()
    evidence: Mapped[Evidence | None] = relationship()

    modifications: Mapped[list[EnzymeModification]] = relationship(
        back_populates="enzyme_state", cascade="all, delete-orphan", passive_deletes=True
    )
    allosteric_interactions: Mapped[list[AllostericInteraction]] = relationship(
        back_populates="enzyme_state", cascade="all, delete-orphan", passive_deletes=True
    )
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="enzyme_state"
    )


class EnzymeModification(Base):
    """One covalent/post-translational modification belonging to one ``EnzymeState``.

    ``identity_key`` covers ``(enzyme_state_id, modification_type, residue,
    residue_position, site_label, modifying_compound_id)`` -- two
    modifications differing only in ``residue_position`` (e.g. Ser15 vs.
    Ser42) or in ``modification_type`` never collapse into one row.
    """

    __tablename__ = "enzyme_modification"
    __table_args__ = (
        Index(
            "uq_enzyme_modification_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    enzyme_state_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_state.id", ondelete="CASCADE"), nullable=False
    )

    modification_type: Mapped[ModificationType] = mapped_column(
        _MODIFICATION_TYPE, nullable=False
    )
    residue: Mapped[str | None] = mapped_column(String)
    residue_position: Mapped[int | None] = mapped_column(Integer)
    site_label: Mapped[str | None] = mapped_column(String)

    modifying_compound_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT")
    )
    stoichiometry: Mapped[Decimal | None] = mapped_column(Numeric)

    identity_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT")
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

    enzyme_state: Mapped[EnzymeState] = relationship(back_populates="modifications")
    modifying_compound: Mapped[Compound | None] = relationship()
    publication: Mapped[Publication | None] = relationship()
    evidence: Mapped[Evidence | None] = relationship()


class AllostericInteraction(Base):
    """One allosteric ligand-binding relationship belonging to one ``EnzymeState``.

    ``ligand_compound_id`` is required (never nullable): a ligand that
    cannot be resolved to a canonical ``Compound`` cannot be represented
    here at all (Increment B instructions, Step 9) -- never identified
    solely by free-text name. ``effect`` is the curated *qualitative*
    regulatory relationship only; the *quantitative* kinetic consequence
    (if any) is a separate, state-specific ``KineticMeasurement`` row
    (Increment B instructions, Step 11) -- this table never stores a
    numeric kinetic value.
    """

    __tablename__ = "allosteric_interaction"
    __table_args__ = (
        Index(
            "uq_allosteric_interaction_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    enzyme_state_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_state.id", ondelete="CASCADE"), nullable=False
    )
    ligand_compound_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT"), nullable=False
    )

    effect: Mapped[AllostericEffect] = mapped_column(_ALLOSTERIC_EFFECT, nullable=False)
    site_label: Mapped[str | None] = mapped_column(String)
    mechanism: Mapped[str | None] = mapped_column(Text)

    identity_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT")
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

    enzyme_state: Mapped[EnzymeState] = relationship(back_populates="allosteric_interactions")
    ligand_compound: Mapped[Compound] = relationship()
    publication: Mapped[Publication | None] = relationship()
    evidence: Mapped[Evidence | None] = relationship()


class EnzymeStateTransition(Base):
    """One transition between two ``EnzymeState`` rows.

    ``from_state_id``/``to_state_id`` must differ
    (``ck_enzyme_state_transition_distinct_states``) -- a transition to
    itself is not meaningful. ``reaction_id`` links to a curated
    ``Reaction`` only when the source knowledge already resolves the
    transition through the existing reaction-curation pipeline (Increment
    B instructions, Step 13) -- this table never causes a ``Reaction`` row
    to be created, and no ATP/ADP/Pi (or any other) participant is ever
    invented on its behalf. The catalyzing enzyme of a modification
    transition (e.g. a specific kinase), when known, is represented via
    that linked reaction's own ``ReactionEnzyme`` rows -- no separate
    "modifying protein" column exists here, since one would duplicate
    that already-established path.
    """

    __tablename__ = "enzyme_state_transition"
    __table_args__ = (
        CheckConstraint(
            "from_state_id <> to_state_id", name=conv("ck_enzyme_state_transition_distinct_states")
        ),
        Index(
            "uq_enzyme_state_transition_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    from_state_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_state.id", ondelete="RESTRICT"), nullable=False
    )
    to_state_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_state.id", ondelete="RESTRICT"), nullable=False
    )

    transition_type: Mapped[EnzymeStateTransitionType] = mapped_column(
        _ENZYME_STATE_TRANSITION_TYPE, nullable=False
    )
    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT")
    )

    identity_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT")
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

    from_state: Mapped[EnzymeState] = relationship(foreign_keys=[from_state_id])
    to_state: Mapped[EnzymeState] = relationship(foreign_keys=[to_state_id])
    reaction: Mapped[Reaction | None] = relationship()
    publication: Mapped[Publication | None] = relationship()
    evidence: Mapped[Evidence | None] = relationship()
