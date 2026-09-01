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

# Verbatim from docs/02_database_schema.md, "Initial Seed Data".
_STANDARD_COMPARTMENT_NAMES = {
    "cytosol",
    "mitochondrial matrix",
    "mitochondrial intermembrane space",
    "mitochondrial inner membrane",
    "mitochondrial outer membrane",
    "peroxisome",
    "endoplasmic reticulum",
    "Golgi",
    "lipid droplet",
    "nucleus",
    "vacuole",
    "plasma membrane",
    "extracellular",
}

_GROUP_A_TABLES = {
    "organism",
    "compound",
    "compound_synonym",
    "compartment",
    "publication",
    "experimental_condition",
}

_GROUP_B_TABLES = {
    "gene",
    "protein",
    "enzyme_complex",
    "enzyme_complex_member",
}

_GROUP_C_TABLES = {
    "reaction",
    "reaction_participant",
    "reaction_enzyme",
}

_GROUP_C_ENUM_TYPES = {
    "curation_state",
    "reaction_participant_role",
}


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


def test_0002_reference_data_revises_0001_baseline(alembic_config: Config) -> None:
    """Migration 0002 chains directly onto the Phase 1 baseline revision."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0002_reference_data")

    assert revision is not None
    assert revision.down_revision == "0001_baseline"


def test_upgrade_to_0002_creates_exactly_group_a_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0002 creates only the six Group A tables, nothing else."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0002_reference_data")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == _GROUP_A_TABLES | {"alembic_version"}

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0002_reference_data"
    finally:
        engine.dispose()


def test_upgrade_to_head_seeds_standard_compartments(
    scratch_database: str, alembic_config: Config
) -> None:
    """The 13 standard compartments are seeded with organism_id = NULL."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT name, organism_id FROM compartment")).all()
    finally:
        engine.dispose()

    assert len(rows) == 13
    assert {name for name, _ in rows} == _STANDARD_COMPARTMENT_NAMES
    assert all(organism_id is None for _, organism_id in rows)


def test_downgrade_from_head_to_0001_baseline_removes_group_a_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """A full downgrade from head leaves no domain tables, indexes, or enum
    types from Group A, B, or C."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0001_baseline")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == {"alembic_version"}

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0001_baseline"

            # Neither Group A nor Group B introduces a native enum type.
            # Group C's two enum types (curation_state,
            # reaction_participant_role) are dropped by 0004's own
            # downgrade, so none should remain after unwinding to base.
            enum_types = connection.execute(
                text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            ).scalars().all()
            assert enum_types == []
    finally:
        engine.dispose()


def test_0003_gene_protein_complex_revises_0002_reference_data(alembic_config: Config) -> None:
    """Migration 0003 chains directly onto migration 0002."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0003_gene_protein_complex")

    assert revision is not None
    assert revision.down_revision == "0002_reference_data"


def test_upgrade_to_0003_creates_group_a_and_group_b_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0003 adds exactly the four Group B tables on top of Group A."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0003_gene_protein_complex")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == _GROUP_A_TABLES | _GROUP_B_TABLES | {"alembic_version"}

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0003_gene_protein_complex"
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0002_removes_group_b_and_c_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading from head (0004) past 0003 removes Group B and Group C,
    leaving Group A (including its seeded compartments) untouched."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0002_reference_data")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == _GROUP_A_TABLES | {"alembic_version"}

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0002_reference_data"

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)

            enum_types = connection.execute(
                text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            ).scalars().all()
            assert enum_types == []
    finally:
        engine.dispose()


def test_0004_reaction_revises_0003_gene_protein_complex(alembic_config: Config) -> None:
    """Migration 0004 chains directly onto migration 0003."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0004_reaction")

    assert revision is not None
    assert revision.down_revision == "0003_gene_protein_complex"


def test_upgrade_to_head_creates_group_c_tables_and_enum_types(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0004 adds exactly the three Group C tables and exactly the
    two enum types Group C actually uses, on top of Groups A and B."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert (
                tables
                == _GROUP_A_TABLES | _GROUP_B_TABLES | _GROUP_C_TABLES | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0004_reaction"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0003_removes_only_group_c_tables_and_enum_types(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading past 0004 removes only Group C's tables and the two enum
    types it introduced, leaving Groups A and B completely intact."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0003_gene_protein_complex")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == _GROUP_A_TABLES | _GROUP_B_TABLES | {"alembic_version"}

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0003_gene_protein_complex"

            enum_types = connection.execute(
                text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            ).scalars().all()
            assert enum_types == []

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)
    finally:
        engine.dispose()
