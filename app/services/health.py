"""Health and system-information services."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.config.logging import get_logger
from app.config.settings import API_VERSION, APPLICATION_NAME, Settings
from app.schemas.health import HealthResponse
from app.schemas.system import SystemInfoResponse

logger = get_logger("services.health")


def check_database(engine: Engine) -> bool:
    """Return whether the database answers a trivial query.

    Connection failure is reported rather than raised, so that ``/health`` can
    still describe the application when the database is unreachable.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("database health check failed", extra={"error": str(exc)})
        return False
    return True


def build_health(engine: Engine) -> HealthResponse:
    """Assemble the health response. Performs no external API calls."""
    database_ok = check_database(engine)
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "error",
        version=__version__,
    )


def build_system_info(settings: Settings) -> SystemInfoResponse:
    """Assemble reproducibility metadata without exposing any credential."""
    return SystemInfoResponse(
        application=APPLICATION_NAME,
        version=__version__,
        api_version=API_VERSION,
        prompt_version=settings.prompt_version,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
    )
