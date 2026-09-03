"""Environment-driven application configuration.

All environment-dependent values are defined here. Credentials are held as
``SecretStr`` so that they are not exposed by accidental serialization, and no
credential is ever given a default value.

Unknown optional configuration stays ``None`` rather than receiving an invented
default (see ``docs/01_overview.md``: unknown information remains unknown).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

APPLICATION_NAME = "Agent 1 Biochemical Evidence Curator"
API_VERSION = "v1"
API_V1_PREFIX = "/api/v1"

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


class Settings(BaseSettings):
    """Runtime configuration read from the environment or a local ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "test", "ci", "production"] = "local"
    log_level: LogLevel = "INFO"
    log_format: Literal["json", "text"] = "json"

    database_url: PostgresDsn
    """Canonical PostgreSQL database. Required; no default is supplied."""

    test_database_url: PostgresDsn | None = None
    """Separate database used by the test suite. Tests refuse to run without it."""

    database_echo: bool = False

    prompt_version: str = "0.1"
    """Version of the version-controlled prompt set, recorded for reproducibility."""

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    """Scientific extraction requires low-randomness settings (``docs/03_agent_behavior.md``)."""

    ncbi_email: str | None = None
    ncbi_tool_name: str | None = None
    ncbi_api_key: SecretStr | None = None

    brenda_username: str | None = None
    brenda_password: SecretStr | None = None

    kegg_base_url: str | None = None

    sgd_base_url: str | None = None
    """Saccharomyces Genome Database REST API base URL. No credential is required
    by SGD's public API; no default is supplied here, matching ``kegg_base_url``."""

    @property
    def sqlalchemy_url(self) -> str:
        """Database URL with an explicit driver.

        SQLAlchemy defaults the bare ``postgresql://`` scheme to psycopg2, which
        this project does not install. The driver is made explicit rather than
        letting connection fail with a confusing import error.
        """
        url = str(self.database_url)
        for bare_scheme in ("postgresql://", "postgres://"):
            if url.startswith(bare_scheme):
                return "postgresql+psycopg://" + url[len(bare_scheme) :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance.

    Cached so that configuration is read once per process. Tests call
    ``get_settings.cache_clear()`` after changing the environment.
    """
    return Settings()  # type: ignore[call-arg]
