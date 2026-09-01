"""Aggregated router for API version 1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import health, system

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(system.router)
