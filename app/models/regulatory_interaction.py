"""Regulatory interaction records.

See ``docs/02_database_schema.md`` ("Table: regulatory_interaction").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CurationState, RegulatoryEffect

if TYPE_CHECKING:
    from app.models.claim import Claim
    from app.models.organism import Organism

# Type creation for curation_state is owned by migration 0004_reaction; for
# regulatory_effect it is owned by migration 0007_regulation_assumptions_gaps.
# Both are referenced here with create_type=False: these ORM-level Enum
# instances only describe the column type, they never issue CREATE TYPE.
_CURATION_STATE = Enum(CurationState, name="curation_state", create_type=False)
_REGULATORY_EFFECT = Enum(RegulatoryEffect, name="regulatory_effect", create_type=False)


class RegulatoryInteraction(Base):
    """A biochemical, signaling, transcriptional, or post-translational
    regulatory relationship between a regulator and a target.

    ``regulator_type``/``regulator_id`` and ``target_type``/``target_id`` are
    polymorphic references with no foreign key: the referenced table varies
    by ``*_type``, which the specification does not constrain to a closed
    enumeration. This is the same known referential-integrity limitation as
    ``claim.subject_id``/``object_id``, deferred to the validation layer
    rather than approximated here with an invented enum, multi-table FK, or
    trigger. ``regulator_id``/``target_id`` may be ``NULL`` (the specification
    gives them no ``NOT NULL``), independent of the required ``*_type``
    columns.
    """

    __tablename__ = "regulatory_interaction"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    regulator_type: Mapped[str] = mapped_column(String, nullable=False)
    regulator_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    effect: Mapped[RegulatoryEffect] = mapped_column(_REGULATORY_EFFECT, nullable=False)

    mechanism: Mapped[str | None] = mapped_column(Text)

    direct: Mapped[bool | None] = mapped_column(Boolean)
    condition_dependent: Mapped[bool | None] = mapped_column(Boolean)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT")
    )

    claim_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("claim.id", ondelete="RESTRICT")
    )

    curation_state: Mapped[CurationState] = mapped_column(
        _CURATION_STATE,
        nullable=False,
        default=CurationState.PROPOSED,
        server_default=CurationState.PROPOSED.value,
    )

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

    organism: Mapped[Organism | None] = relationship(back_populates="regulatory_interactions")
    claim: Mapped[Claim | None] = relationship(back_populates="regulatory_interactions")
