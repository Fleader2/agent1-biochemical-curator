"""Read-only ``ExperimentRecommendationEvent`` history retrieval.

No write of any kind occurs here -- this module only issues a ``SELECT``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.experiment_recommendation import ExperimentRecommendationEvent


def get_experiment_recommendation_history(
    session: Session, recommendation_id: UUID
) -> tuple[ExperimentRecommendationEvent, ...]:
    """Every lifecycle event for one recommendation, oldest first.

    Ordered by ``created_at`` ascending, with ``id`` as a secondary sort
    key purely to make the query's result order stable and reproducible
    when two rows share an identical ``created_at`` -- the same
    stable-not-chronological tiebreak convention
    ``app.review.history.get_review_history`` already documents and uses.
    """
    rows = (
        session.execute(
            select(ExperimentRecommendationEvent)
            .where(ExperimentRecommendationEvent.recommendation_id == recommendation_id)
            .order_by(
                ExperimentRecommendationEvent.created_at.asc(),
                ExperimentRecommendationEvent.id.asc(),
            )
        )
        .scalars()
        .all()
    )
    return tuple(rows)


__all__ = ["get_experiment_recommendation_history"]
