"""Knowledge gap records.

See ``docs/02_database_schema.md`` ("Table: knowledge_gap").
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeGap(Base):
    """Missing information that limits model construction or predictive
    accuracy.

    ``subject_type``/``subject_id`` are polymorphic references with no
    foreign key, the same deferred-to-validation-layer limitation as
    ``claim.subject_id``. ``status`` is a plain ``VARCHAR`` with no enum and
    no CHECK: the specification gives no closed vocabulary for it (unlike,
    for example, ``claim.status``), so any string may be stored and no
    automatic resolution/closure logic exists here. ``priority`` carries no
    range constraint: the specification's "Suggested priority scale" is
    explicitly non-binding guidance, not a stated database constraint.
    """

    __tablename__ = "knowledge_gap"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    missing_information: Mapped[str] = mapped_column(Text, nullable=False)

    importance: Mapped[str | None] = mapped_column(String)

    model_impact: Mapped[str | None] = mapped_column(Text)

    suggested_experiment: Mapped[str | None] = mapped_column(Text)

    priority: Mapped[int | None] = mapped_column()

    status: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
