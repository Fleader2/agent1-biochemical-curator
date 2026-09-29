"""Kinetic measurement reaction-attribution provenance (Evidence-Based Kinetic
Measurement -> Reaction Attribution increment).

Adds one new nullable column to ``kinetic_measurement``:
``reaction_attribution_reason``, a plain, unconstrained ``VARCHAR`` (never a
closed database enum -- mirrors ``parameter_type``'s own identical policy,
``app/models/kinetic_measurement.py``'s own docstring: "not CHECK-restricted...
so that future... types can be recorded without a schema migration"). Preserves
*why* ``reaction_id`` was (or was not) set -- one of the seven values
``app.normalization.kinetic_measurement_reaction_attribution
.KineticMeasurementReactionAttributionReason`` defines -- as an auditable,
persisted fact, never invented evidence: this column records a decision this
repository's own attribution logic made, not a new biological claim.

Deliberately **not** a foreign key or a database ``CHECK``/enum type: the
reason vocabulary lives entirely at the application layer
(``app.normalization.kinetic_measurement_reaction_attribution``), exactly like
``parameter_type``'s own ``KineticParameterType``, so a future increment adding
one more reason needs no migration.

Never populated retroactively for a row whose ``reaction_id`` was already
``NULL`` before this migration -- this migration only adds the column; the
same increment's own executor/persistence changes are what actually compute
and set it, on a later run.

Revision ID: 0018_kinetic_attribution
Revises: 0017_exp_context_qobs
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_kinetic_attribution"
down_revision: str | None = "0017_exp_context_qobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kinetic_measurement",
        sa.Column("reaction_attribution_reason", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kinetic_measurement", "reaction_attribution_reason")
