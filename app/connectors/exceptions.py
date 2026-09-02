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
    """The upstream source returned an HTTP error status."""

    def __init__(self, status_code: int, message: str | None = None) -> None:
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class ConnectorRateLimitError(ConnectorHTTPError):
    """The upstream source responded with HTTP 429 after retries were exhausted."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(429, message or "rate limited (HTTP 429)")


class ConnectorParseError(ConnectorError):
    """A response could not be parsed or normalized into a source-native record."""


__all__ = [
    "ConnectorError",
    "ConnectorHTTPError",
    "ConnectorNetworkError",
    "ConnectorParseError",
    "ConnectorRateLimitError",
]
