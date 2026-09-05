"""Shared persistence outcome types.

``app.normalization``'s ``NormalizationStatus`` answers "what is this
record's identity?". ``PersistenceAction`` answers a different question --
"what did the database actually do about it?" -- and the two are
deliberately kept as separate enums so a normalization verdict is never
confused with a database write action. A ``MATCHED`` normalization result,
for instance, always persists as ``REUSED_EXISTING``, never ``CREATED`` --
conflating the two would make it possible to accidentally treat "the
identity was recognized" as if it meant "a row was inserted."

Nothing here decides identity. Persistence applies only the database action
a normalization decision already permits (see each ``app.persistence.*``
module's docstring for the exact status -> action mapping); it never
re-runs identity inference or overrides what normalization already decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.normalization.types import NormalizationStatus


class PersistenceAction(StrEnum):
    """What the database actually did, as opposed to what identity was decided.

    Deliberately distinct from ``NormalizationStatus`` -- see module
    docstring.
    """

    CREATED = "CREATED"
    REUSED_EXISTING = "REUSED_EXISTING"
    NO_ACTION = "NO_ACTION"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    """The outcome of applying one normalization decision to the database.

    Invariants (enforced in ``__post_init__``, not just documented):

    * ``CREATED``/``REUSED_EXISTING`` always carry ``entity_id`` -- an
      action was actually taken (or an existing row recognized) that
      resolves to one specific row.
    * ``NO_ACTION``/``REQUIRES_REVIEW``/``FAILED`` never carry
      ``entity_id`` -- no single row was created or matched as a result of
      *this* persistence call. (A ``CONFLICTED``/``AMBIGUOUS``
      normalization result's own ``matched_entity_id``/
      ``candidate_entity_ids`` remain available on that result for a
      caller that needs them; this type does not duplicate them.)
    * ``created``/``reused`` are mutually exclusive, and both are ``False``
      unless ``action`` is ``CREATED``/``REUSED_EXISTING`` respectively.
    * ``source_cross_reference_id``/``external_record_id`` may be set on
      any action -- provenance may be recorded even when no biological
      entity was created (see ``app.persistence.provenance``).
    """

    normalization_status: NormalizationStatus
    action: PersistenceAction
    entity_type: str
    entity_id: UUID | None = None
    created: bool = False
    reused: bool = False
    review_required: bool = False
    source_cross_reference_id: UUID | None = None
    external_record_id: UUID | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.entity_type.strip():
            raise ValueError("entity_type must not be empty")

        if self.action is PersistenceAction.CREATED:
            if self.entity_id is None:
                raise ValueError("CREATED requires entity_id")
            if not self.created or self.reused:
                raise ValueError("CREATED requires created=True and reused=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.entity_id is None:
                raise ValueError("REUSED_EXISTING requires entity_id")
            if not self.reused or self.created:
                raise ValueError("REUSED_EXISTING requires reused=True and created=False")
        else:
            if self.entity_id is not None:
                raise ValueError(f"{self.action} must not carry entity_id")
            if self.created or self.reused:
                raise ValueError(f"{self.action} must not set created/reused")

        if self.action is PersistenceAction.REQUIRES_REVIEW and not self.review_required:
            raise ValueError("REQUIRES_REVIEW requires review_required=True")


__all__ = ["PersistenceAction", "PersistenceResult"]
