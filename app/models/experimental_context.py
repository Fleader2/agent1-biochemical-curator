"""Experimental context records (Agent 1.x Increment "Experimental Context and
Quantitative Observation Framework").

See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale.

**Distinct from the pre-existing ``ExperimentalCondition`` table**
(``app/models/experimental_condition.py``, used only by the Claims/Evidence
pipeline via ``EvidenceCondition``): that table has no organism/strain/
genotype/reference-classification fields and is scoped narrowly to one
``Claim``'s own supporting evidence. ``ExperimentalContext`` is the newer,
more general record this framework's own ``QuantitativeObservation`` rows
attach to, and is never merged with or substituted for
``ExperimentalCondition`` -- the two serve different, non-overlapping
consumers and neither is deprecated by the other's existence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ExperimentalContextClassification, SourceType

if TYPE_CHECKING:
    from app.models.organism import Organism
    from app.models.publication import Publication
    from app.models.quantitative_observation import QuantitativeObservation

_EXPERIMENTAL_CONTEXT_CLASSIFICATION = Enum(
    ExperimentalContextClassification, name="experimental_context_classification"
)
_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class ExperimentalContext(Base):
    """The biological/experimental condition one or more ``QuantitativeObservation``
    rows were measured (or curated as reference) under.

    Every field beyond ``id`` is optional -- a source rarely reports every
    one of organism/strain/genotype/medium/carbon source/temperature/pH/
    growth phase/growth condition at once, and this table must never force
    a caller to fabricate a value it does not have (task's own "the
    smallest extensible schema"). ``classification`` records whether this
    context *is* the standard reference/baseline condition for its
    organism, independent of which specific observation later reuses it
    (see ``app.models.enums.ExperimentalContextClassification``'s own
    docstring for why this is a distinct axis from
    ``QuantitativeObservation.evidence_class``).

    Two contexts that happen to describe the same real-world condition are
    never deduplicated by this table -- no natural-key uniqueness
    constraint is placed on any column combination, mirroring
    ``KineticMeasurement``'s own explicit "never merge, always allow
    independent rows" policy for the same reason: two independently
    curated descriptions of "the same" condition may differ in detail or
    provenance, and silently merging them would discard that.
    """

    __tablename__ = "experimental_context"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    organism_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), index=True
    )

    strain: Mapped[str | None] = mapped_column(String)
    genotype: Mapped[str | None] = mapped_column(Text)

    medium: Mapped[str | None] = mapped_column(String)
    carbon_source: Mapped[str | None] = mapped_column(String)

    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric)
    ph: Mapped[Decimal | None] = mapped_column(Numeric)

    growth_phase: Mapped[str | None] = mapped_column(String)
    growth_condition: Mapped[str | None] = mapped_column(String)

    classification: Mapped[ExperimentalContextClassification | None] = mapped_column(
        _EXPERIMENTAL_CONTEXT_CLASSIFICATION
    )

    #: This context record's own provenance -- e.g. "this exact growth
    #: condition is described in publication X" -- independent of, and not
    #: necessarily identical to, any one observation's own provenance
    #: (an observation's value may come from a different source than the
    #: one that first described the context it was measured under).
    source: Mapped[SourceType | None] = mapped_column(_SOURCE_TYPE)
    source_id: Mapped[str | None] = mapped_column(String)
    publication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("publication.id", ondelete="RESTRICT")
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

    organism: Mapped[Organism | None] = relationship(back_populates="experimental_contexts")
    publication: Mapped[Publication | None] = relationship(
        back_populates="experimental_contexts"
    )
    quantitative_observations: Mapped[list[QuantitativeObservation]] = relationship(
        back_populates="experimental_context"
    )
