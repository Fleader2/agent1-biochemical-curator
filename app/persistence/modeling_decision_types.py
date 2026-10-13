"""Data contracts for ``ModelingDecision``/``ModelingDecisionOption`` persistence.
See ``app.persistence.project_types`` for why these reuse ``PersistenceAction``
directly rather than ``NormalizationResult``/``PersistenceResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ModelingDecisionPersistenceResult:
    """The outcome of one ``persist_modeling_decision`` call, keyed by
    ``(project_id, subproject_id, model_id, question)``.
    """

    action: PersistenceAction
    decision_id: UUID | None
    created: bool
    reused_existing: bool
    question: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                "ModelingDecisionPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError(
                "ModelingDecisionPersistenceResult.question must be a non-empty string"
            )

        if self.action is PersistenceAction.CREATED:
            if self.decision_id is None:
                raise ValueError("CREATED requires decision_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.decision_id is None:
                raise ValueError("REUSED_EXISTING requires decision_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.decision_id is not None:
                raise ValueError("FAILED must not carry decision_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


@dataclass(frozen=True, slots=True)
class ModelingDecisionOptionPersistenceResult:
    """The outcome of one ``add_modeling_decision_option`` call, keyed by
    ``(decision_id, label)``.
    """

    action: PersistenceAction
    option_id: UUID | None
    created: bool
    reused_existing: bool
    label: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                "ModelingDecisionOptionPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError(
                "ModelingDecisionOptionPersistenceResult.label must be a non-empty string"
            )

        if self.action is PersistenceAction.CREATED:
            if self.option_id is None:
                raise ValueError("CREATED requires option_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.option_id is None:
                raise ValueError("REUSED_EXISTING requires option_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.option_id is not None:
                raise ValueError("FAILED must not carry option_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["ModelingDecisionOptionPersistenceResult", "ModelingDecisionPersistenceResult"]
