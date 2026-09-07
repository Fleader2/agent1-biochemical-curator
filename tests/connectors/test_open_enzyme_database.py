"""Tests for the Open Enzyme Database (OED) connector: retrieval, parsing, splitting.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using synthetic response JSON shaped after this
increment's live-verified field layout (see
``app/connectors/open_enzyme_database.py``'s module docstring) -- these
fixtures are not a live snapshot and should not be read as a guarantee of
today's exact field values.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.open_enzyme_database import (
    OedDataRow,
    OedKineticParameter,
    OpenEnzymeDatabaseConnector,
    parse_data_response,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_BASE_URL = "https://example.invalid/oed"


def _row(**overrides: object) -> dict:
    base = {
        "ec_number": "1.1.1.1",
        "substrate": "ethanol",
        "organism": "Homo sapiens",
        "uniprot_id": "P00325",
        "enzyme_type": "wildtype",
        "pubmedid": "999999",
        "kcat": "12.3",
        "km": "0.8",
        "kcat_km": None,
        "kcat_unit": "1/s",
        "km_unit": "mM",
        "kcat_km_unit": None,
    }
    base.update(overrides)
    return base


class _RecordingHandler:
    """Mirrors ``tests/connectors/test_uniprot.py``'s own handler exactly."""

    def __init__(
        self, responses: httpx.Response | Exception | list[httpx.Response | Exception]
    ) -> None:
        self._responses = responses if isinstance(responses, list) else [responses]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        result = self._responses[index]
        if isinstance(result, Exception):
            raise result
        return result


def _client_for(handler: _RecordingHandler, **kwargs: object) -> ConnectorHttpClient:
    return ConnectorHttpClient(httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


# --- source attribute --------------------------------------------------------


def test_oed_connector_source_is_oed() -> None:
    assert OpenEnzymeDatabaseConnector.source == SourceType.OED


# --- parse_data_response -------------------------------------------------------


def test_parse_data_response_bare_array() -> None:
    rows = parse_data_response(json.dumps([_row()]))
    assert len(rows) == 1
    assert isinstance(rows[0], OedDataRow)
    assert rows[0].ec_number == "1.1.1.1"
    assert rows[0].pubmed_id == "999999"


def test_parse_data_response_envelope_object() -> None:
    rows = parse_data_response(json.dumps({"results": [_row()]}))
    assert len(rows) == 1


def test_parse_data_response_empty_result_is_not_an_error() -> None:
    assert parse_data_response(json.dumps([])) == []


def test_parse_data_response_rejects_invalid_json() -> None:
    with pytest.raises(ConnectorParseError):
        parse_data_response("not json")


def test_parse_data_response_rejects_object_without_results() -> None:
    with pytest.raises(ConnectorParseError):
        parse_data_response(json.dumps({"count": 0}))


def test_parse_data_response_rejects_non_object_row() -> None:
    with pytest.raises(ConnectorParseError):
        parse_data_response(json.dumps(["not-an-object"]))


def test_raw_retains_full_row() -> None:
    payload = _row()
    rows = parse_data_response(json.dumps([payload]))
    assert rows[0].raw == payload


# --- search() -----------------------------------------------------------------


def test_oed_search_requires_at_least_one_filter() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=[]))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ValueError):
        connector.search()


def test_oed_search_by_ec_number() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=[_row()]))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)

    rows = connector.search(ec_number="1.1.1.1")

    assert len(rows) == 1
    request = handler.requests[0]
    assert request.url.path == "/oed/data"
    assert request.url.params["ec_number"] == "1.1.1.1"


def test_oed_search_calls_only_data_endpoint() -> None:
    """Structural guard: no prediction endpoint is ever called (Increment A Step 11)."""
    handler = _RecordingHandler(httpx.Response(200, json=[]))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)
    connector.search(ec_number="1.1.1.1")
    for request in handler.requests:
        assert request.url.path == "/oed/data"


def test_oed_search_empty_result_is_not_an_error() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=[]))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.search(ec_number="9.9.9.9") == []


# --- fetch() -------------------------------------------------------------------


def test_oed_fetch_not_implemented() -> None:
    """OED has no fetch-by-id endpoint; callers must use search()."""
    handler = _RecordingHandler(httpx.Response(200, json=[]))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(NotImplementedError):
        connector.fetch("anything")
    assert handler.requests == []


# --- normalize(): splitting and deterministic identity --------------------------


def test_oed_normalize_splits_one_row_into_multiple_parameters() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row = parse_data_response(json.dumps([_row()]))[0]
    parameters = connector.normalize(row)
    assert len(parameters) == 2  # kcat and km; kcat_km is None
    assert {p.parameter_type for p in parameters} == {"kcat", "km"}
    assert all(isinstance(p, OedKineticParameter) for p in parameters)


def test_oed_normalize_never_merges_or_averages() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row = parse_data_response(json.dumps([_row(kcat_km="4.1", kcat_km_unit="1/(s*mM)")]))[0]
    parameters = connector.normalize(row)
    assert len(parameters) == 3
    values = {p.parameter_type: p.value for p in parameters}
    assert values == {"kcat": "12.3", "km": "0.8", "kcat_km": "4.1"}


def test_oed_normalize_identity_is_deterministic() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row = parse_data_response(json.dumps([_row()]))[0]
    first = connector.normalize(row)
    second = connector.normalize(row)
    assert {p.source_identifier for p in first} == {p.source_identifier for p in second}


def test_oed_normalize_distinguishes_different_rows() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row_a = parse_data_response(json.dumps([_row(pubmedid="111")]))[0]
    row_b = parse_data_response(json.dumps([_row(pubmedid="222")]))[0]
    ids_a = {p.source_identifier for p in connector.normalize(row_a)}
    ids_b = {p.source_identifier for p in connector.normalize(row_b)}
    assert ids_a.isdisjoint(ids_b)


def test_oed_normalize_never_invents_source_lineage() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row = parse_data_response(json.dumps([_row()]))[0]
    for parameter in connector.normalize(row):
        assert parameter.original_source is None
        assert parameter.original_source_identifier is None


def test_oed_normalize_no_parameter_values_yields_empty_tuple() -> None:
    connector = OpenEnzymeDatabaseConnector.__new__(OpenEnzymeDatabaseConnector)
    row = parse_data_response(json.dumps([_row(kcat=None, km=None, kcat_km=None)]))[0]
    assert connector.normalize(row) == ()


# --- from_settings() -------------------------------------------------------------


def test_oed_from_settings_requires_configured_base_url() -> None:
    from app.config.settings import Settings

    settings = Settings.model_construct(oed_base_url=None)  # type: ignore[call-arg]
    handler = _RecordingHandler(httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        OpenEnzymeDatabaseConnector.from_settings(_client_for(handler), settings)


# --- retry/backoff (shared foundation, not reimplemented) -----------------------


def test_oed_raises_connector_http_error_for_permanent_failure() -> None:
    handler = _RecordingHandler(httpx.Response(400, text="bad request"))
    connector = OpenEnzymeDatabaseConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ConnectorHTTPError):
        connector.search(ec_number="1.1.1.1")
