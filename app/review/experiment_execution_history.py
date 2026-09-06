"""Read-only ``ExperimentExecutionEvent`` history retrieval.

No write of any kind occurs here -- this module only issues a ``SELECT``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.experiment_execution import ExperimentExecutionEvent


def get_experiment_execution_history(
    session: Session, execution_id: UUID
) -> tuple[ExperimentExecutionEvent, ...]:
    """Every lifecycle event for one execution, oldest first.

    Ordered by ``created_at`` ascending, with ``id`` as a secondary sort key
    purely to make the query's result order stable and reproducible when
    two rows share an identical ``created_at`` -- the same
    stable-not-chronological tiebreak convention
    ``app.review.experiment_recommendation_history.get_experiment_recommendation_history``
    already uses.
    """
    rows = (
        session.execute(
            select(ExperimentExecutionEvent)
            .where(ExperimentExecutionEvent.execution_id == execution_id)
            .order_by(
                ExperimentExecutionEvent.created_at.asc(),
                ExperimentExecutionEvent.id.asc(),
            )
        )
        .scalars()
        .all()
    )
    return tuple(rows)


__all__ = ["get_experiment_execution_history"]
