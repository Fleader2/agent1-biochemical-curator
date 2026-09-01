"""Experimental / biological context records.

See ``docs/02_database_schema.md`` ("Table: experimental_condition").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExperimentalCondition(Base):
    """Biological or experimental context under which evidence was gathered.

    Unlike most Group A tables, this one has only ``created_at``: the
    specification does not give it an ``updated_at`` column.
    """

    __tablename__ = "experimental_condition"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    medium: Mapped[str | None] = mapped_column(String)

    carbon_source: Mapped[str | None] = mapped_column(String)
    carbon_concentration: Mapped[Decimal | None] = mapped_column(Numeric)
    carbon_concentration_unit: Mapped[str | None] = mapped_column(String)

    nitrogen_source: Mapped[str | None] = mapped_column(String)

    oxygen_status: Mapped[str | None] = mapped_column(String)

    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric)
    ph: Mapped[Decimal | None] = mapped_column(Numeric)

    growth_phase: Mapped[str | None] = mapped_column(String)
    growth_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    growth_rate_unit: Mapped[str | None] = mapped_column(String)

    culture_mode: Mapped[str | None] = mapped_column(String)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
