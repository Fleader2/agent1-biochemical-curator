"""Group C reaction schema.

Creates ``reaction``, ``reaction_participant``, ``reaction_enzyme``
(``docs/02_database_schema.md``).

This is the first migration that needs a native PostgreSQL enum type. Group C
uses exactly two of the seven enums in ``app/models/enums.py``:

- ``reaction.curation_state`` -> ``CurationState``
- ``reaction_participant.role`` -> ``ReactionParticipantRole``

No other Group C column uses an enum. Both types are created once here, with
stable explicit names (``curation_state``, ``reaction_participant_role``), and
referenced from their columns with ``create_type=False`` so ``op.create_table``
does not attempt to recreate them. ``downgrade()`` drops the three tables
first, then drops only these two enum types.

Revision ID: 0004_reaction
Revises: 0003_gene_protein_complex
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

from app.models.enums import CurationState, ReactionParticipantRole

revision: str = "0004_reaction"
down_revision: str | None = "0003_gene_protein_complex"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_curation_state_enum = postgresql.ENUM(
    *[member.value for member in CurationState], name="curation_state"
)
_reaction_participant_role_enum = postgresql.ENUM(
    *[member.value for member in ReactionParticipantRole], name="reaction_participant_role"
)


def upgrade() -> None:
    bind = op.get_bind()
    _curation_state_enum.create(bind, checkfirst=True)
    _reaction_participant_role_enum.create(bind, checkfirst=True)

    op.create_table(
        "reaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("internal_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversible", sa.Boolean(), nullable=True),
        sa.Column("reaction_type", sa.String(), nullable=True),
        sa.Column("ec_number", sa.String(), nullable=True),
        sa.Column("kegg_reaction_id", sa.String(), nullable=True),
        sa.Column("metacyc_reaction_id", sa.String(), nullable=True),
        sa.Column("rhea_id", sa.String(), nullable=True),
        sa.Column("balanced_mass", sa.Boolean(), nullable=True),
        sa.Column("balanced_charge", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column(
            "curation_state",
            postgresql.ENUM(
                *[member.value for member in CurationState],
                name="curation_state",
                create_type=False,
            ),
            nullable=False,
            server_default=CurationState.PROPOSED.value,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reaction"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_reaction_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("internal_id", name="uq_reaction_internal_id"),
    )
    op.create_index("ix_reaction_kegg_reaction_id", "reaction", ["kegg_reaction_id"])
    op.create_index("ix_reaction_rhea_id", "reaction", ["rhea_id"])
    op.create_index("ix_reaction_ec_number", "reaction", ["ec_number"])

    op.create_table(
        "reaction_participant",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compound_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compartment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "role",
            postgresql.ENUM(
                *[member.value for member in ReactionParticipantRole],
                name="reaction_participant_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("stoichiometry", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reaction_participant"),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_reaction_participant_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compound_id"],
            ["compound.id"],
            name="fk_reaction_participant_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compartment_id"],
            ["compartment.id"],
            name="fk_reaction_participant_compartment_id_compartment",
            ondelete="RESTRICT",
        ),
        # conv() marks the name as already final: this migration runs under
        # migrations/env.py's target_metadata = Base.metadata, whose "ck"
        # naming convention (app/db/base.py) includes a %(constraint_name)s
        # token. Without conv(), SQLAlchemy reprocesses this explicit name
        # through that template and mangles it into a doubled, hash-truncated
        # name — verified directly against this database before this fix.
        sa.CheckConstraint(
            "stoichiometry > 0",
            name=conv("ck_reaction_participant_stoichiometry_positive"),
        ),
    )

    op.create_table(
        "reaction_enzyme",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("complex_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("relationship", sa.String(), nullable=False),
        sa.Column("confidence_summary", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reaction_enzyme"),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_reaction_enzyme_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_reaction_enzyme_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complex_id"],
            ["enzyme_complex.id"],
            name="fk_reaction_enzyme_complex_id_enzyme_complex",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("reaction_enzyme")
    op.drop_table("reaction_participant")
    op.drop_table("reaction")

    bind = op.get_bind()
    _reaction_participant_role_enum.drop(bind, checkfirst=True)
    _curation_state_enum.drop(bind, checkfirst=True)
