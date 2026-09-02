"""Modeling assumption records.

See ``docs/02_database_schema.md`` ("Table: modeling_assumption").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Numeric, String, Text, false, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelingAssumption(Base):
    """An assumption required for downstream model construction.

    Assumptions must never be stored as evidence-backed facts
    (``docs/02_database_schema.md``): this table has no relationship to
    ``claim`` or ``evidence``, and nothing in this module creates one.
    ``subject_type``/``subject_id`` are polymorphic references with no
    foreign key, the same deferred-to-validation-layer limitation as
    ``claim.subject_id``. ``confidence`` carries no range constraint: the
    specification declares it as a plain ``NUMERIC`` with no stated bounds,
    unlike ``claim.confidence_score``.
    """

    __tablename__ = "modeling_assumption"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    assumption: Mapped[str] = mapped_column(Text, nullable=False)

    reason: Mapped[str | None] = mapped_column(Text)

    required_for_model: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    confidence: Mapped[Decimal | None] = mapped_column(Numeric)

    human_approved: Mapped[bool] = mapped_column(
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
