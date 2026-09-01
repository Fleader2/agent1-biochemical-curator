"""Tests for the API error envelope and OpenAPI availability."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import PaginationDep
from app.api.errors import ApiError
from app.config.settings import API_V1_PREFIX
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT, ErrorCode

pytestmark = pytest.mark.api


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get(f"{API_V1_PREFIX}/does-not-exist")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == ErrorCode.NOT_FOUND
    assert isinstance(detail["message"], str)


def test_validation_error_returns_envelope(app: FastAPI) -> None:
    """Query validation failures use the standard envelope, not FastAPI's default."""

    @app.get("/test-only/paginated")
    def paginated(pagination: PaginationDep) -> dict[str, int]:
        return {"limit": pagination.limit, "offset": pagination.offset}

    with TestClient(app) as client:
        response = client.get("/test-only/paginated", params={"limit": "not-a-number"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == ErrorCode.VALIDATION_ERROR
    assert detail["context"]["errors"]


def test_pagination_limit_above_maximum_rejected(app: FastAPI) -> None:
    @app.get("/test-only/paginated-max")
    def paginated(pagination: PaginationDep) -> dict[str, int]:
        return {"limit": pagination.limit, "offset": pagination.offset}

    with TestClient(app) as client:
        response = client.get("/test-only/paginated-max", params={"limit": MAX_LIMIT + 1})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == ErrorCode.VALIDATION_ERROR


def test_pagination_defaults_applied(app: FastAPI) -> None:
    @app.get("/test-only/paginated-defaults")
    def paginated(pagination: PaginationDep) -> dict[str, int]:
        return {"limit": pagination.limit, "offset": pagination.offset}

    with TestClient(app) as client:
        response = client.get("/test-only/paginated-defaults")

    assert response.json() == {"limit": DEFAULT_LIMIT, "offset": 0}


def test_api_error_maps_to_envelope(app: FastAPI) -> None:
    @app.get("/test-only/api-error")
    def raise_api_error() -> None:
        raise ApiError(
            ErrorCode.CONFLICT,
            "Conflicting record state.",
            status_code=409,
            context={"entity_type": "test"},
        )

    with TestClient(app) as client:
        response = client.get("/test-only/api-error")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "CONFLICT",
            "message": "Conflicting record state.",
            "context": {"entity_type": "test"},
        }
    }


@pytest.mark.scientific_integrity
def test_unhandled_error_does_not_leak_internal_detail(app: FastAPI) -> None:
    secret_fragment = "internal-connection-detail"

    @app.get("/test-only/boom")
    def raise_unexpected() -> None:
        raise RuntimeError(f"failure containing {secret_fragment}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-only/boom")

    assert response.status_code == 500
    assert secret_fragment not in response.text
    assert response.json()["detail"]["code"] == ErrorCode.INTERNAL_ERROR


def test_openapi_schema_available(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert f"{API_V1_PREFIX}/health" in schema["paths"]
    assert f"{API_V1_PREFIX}/system/info" in schema["paths"]


def test_documented_endpoints_have_summary_and_description(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    for path in (f"{API_V1_PREFIX}/health", f"{API_V1_PREFIX}/system/info"):
        operation = schema["paths"][path]["get"]
        assert operation["summary"]
        assert operation["description"]
        assert operation["responses"]["200"]
