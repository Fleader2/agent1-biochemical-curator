"""Data contract for ``Model`` persistence. See ``app.persistence.project_types``
for why this reuses ``PersistenceAction`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ModelPersistenceResult:
    """The outcome of one ``persist_model`` call, keyed by
    ``Model.stable_model_identifier`` (globally unique).
    """

    action: PersistenceAction
    model_id: UUID | None
    created: bool
    reused_existing: bool
    stable_model_identifier: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"ModelPersistenceResult.action must be one of {valid_actions}, got {self.action!r}"
            )
        if (
            not isinstance(self.stable_model_identifier, str)
            or not self.stable_model_identifier.strip()
        ):
            raise ValueError(
                "ModelPersistenceResult.stable_model_identifier must be a non-empty string"
            )

        if self.action is PersistenceAction.CREATED:
            if self.model_id is None:
                raise ValueError("CREATED requires model_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.model_id is None:
                raise ValueError("REUSED_EXISTING requires model_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.model_id is not None:
                raise ValueError("FAILED must not carry model_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["ModelPersistenceResult"]
