"""Production-safe allocation of ``Reaction.internal_id``.

**Mechanism: a PostgreSQL sequence (``reaction_internal_id_seq``, created by
migration ``0009_persistence_hardening``).** ``allocate_reaction_internal_id``
calls ``nextval()`` on it and formats the result as ``FFA_R####`` (e.g.
``FFA_R0001``), the exact format documented in
``docs/02_database_schema.md``'s "Internal ID format" section and used
throughout ``docs/04_api_spec.md``/``docs/06_export_format.md``.

**Why a sequence, and not the alternatives this increment was explicitly
told to avoid:**

* Not ``SELECT MAX(...) + 1``: reading the current maximum and computing
  the next value in application code is inherently racy -- two concurrent
  transactions can read the same maximum before either has inserted,
  compute the same "next" value, and either collide on the unique
  constraint (best case) or, without one, silently produce two rows
  claiming the same ``internal_id``.
* Not a process-local or ``itertools.count()``-style counter (the only
  prior precedent in this repository,
  ``tests/database/test_group_c_models.py``'s ``_internal_id()``, which is
  explicitly test-only): safe only for one Python process holding the
  entire counter in memory, never for two application server processes or
  two worker threads with independent counters.
* A PostgreSQL sequence is safe under arbitrarily many concurrent sessions
  by construction: ``nextval()`` is a single atomic, non-transactional
  operation the database itself serializes internally, with no row lock
  held for the duration of the caller's transaction and no risk of two
  callers ever observing the same value. This is the standard PostgreSQL
  mechanism for exactly this problem, and requires no additional locking
  logic in this codebase.

**Non-transactional by design, and why that is fine here.** A sequence's
``nextval()`` advance is *not* undone by ``ROLLBACK`` -- if the caller's
transaction that allocated an id is later rolled back (for example because
participant creation subsequently fails, see
``app.persistence.reaction``'s ``_create``), that allocated number is
permanently consumed and never reused. This produces gaps in the sequence,
never duplicates. Nothing in ``docs/02_database_schema.md`` requires
gapless numbering -- only "Reaction IDs must remain stable after creation"
-- so this is an accepted, harmless trade-off, not a defect.

**Format has no fixed upper bound.** ``FFA_R{n:04d}`` zero-pads to at least
four digits (``FFA_R0001`` .. ``FFA_R9999``) but does not truncate beyond
that (``FFA_R10000`` and onward remain well-formed, still globally unique,
just wider) -- no documented policy caps the count of reactions at 9999,
and inventing a hard ceiling here would be an unrequested schema decision.

**The ``FFA_`` prefix is project-scope-specific**, not a general Agent 1
convention: it names the initial yeast free-fatty-acid project
(``docs/02_database_schema.md``: "For the initial yeast free-fatty-acid
project, reaction IDs should follow: FFA_R0001..."). Whether a future,
differently-scoped project should use a different prefix -- and how that
would be configured -- is unresolved and explicitly out of scope for this
increment (see this increment's completion report); this module hard-codes
``FFA_R`` rather than inventing an unrequested configuration mechanism for
a problem that does not exist yet.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

_SEQUENCE_NAME = "reaction_internal_id_seq"
_PREFIX = "FFA_R"
_PAD_WIDTH = 4


def allocate_reaction_internal_id(session: Session) -> str:
    """Allocate the next ``Reaction.internal_id`` value, safe under concurrent writers.

    Issues ``SELECT nextval('reaction_internal_id_seq')`` on the caller's own
    session/connection -- participating in whatever transaction the caller
    already has open, per this package's transaction-ownership convention
    (``app.persistence``'s package docstring) -- and formats the result as
    ``FFA_R####``. Never queries the ``reaction`` table itself: this
    allocator has no ``MAX``-style read step of any kind (see module
    docstring).
    """
    next_value = session.execute(text(f"SELECT nextval('{_SEQUENCE_NAME}')")).scalar_one()
    return f"{_PREFIX}{next_value:0{_PAD_WIDTH}d}"


__all__ = ["allocate_reaction_internal_id"]
