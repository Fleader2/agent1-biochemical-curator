"""Synchronous, injectable HTTP wrapper shared by every connector.

This is the only place retry, backoff, rate-limiting, timeout, and caching
behavior live -- individual source connectors call ``ConnectorHttpClient.get()``
and contain no retry/backoff logic of their own
(``.cursor/rules/02-architecture.mdc``: "Each connector should support
behavior equivalent to ... caching, rate limiting, retry/backoff").

Retry policy (``docs/03_agent_behavior.md`` "Search Rate Limiting",
``docs/05_testing.md`` "Retry and Backoff Tests"):

* retry transient network failures and timeouts,
* retry HTTP 429,
* retry HTTP 5xx,
* do not retry an ordinary permanent 4xx response,
* use exponential backoff between attempts,
* the retry count is configurable,
* the final failure (after retries are exhausted) is logged.

No random jitter is added: the current specification does not call for it,
and deterministic backoff delays keep tests exact rather than probabilistic.

This module has no knowledge of any particular source's URL scheme or
response format, and no database dependency -- persisting a response as an
``external_record`` row is a separate, later increment.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from app.connectors.base import RawResponse
from app.connectors.cache import ResponseCache, build_cache_key
from app.connectors.exceptions import (
    ConnectorHTTPError,
    ConnectorNetworkError,
    ConnectorRateLimitError,
)
from app.connectors.ratelimit import NullRateLimiter, RateLimiter

logger = logging.getLogger("agent1.connectors.http")

_RETRYABLE_STATUS_CODES = frozenset({429})


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES or 500 <= status_code < 600


def _elapsed_seconds(response: httpx.Response) -> float:
    """Return request duration, or ``0.0`` when the transport did not report it.

    ``httpx.Response.elapsed`` raises ``RuntimeError`` unless the transport
    populated timing information -- real transports do, ``httpx.MockTransport``
    (used throughout this project's tests) does not.
    """
    try:
        return response.elapsed.total_seconds()
    except RuntimeError:
        return 0.0


class ConnectorHttpClient:
    """Retry/backoff/rate-limit/cache-aware wrapper around ``httpx.Client``.

    ``client`` is injected rather than constructed internally so tests can
    supply an ``httpx.Client`` backed by ``httpx.MockTransport`` and make no
    real network call.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        rate_limiter: RateLimiter | None = None,
        cache: ResponseCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._client = client
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._rate_limiter = rate_limiter or NullRateLimiter()
        self._cache = cache
        self._sleep = sleep

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        refresh: bool = False,
    ) -> RawResponse:
        """Perform a GET request, subject to caching, rate limiting, and retry.

        ``refresh=True`` bypasses a cache lookup (an explicit request for the
        current upstream version) but the freshly retrieved response still
        replaces whatever was cached, so a later ordinary call benefits from
        it too.
        """
        cache_key = build_cache_key("GET", url, params)

        if self._cache is not None and not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        response = self._send_with_retry(url, params=params)

        if self._cache is not None:
            self._cache.set(cache_key, response)

        return response

    def _send_with_retry(self, url: str, *, params: Mapping[str, Any] | None) -> RawResponse:
        attempt = 0
        while True:
            self._rate_limiter.acquire()

            try:
                httpx_response = self._client.request(
                    "GET", url, params=params, timeout=self._timeout
                )
            except httpx.TimeoutException as exc:
                self._retry_or_raise_network(url, attempt, exc, reason="timed out")
                attempt += 1
                continue
            except httpx.TransportError as exc:
                self._retry_or_raise_network(url, attempt, exc, reason="network failure")
                attempt += 1
                continue

            status_code = httpx_response.status_code

            if _is_retryable_status(status_code):
                self._retry_or_raise_http(url, attempt, status_code)
                attempt += 1
                continue

            if status_code >= 400:
                message = f"HTTP {status_code} for {url} (not retried)"
                logger.error(message, extra={"url": url, "status_code": status_code})
                raise ConnectorHTTPError(status_code, message)

            return RawResponse(
                status_code=status_code,
                url=str(httpx_response.url),
                headers=dict(httpx_response.headers),
                content=httpx_response.content,
                text=httpx_response.text,
                elapsed_seconds=_elapsed_seconds(httpx_response),
            )

    def _retry_or_raise_network(
        self, url: str, attempt: int, exc: Exception, *, reason: str
    ) -> None:
        if attempt >= self._max_retries:
            message = f"{reason} after {attempt + 1} attempt(s): {url}"
            logger.error(message, extra={"url": url, "attempts": attempt + 1})
            raise ConnectorNetworkError(message) from exc
        self._sleep(self._backoff_base * (2**attempt))

    def _retry_or_raise_http(self, url: str, attempt: int, status_code: int) -> None:
        if attempt >= self._max_retries:
            message = f"HTTP {status_code} for {url} after {attempt + 1} attempt(s)"
            logger.error(message, extra={"url": url, "status_code": status_code})
            if status_code == 429:
                raise ConnectorRateLimitError(message)
            raise ConnectorHTTPError(status_code, message)
        self._sleep(self._backoff_base * (2**attempt))


__all__ = ["ConnectorHttpClient"]
