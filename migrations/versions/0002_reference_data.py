"""Group A reference-data schema.

Creates the six tables with no cross-entity dependencies on the rest of the
schema: ``organism``, ``compound``, ``compound_synonym``, ``compartment``,
``publication``, ``experimental_condition``
(``docs/02_database_schema.md``).

No native PostgreSQL enum type is created here: none of the seven enums in
``app/models/enums.py`` are used by any Group A column, so there is nothing
for this migration's ``downgrade()`` to remove on that front.

Seeds the 13 standard *Saccharomyces cerevisiae* compartments from
``docs/02_database_schema.md`` ("Initial Seed Data"), each with
``organism_id = NULL``: no organism seed row is specified anywhere in that
document.

Revision ID: 0002_reference_data
Revises: 0001_baseline
Create Date: 2026-09-01
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_reference_data"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Verbatim from docs/02_database_schema.md, "Initial Seed Data".
_STANDARD_COMPARTMENTS: tuple[str, ...] = (
    "cytosol",
    "mitochondrial matrix",
    "mitochondrial intermembrane space",
    "mitochondrial inner membrane",
    "mitochondrial outer membrane",
    "peroxisome",
    "endoplasmic reticulum",
    "Golgi",
    "lipid droplet",
    "nucleus",
    "vacuole",
    "plasma membrane",
    "extracellular",
)


def upgrade() -> None:
    op.create_table(
        "organism",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scientific_name", sa.String(), nullable=False),
        sa.Column("common_name", sa.String(), nullable=True),
        sa.Column("ncbi_taxonomy_id", sa.Integer(), nullable=True),
        sa.Column("kegg_code", sa.String(), nullable=True),
        sa.Column("biocyc_id", sa.String(), nullable=True),
        sa.Column("strain", sa.String(), nullable=True),
        sa.Column("strain_parent", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organism"),
    )
    op.create_index("ix_organism_ncbi_taxonomy_id", "organism", ["ncbi_taxonomy_id"])
    op.create_index(
        "uq_organism_scientific_name_strain",
        "organism",
        ["scientific_name", "strain"],
        unique=True,
        postgresql_where=sa.text("strain IS NOT NULL"),
    )

    op.create_table(
        "compound",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.String(), nullable=False),
        sa.Column("formula", sa.String(), nullable=True),
        sa.Column("charge", sa.Integer(), nullable=True),
        sa.Column("molecular_weight", sa.Numeric(), nullable=True),
        sa.Column("chebi_id", sa.String(), nullable=True),
        sa.Column("kegg_compound_id", sa.String(), nullable=True),
        sa.Column("pubchem_cid", sa.String(), nullable=True),
        sa.Column("metacyc_id", sa.String(), nullable=True),
        sa.Column("inchi", sa.Text(), nullable=True),
        sa.Column("inchikey", sa.String(), nullable=True),
        sa.Column("smiles", sa.Text(), nullable=True),
        sa.Column(
            "is_generic", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_compound"),
    )
    op.create_index("ix_compound_canonical_name", "compound", ["canonical_name"])
    op.create_index("ix_compound_chebi_id", "compound", ["chebi_id"])
    op.create_index("ix_compound_kegg_compound_id", "compound", ["kegg_compound_id"])
    op.create_index("ix_compound_inchikey", "compound", ["inchikey"])

    op.create_table(
        "compound_synonym",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compound_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("synonym", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_compound_synonym"),
        sa.ForeignKeyConstraint(
            ["compound_id"],
            ["compound.id"],
            name="fk_compound_synonym_compound_id_compound",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "compound_id", "synonym", name="uq_compound_synonym_compound_id_synonym"
        ),
    )

    op.create_table(
        "compartment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=True),
        sa.Column("ontology_id", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_compartment"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_compartment_organism_id_organism",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "publication",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pmid", sa.String(), nullable=True),
        sa.Column("pmcid", sa.String(), nullable=True),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("journal", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("authors_json", postgresql.JSONB(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("open_access", sa.Boolean(), nullable=True),
        sa.Column("full_text_available", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publication"),
    )
    op.create_index(
        "uq_publication_pmid",
        "publication",
        ["pmid"],
        unique=True,
        postgresql_where=sa.text("pmid IS NOT NULL"),
    )
    op.create_index(
        "uq_publication_pmcid",
        "publication",
        ["pmcid"],
        unique=True,
        postgresql_where=sa.text("pmcid IS NOT NULL"),
    )
    op.create_index(
        "uq_publication_doi",
        "publication",
        ["doi"],
        unique=True,
        postgresql_where=sa.text("doi IS NOT NULL"),
    )

    op.create_table(
        "experimental_condition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("medium", sa.String(), nullable=True),
        sa.Column("carbon_source", sa.String(), nullable=True),
        sa.Column("carbon_concentration", sa.Numeric(), nullable=True),
        sa.Column("carbon_concentration_unit", sa.String(), nullable=True),
        sa.Column("nitrogen_source", sa.String(), nullable=True),
        sa.Column("oxygen_status", sa.String(), nullable=True),
        sa.Column("temperature_c", sa.Numeric(), nullable=True),
        sa.Column("ph", sa.Numeric(), nullable=True),
        sa.Column("growth_phase", sa.String(), nullable=True),
        sa.Column("growth_rate", sa.Numeric(), nullable=True),
        sa.Column("growth_rate_unit", sa.String(), nullable=True),
        sa.Column("culture_mode", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experimental_condition"),
    )

    # Seed the 13 standard compartments. organism_id is NULL: no organism seed
    # row is specified in docs/02_database_schema.md.
    compartment_table = sa.table(
        "compartment",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("organism_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
    )
    op.bulk_insert(
        compartment_table,
        [{"id": uuid.uuid4(), "organism_id": None, "name": name} for name in _STANDARD_COMPARTMENTS],
    )


def downgrade() -> None:
    op.drop_table("experimental_condition")
    op.drop_table("publication")
    op.drop_table("compartment")
    op.drop_table("compound_synonym")
    op.drop_table("compound")
    op.drop_table("organism")
