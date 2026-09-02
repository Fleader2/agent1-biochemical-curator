"""Contract test for the shared SourceConnector protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.connectors.base import SourceConnector
from app.models.enums import SourceType

pytestmark = pytest.mark.connector


@dataclass
class _MinimalConnector:
    """The smallest object that satisfies ``SourceConnector``."""

    source: SourceType = SourceType.KEGG

    def search(self, query: str, **kwargs: Any) -> list[Any]:
        return []

    def fetch(self, external_id: str, **kwargs: Any) -> None:
        return None

    def normalize(self, raw: Any) -> Any:
        return raw


class _MissingNormalize:
    """Implements everything except ``normalize()``."""

    source = SourceType.KEGG

    def search(self, query: str, **kwargs: Any) -> list[Any]:
        return []

    def fetch(self, external_id: str, **kwargs: Any) -> None:
        return None


def test_minimal_implementation_satisfies_source_connector_protocol() -> None:
    assert isinstance(_MinimalConnector(), SourceConnector)


def test_object_missing_normalize_does_not_satisfy_protocol() -> None:
    assert not isinstance(_MissingNormalize(), SourceConnector)
