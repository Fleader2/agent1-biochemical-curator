"""Shared test fixtures.

Tests run against a dedicated PostgreSQL test database identified by
``TEST_DATABASE_URL``. A guard refuses to run when that URL matches
``DATABASE_URL``, so the suite cannot operate on a development database
(``docs/05_testing.md``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.session import get_engine, reset_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Secret-shaped values used only to prove that configured credentials never reach
# an API response. These are test fixtures, not real credentials.
FAKE_SECRETS: dict[str, str] = {
    "LLM_API_KEY": "test-only-llm-key-3f9c1a",
    "NCBI_API_KEY": "test-only-ncbi-key-7b2d40",
    "BRENDA_PASSWORD": "test-only-brenda-password-c81e5",
}


def _configured_urls() -> tuple[str | None, str | None]:
    """Return ``(DATABASE_URL, TEST_DATABASE_URL)`` from the environment or ``.env``.

    Falling back to ``.env`` lets a local ``pytest`` invocation use the same
    configuration as the application without exporting variables by hand.
    """
    primary = os.environ.get("DATABASE_URL")
    test = os.environ.get("TEST_DATABASE_URL")
    if primary and test:
        return primary, test

    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError:
        return primary, test

    if not primary:
        primary = settings.sqlalchemy_url
    if not test and settings.test_database_url is not None:
        test = str(settings.test_database_url)
    return primary, test


def _require_test_database_url() -> str:
    """Return the test database URL, refusing to fall back to the primary database."""
    primary_url, test_url = _configured_urls()

    if not test_url:
        pytest.exit(
            "TEST_DATABASE_URL is not set. Database and API tests require a "
            "dedicated PostgreSQL test database.",
            returncode=1,
        )

    if primary_url and primary_url == test_url:
        pytest.exit(
            "TEST_DATABASE_URL must differ from DATABASE_URL so that tests cannot "
            "modify a development or production database.",
            returncode=1,
        )
    return test_url


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Validated URL of the PostgreSQL test database."""
    return _require_test_database_url()


@pytest.fixture(scope="session", autouse=True)
def test_environment(test_database_url: str) -> Iterator[None]:
    """Point application configuration at the test database for the whole session."""
    previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "APP_ENV", *FAKE_SECRETS)}

    os.environ["DATABASE_URL"] = test_database_url
    os.environ["APP_ENV"] = "test"
    os.environ.update(FAKE_SECRETS)

    get_settings.cache_clear()
    reset_engine()

    yield

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    get_settings.cache_clear()
    reset_engine()


@pytest.fixture(scope="session")
def settings(test_environment: None) -> Settings:
    """Application settings resolved against the test environment."""
    del test_environment
    return get_settings()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    """Alembic configuration rooted at the project directory."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


@pytest.fixture(scope="session")
def migrated_engine(settings: Settings, alembic_config: Config) -> Iterator[Engine]:
    """Engine bound to the test database, migrated to head once per session."""
    alembic_config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)
    command.upgrade(alembic_config, "head")

    engine = get_engine()
    yield engine


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """Session wrapped in a transaction that is rolled back after each test."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def app(migrated_engine: Engine) -> FastAPI:
    """Application instance built against the test configuration."""
    del migrated_engine
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """HTTP client for API tests."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def scratch_database(settings: Settings) -> Iterator[str]:
    """Create an empty PostgreSQL database and drop it afterwards.

    Used to prove that migrations apply to a database with no prior schema.
    """
    base_url = settings.sqlalchemy_url
    admin_url, _, current_database = base_url.rpartition("/")
    scratch_name = f"{current_database}_scratch"

    admin_engine = create_engine(f"{admin_url}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
            connection.execute(text(f'CREATE DATABASE "{scratch_name}"'))

        yield f"{admin_url}/{scratch_name}"

        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
    finally:
        admin_engine.dispose()
