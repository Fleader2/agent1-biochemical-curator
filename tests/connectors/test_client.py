"""Tests for the shared connector HTTP client: retry, backoff, and rate limiting.

Includes the source-agnostic "Retry and Backoff Tests" required by
``docs/05_testing.md``. No test makes a real network call: every request is
served by an ``httpx.MockTransport``, and every delay is captured by an
injected fake ``sleep`` rather than actually waited out.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import (
    ConnectorHTTPError,
    ConnectorNetworkError,
    ConnectorRateLimitError,
)
from app.connectors.http import ConnectorHttpClient
from app.connectors.ratelimit import IntervalRateLimiter

pytestmark = pytest.mark.connector


class _FakeSleep:
    """Records requested delays instead of actually waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _CountingHandler:
    """An ``httpx.MockTransport`` handler that replays a scripted sequence.

    Each entry in ``results`` is either an ``httpx.Response`` to return or an
    exception instance to raise, consumed in call order. The last entry
    repeats for any calls beyond the scripted sequence.
    """

    def __init__(self, results: list[httpx.Response | Exception]) -> None:
        self._results = results
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self._results) - 1)
        result = self._results[index]
        if isinstance(result, Exception):
            raise result
        return result


def _client_for(handler: _CountingHandler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Retry and Backoff Tests (docs/05_testing.md) ---------------------------


def test_transient_failure_retried() -> None:
    """A timeout followed by success succeeds without surfacing an error."""
    handler = _CountingHandler(
        [httpx.TimeoutException("simulated timeout"), httpx.Response(200, text="ok")]
    )
    sleep = _FakeSleep()
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=3, sleep=sleep)

    response = http_client.get("https://example.invalid/resource")

    assert response.status_code == 200
    assert response.text == "ok"
    assert len(handler.calls) == 2
    assert sleep.calls == [0.5]


def test_permanent_400_not_retried_excessively() -> None:
    """An ordinary client error is raised immediately, with no retry attempts."""
    handler = _CountingHandler([httpx.Response(400, text="bad request")])
    sleep = _FakeSleep()
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=3, sleep=sleep)

    with pytest.raises(ConnectorHTTPError) as excinfo:
        http_client.get("https://example.invalid/resource")

    assert excinfo.value.status_code == 400
    assert len(handler.calls) == 1
    assert sleep.calls == []


def test_429_uses_backoff() -> None:
    """Repeated 429s are retried with exponentially increasing backoff delays."""
    handler = _CountingHandler(
        [httpx.Response(429), httpx.Response(429), httpx.Response(200, text="ok")]
    )
    sleep = _FakeSleep()
    http_client = ConnectorHttpClient(
        _client_for(handler), max_retries=3, backoff_base_seconds=0.5, sleep=sleep
    )

    response = http_client.get("https://example.invalid/resource")

    assert response.status_code == 200
    assert sleep.calls == [0.5, 1.0]


def test_retry_count_configurable() -> None:
    """``max_retries`` controls exactly how many attempts are made before failing."""
    handler = _CountingHandler([httpx.Response(503)])
    sleep = _FakeSleep()
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=2, sleep=sleep)

    with pytest.raises(ConnectorHTTPError):
        http_client.get("https://example.invalid/resource")

    assert len(handler.calls) == 3  # initial attempt + 2 retries
    assert len(sleep.calls) == 2


def test_final_failure_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Exhausting retries logs the final failure before raising."""
    handler = _CountingHandler([httpx.Response(503)])
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=0, sleep=_FakeSleep())

    with (
        caplog.at_level(logging.ERROR, logger="agent1.connectors.http"),
        pytest.raises(ConnectorHTTPError),
    ):
        http_client.get("https://example.invalid/resource")

    assert any(record.levelno == logging.ERROR for record in caplog.records)


# --- Additional retry/failure coverage required by this increment -----------


def test_network_error_retried() -> None:
    """A connection-level failure, not just a timeout, is also retried."""
    handler = _CountingHandler(
        [httpx.ConnectError("simulated connection error"), httpx.Response(200, text="ok")]
    )
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=3, sleep=_FakeSleep())

    response = http_client.get("https://example.invalid/resource")

    assert response.status_code == 200
    assert len(handler.calls) == 2


def test_5xx_retried() -> None:
    """A server error is retried the same way a 429 is."""
    handler = _CountingHandler([httpx.Response(503), httpx.Response(200, text="ok")])
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=3, sleep=_FakeSleep())

    response = http_client.get("https://example.invalid/resource")

    assert response.status_code == 200
    assert len(handler.calls) == 2


def test_final_rate_limit_failure_raises_rate_limit_error() -> None:
    """After retries are exhausted, a 429 raises the more specific rate-limit error."""
    handler = _CountingHandler([httpx.Response(429)])
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=0, sleep=_FakeSleep())

    with pytest.raises(ConnectorRateLimitError):
        http_client.get("https://example.invalid/resource")


def test_final_network_failure_raises_network_error() -> None:
    """After retries are exhausted, a timeout raises the network-failure type."""
    handler = _CountingHandler([httpx.TimeoutException("simulated timeout")])
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=0, sleep=_FakeSleep())

    with pytest.raises(ConnectorNetworkError):
        http_client.get("https://example.invalid/resource")


def test_empty_success_response_is_not_an_error() -> None:
    """A successful-but-empty response is a normal result, not a failure.

    Connector failures must remain distinguishable from legitimate
    zero-result searches: the HTTP layer's job is simply to not raise for a
    well-formed empty payload.
    """
    handler = _CountingHandler([httpx.Response(200, json=[])])
    http_client = ConnectorHttpClient(_client_for(handler), max_retries=3)

    response = http_client.get("https://example.invalid/resource")

    assert response.status_code == 200
    assert response.text == "[]"


def test_rate_limiter_is_consulted_before_each_attempt() -> None:
    """The configured rate limiter's ``acquire()`` is called once per attempt."""
    handler = _CountingHandler([httpx.Response(503), httpx.Response(200, text="ok")])
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    http_client = ConnectorHttpClient(
        _client_for(handler), max_retries=3, sleep=_FakeSleep(), rate_limiter=_RecordingLimiter()
    )

    http_client.get("https://example.invalid/resource")

    assert len(acquire_calls) == 2  # one per attempt, including the retried one


def test_response_served_from_cache_makes_no_second_request() -> None:
    """A repeated identical request is served from cache, not the network."""
    handler = _CountingHandler([httpx.Response(200, text="ok")])
    http_client = ConnectorHttpClient(_client_for(handler), cache=InMemoryResponseCache())

    first = http_client.get("https://example.invalid/resource")
    second = http_client.get("https://example.invalid/resource")

    assert first == second
    assert len(handler.calls) == 1


# --- IntervalRateLimiter -----------------------------------------------------


def test_interval_rate_limiter_waits_for_minimum_interval() -> None:
    times = iter([0.0, 0.1, 0.1])
    sleep = _FakeSleep()
    limiter = IntervalRateLimiter(1.0, clock=lambda: next(times), sleep=sleep)

    limiter.acquire()
    limiter.acquire()

    assert sleep.calls == [0.9]


def test_interval_rate_limiter_does_not_wait_when_interval_already_elapsed() -> None:
    times = iter([0.0, 5.0])
    sleep = _FakeSleep()
    limiter = IntervalRateLimiter(1.0, clock=lambda: next(times), sleep=sleep)

    limiter.acquire()
    limiter.acquire()

    assert sleep.calls == []
