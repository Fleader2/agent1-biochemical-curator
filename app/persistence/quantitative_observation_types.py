"""Data contract for QuantitativeObservation persistence (Agent 1.x Increment
"Experimental Context and Quantitative Observation Framework" /
"SGD Reference Protein Abundance Integration").

Mirrors ``app.persistence.kinetic_measurement_types.KineticMeasurementPersistenceResult``
exactly in shape and policy -- reuses ``app.persistence.types.PersistenceAction``
directly rather than inventing a parallel action vocabulary. No derivative-lineage
merge case exists here (unlike kinetic measurements): a quantitative observation
carries no ``original_source``/``original_source_identifier`` concept, so
``REUSED_EXISTING`` here means exactly one thing -- an exact replay of the identical
``(source, source_id)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.enums import SourceType
from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class QuantitativeObservationPersistenceResult:
    """The outcome of one ``persist_quantitative_observation`` call.

    * ``CREATED`` -- a new ``QuantitativeObservation`` row was inserted.
    * ``REUSED_EXISTING`` -- an exact ``(source, source_id)`` replay.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could be found on
      the post-failure recheck (an unexpected condition).
    """

    action: PersistenceAction
    quantitative_observation_id: UUID | None
    created: bool
    reused_existing: bool
    #: Both ``None`` exactly when the persisted identity itself had no deterministic
    #: source identity to key idempotency on (``QuantitativeObservationIdentity.source``/
    #: ``.source_id`` both ``None`` -- a real, valid case this type -- unlike
    #: ``KineticMeasurementPersistenceResult``, whose every real caller always supplies
    #: one -- must represent rather than reject.
    source: SourceType | None
    source_id: str | None
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"QuantitativeObservationPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )
        if self.source_id is not None and not self.source_id.strip():
            raise ValueError(
                "QuantitativeObservationPersistenceResult.source_id must be None or a "
                f"non-empty string, got {self.source_id!r}"
            )
        if (self.source is None) != (self.source_id is None):
            raise ValueError(
                "QuantitativeObservationPersistenceResult.source/source_id must be both "
                f"None or both set, got source={self.source!r} source_id={self.source_id!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.quantitative_observation_id is None:
                raise ValueError("CREATED requires quantitative_observation_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.quantitative_observation_id is None:
                raise ValueError("REUSED_EXISTING requires quantitative_observation_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.quantitative_observation_id is not None:
                raise ValueError("FAILED must not carry quantitative_observation_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["QuantitativeObservationPersistenceResult"]
