"""Source cross-reference records.

See ``docs/02_database_schema.md`` ("Table: source_cross_reference").
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Enum, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SourceType

# Type creation is owned exclusively by migration 0005_claim_evidence, not
# this table's migration (0008 reuses this type rather than recreating it):
# create_type=False means this ORM-level Enum instance only describes the
# column type, it never issues CREATE TYPE itself.
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class SourceCrossReference(Base):
    """An external identifier associated with an internal entity.

    ``entity_type``/``entity_id`` form a polymorphic reference with no
    foreign key: the referenced table varies by ``entity_type``, which the
    specification does not constrain to a closed enumeration. This is the
    same known referential-integrity limitation as ``claim.subject_id``,
    deferred to the validation layer rather than approximated here with an
    invented enum, multi-table FK, or trigger. Unlike the polymorphic
    references on ``claim``/``regulatory_interaction``/``modeling_assumption``/
    ``knowledge_gap``, ``entity_id`` here is ``NOT NULL`` — the specification
    requires it, since a cross-reference with no entity to attach to would be
    meaningless.

    The constraint name ``uq_source_cross_reference_entity_type_entity_id_source_ext_id``
    shortens ``external_id`` to ``ext_id``: the formulaic name
    (``uq_source_cross_reference_entity_type_entity_id_source_external_id``)
    is 66 characters, exceeding PostgreSQL's 63-byte identifier limit.
    """

    __tablename__ = "source_cross_reference"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "source",
            "external_id",
            name="uq_source_cross_reference_entity_type_entity_id_source_ext_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    source: Mapped[SourceType] = mapped_column(_SOURCE_TYPE, nullable=False)

    external_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
