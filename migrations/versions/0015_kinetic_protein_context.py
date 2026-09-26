"""Kinetic measurement protein-context table (Agent 1.x Increment C.6).

Adds ``kinetic_measurement_protein_context``, a pure join table recording
every ``Protein`` one persisted ``KineticMeasurement`` is biologically
applicable to -- not just the single, first-established ``protein_id``
column already on ``kinetic_measurement``, which this migration does not
touch.

Motivated directly by Real Integration Pilot 1 Run 7: SABIO-RK's own search
is EC-scoped, not protein-scoped, so two distinct proteins sharing one EC
(confirmed live: yeast's real FAS1/FAS2 heterodimer) can each independently,
legitimately discover the identical external source record. Before this
table existed, whichever protein's query happened to persist the record
first "won" ``kinetic_measurement.protein_id``, and the second protein's own
equally successful, equally valid query left no trace of its own protein
context at all once ``persist_kinetic_measurement``'s existing
``(source, source_id)`` uniqueness check found the first protein's row
already there.

Mirrors ``enzyme_complex_member``'s own, already-established join-table
shape exactly (``docs/02_database_schema.md``): ``kinetic_measurement_id``
uses ``ON DELETE CASCADE`` (this row has no independent scientific meaning
apart from its own measurement), ``protein_id`` uses ``ON DELETE RESTRICT``
(a protein is an independent scientific record). A ``UniqueConstraint`` on
``(kinetic_measurement_id, protein_id)`` is both the idempotency guarantee
(the same protein linked to the same measurement twice is a no-op, never a
duplicate row) and the concurrency backstop, exactly mirroring
``kinetic_measurement``'s own ``(source, source_id)`` partial unique index's
role for the measurement row itself.

``basis`` is a plain, unconstrained ``VARCHAR`` (never a closed enum) --
today always ``"QUERY_CONTEXT"`` (a query issued *for* this protein
independently discovered the record); reserved so that a future increment
able to derive protein applicability directly from a source's own
explicitly-reported protein/accession evidence never has to overload this
same value to mean something the data does not actually support.

Revision ID: 0015_kinetic_protein_context
Revises: 0014_enzyme_regulatory_states
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_kinetic_protein_context"
down_revision: str | None = "0014_enzyme_regulatory_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kinetic_measurement_protein_context",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kinetic_measurement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("basis", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kinetic_measurement_protein_context"),
        sa.ForeignKeyConstraint(
            ["kinetic_measurement_id"],
            ["kinetic_measurement.id"],
            name="fk_kinetic_measurement_protein_context_kinetic_measurement_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_kinetic_measurement_protein_context_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "kinetic_measurement_id",
            "protein_id",
            name="uq_kinetic_measurement_protein_context_km_id_protein_id",
        ),
    )
    op.create_index(
        "ix_kinetic_measurement_protein_context_kinetic_measurement_id",
        "kinetic_measurement_protein_context",
        ["kinetic_measurement_id"],
    )
    op.create_index(
        "ix_kinetic_measurement_protein_context_protein_id",
        "kinetic_measurement_protein_context",
        ["protein_id"],
    )


def downgrade() -> None:
    op.drop_table("kinetic_measurement_protein_context")
