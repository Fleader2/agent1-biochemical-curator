"""Data contract for ``Project`` persistence.

``Project``/``Subproject``/``Model``/``ModelSpecification``/
``ModelingDecision`` are agent- or user-authored organizational records, not
entities sourced from an external curated database (KEGG, PubMed, ...).
``app.normalization.types.NormalizationResult`` requires a ``SourceType`` and
a full MATCHED/NEW/AMBIGUOUS/CONFLICTED/UNRESOLVED identity-resolution
vocabulary built for exactly that external-source case, so these five
entities do not use it -- the same "a project-invented identity needs its
own small result type, not the external-source normalization pipeline"
precedent already established by ``app.persistence.knowledge_gap_types
.KnowledgeGapPersistenceResult``. ``PersistenceAction`` (``CREATED``/
``REUSED_EXISTING``/``FAILED``) is still reused directly from
``app.persistence.types`` rather than inventing a parallel action
vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ProjectPersistenceResult:
    """The outcome of one ``persist_project`` call, keyed by ``stable_identifier``.

    * ``CREATED`` -- no existing ``Project`` shared this ``stable_identifier``; a
      new row was inserted.
    * ``REUSED_EXISTING`` -- a row with the identical ``stable_identifier``
      already existed. Its fields are never overwritten (same "reuse never
      touches existing content" policy as every other ``MATCHED``-equivalent
      persistence function in this package).
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could
      be found on the post-failure recheck.
    """

    action: PersistenceAction
    project_id: UUID | None
    created: bool
    reused_existing: bool
    stable_identifier: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"ProjectPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.stable_identifier, str) or not self.stable_identifier.strip():
            raise ValueError(
                "ProjectPersistenceResult.stable_identifier must be a non-empty string"
            )

        if self.action is PersistenceAction.CREATED:
            if self.project_id is None:
                raise ValueError("CREATED requires project_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.project_id is None:
                raise ValueError("REUSED_EXISTING requires project_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.project_id is not None:
                raise ValueError("FAILED must not carry project_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["ProjectPersistenceResult"]
