"""Tests for shared error and pagination schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.deps import pagination_params
from app.schemas.common import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    Page,
    PaginationParams,
)

pytestmark = pytest.mark.unit


def test_pagination_defaults() -> None:
    params = pagination_params()

    assert params.limit == DEFAULT_LIMIT
    assert params.offset == 0


def test_pagination_rejects_limit_above_maximum() -> None:
    with pytest.raises(ValidationError):
        PaginationParams(limit=MAX_LIMIT + 1, offset=0)


def test_pagination_rejects_negative_offset() -> None:
    with pytest.raises(ValidationError):
        PaginationParams(limit=DEFAULT_LIMIT, offset=-1)


def test_pagination_rejects_zero_limit() -> None:
    with pytest.raises(ValidationError):
        PaginationParams(limit=0, offset=0)


def test_page_shape() -> None:
    page: Page[str] = Page(items=["a"], limit=DEFAULT_LIMIT, offset=0, total=1)

    assert page.model_dump() == {
        "items": ["a"],
        "limit": DEFAULT_LIMIT,
        "offset": 0,
        "total": 1,
    }


def test_error_response_shape() -> None:
    response = ErrorResponse(
        detail=ErrorDetail(
            code=ErrorCode.NOT_FOUND,
            message="Missing.",
            context={"entity_type": "reaction"},
        )
    )

    assert response.model_dump(mode="json") == {
        "detail": {
            "code": "NOT_FOUND",
            "message": "Missing.",
            "context": {"entity_type": "reaction"},
        }
    }


def test_error_context_is_optional() -> None:
    detail = ErrorDetail(code=ErrorCode.INTERNAL_ERROR, message="Failed.")

    assert detail.context is None
