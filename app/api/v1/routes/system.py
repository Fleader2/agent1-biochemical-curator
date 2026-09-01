"""System information endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.schemas.system import SystemInfoResponse
from app.services.health import build_system_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/info",
    response_model=SystemInfoResponse,
    summary="System information",
    description=(
        "Returns application metadata relevant to reproducibility. Never returns "
        "credentials. Unconfigured values are reported as `null`."
    ),
)
def get_system_info(settings: SettingsDep) -> SystemInfoResponse:
    """Return application, API, prompt, and LLM configuration metadata."""
    return build_system_info(settings)
