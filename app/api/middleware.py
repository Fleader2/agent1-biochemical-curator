"""Request-scoped logging middleware.

Logs the fields required by ``docs/04_api_spec.md``: request id, endpoint,
method, response status, and duration. Request bodies, headers, and query
strings are not logged, so credentials cannot be captured incidentally.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config.logging import get_logger, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger("api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assign a request id, expose it on the response, and log the outcome."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        token = request_id_var.set(request_id)
        started = perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "endpoint": request.url.path,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            raise
        else:
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "endpoint": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
