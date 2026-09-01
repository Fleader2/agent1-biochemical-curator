"""FastAPI application factory.

Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestLoggingMiddleware
from app.api.v1.router import api_router
from app.config.logging import configure_logging, get_logger
from app.config.settings import API_V1_PREFIX, APPLICATION_NAME, get_settings
from app.schemas.common import ErrorResponse

logger = get_logger("main")

DESCRIPTION = (
    "Agent 1 curates biochemical evidence with traceable provenance for downstream "
    "mechanistic modeling agents. It does not generate models, estimate kinetic "
    "parameters, or run simulations."
)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    app = FastAPI(
        title=APPLICATION_NAME,
        version=__version__,
        description=DESCRIPTION,
        responses={
            422: {"model": ErrorResponse, "description": "Validation error"},
            500: {"model": ErrorResponse, "description": "Internal error"},
        },
    )

    app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_V1_PREFIX)

    logger.info(
        "application initialised",
        extra={"app_env": settings.app_env, "version": __version__},
    )
    return app


app = create_app()
