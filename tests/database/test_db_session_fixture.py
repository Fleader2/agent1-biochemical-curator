"""Tests for the ``db_session`` fixture's transaction-isolation contract.

Added alongside a fixture change in ``tests/conftest.py``: a flush-time
``IntegrityError`` followed by ``Session.rollback()`` used to deassociate the
fixture's outer connection transaction, producing an ``SAWarning`` at
teardown (previously observed via ``tests/database/test_group_a_models.py``).
``Session.rollback()`` in this SQLAlchemy version fully ends the session's
current transaction — including its join to that outer transaction — rather
than unwinding only to the nearest SAVEPOINT, so the fix is for teardown to
only roll back the outer transaction when it is still active, relying on
``connection.close()`` to clean up whichever transaction (original or
freshly auto-begun) is open by then. These tests exercise the fixture itself
rather than any application/scientific behavior.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.organism import Organism

pytestmark = pytest.mark.database


def test_session_recovers_and_stays_usable_after_integrity_error(db_session):
    """After a flush failure and ``rollback()``, the session accepts new,
    valid work without raising and without warning at teardown."""
    db_session.add(Organism(scientific_name="test-only fixture species", strain="A"))
    db_session.flush()

    db_session.add(Organism(scientific_name="test-only fixture species", strain="A"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    second = Organism(scientific_name="test-only fixture species", strain="B")
    db_session.add(second)
    db_session.flush()  # must not raise

    assert db_session.get(Organism, second.id) is not None


def test_nested_savepoint_protects_prior_work_from_a_later_failure(db_session):
    """A test that needs to keep earlier flushed data across a later,
    expected failure should open its own nested SAVEPOINT around the risky
    operation: the fixture's own SAVEPOINT covers the whole test, so a bare
    ``rollback()`` after a failure discards everything flushed since the test
    began, not just the failing statement.
    """
    first = Organism(scientific_name="test-only protected species", strain="A")
    db_session.add(first)
    db_session.flush()

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Organism(scientific_name="test-only protected species", strain="A"))
        db_session.flush()

    # first was flushed before the nested SAVEPOINT opened, so it survives
    # the SAVEPOINT-scoped rollback triggered by the failure above.
    assert db_session.get(Organism, first.id) is not None

    second = Organism(scientific_name="test-only protected species", strain="B")
    db_session.add(second)
    db_session.flush()  # must not raise
    assert db_session.get(Organism, second.id) is not None


def test_session_recovers_from_repeated_integrity_errors(db_session):
    """The session keeps recovering correctly across more than one
    failure+rollback cycle in the same test, not just the first.

    Each cycle establishes its own base row rather than reusing one across
    cycles: a bare ``rollback()`` clears everything flushed since the
    transaction began, including rows inserted in an earlier cycle in this
    same test (see ``test_nested_savepoint_protects_prior_work_from_a_later_failure``
    for the pattern to use when a test needs to keep earlier work instead).
    """
    for i in range(3):
        name = f"test-only repeat species {i}"
        db_session.add(Organism(scientific_name=name, strain="A"))
        db_session.flush()

        db_session.add(Organism(scientific_name=name, strain="A"))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    final = Organism(scientific_name="test-only repeat species final", strain="Z")
    db_session.add(final)
    db_session.flush()  # must not raise after three failure+rollback cycles

    assert db_session.get(Organism, final.id) is not None


def test_failed_insert_in_one_test_is_not_visible_in_another(db_session):
    """Isolation check: nothing persisted (successfully or otherwise) by the
    tests above leaks into this one, since each gets a fresh fixture instance
    rolled back to the outer transaction's starting point."""
    rows = (
        db_session.execute(
            select(Organism).where(Organism.scientific_name.like("test-only%species"))
        )
        .scalars()
        .all()
    )
    assert rows == []
