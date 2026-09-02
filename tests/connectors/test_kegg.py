"""Tests for the KEGG connector: retrieval, parsing, and normalization.

Includes the KEGG connector tests required by ``docs/05_testing.md``
("KEGG Connector Tests"): ``test_kegg_search``, ``test_kegg_get``,
``test_kegg_identifier_parsing``, ``test_kegg_reaction_parsing``,
``test_kegg_compound_parsing``, ``test_kegg_rate_limit_enforced``,
``test_kegg_error_handled``, ``test_kegg_cache_used``.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using the static fixtures under
``tests/fixtures/kegg/``. Those fixtures are synthetic, hand-constructed
examples that follow KEGG's documented flat-file grammar
(https://www.kegg.jp/kegg/rest/keggapi.html); they are not a live snapshot
of the current KEGG service and should not be read as a guarantee of
today's exact field values.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggConnector,
    KeggFlatFileRecord,
    KeggReactionRecord,
    normalize_compound,
    normalize_reaction,
    parse_find_response,
    parse_flat_file,
    split_kegg_identifier,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "kegg"
_BASE_URL = "https://example.invalid/kegg"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class _PathRoutingHandler:
    """An ``httpx.MockTransport`` handler that serves canned text by exact URL.

    Tracks every request it receives so tests can assert on cache/retry
    behavior (call counts) and on the exact request path/query produced by
    the connector.
    """

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = str(request.url)
        try:
            return self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected request URL: {key}") from exc


def _client_for(handler: _PathRoutingHandler, **kwargs: object) -> ConnectorHttpClient:
    return ConnectorHttpClient(httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


# --- KEGG Connector Tests (docs/05_testing.md) -------------------------------


def test_kegg_search() -> None:
    """``search()`` calls KEGG's ``find`` endpoint and returns structured hits."""
    handler = _PathRoutingHandler(
        {
            f"{_BASE_URL}/find/compound/glucose": httpx.Response(
                200, text=_fixture("find_compound_glucose.txt")
            )
        }
    )
    connector = KeggConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("glucose", database="compound")

    assert len(handler.requests) == 1
    assert len(hits) == 2
    assert hits[0].entry_id == "cpd:C00031"
    assert hits[0].description == "D-Glucose; Grape sugar; Dextrose"
    assert hits[1].entry_id == "cpd:C00221"
    assert hits[1].description == "beta-D-Glucose"


def test_kegg_get() -> None:
    """``fetch()`` calls KEGG's ``get`` endpoint and returns a parsed record."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(200, text=_fixture("get_compound_c00031.txt"))}
    )
    connector = KeggConnector(_client_for(handler), base_url=_BASE_URL)

    record = connector.fetch("C00031")

    assert len(handler.requests) == 1
    assert isinstance(record, KeggFlatFileRecord)
    assert record.entry_id == "C00031"
    assert record.entry_type == "Compound"


def test_kegg_identifier_parsing() -> None:
    """KEGG identifiers with and without a database prefix are split correctly."""
    assert split_kegg_identifier("cpd:C00031") == ("cpd", "C00031")
    assert split_kegg_identifier("sce:YNR016C") == ("sce", "YNR016C")
    assert split_kegg_identifier("C00031") == (None, "C00031")
    assert split_kegg_identifier("  cpd:C00031  ") == ("cpd", "C00031")


def test_kegg_identifier_parsing_rejects_empty_identifier() -> None:
    with pytest.raises(ValueError, match="empty"):
        split_kegg_identifier("   ")


def test_kegg_reaction_parsing() -> None:
    """A KEGG reaction ``get`` response is parsed and normalized correctly."""
    raw = parse_flat_file(_fixture("get_reaction_r00001.txt"))
    reaction = normalize_reaction(raw)

    assert isinstance(reaction, KeggReactionRecord)
    assert reaction.entry_id == "R00001"
    assert reaction.names == ("polyphosphate polyphosphohydrolase",)
    assert reaction.definition == "Polyphosphate + n H2O <=> (n+1) Oligophosphate"
    assert reaction.equation == "C00404 + n C00001 <=> (n+1) C02174"
    assert reaction.enzymes == ("3.6.1.10",)
    assert reaction.pathways == ("map00190  Oxidative phosphorylation",)
    # Nothing is discarded: the raw parsed record is still reachable.
    assert reaction.raw.entry_type == "Reaction"


def test_kegg_compound_parsing() -> None:
    """A KEGG compound ``get`` response is parsed and normalized correctly.

    Also proves multi-line ``NAME`` continuation and repeated ``PATHWAY``/
    ``DBLINKS`` rows are preserved rather than collapsed or truncated.
    """
    raw = parse_flat_file(_fixture("get_compound_c00031.txt"))
    compound = normalize_compound(raw)

    assert isinstance(compound, KeggCompoundRecord)
    assert compound.entry_id == "C00031"
    assert compound.names == ("D-Glucose", "Grape sugar", "Dextrose")
    assert compound.formula == "C6H12O6"
    assert compound.exact_mass == "180.0634"
    assert compound.mol_weight == "180.1559"
    assert compound.pathways == (
        "map00010  Glycolysis / Gluconeogenesis",
        "map00030  Pentose phosphate pathway",
        "map01100  Metabolic pathways",
    )
    # DBLINKS has no typed field of its own but must still be reachable via raw.
    assert compound.raw.fields["DBLINKS"] == (
        "CAS: 50-99-7",
        "PubChem: 3333",
        "ChEBI: 4167",
    )


def test_kegg_rate_limit_enforced() -> None:
    """KEGG requests go through the injected rate limiter, once per attempt."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(200, text=_fixture("get_compound_c00031.txt"))}
    )
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    connector = KeggConnector(
        _client_for(handler, rate_limiter=_RecordingLimiter()), base_url=_BASE_URL
    )

    connector.fetch("C00031")

    assert len(acquire_calls) == 1


def test_kegg_error_handled() -> None:
    """An upstream failure raises a ConnectorError, not an empty/invented result."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/find/compound/glucose": httpx.Response(503, text="internal error")}
    )
    connector = KeggConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError) as excinfo:
        connector.search("glucose", database="compound")

    assert excinfo.value.status_code == 503


def test_kegg_cache_used() -> None:
    """Repeated identical KEGG requests are served from cache, not re-fetched."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(200, text=_fixture("get_compound_c00031.txt"))}
    )
    connector = KeggConnector(
        _client_for(handler, cache=InMemoryResponseCache()), base_url=_BASE_URL
    )

    first = connector.fetch("C00031")
    second = connector.fetch("C00031")

    assert len(handler.requests) == 1
    assert first == second


# --- Empty result vs. failure, distinguished ---------------------------------


def test_kegg_search_returns_empty_list_for_no_hits() -> None:
    """A legitimate zero-result search returns ``[]``, not an error."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/find/compound/nonexistent-compound": httpx.Response(200, text="")}
    )
    connector = KeggConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("nonexistent-compound", database="compound")

    assert hits == []


def test_kegg_fetch_returns_none_for_unknown_identifier() -> None:
    """A KEGG 404 for ``get`` means "no such record" -- ``None``, not invented data."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C99999": httpx.Response(404, text="not found")}
    )
    connector = KeggConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    record = connector.fetch("C99999")

    assert record is None


def test_kegg_fetch_still_raises_for_non_404_failure() -> None:
    """A non-404 failure on ``fetch()`` still raises -- only 404 means "no record"."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(500, text="internal error")}
    )
    connector = KeggConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.fetch("C00031")


# --- Malformed content ---------------------------------------------------------


def test_kegg_get_malformed_response_raises_parse_error() -> None:
    """A 200 response with no recognizable ENTRY line is a parse failure."""
    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(200, text="not a kegg record at all")}
    )
    connector = KeggConnector(_client_for(handler), base_url=_BASE_URL)

    with pytest.raises(ConnectorParseError):
        connector.fetch("C00031")


def test_parse_find_response_rejects_line_without_tab() -> None:
    with pytest.raises(ConnectorParseError):
        parse_find_response("cpd:C00031 D-Glucose without a tab separator")


def test_parse_flat_file_rejects_continuation_before_any_field() -> None:
    with pytest.raises(ConnectorParseError):
        parse_flat_file("            stray continuation line\n///\n")


def test_parse_flat_file_rejects_empty_text() -> None:
    with pytest.raises(ConnectorParseError):
        parse_flat_file("")


# --- Pure parsing functions, exercised directly (no HTTP) ---------------------


def test_parse_find_response_ignores_blank_lines() -> None:
    hits = parse_find_response("\ncpd:C00031\tD-Glucose\n\n")
    assert len(hits) == 1
    assert hits[0].entry_id == "cpd:C00031"


def test_parse_find_response_empty_text_is_empty_list() -> None:
    assert parse_find_response("") == []
    assert parse_find_response("   \n  \n") == []


def test_parse_flat_file_preserves_entry_id_and_type() -> None:
    record = parse_flat_file(_fixture("get_compound_c00031.txt"))
    assert record.entry_id == "C00031"
    assert record.entry_type == "Compound"


# --- normalize() dispatch -----------------------------------------------------


def test_kegg_normalize_dispatches_compound() -> None:
    connector = KeggConnector(_client_for(_PathRoutingHandler({})), base_url=_BASE_URL)
    raw = parse_flat_file(_fixture("get_compound_c00031.txt"))

    normalized = connector.normalize(raw)

    assert isinstance(normalized, KeggCompoundRecord)


def test_kegg_normalize_dispatches_reaction() -> None:
    connector = KeggConnector(_client_for(_PathRoutingHandler({})), base_url=_BASE_URL)
    raw = parse_flat_file(_fixture("get_reaction_r00001.txt"))

    normalized = connector.normalize(raw)

    assert isinstance(normalized, KeggReactionRecord)


def test_kegg_normalize_returns_raw_record_for_unmodeled_entry_type() -> None:
    """An entry type with no specific normalized shape (e.g. a gene) is passed through."""
    connector = KeggConnector(_client_for(_PathRoutingHandler({})), base_url=_BASE_URL)
    raw = KeggFlatFileRecord(entry_id="YNR016C", entry_type="CDS", fields={"NAME": ("ACC1",)})

    normalized = connector.normalize(raw)

    assert normalized is raw


# --- Stable identifier preservation --------------------------------------------


def test_kegg_search_preserves_full_prefixed_identifier() -> None:
    """Search hit identifiers keep their KEGG database prefix, unmodified."""
    handler = _PathRoutingHandler(
        {
            f"{_BASE_URL}/find/compound/glucose": httpx.Response(
                200, text=_fixture("find_compound_glucose.txt")
            )
        }
    )
    connector = KeggConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("glucose", database="compound")

    assert all(hit.entry_id.startswith("cpd:") for hit in hits)


# --- Construction / configuration ----------------------------------------------


def test_kegg_connector_rejects_empty_base_url() -> None:
    handler = _PathRoutingHandler({})
    with pytest.raises(ValueError, match="base_url"):
        KeggConnector(_client_for(handler), base_url="   ")


def test_kegg_connector_from_settings_uses_configured_base_url() -> None:
    from app.config.settings import Settings

    handler = _PathRoutingHandler(
        {f"{_BASE_URL}/get/C00031": httpx.Response(200, text=_fixture("get_compound_c00031.txt"))}
    )
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        kegg_base_url=_BASE_URL,
    )

    connector = KeggConnector.from_settings(_client_for(handler), settings=settings)
    record = connector.fetch("C00031")

    assert record is not None
    assert record.entry_id == "C00031"


def test_kegg_connector_from_settings_raises_when_unconfigured() -> None:
    from app.config.settings import Settings

    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        kegg_base_url=None,
    )

    with pytest.raises(ValueError, match="kegg_base_url"):
        KeggConnector.from_settings(_client_for(_PathRoutingHandler({})), settings=settings)


def test_kegg_connector_declares_kegg_source() -> None:
    connector = KeggConnector(_client_for(_PathRoutingHandler({})), base_url=_BASE_URL)
    assert connector.source == SourceType.KEGG
