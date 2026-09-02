"""Tests for the shared connector response cache.

Includes the source-agnostic "Connector Cache Tests" required by
``docs/05_testing.md``. The cache stores enough raw response information
(``RawResponse.content``) to reproduce a ``raw_response_hash`` later -- it
does not compute or store a hash itself (that belongs to a later persistence
increment).
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.connectors.base import RawResponse
from app.connectors.cache import InMemoryResponseCache, build_cache_key
from app.connectors.http import ConnectorHttpClient

pytestmark = pytest.mark.connector


def _response(text: str = "ok") -> RawResponse:
    return RawResponse(
        status_code=200,
        url="https://example.invalid/resource",
        headers={},
        content=text.encode(),
        text=text,
        elapsed_seconds=0.0,
    )


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Connector Cache Tests (docs/05_testing.md) ------------------------------


def test_identical_request_uses_cache() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="ok")

    http_client = ConnectorHttpClient(_mock_client(handler), cache=InMemoryResponseCache())

    first = http_client.get("https://example.invalid/resource", params={"id": "1"})
    second = http_client.get("https://example.invalid/resource", params={"id": "1"})

    assert first == second
    assert len(calls) == 1


def test_cache_key_includes_request_parameters() -> None:
    key_a = build_cache_key("GET", "https://example.invalid/x", {"id": "1"})
    key_b = build_cache_key("GET", "https://example.invalid/x", {"id": "2"})
    key_c = build_cache_key("GET", "https://example.invalid/x", None)

    assert key_a != key_b
    assert key_a != key_c
    assert key_b != key_c


def test_cache_key_is_order_independent() -> None:
    """The same parameters in a different order are the same request."""
    key_a = build_cache_key("GET", "https://example.invalid/x", {"a": "1", "b": "2"})
    key_b = build_cache_key("GET", "https://example.invalid/x", {"b": "2", "a": "1"})

    assert key_a == key_b


def test_cached_raw_response_hash_preserved() -> None:
    """A response served from cache hashes identically to the original response."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"raw-bytes-from-source")

    http_client = ConnectorHttpClient(_mock_client(handler), cache=InMemoryResponseCache())

    first = http_client.get("https://example.invalid/resource")
    second = http_client.get("https://example.invalid/resource")

    assert len(calls) == 1  # second call was served from cache, not the network
    assert hashlib.sha256(first.content).hexdigest() == hashlib.sha256(second.content).hexdigest()


def test_cache_does_not_hide_new_version_when_refresh_requested() -> None:
    responses = iter(
        [httpx.Response(200, text="version-1"), httpx.Response(200, text="version-2")]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    http_client = ConnectorHttpClient(_mock_client(handler), cache=InMemoryResponseCache())

    first = http_client.get("https://example.invalid/resource")
    cached = http_client.get("https://example.invalid/resource")
    refreshed = http_client.get("https://example.invalid/resource", refresh=True)
    cached_again = http_client.get("https://example.invalid/resource")

    assert first.text == "version-1"
    assert cached.text == "version-1"
    assert refreshed.text == "version-2"
    assert cached_again.text == "version-2"


# --- InMemoryResponseCache ----------------------------------------------------


def test_in_memory_cache_miss_returns_none() -> None:
    cache = InMemoryResponseCache()

    assert cache.get("missing-key") is None


def test_in_memory_cache_set_then_get_returns_stored_response() -> None:
    cache = InMemoryResponseCache()
    response = _response()

    cache.set("key", response)

    assert cache.get("key") == response


def test_in_memory_cache_set_replaces_prior_entry() -> None:
    cache = InMemoryResponseCache()
    cache.set("key", _response("first"))
    cache.set("key", _response("second"))

    stored = cache.get("key")
    assert stored is not None
    assert stored.text == "second"
