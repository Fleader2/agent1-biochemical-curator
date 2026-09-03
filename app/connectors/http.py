"""Synchronous, injectable HTTP wrapper shared by every connector.

This is the only place retry, backoff, rate-limiting, timeout, and caching
behavior live -- individual source connectors call ``ConnectorHttpClient.get()``
/``post()`` and contain no retry/backoff logic of their own
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

``post()`` was added in the BRENDA increment: BRENDA's SOAP interface
requires an HTTP POST carrying an XML envelope body, which ``get()`` cannot
express. It shares every retry/backoff/timeout/rate-limit/cache code path
with ``get()`` (both call ``_request()``/``_send_with_retry()``) -- nothing
about those concerns is duplicated between the two.

``is_permanent_failure`` was also added in the BRENDA increment: some
sources signal a genuinely permanent failure (BRENDA: an authentication
fault) using a normally-retryable HTTP status (429/5xx) whose body content
is what actually distinguishes it from a transient failure at that same
status. This module has no idea what BRENDA, SOAP, or a fault even are --
it only knows how to ask an injected, source-supplied predicate "is this
particular (status, body) pair permanent?" before deciding to retry it. When
absent (the default for every other connector), retry behavior is exactly
as before this parameter existed.

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
        is_permanent_failure: Callable[[int, str], bool] | None = None,
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
        self._is_permanent_failure = is_permanent_failure

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
        return self._request("GET", url, params=params, refresh=refresh)

    def post(
        self,
        url: str,
        *,
        content: bytes | str,
        headers: Mapping[str, str] | None = None,
        refresh: bool = False,
    ) -> RawResponse:
        """Perform a POST request, subject to caching, rate limiting, and retry.

        For connectors whose actual wire protocol carries its request as a
        body rather than query parameters (e.g. a SOAP envelope). Semantics
        otherwise identical to ``get()``.
        """
        return self._request("POST", url, content=content, headers=headers, refresh=refresh)

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        content: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        refresh: bool = False,
    ) -> RawResponse:
        cache_key = build_cache_key(method, url, params, content)

        if self._cache is not None and not refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        response = self._send_with_retry(
            method, url, params=params, content=content, headers=headers
        )

        if self._cache is not None:
            self._cache.set(cache_key, response)

        return response

    def _send_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        content: bytes | str | None,
        headers: Mapping[str, str] | None,
    ) -> RawResponse:
        attempt = 0
        while True:
            self._rate_limiter.acquire()

            try:
                httpx_response = self._client.request(
                    method,
                    url,
                    params=params,
                    content=content,
                    headers=headers,
                    timeout=self._timeout,
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
                self._retry_or_raise_http(url, attempt, status_code, httpx_response.text)
                attempt += 1
                continue

            if status_code >= 400:
                message = f"HTTP {status_code} for {url} (not retried)"
                logger.error(message, extra={"url": url, "status_code": status_code})
                raise ConnectorHTTPError(status_code, message, body=httpx_response.text)

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

    def _retry_or_raise_http(self, url: str, attempt: int, status_code: int, body: str) -> None:
        permanent = self._is_permanent_failure is not None and self._is_permanent_failure(
            status_code, body
        )
        if permanent or attempt >= self._max_retries:
            message = f"HTTP {status_code} for {url} after {attempt + 1} attempt(s)"
            logger.error(message, extra={"url": url, "status_code": status_code})
            if status_code == 429:
                raise ConnectorRateLimitError(message, body=body)
            raise ConnectorHTTPError(status_code, message, body=body)
        self._sleep(self._backoff_base * (2**attempt))


__all__ = ["ConnectorHttpClient"]
