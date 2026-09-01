"""Health endpoint schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Application health.

    ``status`` is ``degraded`` when a dependency check fails, so that a database
    outage is reported explicitly instead of being hidden behind ``ok``.
    """

    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    version: str = Field(description="Agent 1 software version")
