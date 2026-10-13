"""KEGG NETWORK support: network topology tables (KEGG NETWORK support increment).

Creates seven new tables (``network``, ``network_node``, ``network_edge``,
``network_perturbation``, ``network_pathway``, ``network_disease``,
``network_publication``) and five new native enum types (``network_type``,
``network_node_type``, ``network_node_mapping_status``,
``network_edge_interaction_type``, ``network_perturbation_type``). No
existing table or column is altered.

See ``docs/02_database_schema.md`` ("Table: network" and the six tables that
follow it) and ``docs/28_kegg_network_support.md`` for the full design
rationale. This migration adds schema only -- no connector, no ingestion,
no normalization/persistence logic.

Revision ID: 0019_kegg_network
Revises: 0018_kinetic_attribution
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.enums import (
    NetworkEdgeInteractionType,
    NetworkNodeMappingStatus,
    NetworkNodeType,
    NetworkPerturbationType,
    NetworkType,
)

revision: str = "0019_kegg_network"
down_revision: str | None = "0018_kinetic_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_network_type_enum = postgresql.ENUM(*[member.value for member in NetworkType], name="network_type")
_network_node_type_enum = postgresql.ENUM(
    *[member.value for member in NetworkNodeType], name="network_node_type"
)
_network_node_mapping_status_enum = postgresql.ENUM(
    *[member.value for member in NetworkNodeMappingStatus], name="network_node_mapping_status"
)
_network_edge_interaction_type_enum = postgresql.ENUM(
    *[member.value for member in NetworkEdgeInteractionType],
    name="network_edge_interaction_type",
)
_network_perturbation_type_enum = postgresql.ENUM(
    *[member.value for member in NetworkPerturbationType], name="network_perturbation_type"
)


def upgrade() -> None:
    bind = op.get_bind()
    _network_type_enum.create(bind, checkfirst=True)
    _network_node_type_enum.create(bind, checkfirst=True)
    _network_node_mapping_status_enum.create(bind, checkfirst=True)
    _network_edge_interaction_type_enum.create(bind, checkfirst=True)
    _network_perturbation_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "network",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_type", postgresql.ENUM(name="source_type", create_type=False), nullable=False
        ),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "network_type",
            postgresql.ENUM(
                *[m.value for m in NetworkType], name="network_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("expanded_definition", sa.Text(), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "curation_state",
            postgresql.ENUM(name="curation_state", create_type=False),
            nullable=False,
            server_default="PROPOSED",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_network_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_type", "external_id", name="uq_network_source_type_external_id"
        ),
    )
    op.create_index("ix_network_external_id", "network", ["external_id"])
    op.create_index("ix_network_organism_id", "network", ["organism_id"])

    op.create_table(
        "network_node",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identifier", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "node_type",
            postgresql.ENUM(
                *[m.value for m in NetworkNodeType], name="network_node_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("gene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("complex_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compound_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "mapping_status",
            postgresql.ENUM(
                *[m.value for m in NetworkNodeMappingStatus],
                name="network_node_mapping_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("raw_token", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network_node"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_node_network_id_network",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gene_id"], ["gene.id"], name="fk_network_node_gene_id_gene", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_network_node_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complex_id"],
            ["enzyme_complex.id"],
            name="fk_network_node_complex_id_enzyme_complex",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compound_id"],
            ["compound.id"],
            name="fk_network_node_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_network_node_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_network_node_network_id", "network_node", ["network_id"])
    op.create_index("ix_network_node_gene_id", "network_node", ["gene_id"])
    op.create_index("ix_network_node_protein_id", "network_node", ["protein_id"])
    op.create_index("ix_network_node_complex_id", "network_node", ["complex_id"])
    op.create_index("ix_network_node_compound_id", "network_node", ["compound_id"])
    op.create_index("ix_network_node_reaction_id", "network_node", ["reaction_id"])
    op.create_index("ix_network_node_raw_token", "network_node", ["raw_token"])
    op.create_index(
        "uq_network_node_network_id_raw_token",
        "network_node",
        ["network_id", "raw_token"],
        unique=True,
        postgresql_where=sa.text("raw_token IS NOT NULL"),
    )

    op.create_table(
        "network_edge",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "interaction_type",
            postgresql.ENUM(
                *[m.value for m in NetworkEdgeInteractionType],
                name="network_edge_interaction_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("raw_operator", sa.String(), nullable=True),
        sa.Column("reversible", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_network_edge"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_edge_network_id_network",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id"],
            ["network_node.id"],
            name="fk_network_edge_source_node_id_network_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"],
            ["network_node.id"],
            name="fk_network_edge_target_node_id_network_node",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_network_edge_network_id", "network_edge", ["network_id"])
    op.create_index("ix_network_edge_source_node_id", "network_edge", ["source_node_id"])
    op.create_index("ix_network_edge_target_node_id", "network_edge", ["target_node_id"])
    op.create_index("ix_network_edge_interaction_type", "network_edge", ["interaction_type"])
    op.create_index(
        "uq_network_edge_identity_with_operator",
        "network_edge",
        ["network_id", "source_node_id", "target_node_id", "interaction_type", "raw_operator"],
        unique=True,
        postgresql_where=sa.text("raw_operator IS NOT NULL"),
    )
    op.create_index(
        "uq_network_edge_identity_without_operator",
        "network_edge",
        ["network_id", "source_node_id", "target_node_id", "interaction_type"],
        unique=True,
        postgresql_where=sa.text("raw_operator IS NULL"),
    )

    op.create_table(
        "network_perturbation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "perturbation_type",
            postgresql.ENUM(
                *[m.value for m in NetworkPerturbationType],
                name="network_perturbation_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_identifier", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effect", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network_perturbation"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_perturbation_network_id_network",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"],
            ["network_node.id"],
            name="fk_network_perturbation_target_node_id_network_node",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_network_perturbation_network_id", "network_perturbation", ["network_id"])
    op.create_index(
        "ix_network_perturbation_target_node_id", "network_perturbation", ["target_node_id"]
    )

    op.create_table(
        "network_pathway",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kegg_pathway_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network_pathway"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_pathway_network_id_network",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "network_id", "kegg_pathway_id", name="uq_network_pathway_network_id_kegg_pathway_id"
        ),
    )
    op.create_index("ix_network_pathway_network_id", "network_pathway", ["network_id"])

    op.create_table(
        "network_disease",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kegg_disease_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network_disease"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_disease_network_id_network",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "network_id", "kegg_disease_id", name="uq_network_disease_network_id_kegg_disease_id"
        ),
    )
    op.create_index("ix_network_disease_network_id", "network_disease", ["network_id"])

    op.create_table(
        "network_publication",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_reference", sa.Text(), nullable=False),
        sa.Column("pmid", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_network_publication"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["network.id"],
            name="fk_network_publication_network_id_network",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_network_publication_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "network_id",
            "raw_reference",
            name="uq_network_publication_network_id_raw_reference",
        ),
    )
    op.create_index("ix_network_publication_network_id", "network_publication", ["network_id"])
    op.create_index(
        "ix_network_publication_publication_id", "network_publication", ["publication_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_network_publication_publication_id", table_name="network_publication")
    op.drop_index("ix_network_publication_network_id", table_name="network_publication")
    op.drop_table("network_publication")

    op.drop_index("ix_network_disease_network_id", table_name="network_disease")
    op.drop_table("network_disease")

    op.drop_index("ix_network_pathway_network_id", table_name="network_pathway")
    op.drop_table("network_pathway")

    op.drop_index("ix_network_perturbation_target_node_id", table_name="network_perturbation")
    op.drop_index("ix_network_perturbation_network_id", table_name="network_perturbation")
    op.drop_table("network_perturbation")

    op.drop_index("uq_network_edge_identity_without_operator", table_name="network_edge")
    op.drop_index("uq_network_edge_identity_with_operator", table_name="network_edge")
    op.drop_index("ix_network_edge_interaction_type", table_name="network_edge")
    op.drop_index("ix_network_edge_target_node_id", table_name="network_edge")
    op.drop_index("ix_network_edge_source_node_id", table_name="network_edge")
    op.drop_index("ix_network_edge_network_id", table_name="network_edge")
    op.drop_table("network_edge")

    op.drop_index("uq_network_node_network_id_raw_token", table_name="network_node")
    op.drop_index("ix_network_node_raw_token", table_name="network_node")
    op.drop_index("ix_network_node_reaction_id", table_name="network_node")
    op.drop_index("ix_network_node_compound_id", table_name="network_node")
    op.drop_index("ix_network_node_complex_id", table_name="network_node")
    op.drop_index("ix_network_node_protein_id", table_name="network_node")
    op.drop_index("ix_network_node_gene_id", table_name="network_node")
    op.drop_index("ix_network_node_network_id", table_name="network_node")
    op.drop_table("network_node")

    op.drop_index("ix_network_organism_id", table_name="network")
    op.drop_index("ix_network_external_id", table_name="network")
    op.drop_table("network")

    bind = op.get_bind()
    _network_perturbation_type_enum.drop(bind, checkfirst=True)
    _network_edge_interaction_type_enum.drop(bind, checkfirst=True)
    _network_node_mapping_status_enum.drop(bind, checkfirst=True)
    _network_node_type_enum.drop(bind, checkfirst=True)
    _network_type_enum.drop(bind, checkfirst=True)
