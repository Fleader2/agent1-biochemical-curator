"""Group F regulation/assumption/knowledge-gap schema.

Creates ``regulatory_interaction``, ``modeling_assumption``, ``knowledge_gap``
(``docs/02_database_schema.md``).

``regulatory_interaction`` uses two enums: ``curation_state`` (reused from
migration 0004_reaction, not recreated here) and ``regulatory_effect``, which
is genuinely new — this is the first migration that needs it. It is created
once, with a stable explicit name, and referenced from its column with
``create_type=False`` so ``op.create_table`` does not attempt to recreate it.

Neither ``modeling_assumption`` nor ``knowledge_gap`` uses any enum:
``modeling_assumption.confidence`` is a plain ``NUMERIC`` with no declared
range, and ``knowledge_gap.status`` is a plain ``VARCHAR`` with no declared
vocabulary — the specification defines no enum for either.

No table in this migration has a required index beyond its primary key: none
of the three tables appear in the specification's "Required Indexes" section.

``downgrade()`` drops the three tables first, then drops only
``regulatory_effect`` — the one enum type this migration owns.

Revision ID: 0007_regulation_assumptions_gaps
Revises: 0006_kinetic_measurement
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.enums import CurationState, RegulatoryEffect

revision: str = "0007_regulation_assumptions_gaps"
down_revision: str | None = "0006_kinetic_measurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_regulatory_effect_enum = postgresql.ENUM(
    *[member.value for member in RegulatoryEffect], name="regulatory_effect"
)


def upgrade() -> None:
    bind = op.get_bind()
    _regulatory_effect_enum.create(bind, checkfirst=True)

    op.create_table(
        "regulatory_interaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("regulator_type", sa.String(), nullable=False),
        sa.Column("regulator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "effect",
            postgresql.ENUM(
                *[member.value for member in RegulatoryEffect],
                name="regulatory_effect",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("direct", sa.Boolean(), nullable=True),
        sa.Column("condition_dependent", sa.Boolean(), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_regulatory_interaction"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_regulatory_interaction_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claim.id"],
            name="fk_regulatory_interaction_claim_id_claim",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "modeling_assumption",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assumption", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "required_for_model", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("human_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_modeling_assumption"),
    )

    op.create_table(
        "knowledge_gap",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("missing_information", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(), nullable=True),
        sa.Column("model_impact", sa.Text(), nullable=True),
        sa.Column("suggested_experiment", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_gap"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_gap")
    op.drop_table("modeling_assumption")
    op.drop_table("regulatory_interaction")

    bind = op.get_bind()
    _regulatory_effect_enum.drop(bind, checkfirst=True)
