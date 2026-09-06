"""Read-only ``ReviewEvent`` history retrieval.

No write of any kind occurs here -- this module only issues a ``SELECT``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review_event import ReviewEvent


def get_review_history(
    session: Session, *, entity_type: str, entity_id: UUID
) -> tuple[ReviewEvent, ...]:
    """Every ``ReviewEvent`` for one entity, oldest first.

    Ordered by ``created_at`` ascending, with ``id`` as a secondary sort key
    purely to make the query's result order stable and reproducible when two
    rows share an identical ``created_at`` -- **not** because ``id`` (a
    random UUID) reflects true chronological order. ``ReviewEvent`` has no
    monotonic sequence/version column, so a genuine tie (for example, two
    machine-review transitions committed inside the same database
    transaction, where PostgreSQL's ``now()`` returns one fixed value for
    the whole transaction) cannot be broken correctly by this module --
    disclosed as an architecture limitation in
    ``docs/16_review_workflow_contract.md`` rather than worked around by
    inventing a new column. In normal use this never arises: every
    ``app.review.workflow`` call creates at most one ``ReviewEvent``.
    """
    rows = session.execute(
        select(ReviewEvent)
        .where(ReviewEvent.entity_type == entity_type, ReviewEvent.entity_id == entity_id)
        .order_by(ReviewEvent.created_at.asc(), ReviewEvent.id.asc())
    ).scalars().all()
    return tuple(rows)


__all__ = ["get_review_history"]
