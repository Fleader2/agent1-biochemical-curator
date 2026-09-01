"""System information schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SystemInfoResponse(BaseModel):
    """Application metadata relevant to reproducibility.

    Contains no credentials. Unconfigured values are reported as ``null`` rather
    than as a placeholder string.
    """

    application: str
    version: str = Field(description="Agent 1 software version")
    api_version: str
    prompt_version: str
    llm_provider: str | None = None
    llm_model: str | None = None
