"""Group D claim/evidence schema.

Creates ``claim``, ``evidence``, ``evidence_condition``
(``docs/02_database_schema.md``).

Group D uses exactly four of the seven enums in ``app/models/enums.py``:

- ``claim.status`` -> ``ClaimStatus``
- ``claim.confidence_class`` -> ``ConfidenceClass``
- ``evidence.source_type`` -> ``SourceType``
- ``evidence.evidence_type`` -> ``EvidenceType``

None of these four were created by an earlier migration, and none of the two
enum types created by migration 0004_reaction (``curation_state``,
``reaction_participant_role``) are touched here. Each of the four new types
is created once, with a stable explicit name, and referenced from its column
with ``create_type=False`` so ``op.create_table`` does not attempt to recreate
it. ``downgrade()`` drops the three tables first, then drops only these four
enum types.

Revision ID: 0005_claim_evidence
Revises: 0004_reaction
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType

revision: str = "0005_claim_evidence"
down_revision: str | None = "0004_reaction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_claim_status_enum = postgresql.ENUM(
    *[member.value for member in ClaimStatus], name="claim_status"
)
_confidence_class_enum = postgresql.ENUM(
    *[member.value for member in ConfidenceClass], name="confidence_class"
)
_evidence_type_enum = postgresql.ENUM(
    *[member.value for member in EvidenceType], name="evidence_type"
)
_source_type_enum = postgresql.ENUM(
    *[member.value for member in SourceType], name="source_type"
)


def upgrade() -> None:
    bind = op.get_bind()
    _claim_status_enum.create(bind, checkfirst=True)
    _confidence_class_enum.create(bind, checkfirst=True)
    _evidence_type_enum.create(bind, checkfirst=True)
    _source_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "claim",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("predicate", sa.String(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_numeric", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strain", sa.String(), nullable=True),
        sa.Column("claim_category", sa.String(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *[member.value for member in ClaimStatus],
                name="claim_status",
                create_type=False,
            ),
            nullable=False,
            server_default=ClaimStatus.UNKNOWN.value,
        ),
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
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claim"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_claim_organism_id_organism",
            ondelete="RESTRICT",
        ),
        # conv() marks the name as already final: this migration runs under
        # migrations/env.py's target_metadata = Base.metadata, whose "ck"
        # naming convention (app/db/base.py) includes a %(constraint_name)s
        # token, which would otherwise reprocess and mangle this explicit
        # name (the same issue diagnosed and fixed in 0004_reaction.py).
        sa.CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_claim_confidence_score_range"),
        ),
    )
    op.create_index("ix_claim_subject_type", "claim", ["subject_type"])
    op.create_index("ix_claim_subject_id", "claim", ["subject_id"])
    op.create_index("ix_claim_predicate", "claim", ["predicate"])

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                *[member.value for member in SourceType],
                name="source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("database_name", sa.String(), nullable=True),
        sa.Column("database_accession", sa.String(), nullable=True),
        sa.Column(
            "evidence_type",
            postgresql.ENUM(
                *[member.value for member in EvidenceType],
                name="evidence_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("organism", sa.String(), nullable=True),
        sa.Column("strain", sa.String(), nullable=True),
        sa.Column("experimental_system", sa.Text(), nullable=True),
        sa.Column("assay_type", sa.Text(), nullable=True),
        sa.Column("directness", sa.String(), nullable=True),
        sa.Column("quoted_support", sa.Text(), nullable=True),
        sa.Column("curator_summary", sa.Text(), nullable=False),
        sa.Column("page", sa.String(), nullable=True),
        sa.Column("figure", sa.String(), nullable=True),
        sa.Column("table_reference", sa.String(), nullable=True),
        sa.Column("date_accessed", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name="fk_evidence_claim_id_claim", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_evidence_publication_id_publication",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_evidence_claim_id", "evidence", ["claim_id"])
    op.create_index("ix_evidence_source_type", "evidence", ["source_type"])

    op.create_table(
        "evidence_condition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experimental_condition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_condition"),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_evidence_condition_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
        # Shortened name: see the matching note in app/models/claim.py — the
        # formulaic fk_<table>_<column>_<referred_table> name would be 70
        # characters, exceeding PostgreSQL's 63-byte identifier limit.
        sa.ForeignKeyConstraint(
            ["experimental_condition_id"],
            ["experimental_condition.id"],
            name="fk_evidence_condition_experimental_condition_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "evidence_id",
            "experimental_condition_id",
            name="uq_evidence_condition_evidence_id_experimental_condition_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("evidence_condition")
    op.drop_table("evidence")
    op.drop_table("claim")

    bind = op.get_bind()
    _source_type_enum.drop(bind, checkfirst=True)
    _evidence_type_enum.drop(bind, checkfirst=True)
    _confidence_class_enum.drop(bind, checkfirst=True)
    _claim_status_enum.drop(bind, checkfirst=True)
