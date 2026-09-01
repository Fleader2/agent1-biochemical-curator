"""Group G external-record/source-cross-reference/review-event schema.

Creates ``external_record``, ``source_cross_reference``, ``review_event``
(``docs/02_database_schema.md``).

No new enum type is introduced by this migration. Group G reuses two enums
already owned by earlier migrations, with ``create_type=False`` so
``op.create_table`` does not attempt to recreate either:

- ``source_type`` (owned by 0005_claim_evidence) -> ``external_record.source``,
  ``source_cross_reference.source``
- ``curation_state`` (owned by 0004_reaction) -> ``review_event.previous_state``,
  ``review_event.new_state``

The specification defines no ``record_type`` column on ``external_record``
(unlike, e.g., ``evidence_type`` on ``evidence``) — none is created here.

None of the three tables has a foreign key: ``source_cross_reference.entity_id``
and ``review_event.entity_id`` are polymorphic references left deliberately
unconstrained, per the same referential-integrity limitation already accepted
for ``claim.subject_id`` and others in earlier Group migrations.

``source_cross_reference``'s unique constraint name is shortened from the
formulaic ``uq_source_cross_reference_entity_type_entity_id_source_external_id``
(66 characters) to ``uq_source_cross_reference_entity_type_entity_id_source_ext_id``
(61 characters, abbreviating only ``external_id`` -> ``ext_id``) to stay under
PostgreSQL's 63-byte identifier limit.

No table in this migration has a required index beyond its primary key except
``source_cross_reference.external_id``, per the specification's "Required
Indexes" section.

``downgrade()`` drops the three tables in reverse dependency order. There is
no enum type for it to drop.

Revision ID: 0008_external_records_reviews
Revises: 0007_regulation_assumptions_gaps
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.enums import CurationState, SourceType

revision: str = "0008_external_records_reviews"
down_revision: str | None = "0007_regulation_assumptions_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                *[member.value for member in SourceType],
                name="source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column(
            "retrieval_date", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("request_url", sa.Text(), nullable=True),
        sa.Column("raw_response_hash", sa.String(), nullable=False),
        sa.Column("raw_response_json", postgresql.JSONB(), nullable=True),
        sa.Column("raw_response_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_record"),
    )

    op.create_table(
        "source_cross_reference",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                *[member.value for member in SourceType],
                name="source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_cross_reference"),
        # Shortened name: see the module docstring — the formulaic
        # uq_<table>_<col>_<col>_<col>_<col> name here would be 66
        # characters, exceeding PostgreSQL's 63-byte identifier limit.
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "source",
            "external_id",
            name="uq_source_cross_reference_entity_type_entity_id_source_ext_id",
        ),
    )
    op.create_index(
        "ix_source_cross_reference_external_id", "source_cross_reference", ["external_id"]
    )

    op.create_table(
        "review_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "previous_state",
            postgresql.ENUM(
                *[member.value for member in CurationState],
                name="curation_state",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "new_state",
            postgresql.ENUM(
                *[member.value for member in CurationState],
                name="curation_state",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("reviewer_type", sa.String(), nullable=False),
        sa.Column("reviewer_id", sa.String(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_event"),
    )


def downgrade() -> None:
    op.drop_table("review_event")
    op.drop_table("source_cross_reference")
    op.drop_table("external_record")
