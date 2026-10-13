"""Data contract for ``Subproject`` persistence. See ``app.persistence.project_types``
for why this reuses ``PersistenceAction`` directly rather than
``NormalizationResult``/``PersistenceResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class SubprojectPersistenceResult:
    """The outcome of one ``persist_subproject`` call, keyed by
    ``(project_id, title)`` -- the same natural key as ``uq_subproject_project_id_title``.
    """

    action: PersistenceAction
    subproject_id: UUID | None
    created: bool
    reused_existing: bool
    project_id: UUID
    title: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"SubprojectPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("SubprojectPersistenceResult.title must be a non-empty string")

        if self.action is PersistenceAction.CREATED:
            if self.subproject_id is None:
                raise ValueError("CREATED requires subproject_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.subproject_id is None:
                raise ValueError("REUSED_EXISTING requires subproject_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.subproject_id is not None:
                raise ValueError("FAILED must not carry subproject_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["SubprojectPersistenceResult"]
