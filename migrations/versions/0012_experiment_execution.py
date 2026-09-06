"""Experiment execution and result capture (Increment 25).

Creates three new tables:

* ``experiment_execution`` -- one attempt to carry out an accepted
  ``experiment_recommendation`` row. ``execution_identity`` (a
  deterministic digest of ``recommendation_id`` + caller-supplied
  ``execution_identifier``) carries a database-level unique constraint,
  making repeated persistence of the same execution idempotent; no
  uniqueness exists on ``recommendation_id`` alone, since one recommendation
  may legitimately have many executions (replication).
* ``experiment_execution_event`` -- an append-only lifecycle audit trail,
  structurally independent of ``experiment_recommendation_event`` (a
  distinct status vocabulary with no shared meaning).
* ``experiment_result`` -- one observation/measurement produced by one
  execution. No ``updated_at`` column: this table is append-only.

See ``docs/21_experiment_execution_result_contract.md`` for the full
contract this migration implements.

**No native enum types are created.** ``status``/``result_type`` are plain
``VARCHAR`` columns guarded by ``CHECK`` constraints listing their exact
current member sets -- the same convention migrations
``0010_knowledge_gap_hardening``/``0011_experiment_recommendation`` already
established, to avoid a models-layer import of an upper-layer package.

Revision ID: 0012_experiment_execution
Revises: 0011_experiment_recommendation
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

revision: str = "0012_experiment_execution"
down_revision: str | None = "0011_experiment_recommendation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transcribed verbatim from app.models.experiment_execution.EXECUTION_STATUS_VALUES /
# RESULT_TYPE_VALUES at the time this migration was written -- kept as
# literal tuples here (not imported) so this migration's CHECK constraints
# remain frozen to exactly what was true when it was authored.
_EXECUTION_STATUS_VALUES = ("PLANNED", "IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED")
_RESULT_TYPE_VALUES = (
    "QUANTITATIVE_MEASUREMENT",
    "QUALITATIVE_OBSERVATION",
    "DETECTION",
    "NON_DETECTION",
    "ASSAY_OUTCOME",
)


def _in_list(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({quoted})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    op.create_table(
        "experiment_execution",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_identifier", sa.String(), nullable=False),
        sa.Column("execution_identity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PLANNED"),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("performed_by", sa.String(), nullable=True),
        sa.Column("laboratory", sa.String(), nullable=True),
        sa.Column("protocol_reference", sa.String(), nullable=True),
        sa.Column("planned_conditions_json", postgresql.JSONB(), nullable=False),
        sa.Column("actual_conditions_json", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_execution"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["experiment_recommendation.id"],
            # Explicit, shortened name: the formulaic
            # fk_<table>_<column>_<referred_table> name here would be 67
            # characters, exceeding PostgreSQL's 63-byte identifier limit.
            name="fk_experiment_execution_recommendation_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_experiment_execution_status_valid"),
        "experiment_execution",
        _in_list("status", _EXECUTION_STATUS_VALUES),
    )
    op.create_index(
        "uq_experiment_execution_execution_identity",
        "experiment_execution",
        ["execution_identity"],
        unique=True,
    )
    op.create_index(
        "ix_experiment_execution_recommendation_id",
        "experiment_execution",
        ["recommendation_id"],
    )
    op.create_index("ix_experiment_execution_status", "experiment_execution", ["status"])

    op.create_table(
        "experiment_execution_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", sa.String(), nullable=False),
        sa.Column("new_status", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_execution_event"),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["experiment_execution.id"],
            # Explicit, shortened name -- see experiment_recommendation_event
            # .recommendation_id for the identical 63-byte-limit rationale.
            name="fk_experiment_execution_event_execution_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_experiment_execution_event_previous_status_valid"),
        "experiment_execution_event",
        _in_list("previous_status", _EXECUTION_STATUS_VALUES),
    )
    op.create_check_constraint(
        conv("ck_experiment_execution_event_new_status_valid"),
        "experiment_execution_event",
        _in_list("new_status", _EXECUTION_STATUS_VALUES),
    )
    op.create_index(
        "ix_experiment_execution_event_execution_id",
        "experiment_execution_event",
        ["execution_id"],
    )

    op.create_table(
        "experiment_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_identity", sa.String(), nullable=False),
        sa.Column("result_type", sa.String(), nullable=False),
        sa.Column("measurement_name", sa.String(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_numeric", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("uncertainty_text", sa.Text(), nullable=True),
        sa.Column("statistical_support", sa.Text(), nullable=True),
        sa.Column("sample_identifier", sa.String(), nullable=True),
        sa.Column("replicate_identifier", sa.String(), nullable=True),
        sa.Column("time_point", sa.String(), nullable=True),
        sa.Column("condition_label", sa.String(), nullable=True),
        sa.Column("instrument_reference", sa.String(), nullable=True),
        sa.Column("raw_data_reference", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_result"),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["experiment_execution.id"],
            name="fk_experiment_result_execution_id_experiment_execution",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_experiment_result_result_type_valid"),
        "experiment_result",
        _in_list("result_type", _RESULT_TYPE_VALUES),
    )
    op.create_index(
        "uq_experiment_result_result_identity",
        "experiment_result",
        ["result_identity"],
        unique=True,
    )
    op.create_index("ix_experiment_result_execution_id", "experiment_result", ["execution_id"])
    op.create_index(
        "ix_experiment_result_measurement_name", "experiment_result", ["measurement_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_result_measurement_name", table_name="experiment_result")
    op.drop_index("ix_experiment_result_execution_id", table_name="experiment_result")
    op.drop_index("uq_experiment_result_result_identity", table_name="experiment_result")
    op.drop_table("experiment_result")

    op.drop_index(
        "ix_experiment_execution_event_execution_id", table_name="experiment_execution_event"
    )
    op.drop_table("experiment_execution_event")

    op.drop_index("ix_experiment_execution_status", table_name="experiment_execution")
    op.drop_index("ix_experiment_execution_recommendation_id", table_name="experiment_execution")
    op.drop_index("uq_experiment_execution_execution_identity", table_name="experiment_execution")
    op.drop_table("experiment_execution")
