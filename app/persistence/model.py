"""Model persistence.

A later calibration or modification never overwrites a prior ``Model`` row
-- this module only ever creates new rows (linked via ``parent_model_id``
when the caller supplies one), never updates an existing one, so model
lineage stays reproducible by construction.

When the caller does not supply ``stable_model_identifier``,
``app.persistence.model_id_allocator.allocate_model_stable_identifier``
generates one (sequence-based, globally unique). When the caller does
supply one, it participates in ordinary idempotent reuse: persisting the
identical ``stable_model_identifier`` twice reuses the existing row rather
than creating a second one -- the same "a caller-supplied identifier still
participates in normal reuse" policy as ``Network.external_id``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import ModelSourceType, ModelStatus
from app.models.model import Model
from app.persistence.model_id_allocator import allocate_model_stable_identifier
from app.persistence.model_types import ModelPersistenceResult
from app.persistence.types import PersistenceAction


def _existing_by_stable_model_identifier(
    session: Session, stable_model_identifier: str
) -> Model | None:
    return session.execute(
        select(Model).where(Model.stable_model_identifier == stable_model_identifier)
    ).scalar_one_or_none()


def persist_model(
    *,
    subproject_id: UUID,
    name: str,
    source_type: ModelSourceType,
    session: Session,
    stable_model_identifier: str | None = None,
    status: ModelStatus = ModelStatus.DRAFT,
    parent_model_id: UUID | None = None,
    version_label: str | None = None,
    executable_format: str | None = None,
    notes: str | None = None,
) -> ModelPersistenceResult:
    """Persist one ``Model``. See module docstring for identifier allocation
    and lineage policy.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("persist_model requires a non-empty name")

    if stable_model_identifier is not None:
        existing = _existing_by_stable_model_identifier(session, stable_model_identifier)
        if existing is not None:
            return ModelPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                model_id=existing.id,
                created=False,
                reused_existing=True,
                stable_model_identifier=stable_model_identifier,
                reason=(
                    f"reused existing model {existing.id} with the identical "
                    "stable_model_identifier"
                ),
            )

    resolved_identifier = stable_model_identifier or allocate_model_stable_identifier(session)

    try:
        with session.begin_nested():
            row = Model(
                subproject_id=subproject_id,
                stable_model_identifier=resolved_identifier,
                name=name,
                source_type=source_type,
                status=status,
                parent_model_id=parent_model_id,
                version_label=version_label,
                executable_format=executable_format,
                notes=notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_by_stable_model_identifier(session, resolved_identifier)
        if raced_existing is not None:
            return ModelPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                model_id=raced_existing.id,
                created=False,
                reused_existing=True,
                stable_model_identifier=resolved_identifier,
                reason=f"reused existing model {raced_existing.id} after a concurrent insert won",
            )
        return ModelPersistenceResult(
            action=PersistenceAction.FAILED,
            model_id=None,
            created=False,
            reused_existing=False,
            stable_model_identifier=resolved_identifier,
            reason=(
                "model creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return ModelPersistenceResult(
        action=PersistenceAction.CREATED,
        model_id=row.id,
        created=True,
        reused_existing=False,
        stable_model_identifier=resolved_identifier,
        reason="created new model",
    )


def get_model(session: Session, model_id: UUID) -> Model | None:
    """One ``Model`` row by id, or ``None``. Read-only."""
    return session.get(Model, model_id)


__all__ = ["get_model", "persist_model"]
