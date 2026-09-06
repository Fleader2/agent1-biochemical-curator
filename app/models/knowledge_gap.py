"""Knowledge gap records.

See ``docs/02_database_schema.md`` ("Table: knowledge_gap") and
``docs/18_knowledge_gap_persistence_contract.md`` (Increment 22 schema
hardening).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.naming import conv

from app.db.base import Base

#: Transcribed verbatim from ``app.knowledge_gaps.types.GapType`` (Increment
#: 21) at the time this migration/model was written. Deliberately **not**
#: imported from ``app.knowledge_gaps`` here: ``app/models/`` is the lowest
#: layer in this repository's import graph (every other package imports
#: from it, never the reverse), and every prior local-only, upper-layer
#: ``StrEnum`` persisted to a column in this schema (``app.extraction.types
#: .Directness`` -> ``Evidence.directness``, ``app.review.types.ReviewerType``
#: -> ``ReviewEvent.reviewer_type``) is written as a plain ``VARCHAR`` with
#: no native database enum and no cross-layer import, for exactly this
#: reason. ``gap_type``/``severity`` follow that same established
#: convention: plain ``VARCHAR`` columns, controlled by a ``CHECK``
#: constraint listing the exact literal values below rather than a native
#: ``ENUM`` type (which would require either a models-layer import of
#: ``app.knowledge_gaps.types`` or duplicating the enum definition).
GAP_TYPE_VALUES = (
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

#: Transcribed verbatim from ``app.knowledge_gaps.types.GapSeverity``. See
#: ``GAP_TYPE_VALUES``'s own docstring for why this is a plain ``VARCHAR``
#: with a ``CHECK`` constraint, not a native enum. ``CRITICAL`` is kept even
#: though no current detection rule ever produces it -- the in-memory enum
#: already reserves it.
GAP_SEVERITY_VALUES = ("INFO", "LOW", "MODERATE", "HIGH", "CRITICAL")


class KnowledgeGap(Base):
    """Missing information that limits model construction or predictive
    accuracy.

    ``subject_type``/``subject_id`` are polymorphic references with no
    foreign key, the same deferred-to-validation-layer limitation as
    ``claim.subject_id``. ``status`` is a plain ``VARCHAR`` with no enum and
    no CHECK: the specification gives no closed vocabulary for it (unlike,
    for example, ``claim.status``); Increment 22 introduces a controlled
    vocabulary (``OPEN``/``RESOLVED``/``DISMISSED``,
    ``app.persistence.knowledge_gap_types.KnowledgeGapStatus``) enforced at
    the persistence-API boundary only, not by a database constraint --
    this is a project-invented workflow vocabulary with no authoritative
    specification, unlike ``claim_status``/``curation_state``, and adding a
    database-level constraint for it would overreach what this increment
    was asked to harden (see ``docs/18_knowledge_gap_persistence_contract
    .md`` §12). ``priority`` carries no range constraint: the
    specification's "Suggested priority scale" is explicitly non-binding
    guidance, not a stated database constraint, and this increment does not
    invent a severity -> priority mapping.

    **Increment 22 additions** (``docs/18_knowledge_gap_persistence_contract
    .md``): ``gap_type``/``severity`` (controlled, ``CHECK``-constrained
    ``VARCHAR``s -- see ``GAP_TYPE_VALUES``/``GAP_SEVERITY_VALUES``),
    ``reason_codes_json``/``supporting_claim_ids_json``/
    ``supporting_evidence_ids_json``/``supporting_entity_ids_json`` (``JSONB``
    arrays, lossless order-preserving storage of
    ``KnowledgeGapCandidate``'s own tuple fields -- never a single
    concatenated text blob), and ``identity_key`` (a deterministic,
    versioned SHA-256 digest of the same identity ingredients
    ``KnowledgeGapCandidate.identity_key()`` already uses, unique when
    present -- see ``app.persistence.knowledge_gap.compute_identity_key``).
    All six are nullable, for the same reason ``status`` has no ``NOT NULL``
    tightened onto it here: this migration must not fabricate values for
    any pre-existing row that predates this hardening (there are none in
    practice today, but the column design does not assume that).
    """

    __tablename__ = "knowledge_gap"
    __table_args__ = (
        CheckConstraint(
            "gap_type IS NULL OR gap_type IN ({})".format(
                ", ".join(f"'{value}'" for value in GAP_TYPE_VALUES)
            ),
            name=conv("ck_knowledge_gap_gap_type_valid"),
        ),
        CheckConstraint(
            "severity IS NULL OR severity IN ({})".format(
                ", ".join(f"'{value}'" for value in GAP_SEVERITY_VALUES)
            ),
            name=conv("ck_knowledge_gap_severity_valid"),
        ),
        Index("ix_knowledge_gap_gap_type", "gap_type"),
        Index("ix_knowledge_gap_severity", "severity"),
        Index("ix_knowledge_gap_status", "status"),
        Index("ix_knowledge_gap_subject_type_subject_id", "subject_type", "subject_id"),
        Index(
            "uq_knowledge_gap_identity_key",
            "identity_key",
            unique=True,
            postgresql_where=text("identity_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    missing_information: Mapped[str] = mapped_column(Text, nullable=False)

    importance: Mapped[str | None] = mapped_column(String)

    model_impact: Mapped[str | None] = mapped_column(Text)

    suggested_experiment: Mapped[str | None] = mapped_column(Text)

    priority: Mapped[int | None] = mapped_column()

    status: Mapped[str | None] = mapped_column(String)

    gap_type: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)

    reason_codes_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    supporting_claim_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    supporting_evidence_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    supporting_entity_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)

    identity_key: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
