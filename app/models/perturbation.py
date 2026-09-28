"""Perturbation records (Agent 1.x Increment "Experimental Context and
Quantitative Observation Framework").

See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale.
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
from app.models.enums import SourceType

if TYPE_CHECKING:
    from app.models.publication import Publication
    from app.models.quantitative_observation import QuantitativeObservation

_SOURCE_TYPE = Enum(SourceType, name="source_type", create_type=False)


class Perturbation(Base):
    """One perturbation (genetic, chemical, nutrient/environmental,
    induction/repression, temperature/pH, or any other kind) a
    ``QuantitativeObservation`` may have been measured under.

    ``perturbation_type`` is a deliberately open, unconstrained ``VARCHAR``
    -- never a closed enum -- mirroring ``KineticMeasurement.parameter_type``'s
    own "never restrict the database so tightly that future types cannot
    be added" policy (``app/models/enums.py``'s own module docstring): the
    task's own instruction is to "support genetic, chemical, nutrient/
    environmental, induction/repression, temperature/pH, and similar
    perturbations without hard-coding every possible subtype."
    ``app.normalization.quantitative_observation.PerturbationCategory``
    exists as a typo-proof, normalization-layer vocabulary for *populating*
    this column consistently, exactly mirroring
    ``app.normalization.kinetic_measurement.KineticParameterType``'s own
    relationship to the open ``parameter_type`` column.

    Timing (``start_time_*``/``duration_*``) always preserves the
    source's own as-reported value/unit *and* a canonical-seconds
    conversion, mirroring ``QuantitativeObservation``'s own time-value
    discipline exactly (task §2). An explicit end time is not stored as
    its own column: it is always exactly ``start_time_canonical_s +
    duration_canonical_s`` once both are known, so storing a third,
    derivable value would only ever risk drifting out of sync with the
    other two -- a source that reports an end time instead of a duration
    has that end time converted to a duration relative to the same
    context's own start time at ingestion time (ordinary arithmetic, not
    a scientific derivation requiring ``QuantitativeObservationDependency``
    tracking).
    """

    __tablename__ = "perturbation"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    perturbation_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String)

    magnitude: Mapped[Decimal | None] = mapped_column(Numeric)
    magnitude_unit: Mapped[str | None] = mapped_column(String)

    start_time_value: Mapped[Decimal | None] = mapped_column(Numeric)
    start_time_unit: Mapped[str | None] = mapped_column(String)
    start_time_canonical_s: Mapped[Decimal | None] = mapped_column(Numeric)

    duration_value: Mapped[Decimal | None] = mapped_column(Numeric)
    duration_unit: Mapped[str | None] = mapped_column(String)
    duration_canonical_s: Mapped[Decimal | None] = mapped_column(Numeric)

    description: Mapped[str | None] = mapped_column(Text)

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

    publication: Mapped[Publication | None] = relationship(back_populates="perturbations")
    quantitative_observations: Mapped[list[QuantitativeObservation]] = relationship(
        back_populates="perturbation"
    )
