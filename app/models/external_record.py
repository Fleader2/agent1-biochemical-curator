"""External record records.

See ``docs/02_database_schema.md`` ("Table: external_record").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SourceType

# Type creation is owned exclusively by migration 0005_claim_evidence, not
# this table's migration (0008 reuses this type rather than recreating it):
# create_type=False means this ORM-level Enum instance only describes the
# column type, it never issues CREATE TYPE itself.
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class ExternalRecord(Base):
    """A raw or normalized response retrieved from an external database or
    API, kept for reproducibility and auditability.

    External records should be append-only where practical
    (``docs/02_database_schema.md``): nothing in this module updates or
    overwrites a prior row, and the table has no ``updated_at`` column,
    consistent with that append-only intent. Only ``created_at`` is defined.

    The specification defines no ``record_type`` column on this table
    (unlike, say, ``evidence_type`` on ``evidence``) — none is added here.
    """

    __tablename__ = "external_record"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    source: Mapped[SourceType] = mapped_column(_SOURCE_TYPE, nullable=False)

    external_id: Mapped[str | None] = mapped_column(String)

    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request_url: Mapped[str | None] = mapped_column(Text)

    raw_response_hash: Mapped[str] = mapped_column(String, nullable=False)

    raw_response_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB)
    raw_response_text: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
