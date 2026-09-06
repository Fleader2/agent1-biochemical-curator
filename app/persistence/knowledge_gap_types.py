"""Data contract for KnowledgeGap persistence (Increment 22).

Two types:

1. ``KnowledgeGapStatus`` -- the controlled vocabulary
   ``persist_knowledge_gap`` writes into ``KnowledgeGap.status``.
   ``KnowledgeGap.status`` itself remains a plain ``VARCHAR`` with no
   database ``CHECK`` (see ``app.models.knowledge_gap``'s own docstring for
   why: this is a project-invented workflow vocabulary with no
   authoritative specification, unlike ``ClaimStatus``/``CurationState``,
   so the vocabulary is enforced here, at the persistence-API boundary,
   not in the schema). A local-only ``StrEnum``, the same "vocabulary
   exists in code, not in the database" pattern
   ``app.extraction.types.Directness``/``app.review.types.ReviewerType``
   already use.
2. ``KnowledgeGapPersistenceResult`` -- the outcome of one
   ``app.persistence.knowledge_gap.persist_knowledge_gap`` call. Reuses
   ``app.persistence.types.PersistenceAction`` directly (``CREATED``/
   ``REUSED_EXISTING``/``REQUIRES_REVIEW``/``FAILED``) rather than
   inventing a parallel action vocabulary, but defines its own invariants
   -- unlike the generic ``PersistenceResult``, ``REQUIRES_REVIEW`` here
   always carries the id of the concrete, already-existing terminal-status
   row that caused it (see module docstring in
   ``app.persistence.knowledge_gap`` for exactly when that occurs).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.persistence.types import PersistenceAction


class KnowledgeGapStatus(StrEnum):
    """The lifecycle states ``persist_knowledge_gap`` accepts and enforces.

    ``OPEN`` is the default for every newly created gap. ``RESOLVED``/
    ``DISMISSED`` are terminal: a repeat persistence call for the same
    deterministic gap identity never silently reopens or duplicates one of
    these (see ``app.persistence.knowledge_gap``'s own docstring, "terminal
    gap recurrence").
    """

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


#: Statuses a repeat persistence call must never silently reopen or duplicate.
TERMINAL_KNOWLEDGE_GAP_STATUSES: frozenset[str] = frozenset(
    {KnowledgeGapStatus.RESOLVED.value, KnowledgeGapStatus.DISMISSED.value}
)


@dataclass(frozen=True, slots=True)
class KnowledgeGapPersistenceResult:
    """The outcome of one ``persist_knowledge_gap`` call.

    * ``CREATED`` -- a new ``KnowledgeGap`` row was inserted.
      ``knowledge_gap_id`` is its id; ``created=True``.
    * ``REUSED_EXISTING`` -- a row with the identical ``identity_key``
      already existed and was not ``RESOLVED``/``DISMISSED``.
      ``knowledge_gap_id`` is the existing row's id; ``reused_existing=True``.
      Its structured fields are never overwritten (Increment 22
      instructions, Step 26).
    * ``REQUIRES_REVIEW`` -- a row with the identical ``identity_key``
      already exists but its ``status`` is ``RESOLVED``/``DISMISSED``.
      ``knowledge_gap_id`` is that existing (terminal) row's id;
      ``review_required=True``. Never automatically reopened.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could
      be found on the post-failure recheck (an unexpected condition; see
      ``app.persistence.knowledge_gap`` for when this can arise).
      ``knowledge_gap_id`` is ``None``.
    """

    action: PersistenceAction
    knowledge_gap_id: UUID | None
    created: bool
    reused_existing: bool
    identity_key: str
    review_required: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.REQUIRES_REVIEW,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"KnowledgeGapPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.identity_key, str) or not self.identity_key.strip():
            raise ValueError(
                f"KnowledgeGapPersistenceResult.identity_key must be a non-empty string, "
                f"got {self.identity_key!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.knowledge_gap_id is None:
                raise ValueError("CREATED requires knowledge_gap_id")
            if not self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "CREATED requires created=True, reused_existing=False, review_required=False"
                )
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.knowledge_gap_id is None:
                raise ValueError("REUSED_EXISTING requires knowledge_gap_id")
            if self.created or not self.reused_existing or self.review_required:
                raise ValueError(
                    "REUSED_EXISTING requires created=False, reused_existing=True, "
                    "review_required=False"
                )
        elif self.action is PersistenceAction.REQUIRES_REVIEW:
            if self.knowledge_gap_id is None:
                raise ValueError("REQUIRES_REVIEW requires knowledge_gap_id")
            if self.created or self.reused_existing or not self.review_required:
                raise ValueError(
                    "REQUIRES_REVIEW requires created=False, reused_existing=False, "
                    "review_required=True"
                )
        else:  # FAILED
            if self.knowledge_gap_id is not None:
                raise ValueError("FAILED must not carry knowledge_gap_id")
            if self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "FAILED requires created=False, reused_existing=False, review_required=False"
                )


__all__ = [
    "TERMINAL_KNOWLEDGE_GAP_STATUSES",
    "KnowledgeGapPersistenceResult",
    "KnowledgeGapStatus",
]
