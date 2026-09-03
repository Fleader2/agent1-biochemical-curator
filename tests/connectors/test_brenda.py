"""Tests for the BRENDA connector: SOAP retrieval, delimited-format parsing, and normalization.

Includes the BRENDA connector tests required by ``docs/05_testing.md``
("BRENDA Connector Tests"): ``test_brenda_authentication_required``,
``test_brenda_kinetic_result_parsed``, ``test_brenda_organism_preserved``,
``test_brenda_parameter_units_preserved``,
``test_brenda_multiple_measurements_preserved``,
``test_brenda_rate_limit_enforced``,
``test_brenda_failure_does_not_create_negative_result``.

No test makes a real network call: every request is served by an
``httpx.MockTransport``. **Fixture provenance differs by file**, and this
matters for how much confidence to place in each:

* ``auth_fault.xml`` is a **byte-for-byte reproduction of a real live
  response** -- one actual SOAP request was made to
  ``https://www.brenda-enzymes.org/soap/brenda_server.php`` during this
  increment's verification pass, with an obviously-fake email and password,
  specifically to observe BRENDA's authentication-failure shape. This exact
  fault (faultcode ``401``, that exact faultstring) is confirmed real.
* ``km_multiple_measurements.xml``, ``km_empty.xml``, ``ki_single_measurement.xml``,
  ``ph_optimum.xml``, and ``recommended_name.xml`` are **not** live-observed
  -- no valid BRENDA account was available this increment, so a genuinely
  successful response was never seen. Their field names/nesting follow
  BRENDA's documented request/response grammar
  (https://www.brenda-enzymes.org/soap.php) and WSDL-confirmed complex-type
  field names (https://www.brenda-enzymes.org/soap/brenda.wsdl); the
  enclosing SOAP response envelope shape (``<methodResponse><return>...`` )
  follows conventional SOAP 1.1 RPC style, not something BRENDA-specific
  that was independently confirmed. The numeric/text values inside are
  synthetic.

See ``app/connectors/brenda.py``'s module docstring for the complete
verification record.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config.settings import Settings
from app.connectors.brenda import (
    BrendaConnector,
    BrendaKineticMeasurement,
    BrendaRawResult,
    BrendaSearchHit,
    _is_brenda_permanent_http_failure,
    build_brenda_http_client,
    hash_brenda_password,
    normalize_kinetic_records,
    parse_delimited_records,
    parse_soap_fault,
)
from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import (
    ConnectorAuthenticationError,
    ConnectorHTTPError,
    ConnectorParseError,
)
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "brenda"
_SERVER_URL = "https://example.invalid/brenda/soap"
_FAKE_EMAIL = "agent1-test@example.invalid"
_FAKE_PASSWORD = "test-only-fake-brenda-password"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class _RecordingHandler:
    """An ``httpx.MockTransport`` handler that replays a scripted sequence.

    Each entry in ``responses`` is either an ``httpx.Response`` to return or
    an exception instance to raise, consumed in call order; the last entry
    repeats for any calls beyond the scripted sequence. Every request is
    recorded so tests can inspect the method/body/headers the connector built.
    """

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


def _connector(handler: _RecordingHandler, **client_kwargs: object) -> BrendaConnector:
    return BrendaConnector(
        _client_for(handler, **client_kwargs),
        email=_FAKE_EMAIL,
        password=_FAKE_PASSWORD,
        server_url=_SERVER_URL,
    )


# --- BRENDA Connector Tests (docs/05_testing.md) -----------------------------


def test_brenda_authentication_required() -> None:
    """Credentials are mandatory: construction fails immediately without them."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("recommended_name.xml")))

    with pytest.raises(ValueError, match="email"):
        BrendaConnector(_client_for(handler), email="", password=_FAKE_PASSWORD)

    with pytest.raises(ValueError, match="password"):
        BrendaConnector(_client_for(handler), email=_FAKE_EMAIL, password="")


def test_brenda_authentication_failure_raises_authentication_error() -> None:
    """A real BRENDA authentication fault (live-confirmed) is raised distinctly.

    Uses the byte-for-byte real fault response captured during this
    increment's live verification: HTTP 500, faultcode "401".
    """
    handler = _RecordingHandler(httpx.Response(500, text=_fixture("auth_fault.xml")))
    connector = _connector(handler, max_retries=0)

    with pytest.raises(ConnectorAuthenticationError, match="Username or password"):
        connector.fetch_km("1.1.1.1")


# --- Retry-avoidance for a confirmed auth fault (ConnectorHttpClient.is_permanent_failure) --


def test_brenda_authentication_fault_makes_exactly_one_request() -> None:
    """Wired through build_brenda_http_client(), a confirmed auth fault is never retried.

    Without this classifier, ConnectorHttpClient would retry a plain HTTP
    500 up to max_retries times before BrendaConnector._call() ever gets to
    inspect the body -- wasteful for a failure that cannot succeed by
    retrying, and impolite given BRENDA's documented one-request-per-second
    limit. This proves the fault is recognized on the *first* attempt.
    """
    handler = _RecordingHandler(httpx.Response(500, text=_fixture("auth_fault.xml")))
    http_client = build_brenda_http_client(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=3,
        sleep=lambda _seconds: None,
    )
    connector = BrendaConnector(
        http_client, email=_FAKE_EMAIL, password=_FAKE_PASSWORD, server_url=_SERVER_URL
    )

    with pytest.raises(ConnectorAuthenticationError, match="Username or password"):
        connector.fetch_km("1.1.1.1")

    assert len(handler.requests) == 1


def test_brenda_generic_500_via_build_http_client_still_retries() -> None:
    """A generic BRENDA 500 (not the auth-fault shape) still uses normal retry/backoff."""
    handler = _RecordingHandler(
        [
            httpx.Response(500, text="internal server error"),
            httpx.Response(200, text=_fixture("km_empty.xml")),
        ]
    )
    http_client = build_brenda_http_client(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=3,
        sleep=lambda _seconds: None,
    )
    connector = BrendaConnector(
        http_client, email=_FAKE_EMAIL, password=_FAKE_PASSWORD, server_url=_SERVER_URL
    )

    result = connector.fetch_km("1.1.1.1")

    assert len(handler.requests) == 2
    assert result == []


def test_is_brenda_permanent_http_failure_matches_confirmed_auth_fault() -> None:
    assert _is_brenda_permanent_http_failure(500, _fixture("auth_fault.xml")) is True


def test_is_brenda_permanent_http_failure_does_not_match_generic_500() -> None:
    assert _is_brenda_permanent_http_failure(500, "internal server error") is False


def test_is_brenda_permanent_http_failure_does_not_match_non_xml_body() -> None:
    assert _is_brenda_permanent_http_failure(500, "") is False


def test_is_brenda_permanent_http_failure_ignores_429() -> None:
    """The documented rate limit (429) must still use normal retry/backoff, unaffected."""
    assert _is_brenda_permanent_http_failure(429, _fixture("auth_fault.xml")) is False


def test_brenda_kinetic_result_parsed() -> None:
    """fetch_km() calls the SOAP endpoint and returns parsed, typed measurements."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_multiple_measurements.xml")))
    connector = _connector(handler)

    measurements = connector.fetch_km("1.1.1.1")

    assert len(handler.requests) == 1
    sent = handler.requests[0]
    assert sent.method == "POST"
    assert sent.url == _SERVER_URL
    assert len(measurements) == 2
    first = measurements[0]
    assert isinstance(first, BrendaKineticMeasurement)
    assert first.parameter_type == "Km"
    assert first.parameter_value == "0.5"
    assert first.ec_number == "1.1.1.1"


def test_brenda_organism_preserved() -> None:
    """Each measurement's organism is preserved distinctly, not collapsed to one."""
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("km_multiple_measurements.xml"))),
        method="getKmValue",
    )

    organisms = [m.organism for m in measurements]
    assert organisms == ["Saccharomyces cerevisiae", "Homo sapiens"]


def test_brenda_parameter_units_preserved() -> None:
    """Units follow BRENDA's official per-field documentation, not one shared default.

    Verified directly against https://www.brenda-enzymes.org/datafields.php
    during this increment (not from memory) -- see
    app.connectors.brenda._METHOD_SPECS.
    """
    km = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "kmValue": "0.5"}], method="getKmValue"
    )
    ki = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "kiValue": "0.03"}], method="getKiValue"
    )
    kcat = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "turnoverNumber": "12"}], method="getTurnoverNumber"
    )
    temp = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "temperatureOptimum": "37"}], method="getTemperatureOptimum"
    )
    ph = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "phOptimum": "7.5"}], method="getPhOptimum"
    )

    assert km[0].unit == "mM"
    assert ki[0].unit == "mM"
    assert kcat[0].unit == "1/s"
    assert temp[0].unit == "°C"
    assert ph[0].unit is None  # pH is dimensionless -- no unit is correct, not missing data


def test_brenda_multiple_measurements_preserved() -> None:
    """Multiple BRENDA records for the same EC number stay separate, never merged/averaged."""
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("km_multiple_measurements.xml"))),
        method="getKmValue",
    )

    assert len(measurements) == 2
    assert measurements[0].parameter_value != measurements[1].parameter_value
    assert measurements[0] != measurements[1]


def test_brenda_rate_limit_enforced() -> None:
    """BRENDA requests go through the injected rate limiter, once per attempt."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    connector = _connector(handler, rate_limiter=_RecordingLimiter())

    connector.fetch_km("1.1.1.1")

    assert len(acquire_calls) == 1


def test_brenda_failure_does_not_create_negative_result() -> None:
    """A genuine upstream failure raises; it is never silently treated as ``[]``."""
    handler = _RecordingHandler(httpx.Response(503, text="internal error"))
    connector = _connector(handler, max_retries=0)

    with pytest.raises(ConnectorHTTPError):
        connector.fetch_km("1.1.1.1")


# --- Legitimate empty result vs. failure, distinguished -----------------------


def test_brenda_fetch_km_empty_result_is_empty_list() -> None:
    """BRENDA reporting no Km data for an EC number is a legitimate empty result."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    connector = _connector(handler)

    assert connector.fetch_km("9.9.9.9") == []


def test_brenda_search_returns_none_for_empty_recommended_name() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    connector = _connector(handler)

    assert connector.search("9.9.9.9") is None


# --- search(): EC-number lookup, not free-text (no such BRENDA endpoint exists) --


def test_brenda_search_looks_up_recommended_name_by_ec_number() -> None:
    """search() is deliberately an EC-number lookup, not free text.

    Confirmed by inspecting the live WSDL: BRENDA's SOAP interface has no
    enzyme-name-to-EC-number search operation (getEcNumber/getEnzymeNames/
    getRecommendedName/getSystematicName/getSynonyms all take an EC number
    as input, never as output).
    """
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("recommended_name.xml")))
    connector = _connector(handler)

    hit = connector.search("1.1.1.1")

    assert isinstance(hit, BrendaSearchHit)
    assert hit.ec_number == "1.1.1.1"
    assert hit.recommended_name == "alcohol dehydrogenase"


# --- Substrate vs. inhibitor, distinguished by parameter type ------------------


def test_brenda_ki_measurement_has_inhibitor_not_substrate() -> None:
    """Ki records carry an inhibitor field; Km/kcat records carry substrate -- never conflated."""
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("ki_single_measurement.xml"))),
        method="getKiValue",
    )

    assert len(measurements) == 1
    assert measurements[0].inhibitor == "pyrazole"
    assert measurements[0].substrate is None


def test_brenda_km_measurement_has_substrate_not_inhibitor() -> None:
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("km_multiple_measurements.xml"))),
        method="getKmValue",
    )

    assert measurements[0].substrate == "NAD+"
    assert measurements[0].inhibitor is None


def test_brenda_ph_optimum_has_neither_substrate_nor_inhibitor() -> None:
    """pH optimum is an enzyme-level property -- neither field applies."""
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("ph_optimum.xml"))),
        method="getPhOptimum",
    )

    assert measurements[0].substrate is None
    assert measurements[0].inhibitor is None
    assert measurements[0].parameter_value == "7.5"


# --- Literature/commentary preserved -------------------------------------------


def test_brenda_literature_ids_preserved_as_tuple() -> None:
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("km_multiple_measurements.xml"))),
        method="getKmValue",
    )

    assert measurements[0].literature_ids == ("12345678",)
    assert measurements[1].literature_ids == ("23456789", "34567890")


def test_brenda_commentary_preserved() -> None:
    measurements = normalize_kinetic_records(
        parse_delimited_records(_extract_return(_fixture("km_multiple_measurements.xml"))),
        method="getKmValue",
    )

    assert measurements[0].commentary == "synthetic test commentary, pH 7.0, 30 degrees C"


def test_brenda_raw_field_preserved_even_without_typed_slot() -> None:
    """Fields with no typed dataclass slot (e.g. none modeled here) stay reachable via raw."""
    measurements = normalize_kinetic_records(
        [{"ecNumber": "1.1.1.1", "kmValue": "0.5", "filename": "brenda_download_2020.txt"}],
        method="getKmValue",
    )

    assert measurements[0].raw["filename"] == "brenda_download_2020.txt"


# --- Malformed content ----------------------------------------------------------


def test_brenda_fetch_malformed_soap_response_raises_parse_error() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="this is not xml at all <<<"))
    connector = _connector(handler)

    with pytest.raises(ConnectorParseError):
        connector.fetch_km("1.1.1.1")


def test_brenda_response_missing_return_element_raises_parse_error() -> None:
    handler = _RecordingHandler(
        httpx.Response(
            200,
            text=(
                '<?xml version="1.0"?><SOAP-ENV:Envelope '
                'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
                "<SOAP-ENV:Body><ns1:somethingElse/></SOAP-ENV:Body></SOAP-ENV:Envelope>"
            ),
        )
    )
    connector = _connector(handler)

    with pytest.raises(ConnectorParseError):
        connector.fetch_km("1.1.1.1")


def test_parse_delimited_records_rejects_field_without_star() -> None:
    with pytest.raises(ConnectorParseError):
        parse_delimited_records("ecNumber-1.1.1.1#kmValue*0.5")


def test_parse_delimited_records_empty_text_is_empty_list() -> None:
    assert parse_delimited_records("") == []
    assert parse_delimited_records("   ") == []


def test_normalize_kinetic_records_rejects_unknown_method() -> None:
    with pytest.raises(ConnectorParseError):
        normalize_kinetic_records([{"ecNumber": "1.1.1.1"}], method="getSomethingUnknown")


def test_brenda_unexpected_soap_fault_on_200_raises_parse_error() -> None:
    """A SOAP fault on HTTP 200 (not the confirmed 500 shape) still raises, not parsed as data."""
    fault_in_200 = (
        '<?xml version="1.0"?><SOAP-ENV:Envelope '
        'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body>'
        "<SOAP-ENV:Fault><faultcode>500</faultcode>"
        "<faultstring>internal server error</faultstring></SOAP-ENV:Fault>"
        "</SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )
    handler = _RecordingHandler(httpx.Response(200, text=fault_in_200))
    connector = _connector(handler)

    with pytest.raises(ConnectorParseError, match="fault"):
        connector.fetch_km("1.1.1.1")


# --- Cache integration ----------------------------------------------------------


def test_brenda_response_cached() -> None:
    """Repeated identical BRENDA requests (same body) are served from cache."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_multiple_measurements.xml")))
    connector = _connector(handler, cache=InMemoryResponseCache())

    first = connector.fetch_km("1.1.1.1")
    second = connector.fetch_km("1.1.1.1")

    assert len(handler.requests) == 1
    assert first == second


def test_brenda_different_ec_numbers_do_not_share_cache_entry() -> None:
    handler = _RecordingHandler(
        [
            httpx.Response(200, text=_fixture("km_multiple_measurements.xml")),
            httpx.Response(200, text=_fixture("km_empty.xml")),
        ]
    )
    connector = _connector(handler, cache=InMemoryResponseCache())

    first = connector.fetch_km("1.1.1.1")
    second = connector.fetch_km("2.2.2.2")

    assert len(handler.requests) == 2
    assert len(first) == 2
    assert second == []


# --- Credential handling / secret leakage ---------------------------------------


def test_hash_brenda_password_is_sha256_hex_digest() -> None:
    """https://www.brenda-enzymes.org/soap.php: the password is sent as a SHA-256 hash."""
    import hashlib

    assert hash_brenda_password("hunter2") == hashlib.sha256(b"hunter2").hexdigest()


def test_brenda_password_never_appears_in_connector_repr() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    connector = _connector(handler)

    assert _FAKE_PASSWORD not in repr(connector)
    assert hash_brenda_password(_FAKE_PASSWORD) not in repr(connector)


def test_brenda_password_never_appears_in_raised_exception_messages() -> None:
    handler = _RecordingHandler(httpx.Response(503, text="internal error"))
    connector = _connector(handler, max_retries=0)

    with pytest.raises(ConnectorHTTPError) as excinfo:
        connector.fetch_km("1.1.1.1")

    assert _FAKE_PASSWORD not in str(excinfo.value)
    assert hash_brenda_password(_FAKE_PASSWORD) not in str(excinfo.value)


def test_brenda_connector_from_settings_uses_configured_credentials() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        brenda_username=_FAKE_EMAIL,
        brenda_password=_FAKE_PASSWORD,  # type: ignore[arg-type]
    )

    connector = BrendaConnector.from_settings(
        _client_for(handler), settings=settings, server_url=_SERVER_URL
    )
    connector.fetch_km("1.1.1.1")

    sent_body = handler.requests[0].content.decode("utf-8")
    assert _FAKE_EMAIL in sent_body
    assert hash_brenda_password(_FAKE_PASSWORD) in sent_body
    assert _FAKE_PASSWORD not in sent_body


def test_brenda_connector_from_settings_raises_when_unconfigured() -> None:
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        brenda_username=None,
        brenda_password=None,
    )
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))

    with pytest.raises(ValueError, match="brenda_username"):
        BrendaConnector.from_settings(_client_for(handler), settings=settings)


# --- parse_soap_fault() pure function -------------------------------------------


def test_parse_soap_fault_extracts_real_live_fault() -> None:
    fault = parse_soap_fault(_fixture("auth_fault.xml"))

    assert fault is not None
    assert fault.fault_code == "401"
    assert fault.fault_string == "Username or password is wrong, or account was not activated."


def test_parse_soap_fault_returns_none_for_non_fault_response() -> None:
    assert parse_soap_fault(_fixture("recommended_name.xml")) is None


def test_parse_soap_fault_returns_none_for_non_xml() -> None:
    assert parse_soap_fault("not xml at all") is None


# --- BRENDA rate-limit default (build_brenda_http_client) ----------------------


def test_build_brenda_http_client_uses_documented_one_second_interval() -> None:
    """https://www.brenda-enzymes.org/soap.php: "do not send more than one request per second"."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))

    sleeps: list[float] = []
    times = iter([0.0, 0.1, 0.1])
    client = build_brenda_http_client(
        httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: next(times),
        sleep=sleeps.append,
    )

    client.post("https://example.invalid/x", content=b"a")
    client.post("https://example.invalid/x", content=b"b")

    assert sleeps == [pytest.approx(1.0 - 0.1)]


# --- normalize() dispatch --------------------------------------------------------


def test_brenda_normalize_uses_method_from_raw_result() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    connector = _connector(handler)

    raw = BrendaRawResult(method="getKiValue", records=({"ecNumber": "1.1.1.1", "kiValue": "0.1"},))
    normalized = connector.normalize(raw)

    assert normalized[0].parameter_type == "Ki"
    assert normalized[0].unit == "mM"


# --- Declared source / SourceConnector contract --------------------------------


def test_brenda_connector_declares_brenda_source() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_empty.xml")))
    connector = _connector(handler)
    assert connector.source == SourceType.BRENDA


def test_brenda_fetch_delegates_to_fetch_km() -> None:
    """SourceConnector.fetch() is BRENDA's most fundamental parameter, Km."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("km_multiple_measurements.xml")))
    connector = _connector(handler)

    result = connector.fetch("1.1.1.1")

    assert all(m.parameter_type == "Km" for m in result)


def _extract_return(soap_response_text: str) -> str:
    """Test helper: pull the raw delimited-record text out of a fixture's <return>.

    Uses the same lenient any-namespace element search the connector itself
    uses, kept separate here so parsing-focused tests can exercise
    parse_delimited_records()/normalize_kinetic_records() directly without
    going through the full HTTP-mocked connector path.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(soap_response_text)
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "return":
            return "".join(elem.itertext())
    raise AssertionError("fixture has no <return> element")
