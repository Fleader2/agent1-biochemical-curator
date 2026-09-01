"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import EngineDep
from app.schemas.health import HealthResponse
from app.services.health import build_health

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application health",
    description=(
        "Reports application and database health. Performs no external API calls. "
        "Returns HTTP 200 with `status` set to `degraded` when the database is "
        "unreachable, so that a dependency failure is visible rather than hidden."
    ),
)
def get_health(engine: EngineDep) -> HealthResponse:
    """Return application health including a database connectivity check."""
    return build_health(engine)
