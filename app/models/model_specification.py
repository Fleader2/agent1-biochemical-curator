"""Model specification records: what the user wants a model to contain.

See ``docs/02_database_schema.md`` ("Table: model_specification" and its
eight child tables) and ``docs/30_project_subproject_model_foundation.md``.

A ``ModelSpecification`` is distinct from the executable model artifact
itself (``app.models.model.Model``): it describes constraints and scope,
never executable content. Each child table reuses an existing biological
entity FK wherever one exists (``Compound``, ``Reaction``, ``Gene``,
``Protein``, ``Compartment``, ``RegulatoryInteraction``, ``EnzymeState``)
rather than inventing a parallel representation, and always preserves a
caller's own ``raw_identifier`` text when it cannot yet be resolved to one
of those rows -- never silently discarded, the same precedent as
``NetworkNode.raw_token``. "User assumptions or constraints" that are not
a specific entity reference are represented via the existing
``ModelingAssumption`` table (``subject_type="model_specification"``), not
a new table here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    ModelSpecificationGeneProteinRole,
    ModelSpecificationPathwayRole,
    ModelSpecificationReactionRole,
    ModelSpecificationSpeciesRole,
)
from app.models.subproject import Subproject

if TYPE_CHECKING:
    pass

_MODEL_SPECIFICATION_SPECIES_ROLE = Enum(
    ModelSpecificationSpeciesRole, name="model_specification_species_role", create_type=False
)
_MODEL_SPECIFICATION_REACTION_ROLE = Enum(
    ModelSpecificationReactionRole, name="model_specification_reaction_role", create_type=False
)
_MODEL_SPECIFICATION_GENE_PROTEIN_ROLE = Enum(
    ModelSpecificationGeneProteinRole,
    name="model_specification_gene_protein_role",
    create_type=False,
)
_MODEL_SPECIFICATION_PATHWAY_ROLE = Enum(
    ModelSpecificationPathwayRole, name="model_specification_pathway_role", create_type=False
)


class ModelSpecification(Base):
    """What the user wants constructed for one ``Subproject``.

    One specification per subproject in this increment: ``subproject_id``
    is unique, so a subproject's own constraints are revised in place,
    never versioned as separate rows yet.
    """

    __tablename__ = "model_specification"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    subproject_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subproject.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
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

    subproject: Mapped[Subproject] = relationship(back_populates="specification")
    species: Mapped[list[ModelSpecificationSpecies]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    reactions: Mapped[list[ModelSpecificationReaction]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    gene_proteins: Mapped[list[ModelSpecificationGeneProtein]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    compartments: Mapped[list[ModelSpecificationCompartment]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    regulatory_interactions: Mapped[list[ModelSpecificationRegulatoryInteraction]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    enzyme_states: Mapped[list[ModelSpecificationEnzymeState]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )
    pathways: Mapped[list[ModelSpecificationPathway]] = relationship(
        back_populates="model_specification", cascade="all, delete-orphan", passive_deletes=True
    )


class ModelSpecificationSpecies(Base):
    """One compound/species reference within a ``ModelSpecification``.

    Covers model input/start species, model output/end species, boundary
    species, and seed metabolites in a single table, distinguished by
    ``role`` -- the same single-table-plus-role convention as
    ``ReactionParticipant.role``.
    """

    __tablename__ = "model_specification_species"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the naming-convention-derived name exceeds
        # PostgreSQL's 63-byte identifier limit (same precedent as
        # ``QuantitativeObservation.experimental_context_id``).
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_species_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[ModelSpecificationSpeciesRole] = mapped_column(
        _MODEL_SPECIFICATION_SPECIES_ROLE, nullable=False
    )

    compound_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT"), index=True
    )
    compartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compartment.id", ondelete="RESTRICT"), index=True
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="species")


class ModelSpecificationReaction(Base):
    """Covers seed reactions, required reactions, and excluded reactions in
    one table, distinguished by ``role``.
    """

    __tablename__ = "model_specification_reaction"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_reaction_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[ModelSpecificationReactionRole] = mapped_column(
        _MODEL_SPECIFICATION_REACTION_ROLE, nullable=False
    )

    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), index=True
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="reactions")


class ModelSpecificationGeneProtein(Base):
    """Covers seed genes, seed proteins/enzymes, and genes/proteins known to
    be important, in one table.

    At most one of ``gene_id``/``protein_id`` is ever set, enforced at the
    application level -- the same policy, and the same reason, as
    ``NetworkNode``'s own "at most one entity FK" rule.
    ``localization_compartment_id`` is the "subcellular localization
    constraint" named separately from "compartments to include"
    (``ModelSpecificationCompartment``): it constrains this specific gene/
    protein reference, not the model as a whole.
    """

    __tablename__ = "model_specification_gene_protein"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_gene_protein_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[ModelSpecificationGeneProteinRole] = mapped_column(
        _MODEL_SPECIFICATION_GENE_PROTEIN_ROLE, nullable=False
    )

    gene_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("gene.id", ondelete="RESTRICT"), index=True
    )
    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), index=True
    )
    localization_compartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the naming-convention-derived name exceeds
        # PostgreSQL's 63-byte identifier limit.
        ForeignKey(
            "compartment.id",
            ondelete="RESTRICT",
            name="fk_ms_gene_protein_localization_compartment_id_compartment",
        ),
        index=True,
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="gene_proteins")


class ModelSpecificationCompartment(Base):
    """Compartments the model as a whole should include."""

    __tablename__ = "model_specification_compartment"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_compartment_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    compartment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compartment.id", ondelete="RESTRICT"), index=True
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="compartments")


class ModelSpecificationRegulatoryInteraction(Base):
    """Regulatory interactions to include -- reuses the existing
    ``RegulatoryInteraction`` table directly.
    """

    __tablename__ = "model_specification_regulatory_interaction"
    __table_args__ = (
        # Explicit, shortened index names: the naming-convention-derived names
        # (which would otherwise back these two FK columns) exceed PostgreSQL's
        # 63-byte identifier limit.
        Index(
            "ix_ms_reg_interaction_model_specification_id",
            "model_specification_id",
        ),
        Index(
            "ix_ms_reg_interaction_regulatory_interaction_id",
            "regulatory_interaction_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_reg_interaction_model_specification_id_ms",
        ),
        nullable=False,
    )

    regulatory_interaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the naming-convention-derived name exceeds
        # PostgreSQL's 63-byte identifier limit.
        ForeignKey(
            "regulatory_interaction.id",
            ondelete="RESTRICT",
            name="fk_ms_reg_interaction_regulatory_interaction_id_reg_interaction",
        ),
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(
        back_populates="regulatory_interactions"
    )


class ModelSpecificationEnzymeState(Base):
    """PTMs/protein states to include -- reuses the existing ``EnzymeState``
    table (Agent 1.x Increment B) directly; no new PTM/protein-state entity
    is introduced.
    """

    __tablename__ = "model_specification_enzyme_state"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_enzyme_state_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    enzyme_state_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the naming-convention-derived name exceeds
        # PostgreSQL's 63-byte identifier limit.
        ForeignKey(
            "enzyme_state.id",
            ondelete="RESTRICT",
            name="fk_ms_enzyme_state_enzyme_state_id_enzyme_state",
        ),
        index=True,
    )

    raw_identifier: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="enzyme_states")


class ModelSpecificationPathway(Base):
    """Pathway scope: inclusion and exclusion.

    Agent 1 has no ``Pathway`` entity of its own, so KEGG's (or another
    source's) own pathway identifier is stored directly rather than a
    foreign key into a nonexistent table -- the same precedent as
    ``NetworkPathway``.
    """

    __tablename__ = "model_specification_pathway"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    model_specification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name -- see ModelSpecificationSpecies.model_specification_id.
        ForeignKey(
            "model_specification.id",
            ondelete="CASCADE",
            name="fk_ms_pathway_model_specification_id_ms",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[ModelSpecificationPathwayRole] = mapped_column(
        _MODEL_SPECIFICATION_PATHWAY_ROLE, nullable=False
    )

    external_pathway_id: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_specification: Mapped[ModelSpecification] = relationship(back_populates="pathways")


__all__ = [
    "ModelSpecification",
    "ModelSpecificationCompartment",
    "ModelSpecificationEnzymeState",
    "ModelSpecificationGeneProtein",
    "ModelSpecificationPathway",
    "ModelSpecificationReaction",
    "ModelSpecificationRegulatoryInteraction",
    "ModelSpecificationSpecies",
]
