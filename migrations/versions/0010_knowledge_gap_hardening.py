"""KnowledgeGap schema hardening (Increment 22).

Adds the structured columns needed to persist Increment 21's deterministic
``KnowledgeGapCandidate`` output without losing its audit-critical
structure: ``gap_type``, ``severity``, ``reason_codes_json``,
``supporting_claim_ids_json``, ``supporting_evidence_ids_json``,
``supporting_entity_ids_json``, and ``identity_key``. See
``docs/18_knowledge_gap_persistence_contract.md`` for the full contract.

**No new native enum type.** ``gap_type``/``severity`` are plain
``VARCHAR`` columns guarded by a ``CHECK`` constraint listing the exact
current ``GapType``/``GapSeverity`` members, not a native Postgres
``ENUM``. This follows the same convention already established for every
other *local-only*, upper-layer-defined controlled vocabulary persisted in
this schema (``evidence.directness`` for ``app.extraction.types
.Directness``, ``review_event.reviewer_type`` for ``app.review.types
.ReviewerType``) — both are plain strings, precisely to avoid either a
models-layer import of an upper-layer package or a duplicated enum
definition. A native enum remains available to a future migration if this
vocabulary ever needs first-class database status (as
``claim_status``/``curation_state`` have), but is not warranted here.

**``reason_codes_json``/``supporting_*_ids_json`` are ``JSONB``, not a
concatenated text blob** — each preserves its own ``KnowledgeGapCandidate``
tuple field's exact order and exact string/UUID values losslessly.

**``identity_key``** is a versioned SHA-256 digest
(``app.persistence.knowledge_gap.compute_identity_key``) of the exact same
identity ingredients ``KnowledgeGapCandidate.identity_key()`` already uses
(``gap_type``, ``entity_type``, ``entity_id``, sorted
``supporting_claim_ids``) — never a second, conflicting identity rule. A
partial unique index (``WHERE identity_key IS NOT NULL``) makes repeated
persistence of the same deterministic gap idempotent at the database level,
mirroring every other nullable-but-unique-when-present identifier column
in this schema (for example ``gene.sgd_id``).

**All seven new columns are nullable.** ``knowledge_gap`` currently has no
rows in any environment this migration is aware of (no code path in this
repository has ever written to it before this increment), but this
migration does not assume that as a hard guarantee — it adds every new
column as nullable, exactly as instructed, so no historical row is ever
forced to receive a fabricated ``gap_type``/``severity``/``identity_key``.
``status`` is deliberately left as-is (still nullable, still no ``CHECK``):
its ``OPEN``/``RESOLVED``/``DISMISSED`` vocabulary is enforced at the
persistence-API boundary only (see module docstring above and
``docs/18_knowledge_gap_persistence_contract.md`` §12).

Revision ID: 0010_knowledge_gap_hardening
Revises: 0009_persistence_hardening
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

revision: str = "0010_knowledge_gap_hardening"
down_revision: str | None = "0009_persistence_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transcribed verbatim from app.models.knowledge_gap.GAP_TYPE_VALUES /
# GAP_SEVERITY_VALUES at the time this migration was written -- kept as a
# literal tuple here (not imported) so this migration's CHECK constraints
# remain frozen to exactly what was true when it was authored, independent
# of any later change to the ORM module's own constant.
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


def upgrade() -> None:
    op.add_column("knowledge_gap", sa.Column("gap_type", sa.String(), nullable=True))
    op.add_column("knowledge_gap", sa.Column("severity", sa.String(), nullable=True))
    op.add_column(
        "knowledge_gap", sa.Column("reason_codes_json", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "knowledge_gap",
        sa.Column("supporting_claim_ids_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "knowledge_gap",
        sa.Column("supporting_evidence_ids_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "knowledge_gap",
        sa.Column("supporting_entity_ids_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column("knowledge_gap", sa.Column("identity_key", sa.String(), nullable=True))

    gap_type_list = ", ".join(f"'{value}'" for value in _GAP_TYPE_VALUES)
    severity_list = ", ".join(f"'{value}'" for value in _GAP_SEVERITY_VALUES)
    op.create_check_constraint(
        conv("ck_knowledge_gap_gap_type_valid"),
        "knowledge_gap",
        f"gap_type IS NULL OR gap_type IN ({gap_type_list})",
    )
    op.create_check_constraint(
        conv("ck_knowledge_gap_severity_valid"),
        "knowledge_gap",
        f"severity IS NULL OR severity IN ({severity_list})",
    )

    op.create_index("ix_knowledge_gap_gap_type", "knowledge_gap", ["gap_type"])
    op.create_index("ix_knowledge_gap_severity", "knowledge_gap", ["severity"])
    op.create_index("ix_knowledge_gap_status", "knowledge_gap", ["status"])
    op.create_index(
        "ix_knowledge_gap_subject_type_subject_id",
        "knowledge_gap",
        ["subject_type", "subject_id"],
    )
    op.create_index(
        "uq_knowledge_gap_identity_key",
        "knowledge_gap",
        ["identity_key"],
        unique=True,
        postgresql_where=sa.text("identity_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_gap_identity_key", table_name="knowledge_gap")
    op.drop_index("ix_knowledge_gap_subject_type_subject_id", table_name="knowledge_gap")
    op.drop_index("ix_knowledge_gap_status", table_name="knowledge_gap")
    op.drop_index("ix_knowledge_gap_severity", table_name="knowledge_gap")
    op.drop_index("ix_knowledge_gap_gap_type", table_name="knowledge_gap")

    op.drop_constraint(conv("ck_knowledge_gap_severity_valid"), "knowledge_gap", type_="check")
    op.drop_constraint(conv("ck_knowledge_gap_gap_type_valid"), "knowledge_gap", type_="check")

    op.drop_column("knowledge_gap", "identity_key")
    op.drop_column("knowledge_gap", "supporting_entity_ids_json")
    op.drop_column("knowledge_gap", "supporting_evidence_ids_json")
    op.drop_column("knowledge_gap", "supporting_claim_ids_json")
    op.drop_column("knowledge_gap", "reason_codes_json")
    op.drop_column("knowledge_gap", "severity")
    op.drop_column("knowledge_gap", "gap_type")
