"""Baseline revision.

Establishes the Alembic revision history and the ``alembic_version`` table for an
empty PostgreSQL database. No scientific tables are created here: the schema in
``docs/02_database_schema.md`` (organism, gene, protein, compound, reaction,
claim, evidence, kinetic_measurement, and related tables) is implemented in the
database phase, which will add revisions on top of this baseline.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema objects are created by the baseline revision."""


def downgrade() -> None:
    """No schema objects are removed by the baseline revision."""
