"""Modeling decision records: competing alternatives requiring reconciliation.

See ``docs/02_database_schema.md`` ("Table: modeling_decision", "Table:
modeling_decision_option") and
``docs/30_project_subproject_model_foundation.md``.

Distinct from the existing ``ModelingAssumption`` table (a single
declarative statement with no competing options to choose between): a
``ModelingDecision`` has multiple ``ModelingDecisionOption`` rows, each a
candidate answer, with a resolution workflow (``status``,
``selected_option_id``, ``resolution_source``). This will later support a
UI screen where users resolve competing biological/modeling alternatives;
that UI is not implemented by this increment.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base
from app.models.enums import (
    ConfidenceClass,
    ModelingDecisionResolutionSource,
    ModelingDecisionStatus,
)

_MODELING_DECISION_STATUS = Enum(
    ModelingDecisionStatus, name="modeling_decision_status", create_type=False
)
_MODELING_DECISION_RESOLUTION_SOURCE = Enum(
    ModelingDecisionResolutionSource,
    name="modeling_decision_resolution_source",
    create_type=False,
)
_CONFIDENCE_CLASS = Enum(ConfidenceClass, name="confidence_class", create_type=False)


class ModelingDecision(Base):
    """One modeling decision requiring reconciliation between competing
    alternatives.

    ``project_id``/``subproject_id``/``model_id`` are all nullable: a
    decision's own context may be scoped at whichever level is appropriate,
    and more than one may be set when a decision is naturally scoped at
    multiple levels at once. ``selected_option_id`` is a genuine forward
    reference to ``ModelingDecisionOption`` -- resolved via a second
    ``ALTER TABLE ... ADD CONSTRAINT`` in the migration once that table
    exists, and ``post_update=True`` here so SQLAlchemy's unit-of-work
    issues the two inserts (decision, then its selected option) without
    tripping over the circular reference.
    """

    __tablename__ = "modeling_decision"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project.id", ondelete="RESTRICT"), index=True
    )
    subproject_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subproject.id", ondelete="RESTRICT"), index=True
    )
    model_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("model.id", ondelete="RESTRICT"), index=True
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    decision_type: Mapped[str | None] = mapped_column(String)

    status: Mapped[ModelingDecisionStatus] = mapped_column(
        _MODELING_DECISION_STATUS,
        nullable=False,
        default=ModelingDecisionStatus.OPEN,
        server_default=ModelingDecisionStatus.OPEN.value,
    )

    selected_option_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the naming-convention-derived name exceeds
        # PostgreSQL's 63-byte identifier limit.
        ForeignKey(
            "modeling_decision_option.id",
            ondelete="RESTRICT",
            name="fk_modeling_decision_selected_option_id_mdec_opt",
        ),
        index=True,
    )
    resolution_source: Mapped[ModelingDecisionResolutionSource | None] = mapped_column(
        _MODELING_DECISION_RESOLUTION_SOURCE
    )

    rationale: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    options: Mapped[list[ModelingDecisionOption]] = relationship(
        back_populates="decision",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="ModelingDecisionOption.decision_id",
    )
    selected_option: Mapped[ModelingDecisionOption | None] = relationship(
        foreign_keys=[selected_option_id], post_update=True
    )


class ModelingDecisionOption(Base):
    """One candidate option for a ``ModelingDecision``.

    ``confidence_score`` reuses ``Claim.confidence_score``'s own 0-100
    range ``CHECK`` and ``confidence_class`` reuses the existing
    ``ConfidenceClass`` enum directly -- no new confidence representation
    is invented.
    """

    __tablename__ = "modeling_decision_option"
    __table_args__ = (
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_modeling_decision_option_confidence_score_range"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("modeling_decision.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    label: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    evidence_summary: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric)
    confidence_class: Mapped[ConfidenceClass | None] = mapped_column(_CONFIDENCE_CLASS)

    consequence_summary: Mapped[str | None] = mapped_column(Text)

    recommended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
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

    decision: Mapped[ModelingDecision] = relationship(
        back_populates="options", foreign_keys=[decision_id]
    )


__all__ = ["ModelingDecision", "ModelingDecisionOption"]
