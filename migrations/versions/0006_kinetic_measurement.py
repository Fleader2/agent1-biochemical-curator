"""Group E kinetic_measurement schema.

Creates ``kinetic_measurement`` (``docs/02_database_schema.md``).

This table uses exactly one enum, and it is not a new one: ``confidence_class``
(``ConfidenceClass``), already created by migration 0005_claim_evidence. This
migration reuses that type with ``create_type=False`` and does not create,
drop, or otherwise touch it, nor any of the other five enum types owned by
earlier migrations (``curation_state``, ``reaction_participant_role``,
``claim_status``, ``evidence_type``, ``source_type``). No new enum type is
introduced here: ``parameter_type`` remains a plain, unconstrained ``VARCHAR``
per the specification ("Do not restrict the database so tightly that future
parameter types cannot be added").

``downgrade()`` drops only the ``kinetic_measurement`` table. There is no
enum type for it to drop.

Revision ID: 0006_kinetic_measurement
Revises: 0005_claim_evidence
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

from app.models.enums import ConfidenceClass

revision: str = "0006_kinetic_measurement"
down_revision: str | None = "0005_claim_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kinetic_measurement",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("complex_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parameter_type", sa.String(), nullable=False),
        sa.Column("parameter_value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("original_value", sa.Numeric(), nullable=True),
        sa.Column("original_unit", sa.String(), nullable=True),
        sa.Column("normalized_value", sa.Numeric(), nullable=True),
        sa.Column("normalized_unit", sa.String(), nullable=True),
        sa.Column("substrate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strain", sa.String(), nullable=True),
        sa.Column("temperature_c", sa.Numeric(), nullable=True),
        sa.Column("ph", sa.Numeric(), nullable=True),
        sa.Column("ionic_strength", sa.Numeric(), nullable=True),
        sa.Column("ionic_strength_unit", sa.String(), nullable=True),
        sa.Column("buffer", sa.Text(), nullable=True),
        sa.Column("enzyme_concentration", sa.Numeric(), nullable=True),
        sa.Column("enzyme_concentration_unit", sa.String(), nullable=True),
        sa.Column("substrate_concentrations_json", postgresql.JSONB(), nullable=True),
        sa.Column("protein_form", sa.String(), nullable=True),
        sa.Column("purification_state", sa.String(), nullable=True),
        sa.Column("assay_type", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(), nullable=True),
        sa.Column(
            "confidence_class",
            postgresql.ENUM(
                *[member.value for member in ConfidenceClass],
                name="confidence_class",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("model_applicability_score", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kinetic_measurement"),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_kinetic_measurement_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_kinetic_measurement_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complex_id"],
            ["enzyme_complex.id"],
            name="fk_kinetic_measurement_complex_id_enzyme_complex",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["substrate_id"],
            ["compound.id"],
            name="fk_kinetic_measurement_substrate_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_kinetic_measurement_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_kinetic_measurement_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_kinetic_measurement_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
        # conv() marks the name as already final: this migration runs under
        # migrations/env.py's target_metadata = Base.metadata, whose "ck"
        # naming convention (app/db/base.py) includes a %(constraint_name)s
        # token, which would otherwise reprocess and mangle this explicit
        # name (the same issue diagnosed and fixed in 0004_reaction.py and
        # 0005_claim_evidence.py).
        sa.CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_kinetic_measurement_confidence_score_range"),
        ),
    )
    op.create_index(
        "ix_kinetic_measurement_parameter_type", "kinetic_measurement", ["parameter_type"]
    )
    op.create_index(
        "ix_kinetic_measurement_reaction_id", "kinetic_measurement", ["reaction_id"]
    )
    op.create_index(
        "ix_kinetic_measurement_protein_id", "kinetic_measurement", ["protein_id"]
    )


def downgrade() -> None:
    op.drop_table("kinetic_measurement")
