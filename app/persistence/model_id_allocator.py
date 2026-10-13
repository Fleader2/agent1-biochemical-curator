"""Production-safe allocation of ``Model.stable_model_identifier``.

**Mechanism: a PostgreSQL sequence (``model_stable_identifier_seq``, created
by migration ``0020_project_subproject_model``).**
``allocate_model_stable_identifier`` calls ``nextval()`` on it and formats
the result as ``MODEL####`` (e.g. ``MODEL0001``) -- the same
sequence-based, non-transactional, gap-tolerant mechanism as
``app.persistence.reaction_id_allocator.allocate_reaction_internal_id``, for
the identical reason: a PostgreSQL sequence is safe under arbitrarily many
concurrent sessions by construction, with no ``SELECT MAX(...) + 1`` race
and no in-process counter that would only be safe for one Python process.

Unlike ``Reaction.internal_id`` (always allocated), this allocator is only
consulted when the caller does not supply its own
``stable_model_identifier`` -- a caller-supplied identifier still
participates in normal MATCHED/CONFLICTED normalization resolution, the
same way ``Network.external_id`` does.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

_SEQUENCE_NAME = "model_stable_identifier_seq"
_PREFIX = "MODEL"
_PAD_WIDTH = 4


def allocate_model_stable_identifier(session: Session) -> str:
    """Allocate the next ``Model.stable_model_identifier`` value, safe under
    concurrent writers.

    Issues ``SELECT nextval('model_stable_identifier_seq')`` on the
    caller's own session/connection, participating in whatever transaction
    the caller already has open. Never queries the ``model`` table itself.
    """
    next_value = session.execute(text(f"SELECT nextval('{_SEQUENCE_NAME}')")).scalar_one()
    return f"{_PREFIX}{next_value:0{_PAD_WIDTH}d}"


__all__ = ["allocate_model_stable_identifier"]
