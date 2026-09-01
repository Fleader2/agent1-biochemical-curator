"""Migration tests.

Proves that a completely empty PostgreSQL database can be migrated to head, which
``docs/05_testing.md`` requires of continuous integration.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text

pytestmark = pytest.mark.database


def _head_revision(alembic_config: Config) -> str:
    script = ScriptDirectory.from_config(alembic_config)
    head = script.get_current_head()
    assert head is not None
    return head


def test_upgrade_empty_database_to_head(scratch_database: str, alembic_config: Config) -> None:
    """An empty database migrates to head and records the head revision."""
    from alembic import command

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == []

        alembic_config.set_main_option("sqlalchemy.url", scratch_database)
        command.upgrade(alembic_config, "head")

        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            assert "alembic_version" in tables

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == _head_revision(alembic_config)
    finally:
        engine.dispose()


def test_downgrade_to_base_after_upgrade(scratch_database: str, alembic_config: Config) -> None:
    """Migrations are reversible down to base."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored is None
    finally:
        engine.dispose()


def test_revision_history_is_linear(alembic_config: Config) -> None:
    """A single head keeps migration ordering unambiguous."""
    script = ScriptDirectory.from_config(alembic_config)

    assert len(script.get_heads()) == 1


def test_session_fixture_connects_to_test_database(db_session, migrated_engine: Engine) -> None:
    """The session fixture is bound to the migrated test database."""
    assert db_session.execute(text("SELECT 1")).scalar() == 1
    assert db_session.get_bind().engine is migrated_engine
