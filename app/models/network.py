"""KEGG NETWORK topology records.

See ``docs/02_database_schema.md`` ("Table: network", "Table: network_node",
"Table: network_edge", "Table: network_perturbation", "Table: network_pathway",
"Table: network_disease", "Table: network_publication") and
``docs/28_kegg_network_support.md`` for the full design rationale.

A ``Network`` represents biological or regulatory *topology*: a higher-level
layer above the existing reaction-centric biochemical schema, never a
replacement for it. A ``NetworkEdge`` is never itself a biochemical reaction
unless it is explicitly linked to a normalized ``Reaction`` entity via
``NetworkNode.reaction_id``.

At most one of ``NetworkNode.gene_id``/``protein_id``/``complex_id``/
``compound_id``/``reaction_id`` is ever set on one row. This is enforced at
the application level (``app.persistence.network``), not by a database
``CHECK`` across the five nullable FK columns -- consistent with this
schema's own stated preference for application-level validation where a
rigid multi-column ``CHECK`` is not already an established pattern
(``docs/02_database_schema.md``, "Table: network_node", "Constraints").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    CurationState,
    NetworkEdgeInteractionType,
    NetworkNodeMappingStatus,
    NetworkNodeType,
    NetworkPerturbationType,
    NetworkType,
    SourceType,
)

if TYPE_CHECKING:
    from app.models.publication import Publication

_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)
_CURATION_STATE = Enum(CurationState, name="curation_state", create_type=False)
_NETWORK_TYPE = Enum(NetworkType, name="network_type", create_type=False)
_NETWORK_NODE_TYPE = Enum(NetworkNodeType, name="network_node_type", create_type=False)
_NETWORK_NODE_MAPPING_STATUS = Enum(
    NetworkNodeMappingStatus, name="network_node_mapping_status", create_type=False
)
_NETWORK_EDGE_INTERACTION_TYPE = Enum(
    NetworkEdgeInteractionType, name="network_edge_interaction_type", create_type=False
)
_NETWORK_PERTURBATION_TYPE = Enum(
    NetworkPerturbationType, name="network_perturbation_type", create_type=False
)


class Network(Base):
    """One imported KEGG NETWORK record's own identity and metadata.

    ``definition``/``expanded_definition`` preserve KEGG's own text exactly
    as received, and are never treated as the canonical internal
    representation of topology once a network has been successfully parsed
    -- explicit ``NetworkNode``/``NetworkEdge`` rows are canonical from that
    point on.
    """

    __tablename__ = "network"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_network_source_type_external_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    source_type: Mapped[SourceType] = mapped_column(_SOURCE_TYPE, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    name: Mapped[str] = mapped_column(String, nullable=False)

    network_type: Mapped[NetworkType] = mapped_column(_NETWORK_TYPE, nullable=False)

    definition: Mapped[str | None] = mapped_column(Text)
    expanded_definition: Mapped[str | None] = mapped_column(Text)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), index=True
    )

    curation_state: Mapped[CurationState] = mapped_column(
        _CURATION_STATE,
        nullable=False,
        default=CurationState.PROPOSED,
        server_default=CurationState.PROPOSED.value,
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

    nodes: Mapped[list[NetworkNode]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )
    edges: Mapped[list[NetworkEdge]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )
    perturbations: Mapped[list[NetworkPerturbation]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )
    pathways: Mapped[list[NetworkPathway]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )
    diseases: Mapped[list[NetworkDisease]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )
    publications: Mapped[list[NetworkPublication]] = relationship(
        back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )


class NetworkNode(Base):
    """One node in a network's topology, optionally linked to a normalized
    Agent 1 entity.

    A node is never fabricated into existence as a new ``Gene``/``Protein``/
    ``Compound``/``Reaction``/``EnzymeComplex`` row merely because a KEGG
    NETWORK node references it -- mapping only ever links to an entity that
    already exists.
    """

    __tablename__ = "network_node"
    __table_args__ = (
        Index(
            "uq_network_node_network_id_raw_token",
            "network_id",
            "raw_token",
            unique=True,
            postgresql_where=text("raw_token IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    external_identifier: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String)

    node_type: Mapped[NetworkNodeType] = mapped_column(_NETWORK_NODE_TYPE, nullable=False)

    gene_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("gene.id", ondelete="RESTRICT"), index=True
    )
    protein_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("protein.id", ondelete="RESTRICT"), index=True
    )
    complex_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("enzyme_complex.id", ondelete="RESTRICT"), index=True
    )
    compound_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("compound.id", ondelete="RESTRICT"), index=True
    )
    reaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reaction.id", ondelete="RESTRICT"), index=True
    )

    mapping_status: Mapped[NetworkNodeMappingStatus] = mapped_column(
        _NETWORK_NODE_MAPPING_STATUS, nullable=False
    )

    raw_token: Mapped[str | None] = mapped_column(String, index=True)

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

    network: Mapped[Network] = relationship(back_populates="nodes")
    outgoing_edges: Mapped[list[NetworkEdge]] = relationship(
        back_populates="source_node",
        foreign_keys="NetworkEdge.source_node_id",
    )
    incoming_edges: Mapped[list[NetworkEdge]] = relationship(
        back_populates="target_node",
        foreign_keys="NetworkEdge.target_node_id",
    )


class NetworkEdge(Base):
    """One directed relationship between two ``NetworkNode`` rows, derived
    deterministically from a network's own ``DEFINITION``/``EXPANDED``
    topology expression.

    No timestamp columns: this table has no independent lifecycle apart
    from the network it belongs to and the parser that produced it (same
    precedent as ``ReactionParticipant``/``ReactionEnzyme``).
    """

    __tablename__ = "network_edge"
    __table_args__ = (
        Index(
            "uq_network_edge_identity_with_operator",
            "network_id",
            "source_node_id",
            "target_node_id",
            "interaction_type",
            "raw_operator",
            unique=True,
            postgresql_where=text("raw_operator IS NOT NULL"),
        ),
        Index(
            "uq_network_edge_identity_without_operator",
            "network_id",
            "source_node_id",
            "target_node_id",
            "interaction_type",
            unique=True,
            postgresql_where=text("raw_operator IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network_node.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network_node.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    interaction_type: Mapped[NetworkEdgeInteractionType] = mapped_column(
        _NETWORK_EDGE_INTERACTION_TYPE, nullable=False, index=True
    )

    raw_operator: Mapped[str | None] = mapped_column(String)

    reversible: Mapped[bool | None] = mapped_column(Boolean)

    notes: Mapped[str | None] = mapped_column(Text)

    network: Mapped[Network] = relationship(back_populates="edges")
    source_node: Mapped[NetworkNode] = relationship(
        back_populates="outgoing_edges", foreign_keys=[source_node_id]
    )
    target_node: Mapped[NetworkNode] = relationship(
        back_populates="incoming_edges", foreign_keys=[target_node_id]
    )


class NetworkPerturbation(Base):
    """One perturbation (genetic variant, drug, viral factor, environmental
    factor, or other) a KEGG NETWORK record describes -- KEGG's own
    ``VARIANT``/``PERTURBANT`` field content.

    Distinct from the existing ``Perturbation`` model (an experimental
    condition applied when a ``QuantitativeObservation`` was measured in a
    wet-lab experiment): this table describes what a KEGG topology record
    itself represents, never an experimental measurement condition, and the
    two are never merged or cross-referenced.
    """

    __tablename__ = "network_perturbation"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    perturbation_type: Mapped[NetworkPerturbationType] = mapped_column(
        _NETWORK_PERTURBATION_TYPE, nullable=False
    )

    external_identifier: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)

    target_node_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("network_node.id", ondelete="RESTRICT"), index=True
    )

    effect: Mapped[str | None] = mapped_column(String)

    description: Mapped[str | None] = mapped_column(Text)

    raw_value: Mapped[str | None] = mapped_column(Text)

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

    network: Mapped[Network] = relationship(back_populates="perturbations")
    target_node: Mapped[NetworkNode | None] = relationship()


class NetworkPathway(Base):
    """Associates a ``Network`` with a KEGG pathway it references (the
    ``PATHWAY`` field).

    Agent 1 has no ``Pathway`` entity of its own, so this stores KEGG's own
    pathway identifier directly rather than a foreign key into a nonexistent
    table.
    """

    __tablename__ = "network_pathway"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "kegg_pathway_id", name="uq_network_pathway_network_id_kegg_pathway_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kegg_pathway_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    network: Mapped[Network] = relationship(back_populates="pathways")


class NetworkDisease(Base):
    """Associates a ``Network`` with a KEGG disease it references (the
    ``DISEASE`` field) -- same rationale as ``NetworkPathway``.
    """

    __tablename__ = "network_disease"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "kegg_disease_id", name="uq_network_disease_network_id_kegg_disease_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kegg_disease_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    network: Mapped[Network] = relationship(back_populates="diseases")


class NetworkPublication(Base):
    """Associates a ``Network`` with a publication it references (the
    ``REFERENCE`` field).

    Reuses the existing ``Publication`` table -- which, unlike pathway/
    disease, already exists as a real Agent 1 entity -- via
    ``publication_id`` when the reference's PMID resolves to an existing or
    newly-curated ``Publication`` row; ``raw_reference`` always preserves
    KEGG's own reference text verbatim regardless of whether resolution
    succeeds.
    """

    __tablename__ = "network_publication"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "raw_reference", name="uq_network_publication_network_id_raw_reference"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    network_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("network.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT"), index=True
    )
    raw_reference: Mapped[str] = mapped_column(Text, nullable=False)
    pmid: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    network: Mapped[Network] = relationship(back_populates="publications")
    publication: Mapped[Publication | None] = relationship()


__all__ = [
    "Network",
    "NetworkDisease",
    "NetworkEdge",
    "NetworkNode",
    "NetworkPathway",
    "NetworkPerturbation",
    "NetworkPublication",
]
