"""Persisted experiment recommendations and their lifecycle audit trail.

See ``docs/20_experiment_recommendation_persistence_contract.md`` for the
full contract. No prior persistence model for this existed anywhere in
this repository before Increment 24 (verified by inspection: no
``experiment``/``recommendation``/``research plan``/``intervention``/
``task`` table or model anywhere in ``app/models/``/``migrations/`` before
this one) -- this is a new schema addition, not a reuse of
``KnowledgeGap.suggested_experiment`` (a free-``TEXT`` legacy column this
increment deliberately never writes to) or of ``ReviewEvent`` (see
``ExperimentRecommendationEvent``'s own docstring for why that table's
polymorphic shape is not safely reusable here).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.knowledge_gap import GAP_SEVERITY_VALUES, GAP_TYPE_VALUES

#: Transcribed verbatim from ``app.experiment_recommendation.types
#: .RecommendationStatus`` at the time this model was written. Not imported
#: from there directly -- ``app/models/`` is the lowest layer in this
#: repository's import graph, the same reasoning
#: ``app.models.knowledge_gap.GAP_TYPE_VALUES``/``GAP_SEVERITY_VALUES``
#: already documents for themselves.
RECOMMENDATION_STATUS_VALUES = (
    "RECOMMENDED",
    "NOT_APPLICABLE",
    "REQUIRES_HUMAN_DESIGN",
    "INSUFFICIENT_INFORMATION",
)

#: Transcribed verbatim from ``app.experiment_recommendation.types.ExperimentClass``.
EXPERIMENT_CLASS_VALUES = (
    "REPLICATION_EXPERIMENT",
    "PROTEIN_LOCALIZATION_ASSAY",
    "EXPERIMENTAL_CONTEXT_CHARACTERIZATION",
    "REACTION_VALIDATION",
    "ENZYME_SUBSTRATE_ASSAY",
)

#: The lifecycle vocabulary Increment 24 introduces
#: (``app.review.experiment_recommendation_types.RecommendationLifecycleStatus``).
#: Distinct from, and never confused with, ``RECOMMENDATION_STATUS_VALUES``
#: above -- see ``docs/20_experiment_recommendation_persistence_contract.md``
#: §4 for why the two must never be conflated.
LIFECYCLE_STATUS_VALUES = ("PROPOSED", "ACCEPTED", "REJECTED", "DEFERRED", "SUPERSEDED")


def _in_list_check(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({quoted})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


class ExperimentRecommendationRecord(Base):
    """One persisted, deterministic ``ExperimentRecommendation`` (Increment 23) output.

    Named ``...Record`` rather than reusing ``ExperimentRecommendation``
    (the in-memory dataclass) to avoid import ambiguity between
    ``app.experiment_recommendation.types.ExperimentRecommendation`` and
    this ORM class -- the same "no repurposed/ambiguous name" requirement
    that motivated ``app.knowledge_gaps.types.KnowledgeGapCandidate`` vs.
    ``app.models.knowledge_gap.KnowledgeGap``.

    **Generation status vs. lifecycle status** (kept as two independent
    columns, never conflated): ``recommendation_status`` is
    Increment 23's own deterministic engine output (``RECOMMENDED``/
    ``NOT_APPLICABLE``/``REQUIRES_HUMAN_DESIGN``/
    ``INSUFFICIENT_INFORMATION``) -- immutable once persisted, since it
    describes what the engine concluded, not what a human decided.
    ``lifecycle_status`` is the separate human-workflow state
    (``PROPOSED``/``ACCEPTED``/``REJECTED``/``DEFERRED``/``SUPERSEDED``)
    this increment's own transition API mutates. A ``RECOMMENDED``
    generation status does not imply ``ACCEPTED`` lifecycle status, and a
    ``NOT_APPLICABLE``/``REQUIRES_HUMAN_DESIGN``/``INSUFFICIENT_INFORMATION``
    row is still fully trackable through the same lifecycle (even "no
    experiment recommended" is an auditable conclusion someone may want to
    formally dismiss or accept-as-is).

    Every ``gap_type``/``severity``/``recommendation_status``/
    ``experiment_class`` value is guarded by a ``CHECK`` constraint rather
    than a native ``ENUM`` -- see ``GAP_TYPE_VALUES``'s own docstring in
    ``app.models.knowledge_gap`` for the established rationale (no
    models-layer import of an upper-layer package, no duplicated enum
    definition). ``lifecycle_status`` follows the identical convention.

    ``knowledge_gap_id`` is ``NOT NULL`` with ``ON DELETE RESTRICT`` -- a
    recommendation can never exist without, or outlive, its originating
    ``KnowledgeGap`` row, mirroring every other ownership FK in this
    schema (e.g. ``evidence.claim_id``).
    """

    __tablename__ = "experiment_recommendation"
    __table_args__ = (
        CheckConstraint(
            _in_list_check("gap_type", GAP_TYPE_VALUES),
            name=conv("ck_experiment_recommendation_gap_type_valid"),
        ),
        CheckConstraint(
            _in_list_check("gap_severity", GAP_SEVERITY_VALUES),
            name=conv("ck_experiment_recommendation_gap_severity_valid"),
        ),
        CheckConstraint(
            _in_list_check("recommendation_status", RECOMMENDATION_STATUS_VALUES),
            name=conv("ck_experiment_recommendation_recommendation_status_valid"),
        ),
        CheckConstraint(
            _in_list_check("experiment_class", EXPERIMENT_CLASS_VALUES, nullable=True),
            name=conv("ck_experiment_recommendation_experiment_class_valid"),
        ),
        CheckConstraint(
            _in_list_check("lifecycle_status", LIFECYCLE_STATUS_VALUES),
            name=conv("ck_experiment_recommendation_lifecycle_status_valid"),
        ),
        Index(
            "uq_experiment_recommendation_recommendation_identity",
            "recommendation_identity",
            unique=True,
        ),
        Index("ix_experiment_recommendation_knowledge_gap_id", "knowledge_gap_id"),
        Index("ix_experiment_recommendation_lifecycle_status", "lifecycle_status"),
        Index("ix_experiment_recommendation_recommendation_status", "recommendation_status"),
        Index(
            "ix_experiment_recommendation_template_id_template_version",
            "template_id",
            "template_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    knowledge_gap_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_gap.id", ondelete="RESTRICT"),
        nullable=False,
    )

    recommendation_identity: Mapped[str] = mapped_column(String, nullable=False)

    gap_type: Mapped[str] = mapped_column(String, nullable=False)
    gap_severity: Mapped[str] = mapped_column(String, nullable=False)
    recommendation_status: Mapped[str] = mapped_column(String, nullable=False)
    experiment_class: Mapped[str | None] = mapped_column(String)

    objective: Mapped[str] = mapped_column(Text, nullable=False)

    target_entity_type: Mapped[str | None] = mapped_column(String)
    target_entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    required_measurement: Mapped[str | None] = mapped_column(Text)
    required_comparison: Mapped[str | None] = mapped_column(Text)
    experimental_context_requirements_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False
    )
    success_criterion: Mapped[str | None] = mapped_column(Text)

    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    supporting_claim_ids_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    supporting_evidence_ids_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    reason_codes_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    template_id: Mapped[str] = mapped_column(String, nullable=False)
    template_version: Mapped[str] = mapped_column(String, nullable=False)

    lifecycle_status: Mapped[str] = mapped_column(
        String, nullable=False, default="PROPOSED", server_default="PROPOSED"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list[ExperimentRecommendationEvent]] = relationship(
        back_populates="recommendation"
    )


class ExperimentRecommendationEvent(Base):
    """One lifecycle transition in a recommendation's audit trail.

    **Why this is a dedicated table, not a reuse of ``ReviewEvent``.**
    ``ReviewEvent.previous_state``/``new_state`` are backed by a native
    PostgreSQL ``curation_state`` ``ENUM`` whose fixed member set
    (``PROPOSED``/``MACHINE_REVIEWED``/``NEEDS_REVIEW``/``HUMAN_ACCEPTED``/
    ``REJECTED``) does not contain ``ACCEPTED``/``DEFERRED``/``SUPERSEDED``
    at all -- reusing it would require either misusing ``HUMAN_ACCEPTED``/
    ``REJECTED`` to mean something they were never defined to mean, with no
    representation whatsoever for ``DEFERRED``/``SUPERSEDED``, or altering
    a shared enum type also used by ``Claim`` review history for an
    unrelated entity's workflow. Both are exactly the kind of semantic
    conflation ``docs/16_review_workflow_contract.md`` warns against. A
    dedicated table with its own plain-``VARCHAR`` + ``CHECK`` status
    columns (the same convention ``ExperimentRecommendationRecord`` itself
    uses) avoids the conflict entirely.

    ``actor_type``/``actor_id`` mirror ``review_event.reviewer_type``/
    ``reviewer_id`` exactly, including the identical trust boundary: both
    are plain, unconstrained ``VARCHAR`` (no ``CHECK``, no enum) because
    the schema cannot authenticate a caller-supplied actor type as
    authorization any more than ``ReviewEvent`` can (see that model's own
    docstring) -- enforcement that only a human may reach ``ACCEPTED``
    lives in ``app.review.experiment_recommendation_workflow`` (the only
    code path that ever constructs this table's rows), not in the schema.

    Append-only: only ``created_at``, no ``updated_at`` -- the same
    convention ``ReviewEvent``/``ExternalRecord`` already use for an audit
    trail that is never mutated after the fact.
    """

    __tablename__ = "experiment_recommendation_event"
    __table_args__ = (
        CheckConstraint(
            _in_list_check("previous_status", LIFECYCLE_STATUS_VALUES),
            name=conv("ck_experiment_recommendation_event_previous_status_valid"),
        ),
        CheckConstraint(
            _in_list_check("new_status", LIFECYCLE_STATUS_VALUES),
            name=conv("ck_experiment_recommendation_event_new_status_valid"),
        ),
        Index("ix_experiment_recommendation_event_recommendation_id", "recommendation_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    recommendation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # name= is explicit and shortened: the formulaic
        # fk_<table>_<column>_<referred_table> name here would be 78
        # characters, exceeding PostgreSQL's 63-byte identifier limit (the
        # column name already embeds "recommendation", making the suffix
        # redundant) -- matches the identical shortened name in the
        # migration, and the same pattern already used for
        # evidence_condition.experimental_condition_id.
        ForeignKey(
            "experiment_recommendation.id",
            ondelete="RESTRICT",
            name="fk_experiment_recommendation_event_recommendation_id",
        ),
        nullable=False,
    )

    previous_status: Mapped[str] = mapped_column(String, nullable=False)
    new_status: Mapped[str] = mapped_column(String, nullable=False)

    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)

    comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    recommendation: Mapped[ExperimentRecommendationRecord] = relationship(back_populates="events")


__all__ = [
    "EXPERIMENT_CLASS_VALUES",
    "LIFECYCLE_STATUS_VALUES",
    "RECOMMENDATION_STATUS_VALUES",
    "ExperimentRecommendationEvent",
    "ExperimentRecommendationRecord",
]
