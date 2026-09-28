"""GotEnzymes2 source provenance (Agent 1.x Increment C.11).

Adds one new ``source_type`` enum value, ``GOTENZYMES`` -- the Metabolic
Atlas / GotEnzymes2 AI-predicted kinetic-parameter database
(https://metabolicatlas.org/gotenzymes; live API confirmed this increment
at ``https://metabolicatlas.org/api/v2/gotenzymes/enzymes``). No new column
and no new table: ``kinetic_measurement.source``/``source_id`` (migration
``0013_kinetic_measurement_sources``) already exist and already support any
``SourceType`` value, and every other field a ``GotEnzymes2``-sourced
``KineticMeasurementIdentity`` needs (``parameter_type``, ``value``,
``unit``, ``protein_id``, ``substrate_id``, ``organism_id``, ``notes``) is
likewise already a column on this table.

``SourceType.GOTENZYMES`` **is itself the "distinct, never-confused-with-
experimental" provenance marker** this increment's own instructions ask
for (their own suggested vocabulary, "AI_PREDICTED"): every GotEnzymes2
record is, by construction, an AI prediction, never a curated experimental
measurement -- there is no further split *within* this one source the way
there is, say, within BRENDA's own mix of point values and range maxima.
Introducing a second, parallel "is this predicted" enum/boolean column
alongside ``source`` would duplicate information ``source`` alone already
carries exactly and unambiguously; a future increment that ever adds a
*second* AI-prediction source would be the right moment to introduce that
separate axis, not this one (see ``docs/24_kinetic_data_curation_and_
handoff.md`` for the running "smallest architecture change actually
justified by real, current sources" convention this increment continues).
Downstream, ``KineticMeasurement.source == SourceType.GOTENZYMES`` is
therefore the one fact Agent 2 (not modified by this increment) would need
to apply the ``LITERATURE_DERIVED > AI_PREDICTED`` precedence its own
future increment might implement.

**Enum value addition is one-way in PostgreSQL**, exactly as migration
``0013``'s own docstring already documents for ``SABIORK``/``OED`` --
``downgrade()`` accordingly leaves ``GOTENZYMES`` present but unused in the
``source_type`` enum, since there is nothing else this migration added to
reverse.

Revision ID: 0016_gotenzymes_source
Revises: 0015_kinetic_protein_context
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_gotenzymes_source"
down_revision: str | None = "0015_kinetic_protein_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transcribed verbatim from app.models.enums.SourceType's one new member at
# the time this migration was written.
_NEW_SOURCE_TYPE_VALUES = ("GOTENZYMES",)


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the same transaction that
    # later uses the new value (PostgreSQL restriction) -- this migration
    # only adds the value and never uses it itself, so no autocommit
    # workaround is needed.
    for value in _NEW_SOURCE_TYPE_VALUES:
        op.execute(f"ALTER TYPE source_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # See module docstring: PostgreSQL cannot remove a single enum value
    # without recreating the whole type, and nothing else was added here to
    # reverse.
    pass
