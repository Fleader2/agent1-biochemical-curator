"""Data contracts for ``ModelSpecification`` persistence (the header row and
its eight child association tables). See ``app.persistence.project_types``
for why these reuse ``PersistenceAction`` directly rather than
``NormalizationResult``/``PersistenceResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ModelSpecificationPersistenceResult:
    """The outcome of one ``persist_model_specification`` call, keyed by
    ``ModelSpecification.subproject_id`` (one specification per subproject in
    this increment).

    ``REUSED_EXISTING`` never touches an existing specification's own
    ``notes`` field or any of its already-persisted child rows -- the same
    "reuse never touches existing content" policy as
    ``app.persistence.project``.
    """

    action: PersistenceAction
    model_specification_id: UUID | None
    created: bool
    reused_existing: bool
    subproject_id: UUID
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                "ModelSpecificationPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.model_specification_id is None:
                raise ValueError("CREATED requires model_specification_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.model_specification_id is None:
                raise ValueError("REUSED_EXISTING requires model_specification_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.model_specification_id is not None:
                raise ValueError("FAILED must not carry model_specification_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


@dataclass(frozen=True, slots=True)
class ModelSpecificationChildPersistenceResult:
    """The outcome of one add-one-child-row call (species/reaction/gene_protein/
    compartment/regulatory_interaction/enzyme_state/pathway), keyed by the
    full set of fields that identify that one row -- a repeat call with the
    identical fields reuses the existing row rather than duplicating it.

    One generic result shape serves all seven child tables: each carries the
    same action/created/reused_existing/id shape, differing only in which
    table ``child_id`` refers to (named by ``table_name``).
    """

    action: PersistenceAction
    child_id: UUID | None
    created: bool
    reused_existing: bool
    table_name: str
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                "ModelSpecificationChildPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )
        if not isinstance(self.table_name, str) or not self.table_name.strip():
            raise ValueError(
                "ModelSpecificationChildPersistenceResult.table_name must be non-empty"
            )

        if self.action is PersistenceAction.CREATED:
            if self.child_id is None:
                raise ValueError("CREATED requires child_id")
            if not self.created or self.reused_existing:
                raise ValueError("CREATED requires created=True, reused_existing=False")
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.child_id is None:
                raise ValueError("REUSED_EXISTING requires child_id")
            if self.created or not self.reused_existing:
                raise ValueError("REUSED_EXISTING requires created=False, reused_existing=True")
        else:  # FAILED
            if self.child_id is not None:
                raise ValueError("FAILED must not carry child_id")
            if self.created or self.reused_existing:
                raise ValueError("FAILED requires created=False, reused_existing=False")


__all__ = ["ModelSpecificationChildPersistenceResult", "ModelSpecificationPersistenceResult"]
