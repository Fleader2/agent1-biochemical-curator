"""In-memory response caching for outbound connector requests.

``docs/03_agent_behavior.md`` ("Search Rate Limiting") requires connectors to
"cache successful responses" and "avoid duplicate requests". This cache is a
request-avoidance optimization only, distinct from the durable,
append-only ``external_record`` audit trail: a cache hit means the network
was not asked again, not that a database row was or was not written (that is
a later, separate persistence increment). A refresh/bypass path exists
precisely so a cached response can never hide the fact that an upstream
resource has changed.

Only an in-memory implementation is provided here, per this increment's
scope. A durable (database-backed) cache is future work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.connectors.base import RawResponse


def build_cache_key(method: str, url: str, params: Mapping[str, Any] | None) -> str:
    """Build a deterministic cache key that distinguishes request parameters.

    Two requests to the same URL with different parameters (or a different
    HTTP method) must never collide; two requests with the same parameters in
    a different order must collide, since they are the same request.
    """
    normalized_params = tuple(sorted((params or {}).items(), key=lambda item: item[0]))
    return f"{method.upper()} {url} {normalized_params!r}"


class ResponseCache(Protocol):
    """Something that can remember and recall a previous ``RawResponse``."""

    def get(self, key: str) -> RawResponse | None:
        """Return the cached response for ``key``, or ``None`` on a miss."""
        ...

    def set(self, key: str, response: RawResponse) -> None:
        """Store ``response`` under ``key``, replacing any prior entry."""
        ...


class InMemoryResponseCache:
    """A plain in-process cache with no eviction or expiry.

    Sufficient for a single connector run/test; not suitable as a durable
    cache across process restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, RawResponse] = {}

    def get(self, key: str) -> RawResponse | None:
        return self._store.get(key)

    def set(self, key: str, response: RawResponse) -> None:
        self._store[key] = response


__all__ = ["InMemoryResponseCache", "ResponseCache", "build_cache_key"]
