"""Connector-specific error hierarchy.

A connector failure and a legitimate empty result must never be confused
(``.cursor/rules/02-architecture.mdc``: "Connector failures must remain
distinguishable from legitimate zero-result searches"). Every failure mode
below is therefore always raised, never returned — a ``search()``/``fetch()``
implementation that finds nothing returns an empty result, and only an actual
failure to retrieve or interpret a response raises one of these.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for all connector failures."""


class ConnectorNetworkError(ConnectorError):
    """A request could not complete: a timeout or another transport-level failure.

    Covers both slow responses and lower-level connection failures (DNS,
    connection refused, connection reset) alike, since both mean the same
    thing to a caller: no response was obtained, and retrying may help.
    """


class ConnectorHTTPError(ConnectorError):
    """The upstream source returned an HTTP error status.

    ``body`` (the raw response text, when available) lets a connector
    inspect what an opaque HTTP status actually meant -- some sources (e.g.
    BRENDA's SOAP interface: an authentication failure and a generic server
    error are both plain HTTP 500 at the transport level, distinguishable
    only by a fault embedded in the body) require this to raise a more
    specific error themselves after catching this one.
    """

    def __init__(
        self, status_code: int, message: str | None = None, *, body: str | None = None
    ) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message or f"HTTP {status_code}")


class ConnectorRateLimitError(ConnectorHTTPError):
    """The upstream source responded with HTTP 429 after retries were exhausted."""

    def __init__(self, message: str | None = None, *, body: str | None = None) -> None:
        super().__init__(429, message or "rate limited (HTTP 429)", body=body)


class ConnectorAuthenticationError(ConnectorError):
    """The upstream source rejected the configured credentials.

    Kept distinct from ``ConnectorHTTPError`` because some sources signal an
    authentication failure through response content rather than a dedicated
    HTTP status the shared HTTP layer could recognize on its own -- the
    connector-level code that can parse that content is what raises this,
    typically by re-classifying a caught ``ConnectorHTTPError`` after
    inspecting its ``body``.
    """


class ConnectorParseError(ConnectorError):
    """A response could not be parsed or normalized into a source-native record."""


__all__ = [
    "ConnectorAuthenticationError",
    "ConnectorError",
    "ConnectorHTTPError",
    "ConnectorNetworkError",
    "ConnectorParseError",
    "ConnectorRateLimitError",
]
