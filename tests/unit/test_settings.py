"""Tests for application configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import Settings

pytestmark = pytest.mark.unit

BASE_URL = "postgresql+psycopg://user:password@localhost:5432/agent1"

_CONFIG_ENV_VARS = (
    "APP_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "DATABASE_ECHO",
    "PROMPT_VERSION",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_TEMPERATURE",
    "NCBI_EMAIL",
    "NCBI_TOOL_NAME",
    "NCBI_API_KEY",
    "BRENDA_USERNAME",
    "BRENDA_PASSWORD",
    "KEGG_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove configuration variables so defaults can be observed directly."""
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_database_url_is_required() -> None:
    """No invented default database is supplied."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_bare_postgresql_scheme_gets_explicit_driver() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://user:password@localhost:5432/agent1",
    )

    assert settings.sqlalchemy_url.startswith("postgresql+psycopg://")


def test_explicit_driver_is_preserved() -> None:
    settings = Settings(_env_file=None, database_url=BASE_URL)  # type: ignore[call-arg]

    assert settings.sqlalchemy_url == BASE_URL


def test_optional_credentials_default_to_none() -> None:
    settings = Settings(_env_file=None, database_url=BASE_URL)  # type: ignore[call-arg]

    assert settings.llm_api_key is None
    assert settings.ncbi_api_key is None
    assert settings.brenda_password is None
    assert settings.llm_provider is None
    assert settings.llm_model is None
    assert settings.kegg_base_url is None


def test_llm_temperature_defaults_to_zero() -> None:
    """Scientific extraction requires a low-randomness configuration."""
    settings = Settings(_env_file=None, database_url=BASE_URL)  # type: ignore[call-arg]

    assert settings.llm_temperature == 0.0


@pytest.mark.scientific_integrity
def test_secret_values_are_not_exposed_by_repr() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=BASE_URL,
        llm_api_key="test-only-secret-value",
    )

    assert "test-only-secret-value" not in repr(settings)
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-only-secret-value"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=BASE_URL, log_level="LOUD")  # type: ignore[call-arg]
