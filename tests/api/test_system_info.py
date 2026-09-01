"""Tests for ``GET /api/v1/system/info``."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config.settings import API_V1_PREFIX, API_VERSION, APPLICATION_NAME, Settings
from tests.conftest import FAKE_SECRETS

pytestmark = pytest.mark.api

SYSTEM_INFO_URL = f"{API_V1_PREFIX}/system/info"


def test_system_info(client: TestClient, settings: Settings) -> None:
    response = client.get(SYSTEM_INFO_URL)

    assert response.status_code == 200
    assert response.json() == {
        "application": APPLICATION_NAME,
        "version": __version__,
        "api_version": API_VERSION,
        "prompt_version": settings.prompt_version,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


def test_unconfigured_llm_metadata_remains_null(client: TestClient) -> None:
    """Unknown configuration is reported as ``null`` rather than a placeholder."""
    body = client.get(SYSTEM_INFO_URL).json()

    assert body["llm_provider"] is None
    assert body["llm_model"] is None


@pytest.mark.scientific_integrity
def test_env_secrets_not_in_system_info(client: TestClient, settings: Settings) -> None:
    """Configured credentials must never appear in the response."""
    raw_body = client.get(SYSTEM_INFO_URL).text

    for secret in FAKE_SECRETS.values():
        assert secret not in raw_body

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() not in raw_body
    assert str(settings.database_url) not in raw_body

    database_password = urlsplit(settings.sqlalchemy_url).password
    assert database_password
    assert database_password not in raw_body
