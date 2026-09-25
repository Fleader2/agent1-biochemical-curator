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


# --- Increment C.5: robust live-record parsing ---------------------------------
#
# Real Integration Pilot 1 Run 6 crashed with an uncaught AttributeError on every
# one of 7 real, live SABIO-RK entries for EC 2.3.1.86 (S. cerevisiae fatty-acyl-CoA
# synthase, FAS1/FAS2): experimental_conditions.envvar_temperature.unit was a bare
# string ("°C"), not the {"name": "..."} object parse_kinetic_law_json assumed. A
# live, read-only inspection of those same 7 records found one further, silent
# (non-crashing) instance of the identical assumption: general.strain/general.tissue
# are themselves always {"id": ..., "name": ...}-shaped objects. These tests convert
# both real, live-confirmed variants into deterministic, offline fixtures.


def test_c5_temperature_unit_as_bare_string_is_parsed() -> None:
    """The exact real, live shape that crashed Pilot 1 Run 6 for all 7 EC 2.3.1.86
    entries: envvar_temperature.unit is a bare string, not {"name": ...}."""
    entry = _entry_json(
        experimental_conditions={
            "buffer": "phosphate buffer",
            "envvar_ph": {"start_value": "6.5"},
            "envvar_temperature": {"start_value": "25.0", "unit": "°C"},
        }
    )
    record = parse_kinetic_law_json("18229", json.dumps(entry))
    assert record.temperature == "25.0"
    assert record.temperature_unit == "°C"


def test_c5_temperature_unit_as_object_still_parses() -> None:
    """The other, documented-only shape ({"name": ...}) must keep working exactly
    as before -- this is a supported variant, not the only one."""
    entry = _entry_json(
        experimental_conditions={
            "envvar_temperature": {"start_value": "25", "unit": {"name": "°C"}},
        }
    )
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert record.temperature_unit == "°C"


def test_c5_temperature_unit_null_is_none_not_fabricated() -> None:
    entry = _entry_json(
        experimental_conditions={"envvar_temperature": {"start_value": "25", "unit": None}}
    )
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert record.temperature == "25"
    assert record.temperature_unit is None


def test_c5_temperature_unit_missing_is_none_not_fabricated() -> None:
    entry = _entry_json(
        experimental_conditions={"envvar_temperature": {"start_value": "25"}}
    )
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert record.temperature == "25"
    assert record.temperature_unit is None


def test_c5_missing_experimental_conditions_fields_do_not_raise() -> None:
    """A real record with no experimental_conditions section at all -- every
    dependent field is None, never an error, never a guessed value."""
    entry = _entry_json(experimental_conditions={})
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert record.ph is None
    assert record.temperature is None
    assert record.temperature_unit is None
    assert record.buffer is None


def test_c5_strain_and_tissue_as_named_objects_are_parsed() -> None:
    """Real, live shape (Increment C.5 inspection): general.strain/general.tissue are
    always {"id": ..., "name": ...}-shaped objects, never bare strings. Pre-C.5 code
    stringified the whole dict (e.g. "{'id': 13, 'name': 'v.R'}") instead of
    extracting the reported name -- a silent, non-crashing data-corruption bug this
    increment also fixes."""
    entry = _entry_json(
        general={
            "organism": {"name": "Saccharomyces cerevisiae"},
            "strain": {"id": 13, "name": "v.R"},
            "tissue": {},
        }
    )
    record = parse_kinetic_law_json("18229", json.dumps(entry))
    assert record.strain == "v.R"
    assert record.tissue is None  # {} has no "name" key -- absence, not "{}" the string


def test_c5_strain_as_bare_string_still_parses() -> None:
    """The other, previously-assumed-universal shape (a bare string) must keep
    working exactly as before -- this is a supported variant, not the only one."""
    entry = _entry_json(general={"organism": {"name": "Escherichia coli"}, "strain": "K12"})
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert record.strain == "K12"


def test_c5_parameter_type_and_unit_as_bare_strings_still_parse() -> None:
    """kineticlaw.parameter[].parameter_type/unit are confirmed {"name": ...}-shaped
    for every one of the 7 real EC 2.3.1.86 entries examined -- but the same
    _named_or_scalar helper is applied here too (Increment C.5: "prefer one
    reusable helper" over field-specific patches), so a bare-string representation,
    if one is ever reported, parses correctly rather than crashing."""
    entry = _entry_json(
        kineticlaw={
            "parameter": [
                {
                    "name": "Km",
                    "role": "Constant",
                    "parameter_type": "Km",
                    "start_value": "0.5",
                    "unit": "mM",
                }
            ]
        }
    )
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert len(record.parameters) == 1
    assert record.parameters[0].parameter_type == "Km"
    assert record.parameters[0].unit == "mM"


def test_c5_genuinely_malformed_kineticlaw_section_raises_connector_parse_error() -> None:
    """A structure this parser cannot safely interpret even after every confirmed
    schema variant is accounted for -- kineticlaw itself is a bare string, not an
    object -- is raised as the connector's own, already-understood
    ConnectorParseError, never a bare AttributeError/TypeError/KeyError/IndexError
    that would escape uncaught (Pilot 1 Run 6's own original failure mode)."""
    entry = _entry_json(kineticlaw="not an object")
    with pytest.raises(ConnectorParseError):
        parse_kinetic_law_json("42", json.dumps(entry))


def test_c5_no_fabricated_value_or_unit_when_genuinely_absent() -> None:
    entry = _entry_json(
        kineticlaw={
            "parameter": [
                {
                    "name": "Km",
                    "role": "Constant",
                    "parameter_type": None,
                    "start_value": None,
                    "unit": None,
                }
            ]
        }
    )
    record = parse_kinetic_law_json("42", json.dumps(entry))
    assert len(record.parameters) == 1
    param = record.parameters[0]
    assert param.parameter_type is None
    assert param.value is None
    assert param.unit is None


def test_c5_parsing_is_deterministic() -> None:
    entry = _entry_json(
        experimental_conditions={"envvar_temperature": {"start_value": "25", "unit": "°C"}}
    )
    text = json.dumps(entry)
    first = parse_kinetic_law_json("42", text)
    second = parse_kinetic_law_json("42", text)
    assert first == second


def test_c5_real_ec_2_3_1_86_record_shape_survives_parsing_intact() -> None:
    """A hand-transcribed real SABIO-RK entry (EC 2.3.1.86, entry 18229, live-verified
    Increment C.5) -- proves this increment preserves actual kinetic information
    faithfully, not merely that it no longer crashes. FAS1/FAS2's real complex
    stoichiometry notation ("((P19097)*6(P07149)*6)") is preserved exactly as
    reported, never filtered or reinterpreted -- SABIO-RK's own notation for a
    hetero-oligomeric enzyme, not malformed data."""
    entry = {
        "kineticlaw": {
            "parameter": [
                {
                    "name": None,
                    "role": "Constant",
                    "parameter_type": {"id": 6, "name": "Vmax", "sbo_term": "SBO:0000186"},
                    "unit": {"id": 38, "name": "nmol/(min*mg)", "n_name": "mol*s^(-1)*g^(-1)"},
                    "start_value": 3340.0,
                    "species": {"species_ref_type": "species"},
                },
                {
                    "name": None,
                    "role": "Constant",
                    "parameter_type": {"id": 8, "name": "Km", "sbo_term": "SBO:0000027"},
                    "unit": {"id": 3, "name": "µM", "n_name": "M"},
                    "start_value": 18.0,
                    "species": {
                        "species_ref_type": "species",
                        "species_key": "n | Malonyl-CoA | Substrate",
                    },
                },
                {
                    "name": None,
                    "role": "Variable",
                    "parameter_type": {"id": 9, "name": "concentration"},
                    "unit": {"id": 3, "name": "µM"},
                    "start_value": 150.0,
                    "species": {
                        "species_ref_type": "species",
                        "species_key": "1 | Acetyl-CoA | Substrate",
                    },
                },
            ]
        },
        "general": {
            "organism": {"id": 4, "name": "Saccharomyces cerevisiae", "ncbi_taxonomy_id": 4932},
            "strain": {"id": 13, "name": "v.R"},
            "tissue": {},
        },
        "enzyme_description": {
            "ec_number": "2.3.1.86",
            "enzyme_name": "fatty-acyl-CoA synthase",
            "wildtype": "wildtype",
            "is_recombinant": False,
            "proteins": [
                {"uniprot_id": "((P19097)*6(P07149)*6)"},
                {"stoch_value": "6", "uniprot_id": "P19097"},
                {"stoch_value": "6", "uniprot_id": "P07149"},
            ],
        },
        "experimental_conditions": {
            "buffer": "100 mM Potassium phosphate, 4 mM Dithiothreitol, 2.5 mM EDTA, "
            "0.3 mg/ml Bovine serum albumin",
            "envvar_ph": {"start_value": 6.5, "unit": None},
            "envvar_temperature": {"start_value": 25.0, "unit": "°C"},
        },
        "reaction": {
            "equation": "2n NADPH + Acetyl-CoA + n Malonyl-CoA + H+ = Long-chain fatty acid "
            "+ n CO2 + 2n NADP+ + n+1 Coenzyme A"
        },
        "publication": {
            "pubmed_id": "7044669",
            "title": "Comparative studies on the kinetic parameters and product analyses of "
            "chicken and rat liver and yeast fatty acid synthetase",
        },
    }

    record = parse_kinetic_law_json("18229", json.dumps(entry))

    # Only the two "Constant"-role parameters -- the "Variable" concentration is not
    # a reported kinetic constant.
    assert len(record.parameters) == 2
    vmax, km = record.parameters
    assert vmax.parameter_type == "Vmax"
    assert vmax.unit == "nmol/(min*mg)"
    assert vmax.value == "3340.0"
    assert vmax.species_label is None  # no species_key on the overall-rate parameter
    assert km.parameter_type == "Km"
    assert km.unit == "µM"
    assert km.value == "18.0"
    assert km.species_label == "n | Malonyl-CoA | Substrate"

    assert record.ec_number == "2.3.1.86"
    assert record.enzyme_name == "fatty-acyl-CoA synthase"
    assert record.uniprot_ids == ("((P19097)*6(P07149)*6)", "P19097", "P07149")
    assert record.organism == "Saccharomyces cerevisiae"
    assert record.ncbi_taxonomy_id == "4932"
    assert record.strain == "v.R"
    assert record.tissue is None
    assert record.ph == "6.5"
    assert record.temperature == "25.0"
    assert record.temperature_unit == "°C"
    assert record.pubmed_id == "7044669"
    assert record.reaction_equation is not None and "Malonyl-CoA" in record.reaction_equation


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
