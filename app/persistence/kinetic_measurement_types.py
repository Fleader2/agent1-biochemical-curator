"""Data contract for KineticMeasurement persistence (Agent 1.x Increment A).

Reuses ``app.persistence.types.PersistenceAction`` directly (``CREATED``/
``REUSED_EXISTING``/``FAILED``) rather than inventing a parallel action
vocabulary, mirroring ``app.persistence.knowledge_gap_types``. No
``REQUIRES_REVIEW`` action exists here: unlike ``KnowledgeGap`` or the
identity-dedup modules (``compound``, ``reaction_enzyme``, ...),
``KineticMeasurement`` has no ambiguous-match/conflict case to route to
human review -- ``app/models/kinetic_measurement.py``'s own long-standing
policy is that every measurement is independent, so there is never a
"more than one existing row might be this one" decision to make (see
``app.normalization.kinetic_measurement``'s module docstring for why this
module has no ``NormalizationResult``/``NormalizationStatus`` at all).

``REUSED_EXISTING`` here covers two distinct, both-legitimate cases (see
``app.persistence.kinetic_measurement`` for exactly when each applies):

1. An exact replay of the identical ``(source, source_id)`` -- the same
   source record ingested again.
2. A conservative, provenance-driven derivative merge -- a new record
   whose own ``original_source``/``original_source_identifier`` name an
   *existing* row's ``(source, source_id)`` exactly, so it is recorded as
   an additional ``SourceCrossReference`` on that existing row rather than
   a duplicate new row (Increment A Step 25).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.enums import SourceType
from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class KineticMeasurementPersistenceResult:
    """The outcome of one ``persist_kinetic_measurement`` call.

    * ``CREATED`` -- a new ``KineticMeasurement`` row was inserted.
      ``kinetic_measurement_id`` is its id; ``created=True``.
    * ``REUSED_EXISTING`` -- either an exact ``(source, source_id)`` replay
      or a derivative-lineage merge (see module docstring).
      ``kinetic_measurement_id`` is the existing row's id;
      ``reused_existing=True``.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could
      be found on the post-failure recheck (an unexpected condition).
      ``kinetic_measurement_id`` is ``None``.
    """

    action: PersistenceAction
    kinetic_measurement_id: UUID | None
    created: bool
    reused_existing: bool
    source: SourceType
    source_id: str
    source_cross_reference_id: UUID | None = None
    external_record_id: UUID | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"KineticMeasurementPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError(
                "KineticMeasurementPersistenceResult.source_id must be a non-empty string, "
                f"got {self.source_id!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.kinetic_measurement_id is None:
                raise ValueError("CREATED requires kinetic_measurement_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.kinetic_measurement_id is None:
                raise ValueError("REUSED_EXISTING requires kinetic_measurement_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.kinetic_measurement_id is not None:
                raise ValueError("FAILED must not carry kinetic_measurement_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["KineticMeasurementPersistenceResult"]
