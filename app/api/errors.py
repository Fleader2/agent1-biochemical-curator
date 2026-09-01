"""API error handling.

Every failure is returned in the envelope defined by ``docs/04_api_spec.md``:

    {"detail": {"code": ..., "message": ..., "context": ...}}

Internal exception text is never returned to the client, so that credentials or
connection strings cannot leak through an error response.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.logging import get_logger
from app.schemas.common import ErrorCode, ErrorDetail, ErrorResponse

logger = get_logger("api.errors")

# Written numerically because Starlette has renamed its 422 constant across
# versions; the status code itself is stable.
HTTP_422_VALIDATION_ERROR = 422

_STATUS_CODE_MAP: dict[int, ErrorCode] = {
    status.HTTP_400_BAD_REQUEST: ErrorCode.BAD_REQUEST,
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
    HTTP_422_VALIDATION_ERROR: ErrorCode.VALIDATION_ERROR,
    status.HTTP_500_INTERNAL_SERVER_ERROR: ErrorCode.INTERNAL_ERROR,
}


class ApiError(Exception):
    """Application error carrying an API error code and HTTP status."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = context


def _error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(detail=ErrorDetail(code=code, message=message, context=context))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    """Install the handlers that produce the standard error envelope."""

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.context)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            HTTP_422_VALIDATION_ERROR,
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed.",
            {"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(exc.status_code, ErrorCode.HTTP_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled application error",
            extra={"path": request.url.path, "method": request.method},
        )
        del exc
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorCode.INTERNAL_ERROR,
            "An internal error occurred.",
        )
