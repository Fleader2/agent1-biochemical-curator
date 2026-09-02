"""Rate limiting for outbound connector requests.

``docs/03_agent_behavior.md`` ("Search Rate Limiting") requires every
connector to enforce source-specific, configurable rate limiting. Nothing in
the current specification calls for coordinating limits across multiple
processes or hosts, so a single-process, per-connector-instance limiter is
the smallest useful abstraction -- not a distributed token-bucket service.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class RateLimiter(Protocol):
    """Something a connector can ask permission from before each request."""

    def acquire(self) -> None:
        """Block (if necessary) until another request is allowed to proceed."""
        ...


class NullRateLimiter:
    """A rate limiter that never waits. Default when no limit is configured."""

    def acquire(self) -> None:
        return None


class IntervalRateLimiter:
    """Enforces a minimum interval between successive requests.

    A simple leaky-bucket-of-one: the first call never waits, and every
    subsequent call waits only long enough that at least ``min_interval_seconds``
    has elapsed since the previous call started.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    def acquire(self) -> None:
        now = self._clock()
        if self._last_call is not None:
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_call = now


__all__ = ["IntervalRateLimiter", "NullRateLimiter", "RateLimiter"]
