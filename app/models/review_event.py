"""Review event records.

See ``docs/02_database_schema.md`` ("Table: review_event").
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CurationState

# Type creation is owned exclusively by migration 0004_reaction, not this
# table's migration (0008 reuses this type rather than recreating it):
# create_type=False means this ORM-level Enum instance only describes the
# column type, it never issues CREATE TYPE itself.
_CURATION_STATE = Enum(CurationState, name="curation_state", create_type=False)


class ReviewEvent(Base):
    """A single step in a curated record's review-state history, forming an
    audit trail of curation decisions.

    ``entity_type``/``entity_id`` form a polymorphic reference with no
    foreign key, the same deferred-to-validation-layer limitation as
    ``source_cross_reference.entity_id`` — and, like it, ``entity_id`` is
    ``NOT NULL`` here. ``reviewer_type`` is a plain ``VARCHAR``: the
    specification gives only "Examples" (``AI_CRITIC``, ``HUMAN``,
    ``DETERMINISTIC_VALIDATOR``), not a closed enumeration, so no
    ``ReviewerType`` enum or vocabulary CHECK is introduced. In particular,
    nothing here enforces the "automated processes must never set
    HUMAN_ACCEPTED" rule (``01-scientific-integrity.mdc``) — the schema
    cannot trust a client-supplied ``reviewer_type`` as authorization
    (``03-database-api.mdc``); that enforcement belongs to the API/auth layer
    in a later phase.
    """

    __tablename__ = "review_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    previous_state: Mapped[CurationState | None] = mapped_column(_CURATION_STATE)
    new_state: Mapped[CurationState] = mapped_column(_CURATION_STATE, nullable=False)

    reviewer_type: Mapped[str] = mapped_column(String, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String)

    comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
