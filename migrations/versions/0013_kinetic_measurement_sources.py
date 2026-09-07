"""Kinetic measurement source provenance (Agent 1.x Increment A).

Adds two new ``source_type`` enum values (``SABIORK``, ``OPEN ENZYME
DATABASE`` -- see below) and four new nullable columns to
``kinetic_measurement``: ``source``/``source_id`` (which connector-ingested
source created this row -- the same shape ``evidence.source_type``/
``source_id`` already use) and ``reported_rate_law``/
``reported_parameter_type`` (a source's own free-text framing, preserved
independently of this table's controlled ``parameter_type`` column). A
partial unique index on ``(source, source_id)`` (both non-null) is the
database-level idempotency guarantee for re-ingesting the same source
record. See ``docs/24_kinetic_data_curation_and_handoff.md``.

**Enum value addition is one-way in PostgreSQL.** ``ALTER TYPE ... ADD
VALUE`` has no reverse operation (``ALTER TYPE ... DROP VALUE`` does not
exist) short of recreating the entire type -- which here would mean
touching ``evidence.source_type``, ``external_record.source``,
``source_cross_reference.source``, and this migration's own
``kinetic_measurement.source`` columns all at once, for a downgrade path
this repository's own migration-safety tests do not require (they check
that a downgrade-to-base completes and removes every table, not that every
enum value ever added is later removed). ``downgrade()`` therefore removes
only what it can safely reverse (the four new columns and the unique
index) and leaves ``SABIORK``/``OED`` present but unused in the
``source_type`` enum -- a documented, deliberate limitation, not an
oversight.

Revision ID: 0013_kinetic_measurement_sources
Revises: 0012_experiment_execution
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_kinetic_measurement_sources"
down_revision: str | None = "0012_experiment_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transcribed verbatim from app.models.enums.SourceType's two new members at
# the time this migration was written.
_NEW_SOURCE_TYPE_VALUES = ("SABIORK", "OED")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the same transaction that
    # later uses the new value (PostgreSQL restriction) -- this migration
    # only adds the values and never uses them itself, so no autocommit
    # workaround is needed; the values are simply not usable until this
    # migration's own transaction commits, which is exactly when this
    # migration ends.
    for value in _NEW_SOURCE_TYPE_VALUES:
        op.execute(f"ALTER TYPE source_type ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column(
        "kinetic_measurement",
        sa.Column(
            "source",
            sa.Enum(name="source_type", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("kinetic_measurement", sa.Column("source_id", sa.String(), nullable=True))
    op.add_column(
        "kinetic_measurement", sa.Column("reported_rate_law", sa.Text(), nullable=True)
    )
    op.add_column(
        "kinetic_measurement", sa.Column("reported_parameter_type", sa.String(), nullable=True)
    )

    op.create_index(
        "uq_kinetic_measurement_source_source_id",
        "kinetic_measurement",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )


def downgrade() -> None:
    # See module docstring: the two new source_type enum values are not
    # removed (PostgreSQL cannot do this without recreating the type) --
    # only the columns/index this migration itself created are reversed.
    op.drop_index("uq_kinetic_measurement_source_source_id", table_name="kinetic_measurement")
    op.drop_column("kinetic_measurement", "reported_parameter_type")
    op.drop_column("kinetic_measurement", "reported_rate_law")
    op.drop_column("kinetic_measurement", "source_id")
    op.drop_column("kinetic_measurement", "source")
