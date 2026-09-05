"""Internal helper: persistence-time freshness recheck before creating a ``NEW`` row.

Private to ``app.persistence`` -- see the package docstring's "Stale-``NEW``-
result safety" section for why every entity module's ``NEW`` path calls
this immediately before inserting.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session


def any_row_matches(
    session: Session, id_column: ColumnElement, conditions: Sequence[ColumnElement[bool]]
) -> bool:
    """``True`` if any one of ``conditions`` currently matches an existing row.

    Each condition is checked independently with its own query (mirroring
    how ``app.normalization.*`` reconciles multiple strong anchors
    independently rather than combining them into one query) -- the first
    match found short-circuits the rest.
    """
    for condition in conditions:
        if session.execute(select(id_column).where(condition).limit(1)).first() is not None:
            return True
    return False


__all__ = ["any_row_matches"]
