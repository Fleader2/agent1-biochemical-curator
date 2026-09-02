"""Shared connector interface and value types.

This module defines the minimal contract every source-specific connector
(PubMed, KEGG, BRENDA, SGD, BioCyc, ...) will implement, and the one value
type the shared HTTP/cache foundation already needs today.

Two different transformations are both called out by this contract, and they
must not be collapsed into one another:

``parse_*()`` (source-specific, lives in each connector module, not defined
here): raw source representation -> source-native typed representation. For
example, KEGG flat text or PubMed XML turned into a plain Python structure
that says only what the source said.

``normalize()`` (part of :class:`SourceConnector` below): source-native
representation -> connector-normalized representation. This still belongs to
the connector and still contains no curation policy or cross-source entity
resolution -- it only puts the source-native record into a shape the rest of
Agent 1 can consume consistently across sources (for example, a common field
name for "external identifier" regardless of what the source itself calls
it).

Cross-source entity resolution (deciding that a KEGG compound and a ChEBI
compound are the same ``Compound`` row) is a later phase's concern and lives
in ``app/normalization/``, not in a connector's ``normalize()``.

No concrete connector exists yet (Phase 3 has not started implementing
individual sources), so ``search()``/``fetch()``/``normalize()`` below are
intentionally loosely typed with ``Any``. Committing now to concrete
dataclasses for search hits or parsed records would mean guessing at shapes
before a real source's response format has been designed against, which is
exactly the kind of over-generalization this increment avoids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.models.enums import SourceType


@dataclass(frozen=True, slots=True)
class RawResponse:
    """An unparsed HTTP response, as returned by ``ConnectorHttpClient``.

    This is the one shared value type the foundation itself needs: it is
    produced by ``app.connectors.http`` and stored/retrieved by
    ``app.connectors.cache``. ``content`` (raw bytes) is kept, not just
    ``text``, so that a later persistence increment can compute a stable
    ``raw_response_hash`` (``external_record.raw_response_hash``) from
    exactly what was received on the wire.
    """

    status_code: int
    url: str
    headers: dict[str, str]
    content: bytes
    text: str
    elapsed_seconds: float


@runtime_checkable
class SourceConnector(Protocol):
    """Minimal shared contract every source-specific connector implements.

    A connector performs retrieval and parsing only, never curation policy
    (``app/connectors/__init__.py``, ``.cursor/rules/02-architecture.mdc``).
    ``search()``/``fetch()`` return an empty/`None` result for a legitimate
    zero-result query; they raise an ``app.connectors.exceptions.ConnectorError``
    subclass only when retrieval or interpretation actually failed. The two
    must never be confused.
    """

    source: SourceType

    def search(self, query: str, **kwargs: Any) -> Any:
        """Search the source for candidates matching ``query``."""
        ...

    def fetch(self, external_id: str, **kwargs: Any) -> Any:
        """Retrieve a single record by the source's own stable identifier."""
        ...

    def normalize(self, raw: Any) -> Any:
        """Map a source-native parsed record onto Agent 1's connector-level shape."""
        ...


__all__ = ["RawResponse", "SourceConnector"]
