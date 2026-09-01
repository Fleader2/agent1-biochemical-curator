"""Tests for ``GET /api/v1/health``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import __version__
from app.api.middleware import REQUEST_ID_HEADER
from app.config.settings import API_V1_PREFIX
from app.services.health import build_health, check_database

pytestmark = pytest.mark.api

HEALTH_URL = f"{API_V1_PREFIX}/health"


def test_health(client: TestClient) -> None:
    response = client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "version": __version__,
    }


def test_health_reports_request_id_header(client: TestClient) -> None:
    response = client.get(HEALTH_URL)

    assert response.headers[REQUEST_ID_HEADER]


def test_health_preserves_supplied_request_id(client: TestClient) -> None:
    response = client.get(HEALTH_URL, headers={REQUEST_ID_HEADER: "test-request-id"})

    assert response.headers[REQUEST_ID_HEADER] == "test-request-id"


@pytest.mark.database
def test_check_database_reports_failure_instead_of_raising() -> None:
    """An unreachable database is reported, not raised.

    ``/health`` must still describe the application when the database is down.
    """
    unreachable = create_engine(
        "postgresql+psycopg://agent1:agent1@127.0.0.1:1/does-not-exist",
        connect_args={"connect_timeout": 1},
    )
    try:
        assert check_database(unreachable) is False

        health = build_health(unreachable)
        assert health.status == "degraded"
        assert health.database == "error"
    finally:
        unreachable.dispose()
