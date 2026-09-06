"""Experiment recommendation persistence and lifecycle (Increment 24).

Creates two new tables:

* ``experiment_recommendation`` -- one persisted, deterministic
  ``ExperimentRecommendation`` (Increment 23) output per row, linked to its
  originating ``knowledge_gap`` row via a required, ``ON DELETE RESTRICT``
  foreign key. ``recommendation_identity`` (Increment 23's own
  ``compute_recommendation_identity``, ``"experiment-rec-v1:<sha256>"``)
  carries a database-level unique constraint, making repeated persistence
  of the same deterministic recommendation idempotent at the database
  layer, not merely at the application layer.
* ``experiment_recommendation_event`` -- an append-only lifecycle audit
  trail, structurally independent of ``review_event`` (see
  ``app.models.experiment_recommendation.ExperimentRecommendationEvent``'s
  own docstring for why reusing it was considered and rejected: its
  ``curation_state`` enum has no room for ``ACCEPTED``/``DEFERRED``/
  ``SUPERSEDED``).

See ``docs/20_experiment_recommendation_persistence_contract.md`` for the
full contract this migration implements.

**No native enum types are created.** ``gap_type``/``gap_severity``/
``recommendation_status``/``experiment_class``/``lifecycle_status`` are
all plain ``VARCHAR`` columns guarded by ``CHECK`` constraints listing
their exact current member sets -- the same convention migration
``0010_knowledge_gap_hardening`` already established for
``knowledge_gap.gap_type``/``severity``, to avoid a models-layer import of
an upper-layer package. ``experiment_recommendation_event.actor_type`` has
no ``CHECK`` at all, mirroring ``review_event.reviewer_type``'s own
unconstrained-``VARCHAR`` trust-boundary design.

Revision ID: 0011_experiment_recommendation
Revises: 0010_knowledge_gap_hardening
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

revision: str = "0011_experiment_recommendation"
down_revision: str | None = "0010_knowledge_gap_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transcribed verbatim from app.models.knowledge_gap.GAP_TYPE_VALUES /
# GAP_SEVERITY_VALUES and app.models.experiment_recommendation's own
# RECOMMENDATION_STATUS_VALUES / EXPERIMENT_CLASS_VALUES /
# LIFECYCLE_STATUS_VALUES, at the time this migration was written -- kept
# as literal tuples here (not imported) so this migration's CHECK
# constraints remain frozen to exactly what was true when it was authored.
_GAP_TYPE_VALUES = (
    "CONFLICTING_CLAIMS",
    "LOW_CONFIDENCE_CLAIM",
    "SINGLE_SOURCE_SUPPORT",
    "NO_PRIMARY_EXPERIMENTAL_EVIDENCE",
    "MISSING_PUBLICATION",
    "MISSING_EXPERIMENTAL_CONTEXT",
    "REACTION_WITHOUT_ENZYME",
    "PROTEIN_WITHOUT_REACTION",
    "GENE_WITHOUT_PROTEIN",
    "REACTION_WITHOUT_PARTICIPANTS",
    "ISOLATED_COMPOUND",
)
_GAP_SEVERITY_VALUES = ("INFO", "LOW", "MODERATE", "HIGH", "CRITICAL")
_RECOMMENDATION_STATUS_VALUES = (
    "RECOMMENDED",
    "NOT_APPLICABLE",
    "REQUIRES_HUMAN_DESIGN",
    "INSUFFICIENT_INFORMATION",
)
_EXPERIMENT_CLASS_VALUES = (
    "REPLICATION_EXPERIMENT",
    "PROTEIN_LOCALIZATION_ASSAY",
    "EXPERIMENTAL_CONTEXT_CHARACTERIZATION",
    "REACTION_VALIDATION",
    "ENZYME_SUBSTRATE_ASSAY",
)
_LIFECYCLE_STATUS_VALUES = ("PROPOSED", "ACCEPTED", "REJECTED", "DEFERRED", "SUPERSEDED")


def _in_list(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({quoted})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    op.create_table(
        "experiment_recommendation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_gap_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_identity", sa.String(), nullable=False),
        sa.Column("gap_type", sa.String(), nullable=False),
        sa.Column("gap_severity", sa.String(), nullable=False),
        sa.Column("recommendation_status", sa.String(), nullable=False),
        sa.Column("experiment_class", sa.String(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("target_entity_type", sa.String(), nullable=True),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("required_measurement", sa.Text(), nullable=True),
        sa.Column("required_comparison", sa.Text(), nullable=True),
        sa.Column("experimental_context_requirements_json", postgresql.JSONB(), nullable=False),
        sa.Column("success_criterion", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("supporting_claim_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column("supporting_evidence_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column("reason_codes_json", postgresql.JSONB(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("template_version", sa.String(), nullable=False),
        sa.Column(
            "lifecycle_status", sa.String(), nullable=False, server_default="PROPOSED"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_recommendation"),
        sa.ForeignKeyConstraint(
            ["knowledge_gap_id"],
            ["knowledge_gap.id"],
            name="fk_experiment_recommendation_knowledge_gap_id_knowledge_gap",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_gap_type_valid"),
        "experiment_recommendation",
        _in_list("gap_type", _GAP_TYPE_VALUES),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_gap_severity_valid"),
        "experiment_recommendation",
        _in_list("gap_severity", _GAP_SEVERITY_VALUES),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_recommendation_status_valid"),
        "experiment_recommendation",
        _in_list("recommendation_status", _RECOMMENDATION_STATUS_VALUES),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_experiment_class_valid"),
        "experiment_recommendation",
        _in_list("experiment_class", _EXPERIMENT_CLASS_VALUES, nullable=True),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_lifecycle_status_valid"),
        "experiment_recommendation",
        _in_list("lifecycle_status", _LIFECYCLE_STATUS_VALUES),
    )
    op.create_index(
        "uq_experiment_recommendation_recommendation_identity",
        "experiment_recommendation",
        ["recommendation_identity"],
        unique=True,
    )
    op.create_index(
        "ix_experiment_recommendation_knowledge_gap_id",
        "experiment_recommendation",
        ["knowledge_gap_id"],
    )
    op.create_index(
        "ix_experiment_recommendation_lifecycle_status",
        "experiment_recommendation",
        ["lifecycle_status"],
    )
    op.create_index(
        "ix_experiment_recommendation_recommendation_status",
        "experiment_recommendation",
        ["recommendation_status"],
    )
    op.create_index(
        "ix_experiment_recommendation_template_id_template_version",
        "experiment_recommendation",
        ["template_id", "template_version"],
    )

    op.create_table(
        "experiment_recommendation_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", sa.String(), nullable=False),
        sa.Column("new_status", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_recommendation_event"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["experiment_recommendation.id"],
            # Explicit, shortened name: the column name already embeds
            # "recommendation", making the formulaic
            # fk_<table>_<column>_<referred_table> name redundant and, at
            # 78 characters, over PostgreSQL's 63-byte identifier limit --
            # the same pitfall (and fix) as evidence_condition's FK in
            # migration 0005_claim_evidence.
            name="fk_experiment_recommendation_event_recommendation_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_event_previous_status_valid"),
        "experiment_recommendation_event",
        _in_list("previous_status", _LIFECYCLE_STATUS_VALUES),
    )
    op.create_check_constraint(
        conv("ck_experiment_recommendation_event_new_status_valid"),
        "experiment_recommendation_event",
        _in_list("new_status", _LIFECYCLE_STATUS_VALUES),
    )
    op.create_index(
        "ix_experiment_recommendation_event_recommendation_id",
        "experiment_recommendation_event",
        ["recommendation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_recommendation_event_recommendation_id",
        table_name="experiment_recommendation_event",
    )
    op.drop_table("experiment_recommendation_event")

    op.drop_index(
        "ix_experiment_recommendation_template_id_template_version",
        table_name="experiment_recommendation",
    )
    op.drop_index(
        "ix_experiment_recommendation_recommendation_status",
        table_name="experiment_recommendation",
    )
    op.drop_index(
        "ix_experiment_recommendation_lifecycle_status", table_name="experiment_recommendation"
    )
    op.drop_index(
        "ix_experiment_recommendation_knowledge_gap_id", table_name="experiment_recommendation"
    )
    op.drop_index(
        "uq_experiment_recommendation_recommendation_identity",
        table_name="experiment_recommendation",
    )
    op.drop_table("experiment_recommendation")
