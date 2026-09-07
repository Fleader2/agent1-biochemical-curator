"""Tests for the SABIO-RK connector: retrieval, parsing, and normalization.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using synthetic, hand-constructed Solr response
JSON shaped after this increment's live-verified field layout (see
``app/connectors/sabiork.py``'s module docstring) -- these fixtures are not
a live snapshot and should not be read as a guarantee of today's exact
field values.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.sabiork import (
    SabioKineticParameter,
    SabioKineticRecord,
    SabiorkConnector,
    parse_kinetic_law_json,
    parse_solr_response,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_BASE_URL = "https://example.invalid/sabiork"


def _entry_json(**overrides: object) -> dict:
    base = {
        "kineticlaw": {
            "parameter": [
                {
                    "name": "Km",
                    "role": "Constant",
                    "parameter_type": {"name": "Km"},
                    "start_value": "0.5",
                    "unit": {"name": "mM"},
                    "species": {"species_key": "S1"},
                    "comment": "substrate saturation",
                },
                {
                    "name": "kcat",
                    "role": "Constant",
                    "parameter_type": {"name": "kcat"},
                    "start_value": "12.0",
                    "unit": {"name": "1/s"},
                },
                {
                    "name": "S",
                    "role": "Variable",
                    "parameter_type": {"name": "concentration"},
                    "start_value": "1.0",
                    "unit": {"name": "mM"},
                },
            ]
        },
        "general": {
            "organism": {"name": "Escherichia coli", "ncbi_taxonomy_id": "511145"},
            "strain": "K12",
            "tissue": None,
        },
        "reaction": {"equation": "A + B <=> C"},
        "enzyme_description": {
            "ec_number": "1.1.1.1",
            "enzyme_name": "test dehydrogenase",
            "wildtype": True,
            "is_recombinant": False,
            "proteins": [{"uniprot_id": "P12345"}],
        },
        "experimental_conditions": {
            "buffer": "phosphate buffer",
            "envvar_ph": {"start_value": "7.0"},
            "envvar_temperature": {"start_value": "25", "unit": {"name": "°C"}},
        },
        "publication": {"pubmed_id": "12345678", "title": "A paper about an enzyme"},
    }
    base.update(overrides)
    return base


def _solr_response(docs: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"response": {"numFound": len(docs), "docs": docs}})


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


def test_sabiork_connector_source_is_sabiork() -> None:
    assert SabiorkConnector.source == SourceType.SABIORK


# --- parse_solr_response ------------------------------------------------------


def test_parse_solr_response_empty_result_is_not_an_error() -> None:
    assert parse_solr_response(json.dumps({"response": {"numFound": 0, "docs": []}})) == []


def test_parse_solr_response_rejects_invalid_json() -> None:
    with pytest.raises(ConnectorParseError):
        parse_solr_response("not json")


def test_parse_solr_response_rejects_missing_docs() -> None:
    with pytest.raises(ConnectorParseError):
        parse_solr_response(json.dumps({"response": {}}))


# --- parse_kinetic_law_json ---------------------------------------------------


def test_parse_kinetic_law_json_extracts_constant_role_parameters_only() -> None:
    record = parse_kinetic_law_json("42", json.dumps(_entry_json()))
    assert isinstance(record, SabioKineticRecord)
    assert record.entry_id == "42"
    # Only the two "Constant"-role parameters, not the "Variable" one.
    assert len(record.parameters) == 2
    assert {p.parameter_type for p in record.parameters} == {"Km", "kcat"}
    assert record.ec_number == "1.1.1.1"
    assert record.uniprot_ids == ("P12345",)
    assert record.organism == "Escherichia coli"
    assert record.strain == "K12"
    assert record.ph == "7.0"
    assert record.temperature == "25"
    assert record.pubmed_id == "12345678"


def test_parse_kinetic_law_json_rejects_invalid_json() -> None:
    with pytest.raises(ConnectorParseError):
        parse_kinetic_law_json("42", "not json")


def test_parse_kinetic_law_json_missing_sections_does_not_raise() -> None:
    """Missing optional sections (e.g. no publication) yield None fields, not an error."""
    record = parse_kinetic_law_json("42", json.dumps({"kineticlaw": {"parameter": []}}))
    assert record.parameters == ()
    assert record.pubmed_id is None
    assert record.ec_number is None


def test_parse_kinetic_law_json_rejects_non_object_payload() -> None:
    with pytest.raises(ConnectorParseError):
        parse_kinetic_law_json("42", json.dumps(["not", "an", "object"]))


def test_raw_retains_full_payload() -> None:
    payload = _entry_json()
    record = parse_kinetic_law_json("42", json.dumps(payload))
    assert record.raw == payload


# --- search() -----------------------------------------------------------------


def test_sabiork_search_by_ec_number() -> None:
    handler = _RecordingHandler(
        _solr_response([{"EntryID": ["42"], "ECNumber": ["1.1.1.1"]}])
    )
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("1.1.1.1")

    assert len(hits) == 1
    assert hits[0].entry_id == "42"
    assert hits[0].ec_numbers == ("1.1.1.1",)
    request = handler.requests[0]
    assert request.url.params["q"] == "ECNumber:1.1.1.1"


def test_sabiork_search_combines_optional_filters() -> None:
    handler = _RecordingHandler(_solr_response([]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)

    connector.search("1.1.1.1", organism="Homo sapiens", uniprot_id="P12345")

    query = handler.requests[0].url.params["q"]
    assert "ECNumber:1.1.1.1" in query
    assert 'Organism:"Homo sapiens"' in query
    assert "UniProtID:P12345" in query


def test_sabiork_search_empty_result_is_not_an_error() -> None:
    handler = _RecordingHandler(_solr_response([]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.search("9.9.9.9") == []


def test_sabiork_search_rejects_empty_query() -> None:
    handler = _RecordingHandler(_solr_response([]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ValueError):
        connector.search("   ")


# --- fetch() -------------------------------------------------------------------


def test_sabiork_fetch_returns_parsed_record() -> None:
    entry = _entry_json()
    handler = _RecordingHandler(
        _solr_response([{"EntryID": ["42"], "Json": [json.dumps(entry)]}])
    )
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)

    record = connector.fetch("42")

    assert record is not None
    assert record.entry_id == "42"
    assert len(record.parameters) == 2
    request = handler.requests[0]
    assert request.url.params["fl"] == "EntryID,Json"


def test_sabiork_fetch_no_such_entry_returns_none() -> None:
    handler = _RecordingHandler(_solr_response([]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.fetch("does-not-exist") is None


def test_sabiork_fetch_missing_json_field_raises_parse_error() -> None:
    handler = _RecordingHandler(_solr_response([{"EntryID": ["42"]}]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ConnectorParseError):
        connector.fetch("42")


def test_sabiork_fetch_404_returns_none() -> None:
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.fetch("42") is None


def test_sabiork_fetch_rejects_empty_id() -> None:
    handler = _RecordingHandler(_solr_response([]))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ValueError):
        connector.fetch("")


# --- normalize() ---------------------------------------------------------------


def test_sabiork_normalize_never_merges_multiple_parameters() -> None:
    record = parse_kinetic_law_json("42", json.dumps(_entry_json()))
    connector = SabiorkConnector.__new__(SabiorkConnector)
    parameters = connector.normalize(record)
    assert len(parameters) == 2
    assert all(isinstance(p, SabioKineticParameter) for p in parameters)


# --- from_settings() -------------------------------------------------------------


def test_sabiork_from_settings_requires_configured_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.settings import Settings

    settings = Settings.model_construct(sabiork_base_url=None)  # type: ignore[call-arg]
    handler = _RecordingHandler(_solr_response([]))
    with pytest.raises(ValueError):
        SabiorkConnector.from_settings(_client_for(handler), settings)


# --- retry/backoff/cache reuse (shared foundation, not reimplemented) -----------


def test_sabiork_uses_shared_http_client_retry() -> None:
    handler = _RecordingHandler(
        [httpx.Response(500, text="oops"), _solr_response([])]
    )
    client = ConnectorHttpClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=2,
        backoff_base_seconds=0,
        sleep=lambda _seconds: None,
    )
    connector = SabiorkConnector(client, base_url=_BASE_URL)
    connector.search("1.1.1.1")
    assert len(handler.requests) == 2


def test_sabiork_raises_connector_http_error_for_permanent_failure() -> None:
    handler = _RecordingHandler(httpx.Response(400, text="bad request"))
    connector = SabiorkConnector(_client_for(handler), base_url=_BASE_URL)
    with pytest.raises(ConnectorHTTPError):
        connector.search("1.1.1.1")
