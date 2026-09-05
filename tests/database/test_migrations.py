"""Migration tests.

Proves that a completely empty PostgreSQL database can be migrated to head, which
``docs/05_testing.md`` requires of continuous integration.
"""

from __future__ import annotations

import logging

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

_GROUP_D_TABLES = {
    "claim",
    "evidence",
    "evidence_condition",
}

_GROUP_D_ENUM_TYPES = {
    "claim_status",
    "confidence_class",
    "evidence_type",
    "source_type",
}

_GROUP_E_TABLES = {
    "kinetic_measurement",
}

_GROUP_F_TABLES = {
    "regulatory_interaction",
    "modeling_assumption",
    "knowledge_gap",
}

_GROUP_F_ENUM_TYPES = {
    "regulatory_effect",
}

_GROUP_G_TABLES = {
    "external_record",
    "source_cross_reference",
    "review_event",
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


def test_alembic_upgrade_does_not_disable_preexisting_application_logger(
    scratch_database: str, alembic_config: Config
) -> None:
    """Running Alembic must not silently disable an already-registered application logger.

    ``alembic.ini`` only lists ``root``, ``sqlalchemy``, and ``alembic`` under
    ``[loggers]``. ``migrations/env.py`` calls ``logging.config.fileConfig``,
    which defaults to ``disable_existing_loggers=True`` -- that would silently
    disable any other logger already registered at the moment it runs, such as
    an application logger created by an ordinary module import (for example
    ``app.connectors.http``'s module-level logger) before any migration ever
    executes. Regression test for that behavior being turned off.
    """
    from alembic import command

    logger_name = "agent1.regression_test.preexisting_before_alembic"
    preexisting_logger = logging.getLogger(logger_name)
    assert preexisting_logger.disabled is False  # sanity: starts enabled

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")

    assert preexisting_logger.disabled is False


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
    types from Group A, B, C, D, E, F, or G."""
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
            # Group C's two enum types and Group D's four are each dropped
            # by their own migration's downgrade, so none should remain
            # after unwinding all the way to base.
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


def test_upgrade_to_0004_creates_group_c_tables_and_enum_types(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0004 adds exactly the three Group C tables and exactly the
    two enum types Group C actually uses, on top of Groups A and B."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0004_reaction")

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


def test_downgrade_from_head_to_0003_removes_group_c_and_d_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading from head (0005) past 0004 removes Group C and Group D
    and all six of their enum types, leaving Groups A and B intact."""
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


def test_0005_claim_evidence_revises_0004_reaction(alembic_config: Config) -> None:
    """Migration 0005 chains directly onto migration 0004."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0005_claim_evidence")

    assert revision is not None
    assert revision.down_revision == "0004_reaction"


def test_upgrade_to_0005_creates_group_d_tables_and_enum_types(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0005 adds exactly the three Group D tables and exactly the
    four enum types Group D actually uses, on top of Groups A, B, and C. The
    two enum types 0004 already created remain present and unchanged."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0005_claim_evidence")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0005_claim_evidence"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0004_removes_group_d_and_e_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading from head (0006) past 0005 removes Group D's tables (and
    its four enum types) and Group E's table (which introduced no enum of
    its own). Groups A, B, and C remain intact, and 0004's two enum types
    (curation_state, reaction_participant_role) survive."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0004_reaction")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES | _GROUP_B_TABLES | _GROUP_C_TABLES | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0004_reaction"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)
    finally:
        engine.dispose()


def test_0006_kinetic_measurement_revises_0005_claim_evidence(alembic_config: Config) -> None:
    """Migration 0006 chains directly onto migration 0005."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0006_kinetic_measurement")

    assert revision is not None
    assert revision.down_revision == "0005_claim_evidence"


def test_upgrade_to_0006_creates_only_kinetic_measurement_table(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0006 adds exactly the kinetic_measurement table on top of
    Groups A-D, and introduces no new enum type — it reuses confidence_class
    from 0005 rather than recreating it, and no unrelated enum type appears."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0006_kinetic_measurement")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | _GROUP_E_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0006_kinetic_measurement"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0005_removes_kinetic_measurement_and_group_f(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading from head (0007) past 0006 removes kinetic_measurement
    (no enum of its own) and Group F's three tables plus its one enum
    (regulatory_effect). All six enum types owned by 0004 and 0005 remain
    exactly as they were, and Groups A-D remain intact."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0005_claim_evidence")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0005_claim_evidence"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)
    finally:
        engine.dispose()


def test_0007_regulation_assumptions_gaps_revises_0006_kinetic_measurement(
    alembic_config: Config,
) -> None:
    """Migration 0007 chains directly onto migration 0006."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0007_regulation_assumptions_gaps")

    assert revision is not None
    assert revision.down_revision == "0006_kinetic_measurement"


def test_upgrade_to_0007_creates_group_f_tables_and_enum_type(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0007 adds exactly the three Group F tables and exactly the
    one new enum type (regulatory_effect) it genuinely needs, on top of
    Groups A-E. All six earlier enum types remain present and unchanged, and
    no unrelated enum type appears."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0007_regulation_assumptions_gaps")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | _GROUP_E_TABLES
                | _GROUP_F_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0007_regulation_assumptions_gaps"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES | _GROUP_F_ENUM_TYPES

            regulatory_effect_values = connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                    "WHERE pg_type.typname = 'regulatory_effect' "
                    "ORDER BY enumsortorder"
                )
            ).scalars().all()
            from app.models.enums import RegulatoryEffect

            assert regulatory_effect_values == [member.value for member in RegulatoryEffect]
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0006_removes_group_f_and_g_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading from head (0008) past 0007 removes Group F's three tables
    plus its one enum (regulatory_effect), and Group G's three tables (which
    introduce no enum of their own). Groups A-E remain intact, and all six
    enum types owned by 0004 and 0005 survive unchanged."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0006_kinetic_measurement")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | _GROUP_E_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0006_kinetic_measurement"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert enum_types == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)
    finally:
        engine.dispose()


def test_0008_external_records_reviews_revises_0007_regulation_assumptions_gaps(
    alembic_config: Config,
) -> None:
    """Migration 0008 chains directly onto migration 0007."""
    script = ScriptDirectory.from_config(alembic_config)
    revision = script.get_revision("0008_external_records_reviews")

    assert revision is not None
    assert revision.down_revision == "0007_regulation_assumptions_gaps"


def test_upgrade_to_0008_creates_group_g_tables_with_no_new_enum(
    scratch_database: str, alembic_config: Config
) -> None:
    """Migration 0008 adds exactly the three Group G tables on top of
    Groups A-F and introduces no new enum type — it reuses source_type and
    curation_state rather than recreating either, and no unrelated enum
    type appears.

    Upgrades to the specific ``0008_external_records_reviews`` revision
    rather than ``head`` -- ``head`` moved to
    ``0009_persistence_hardening`` once that migration was added, matching
    the same specific-revision pattern every other group test in this file
    already uses (e.g. ``test_upgrade_to_0002_creates_exactly_group_a_tables``)."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "0008_external_records_reviews")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | _GROUP_E_TABLES
                | _GROUP_F_TABLES
                | _GROUP_G_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0008_external_records_reviews"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert (
                enum_types
                == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES | _GROUP_F_ENUM_TYPES
            )
    finally:
        engine.dispose()


def test_downgrade_from_head_to_0007_removes_only_group_g_tables(
    scratch_database: str, alembic_config: Config
) -> None:
    """Downgrading past 0008 removes only Group G's three tables. There is
    no enum type for its downgrade to drop, so all seven enum types owned
    by 0004, 0005, and 0007 remain exactly as they were, and Groups A-F
    remain intact."""
    from alembic import command

    alembic_config.set_main_option("sqlalchemy.url", scratch_database)
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0007_regulation_assumptions_gaps")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert tables == (
                _GROUP_A_TABLES
                | _GROUP_B_TABLES
                | _GROUP_C_TABLES
                | _GROUP_D_TABLES
                | _GROUP_E_TABLES
                | _GROUP_F_TABLES
                | {"alembic_version"}
            )

            stored = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert stored == "0007_regulation_assumptions_gaps"

            enum_types = set(
                connection.execute(
                    text("SELECT typname FROM pg_type WHERE typtype = 'e'")
                ).scalars().all()
            )
            assert (
                enum_types
                == _GROUP_C_ENUM_TYPES | _GROUP_D_ENUM_TYPES | _GROUP_F_ENUM_TYPES
            )

            compartment_count = connection.execute(
                text("SELECT count(*) FROM compartment")
            ).scalar()
            assert compartment_count == len(_STANDARD_COMPARTMENT_NAMES)
    finally:
        engine.dispose()
