"""Shared API schemas: error envelope and pagination.

The error envelope and pagination shape follow ``docs/04_api_spec.md``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


class ErrorCode(StrEnum):
    """Machine-readable error codes.

    Only codes raised by the current implementation are defined. Codes for
    unimplemented behaviour are added by the phase that implements them.
    """

    VALIDATION_ERROR = "VALIDATION_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """Structured description of a single API failure."""

    code: ErrorCode
    message: str
    context: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Top-level error envelope returned by every failing endpoint."""

    detail: ErrorDetail


class PaginationParams(BaseModel):
    """Validated ``limit``/``offset`` pair for list endpoints."""

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)


class Page[ItemT](BaseModel):
    """Paginated collection response."""

    items: list[ItemT]
    limit: int = Field(ge=1, le=MAX_LIMIT)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
