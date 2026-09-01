"""Group B gene/protein/enzyme-complex schema.

Creates ``gene``, ``protein``, ``enzyme_complex``, ``enzyme_complex_member``
(``docs/02_database_schema.md``). All four reference ``organism`` (directly or
transitively); ``protein`` optionally references ``gene``;
``enzyme_complex_member`` associates ``protein`` with ``enzyme_complex``.

No native PostgreSQL enum type is created here: none of the seven enums in
``app/models/enums.py`` are used by any Group B column.

Revision ID: 0003_gene_protein_complex
Revises: 0002_reference_data
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_gene_protein_complex"
down_revision: str | None = "0002_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gene",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("systematic_name", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sgd_id", sa.String(), nullable=True),
        sa.Column("ncbi_gene_id", sa.String(), nullable=True),
        sa.Column("uniprot_id", sa.String(), nullable=True),
        sa.Column("kegg_gene_id", sa.String(), nullable=True),
        sa.Column("chromosome", sa.String(), nullable=True),
        sa.Column("aliases_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gene"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_gene_organism_id_organism",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_gene_symbol", "gene", ["symbol"])
    op.create_index("ix_gene_systematic_name", "gene", ["systematic_name"])
    op.create_index(
        "uq_gene_sgd_id", "gene", ["sgd_id"], unique=True, postgresql_where=sa.text("sgd_id IS NOT NULL")
    )
    op.create_index(
        "uq_gene_ncbi_gene_id",
        "gene",
        ["ncbi_gene_id"],
        unique=True,
        postgresql_where=sa.text("ncbi_gene_id IS NOT NULL"),
    )
    op.create_index(
        "uq_gene_uniprot_id",
        "gene",
        ["uniprot_id"],
        unique=True,
        postgresql_where=sa.text("uniprot_id IS NOT NULL"),
    )
    op.create_index(
        "uq_gene_kegg_gene_id",
        "gene",
        ["kegg_gene_id"],
        unique=True,
        postgresql_where=sa.text("kegg_gene_id IS NOT NULL"),
    )

    op.create_table(
        "protein",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("uniprot_id", sa.String(), nullable=True),
        sa.Column("ec_number", sa.String(), nullable=True),
        sa.Column("subunit_state", sa.String(), nullable=True),
        sa.Column("localization_consensus", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_protein"),
        sa.ForeignKeyConstraint(
            ["gene_id"], ["gene.id"], name="fk_protein_gene_id_gene", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_protein_organism_id_organism",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_protein_uniprot_id", "protein", ["uniprot_id"])
    op.create_index("ix_protein_ec_number", "protein", ["ec_number"])

    op.create_table(
        "enzyme_complex",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stoichiometry_json", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enzyme_complex"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_enzyme_complex_organism_id_organism",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "enzyme_complex_member",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("complex_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stoichiometry", sa.Numeric(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_enzyme_complex_member"),
        sa.ForeignKeyConstraint(
            ["complex_id"],
            ["enzyme_complex.id"],
            name="fk_enzyme_complex_member_complex_id_enzyme_complex",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_enzyme_complex_member_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "complex_id", "protein_id", name="uq_enzyme_complex_member_complex_id_protein_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("enzyme_complex_member")
    op.drop_table("enzyme_complex")
    op.drop_table("protein")
    op.drop_table("gene")
