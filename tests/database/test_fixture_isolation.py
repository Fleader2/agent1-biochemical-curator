"""Regression tests for test-database isolation (``docs/29_test_database_isolation.md``).

Confirms the root-cause fix for persistent-state contamination in the Agent 1 test
database: ``tests.conftest.reset_test_database`` (wired into the session-scoped
``migrated_engine`` fixture) guarantees every test run starts from a clean,
deterministic baseline, regardless of what any prior connection -- a concurrency test
using two real connections, a one-off script, a previous pytest invocation entirely --
already committed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.models.organism import Organism
from tests.conftest import reset_test_database

pytestmark = pytest.mark.database


def test_reset_test_database_clears_rows_committed_outside_db_session(
    scratch_database: str, alembic_config: Config
) -> None:
    """A row committed on a connection entirely independent of ``db_session`` (exactly
    the shape of a concurrency test's own two real connections, or any other direct
    write) is removed by ``reset_test_database`` -- never requiring the writer's own
    cooperation or cleanup to have run."""
    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    reset_test_database(alembic_config)  # first pass: establish the clean baseline

    engine = create_engine(scratch_database)
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO organism (id, scientific_name) "
                "VALUES (gen_random_uuid(), 'contamination-test organism')"
            )
        )
        connection.commit()  # a real, independent commit -- db_session is never involved

    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM organism")).scalar() == 1

        reset_test_database(alembic_config)

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM organism")).scalar() == 0
    finally:
        engine.dispose()


def test_reset_test_database_preserves_migration_seeded_reference_data(
    scratch_database: str, alembic_config: Config
) -> None:
    """The 13 standard reference compartments migration ``0002_reference_data`` seeds
    are reference data, not test data -- they must survive a reset exactly as they
    survive any other ``alembic upgrade head``. (An earlier, blanket-``TRUNCATE``-based
    version of this fix discarded them; this is the regression that caught it.)"""
    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    reset_test_database(alembic_config)

    engine = create_engine(scratch_database)
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM compartment")).scalar()
    engine.dispose()

    assert count == 13


def test_reset_test_database_handles_foreign_key_dependent_chain_including_network_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """A realistic foreign-key chain -- ``organism`` -> ``network`` -> ``network_node``
    -> ``network_edge`` -- committed directly (bypassing ``db_session``, and therefore
    exactly the shape a crashed or interrupted commit-based test would leave behind) is
    fully cleared, with no foreign-key violation, regardless of insertion/deletion
    order. Exercises the KEGG NETWORK support tables specifically, per the task's own
    callout that any fix must cover them."""
    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    reset_test_database(alembic_config)

    engine = create_engine(scratch_database)
    with engine.connect() as connection:
        organism_id = connection.execute(
            text(
                "INSERT INTO organism (id, scientific_name) "
                "VALUES (gen_random_uuid(), 'fk-chain-test organism') RETURNING id"
            )
        ).scalar()
        network_id = connection.execute(
            text(
                "INSERT INTO network "
                "(id, source_type, external_id, name, network_type, organism_id) "
                "VALUES (gen_random_uuid(), 'KEGG', 'N00001-fk-chain-test', "
                "'fk-chain-test network', 'REFERENCE', :organism_id) RETURNING id"
            ),
            {"organism_id": organism_id},
        ).scalar()
        node_a_id = connection.execute(
            text(
                "INSERT INTO network_node "
                "(id, network_id, node_type, mapping_status, raw_token) "
                "VALUES (gen_random_uuid(), :network_id, 'UNKNOWN', 'UNRESOLVED', 'A') "
                "RETURNING id"
            ),
            {"network_id": network_id},
        ).scalar()
        node_b_id = connection.execute(
            text(
                "INSERT INTO network_node "
                "(id, network_id, node_type, mapping_status, raw_token) "
                "VALUES (gen_random_uuid(), :network_id, 'UNKNOWN', 'UNRESOLVED', 'B') "
                "RETURNING id"
            ),
            {"network_id": network_id},
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO network_edge "
                "(id, network_id, source_node_id, target_node_id, interaction_type, "
                "raw_operator) "
                "VALUES (gen_random_uuid(), :network_id, :source_id, :target_id, "
                "'UNKNOWN', '->')"
            ),
            {"network_id": network_id, "source_id": node_a_id, "target_id": node_b_id},
        )
        connection.commit()

        expected_before_reset = {
            "organism": 1,
            "network": 1,
            "network_node": 2,  # node_a and node_b
            "network_edge": 1,
        }
        for table, expected in expected_before_reset.items():
            count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            assert count == expected, (
                f"{table} should have exactly {expected} row(s) before reset, got {count}"
            )

    try:
        reset_test_database(alembic_config)

        with engine.connect() as connection:
            for table in expected_before_reset:
                count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                assert count == 0, f"{table} should be empty after reset, got {count}"
    finally:
        # Guarantee the scratch database's own connection pool is released even if an
        # assertion above fails -- otherwise the `scratch_database` fixture's own
        # teardown (DROP DATABASE) fails with "database is being accessed by other
        # users", masking this test's real failure with an unrelated one.
        engine.dispose()


_ISOLATION_MARKER_NAME = "db-session-isolation-marker-organism"


def test_db_session_isolation_data_inserted_in_one_test_is_absent_from_the_next_a(
    db_session,
) -> None:
    """Paired with the ``_b`` test below: inserts a uniquely-named organism via the
    ordinary ``db_session`` fixture, deliberately never committing or rolling back
    itself -- isolation must come entirely from the fixture's own teardown."""
    db_session.add(Organism(scientific_name=_ISOLATION_MARKER_NAME))
    db_session.flush()
    rows = db_session.query(Organism).filter(Organism.scientific_name == _ISOLATION_MARKER_NAME)
    assert rows.count() == 1


def test_db_session_isolation_data_inserted_in_one_test_is_absent_from_the_next_b(
    db_session,
) -> None:
    """Runs after the ``_a`` test above (default pytest file-order collection) on a
    *fresh* ``db_session`` -- if the previous test's insert had leaked, it would be
    visible here."""
    rows = db_session.query(Organism).filter(Organism.scientific_name == _ISOLATION_MARKER_NAME)
    assert rows.count() == 0


def test_db_session_rollback_isolates_even_an_uncommitted_write_from_other_connections(
    db_session, migrated_engine: Engine
) -> None:
    """``db_session`` never commits (see its own docstring) -- a write made through it
    is invisible to a totally independent connection *immediately*, not only after
    teardown. This is the mechanism that makes "cleanup still occurs after a test
    raises an exception" true for every ordinary (non-concurrency) test: there is
    nothing *to* clean up outside the one connection a raising test's own ``db_session``
    already owns, and that connection's transaction is rolled back unconditionally in
    the fixture's own ``finally`` block regardless of how the test ends."""
    marker_id = uuid4()
    db_session.add(Organism(id=marker_id, scientific_name=f"rollback-visibility-{marker_id}"))
    db_session.flush()

    with migrated_engine.connect() as other_connection:
        visible = other_connection.execute(
            text("SELECT count(*) FROM organism WHERE id = :id"), {"id": str(marker_id)}
        ).scalar()
    assert visible == 0, "an uncommitted db_session write must not be visible on another connection"


def test_contamination_does_not_persist_across_separate_pytest_invocations(
    scratch_database: str, alembic_config: Config
) -> None:
    """Simulates "a separate pytest run": a fresh ``Config``/engine pointed at a
    database that already has committed contamination from some *prior* process (the
    scenario that matches how the real ``agent1_test`` contamination was discovered --
    real data left behind by a run this suite had no part in). The same
    ``reset_test_database`` call ``migrated_engine`` makes at the start of every
    session clears it deterministically, exactly as it would at the start of any new
    pytest invocation against this same physical database."""
    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    reset_test_database(alembic_config)

    # Simulate contamination left behind by some entirely separate, already-finished
    # process -- not this test's own fixtures.
    engine = create_engine(scratch_database)
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO network (id, source_type, external_id, name, network_type) "
                "VALUES (gen_random_uuid(), 'KEGG', 'N-leftover', 'leftover network', "
                "'REFERENCE')"
            )
        )
        connection.commit()
    engine.dispose()

    # A brand-new engine/config, as a fresh pytest session's own migrated_engine
    # fixture would build -- never reusing the connection/session above.
    fresh_engine = create_engine(scratch_database)
    try:
        reset_test_database(alembic_config)
        with fresh_engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM network")).scalar() == 0
    finally:
        fresh_engine.dispose()
