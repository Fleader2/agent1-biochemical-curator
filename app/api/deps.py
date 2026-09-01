"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.session import get_engine, get_session
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT, PaginationParams

SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
SessionDep = Annotated[Session, Depends(get_session)]


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginationParams:
    """Validate list-endpoint pagination query parameters.

    Bounds are declared on the query parameters so that an out-of-range request
    returns the standard 422 error envelope rather than a server error.
    """
    return PaginationParams(limit=limit, offset=offset)


PaginationDep = Annotated[PaginationParams, Depends(pagination_params)]

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "EngineDep",
    "PaginationDep",
    "SessionDep",
    "SettingsDep",
    "pagination_params",
]
