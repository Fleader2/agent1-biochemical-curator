"""Experimental context and quantitative observation framework (Agent 1.x
Increment "Experimental Context and Quantitative Observation Framework").

Creates four new tables (``experimental_context``, ``perturbation``,
``quantitative_observation``, ``quantitative_observation_dependency``) and
three new native enum types (``experimental_context_classification``,
``quantitative_evidence_class``, ``time_reference_basis``). No existing
table or column is altered.

See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale. This migration adds schema only -- no
connector, no ingestion, no Agent 2/4 logic.

Revision ID: 0017_exp_context_qobs
Revises: 0016_gotenzymes_source
Create Date: 2026-09-29

**Revision id kept short deliberately**: ``alembic_version.version_num`` is
``VARCHAR(32)`` (confirmed directly -- ``StringDataRightTruncation`` on the
first attempt with the fully descriptive id) -- every prior revision id in
this repository is likewise <= 32 characters; this file's own descriptive
name is carried in the filename and this docstring instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

from app.models.enums import (
    ExperimentalContextClassification,
    QuantitativeEvidenceClass,
    TimeReferenceBasis,
)

revision: str = "0017_exp_context_qobs"
down_revision: str | None = "0016_gotenzymes_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_experimental_context_classification_enum = postgresql.ENUM(
    *[member.value for member in ExperimentalContextClassification],
    name="experimental_context_classification",
)
_quantitative_evidence_class_enum = postgresql.ENUM(
    *[member.value for member in QuantitativeEvidenceClass], name="quantitative_evidence_class"
)
_time_reference_basis_enum = postgresql.ENUM(
    *[member.value for member in TimeReferenceBasis], name="time_reference_basis"
)


def upgrade() -> None:
    bind = op.get_bind()
    _experimental_context_classification_enum.create(bind, checkfirst=True)
    _quantitative_evidence_class_enum.create(bind, checkfirst=True)
    _time_reference_basis_enum.create(bind, checkfirst=True)

    op.create_table(
        "experimental_context",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strain", sa.String(), nullable=True),
        sa.Column("genotype", sa.Text(), nullable=True),
        sa.Column("medium", sa.String(), nullable=True),
        sa.Column("carbon_source", sa.String(), nullable=True),
        sa.Column("temperature_c", sa.Numeric(), nullable=True),
        sa.Column("ph", sa.Numeric(), nullable=True),
        sa.Column("growth_phase", sa.String(), nullable=True),
        sa.Column("growth_condition", sa.String(), nullable=True),
        sa.Column(
            "classification",
            postgresql.ENUM(
                *[member.value for member in ExperimentalContextClassification],
                name="experimental_context_classification",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("source", postgresql.ENUM(name="source_type", create_type=False), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experimental_context"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_experimental_context_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_experimental_context_publication_id_publication",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_experimental_context_organism_id", "experimental_context", ["organism_id"]
    )

    op.create_table(
        "perturbation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("perturbation_type", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("magnitude", sa.Numeric(), nullable=True),
        sa.Column("magnitude_unit", sa.String(), nullable=True),
        sa.Column("start_time_value", sa.Numeric(), nullable=True),
        sa.Column("start_time_unit", sa.String(), nullable=True),
        sa.Column("start_time_canonical_s", sa.Numeric(), nullable=True),
        sa.Column("duration_value", sa.Numeric(), nullable=True),
        sa.Column("duration_unit", sa.String(), nullable=True),
        sa.Column("duration_canonical_s", sa.Numeric(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", postgresql.ENUM(name="source_type", create_type=False), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_perturbation"),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_perturbation_publication_id_publication",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_perturbation_perturbation_type", "perturbation", ["perturbation_type"])

    op.create_table(
        "quantitative_observation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_type", sa.String(), nullable=False),
        sa.Column("reported_observation_type", sa.String(), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("normalized_value", sa.Numeric(), nullable=True),
        sa.Column("normalized_unit", sa.String(), nullable=True),
        sa.Column("uncertainty", sa.Numeric(), nullable=True),
        sa.Column("lower_bound", sa.Numeric(), nullable=True),
        sa.Column("upper_bound", sa.Numeric(), nullable=True),
        sa.Column("measurement_method", sa.String(), nullable=True),
        sa.Column(
            "evidence_class",
            postgresql.ENUM(
                *[member.value for member in QuantitativeEvidenceClass],
                name="quantitative_evidence_class",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "time_reference_basis",
            postgresql.ENUM(
                *[member.value for member in TimeReferenceBasis],
                name="time_reference_basis",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("time_value", sa.Numeric(), nullable=True),
        sa.Column("time_unit", sa.String(), nullable=True),
        sa.Column("time_canonical_s", sa.Numeric(), nullable=True),
        sa.Column("experimental_context_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("perturbation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("biological_replicate_id", sa.String(), nullable=True),
        sa.Column("technical_replicate_id", sa.String(), nullable=True),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compound_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unresolved_identity_kind", sa.String(), nullable=True),
        sa.Column("unresolved_identity_text", sa.Text(), nullable=True),
        sa.Column("source", postgresql.ENUM(name="source_type", create_type=False), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_id", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quantitative_observation"),
        sa.ForeignKeyConstraint(
            ["experimental_context_id"],
            ["experimental_context.id"],
            name="fk_qobs_experimental_context_id_experimental_context",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["perturbation_id"],
            ["perturbation.id"],
            name="fk_quantitative_observation_perturbation_id_perturbation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_quantitative_observation_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compound_id"],
            ["compound.id"],
            name="fk_quantitative_observation_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_quantitative_observation_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_quantitative_observation_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_quantitative_observation_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "time_reference_basis IS NULL OR time_canonical_s IS NOT NULL "
            "OR time_value IS NOT NULL",
            name=conv("ck_quantitative_observation_time_reference_requires_time"),
        ),
    )
    op.create_index(
        "ix_quantitative_observation_observation_type",
        "quantitative_observation",
        ["observation_type"],
    )
    op.create_index(
        "ix_quantitative_observation_experimental_context_id",
        "quantitative_observation",
        ["experimental_context_id"],
    )
    op.create_index(
        "ix_quantitative_observation_perturbation_id",
        "quantitative_observation",
        ["perturbation_id"],
    )
    op.create_index(
        "ix_quantitative_observation_protein_id", "quantitative_observation", ["protein_id"]
    )
    op.create_index(
        "ix_quantitative_observation_compound_id", "quantitative_observation", ["compound_id"]
    )
    op.create_index(
        "ix_quantitative_observation_reaction_id", "quantitative_observation", ["reaction_id"]
    )
    op.create_index(
        "ix_quantitative_observation_organism_id", "quantitative_observation", ["organism_id"]
    )
    op.create_index(
        "uq_quantitative_observation_source_source_id",
        "quantitative_observation",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )

    op.create_table(
        "quantitative_observation_dependency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("derived_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("assumption_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quantitative_observation_dependency"),
        sa.ForeignKeyConstraint(
            ["derived_observation_id"],
            ["quantitative_observation.id"],
            name="fk_qo_dependency_derived_observation_id_qo",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["input_observation_id"],
            ["quantitative_observation.id"],
            name="fk_qo_dependency_input_observation_id_qo",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "derived_observation_id",
            "input_observation_id",
            name="uq_quantitative_observation_dependency_derived_input",
        ),
        sa.CheckConstraint(
            "derived_observation_id != input_observation_id",
            name=conv("ck_quantitative_observation_dependency_not_self"),
        ),
    )
    op.create_index(
        "ix_quantitative_observation_dependency_derived_observation_id",
        "quantitative_observation_dependency",
        ["derived_observation_id"],
    )
    op.create_index(
        "ix_quantitative_observation_dependency_input_observation_id",
        "quantitative_observation_dependency",
        ["input_observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quantitative_observation_dependency_input_observation_id",
        table_name="quantitative_observation_dependency",
    )
    op.drop_index(
        "ix_quantitative_observation_dependency_derived_observation_id",
        table_name="quantitative_observation_dependency",
    )
    op.drop_table("quantitative_observation_dependency")

    op.drop_index(
        "uq_quantitative_observation_source_source_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_organism_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_reaction_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_compound_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_protein_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_perturbation_id", table_name="quantitative_observation"
    )
    op.drop_index(
        "ix_quantitative_observation_experimental_context_id",
        table_name="quantitative_observation",
    )
    op.drop_index(
        "ix_quantitative_observation_observation_type", table_name="quantitative_observation"
    )
    op.drop_table("quantitative_observation")

    op.drop_index("ix_perturbation_perturbation_type", table_name="perturbation")
    op.drop_table("perturbation")

    op.drop_index("ix_experimental_context_organism_id", table_name="experimental_context")
    op.drop_table("experimental_context")

    bind = op.get_bind()
    _time_reference_basis_enum.drop(bind, checkfirst=True)
    _quantitative_evidence_class_enum.drop(bind, checkfirst=True)
    _experimental_context_classification_enum.drop(bind, checkfirst=True)
