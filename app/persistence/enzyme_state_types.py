"""Data contract for enzyme regulatory state persistence (Agent 1.x Increment B).

Reuses ``app.persistence.types.PersistenceAction`` directly (``CREATED``/
``REUSED_EXISTING``/``FAILED``) rather than inventing a parallel action
vocabulary, mirroring ``app.persistence.knowledge_gap_types``. The generic
``app.persistence.types.PersistenceResult`` is deliberately **not** reused
here: it requires a ``NormalizationStatus``, which presumes a genuine
MATCHED/NEW/AMBIGUOUS/CONFLICTED decision was made against fetched
candidates -- but every table ``app.persistence.enzyme_state`` writes to is
content-addressable (identical curated content always yields the identical
``identity_key``), so there is no normalization decision to report at all,
only a persistence outcome. One shared result type serves all four tables
(``entity_type`` distinguishes them), since all four share the identical
CREATED/REUSED_EXISTING/FAILED shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class EnzymeRegulatoryPersistenceResult:
    """The outcome of one ``app.persistence.enzyme_state.persist_*`` call.

    * ``CREATED`` -- a new row was inserted. ``entity_id`` is its id;
      ``created=True``.
    * ``REUSED_EXISTING`` -- a row with the identical ``identity_key``
      already existed. ``entity_id`` is the existing row's id;
      ``reused=True``. Its columns are never rewritten.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row
      could be found on recheck (an unexpected condition). ``entity_id``
      is ``None``.
    """

    action: PersistenceAction
    entity_type: str
    entity_id: UUID | None
    created: bool
    reused: bool
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
                f"EnzymeRegulatoryPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.entity_type, str) or not self.entity_type.strip():
            raise ValueError(
                f"EnzymeRegulatoryPersistenceResult.entity_type must be a non-empty string, "
                f"got {self.entity_type!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.entity_id is None:
                raise ValueError("CREATED requires entity_id")
            if not self.created or self.reused:
                raise ValueError("CREATED requires created=True, reused=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.entity_id is None:
                raise ValueError("REUSED_EXISTING requires entity_id")
            if self.created or not self.reused:
                raise ValueError("REUSED_EXISTING requires created=False, reused=True")
        else:  # FAILED
            if self.entity_id is not None:
                raise ValueError("FAILED must not carry entity_id")
            if self.created or self.reused:
                raise ValueError("FAILED requires created=False, reused=False")


__all__ = ["EnzymeRegulatoryPersistenceResult"]
