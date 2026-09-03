"""BRENDA connector: retrieval and parsing only, no persistence or curation policy.

**Verified against official BRENDA documentation and one live authentication
check.** This module was written from BRENDA's current official SOAP
documentation (https://www.brenda-enzymes.org/soap.php), its live WSDL
(https://www.brenda-enzymes.org/soap/brenda.wsdl, fetched directly, not from
memory), and its official data-field unit reference
(https://www.brenda-enzymes.org/datafields.php) -- not from remembered or
inferred legacy behavior, per this increment's instructions. One live SOAP
request was made, with obviously-fake credentials, to observe BRENDA's
actual authentication-failure response shape (see
``ConnectorAuthenticationError`` below); no valid BRENDA account was
available, so the *successful* response shape is documentation-verified
only, not live-confirmed. That distinction is called out everywhere it
matters below and in ``tests/connectors/test_brenda.py``.

**Current interface: still SOAP**, confirmed live and via documentation --
BRENDA has not moved to REST/JSON. No modern replacement was found.

* WSDL: ``https://www.brenda-enzymes.org/soap/brenda.wsdl`` (public, no
  authentication -- confirmed live). BRENDA publishes **two** WSDL contracts
  (soap.php): ``brenda.wsdl``, for traditional SOAP clients (Perl/PHP/older
  Python) that pass one concatenated string parameter, and
  ``brenda_zeep.wsdl``, for Python 3's Zeep library specifically, which
  passes each field as a separate argument instead. This module manually
  builds a single ``<parameters xsi:type="xsd:string">`` element holding a
  concatenated string -- the ``brenda.wsdl`` contract, not the Zeep one --
  and the one live authentication-fault request (below) was built exactly
  this way and was accepted far enough for the server to parse it and
  evaluate the credentials, confirming this manual construction is a valid
  implementation of the ``brenda.wsdl`` contract. There is accordingly no
  reason to migrate to Zeep or add it as a dependency.
* Server endpoint: ``https://www.brenda-enzymes.org/soap/brenda_server.php``
  (confirmed live, including its authentication-failure behavior).
* Authentication: a registered email address plus a **SHA-256 hex digest of
  the password** (never the plaintext password), documented at soap.php and
  confirmed live: a request built this way, with the hash computed from an
  obviously-wrong password, produced a *credentials-rejected* fault rather
  than a malformed-request fault, meaning the server accepted the shape of
  the request and evaluated it as bad credentials specifically. See
  ``hash_brenda_password()``.
* Every SOAP call takes exactly one string parameter, itself a
  comma/`` # ``/``*``-structured string: ``"{email},{sha256_password},
  key1*value1#key2*value2#"``. See ``_build_soap_envelope()``.
* A successful call's SOAP response wraps a ``<return>`` string. For the
  kinetic-parameter methods (documented and WSDL-confirmed), that string is
  itself structured: zero or more records separated by ``!``, each a set of
  ``key*value`` fields separated by ``#``. See ``parse_delimited_records()``.
  This exact shape was **not** independently reproduced with real data this
  increment (no valid credentials); it is asserted here as documented,
  self-consistent behavior confirmed for the *request* side, not observed
  for a *successful response*.
* An authentication failure (live-confirmed) is an HTTP 500 response whose
  body is a SOAP 1.1 Fault: ``<faultcode>401</faultcode><faultstring>Username
  or password is wrong, or account was not activated.</faultstring>``. See
  ``ConnectorAuthenticationError`` / ``_is_authentication_fault()``.
* Rate limit (documented, not independently re-derivable from a handful of
  requests): "do not send more than one request per second"
  (https://www.brenda-enzymes.org/soap.php). See
  ``build_brenda_http_client()``.
* Access: "Use of this online version of BRENDA is free under the CC BY 4.0
  license"; the SOAP web service itself requires registering an account
  (email + password). No terms encountered during this verification
  prohibit the kind of automated, low-volume, registered access this
  connector performs; this is not a substitute for reading BRENDA's current
  terms of use yourself before relying on this in production.

Existing settings (``app.config.settings.Settings.brenda_username``/
``brenda_password``) are used as-is -- no new setting was added.
``brenda_username``'s configured value is treated as the registered email
address: BRENDA's account identifier *is* an email address (soap.php: "you
need a valid email address and password"), and there is no separate
"username" concept to store distinctly. See ``BrendaConnector.from_settings``.

Every SOAP call goes through ``app.connectors.http.ConnectorHttpClient.post()``
(added in this increment -- BRENDA is the first connector whose real wire
protocol needs a request body rather than query parameters), which owns
every retry, backoff, timeout, rate-limit, and cache concern. Nothing in
this module retries a request, waits out a rate limit, or reads/writes a
database.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``/``fetch_km()``/``fetch_ki()``/etc.):
  network I/O only, via ``ConnectorHttpClient.post()``.
* parsing (``parse_delimited_records()``): raw BRENDA response text -> a
  list of generic field dictionaries. Pure, no I/O, nothing discarded.
* normalization (``normalize()``, ``normalize_kinetic_records()``): generic
  field dictionaries plus which SOAP method produced them -> BRENDA-scoped
  typed records (``BrendaKineticMeasurement``). Still BRENDA-only; no
  cross-source entity resolution, no claim/evidence generation, no
  ``KineticMeasurement`` row is written.
* persistence: not implemented here (a later increment).

A scientific-integrity note: BRENDA is a curated database, not a primary
experimental source in this connector's hands
(``.cursor/rules/01-scientific-integrity.mdc``). Every
``BrendaKineticMeasurement`` carries ``source_category_label =
"database_annotation"`` for exactly this reason, and this connector never
assigns an ``app.models.enums.EvidenceType`` itself (a later
evidence-extraction phase's job). Each record BRENDA reports separately
stays a separate ``BrendaKineticMeasurement`` -- nothing here averages,
merges, or picks "the best" value among several; organism, substrate/
inhibitor, and commentary qualifiers are preserved on every record rather
than being dropped once "enough" context has been captured.
"""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.config.settings import Settings, get_settings
from app.connectors.cache import ResponseCache
from app.connectors.exceptions import (
    ConnectorAuthenticationError,
    ConnectorHTTPError,
    ConnectorParseError,
)
from app.connectors.http import ConnectorHttpClient
from app.connectors.ratelimit import IntervalRateLimiter
from app.models.enums import SourceType

_DEFAULT_SERVER_URL = "https://www.brenda-enzymes.org/soap/brenda_server.php"
_SOAP_ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_BRENDA_METHOD_NS = "https://www.brenda-enzymes.org/soap"

# https://www.brenda-enzymes.org/soap.php: "do not send more than one
# request per second" -- a real, documented, numeric limit, unlike SGD.
_BRENDA_MIN_INTERVAL_SECONDS = 1.0

# Live-confirmed (this increment, with obviously-fake credentials):
# faultcode "401", faultstring "Username or password is wrong, or account
# was not activated." Text markers are a defensive fallback for a
# differently-worded but equivalent fault.
_CONFIRMED_AUTH_FAULT_CODE = "401"
_AUTH_FAULT_TEXT_MARKERS = ("username or password", "not activated")


def hash_brenda_password(password: str) -> str:
    """SHA-256 hex digest of a BRENDA password.

    BRENDA's documented authentication format (soap.php) is
    ``"email,sha256(password),..."`` -- the plaintext password is never sent
    and never stored by this connector past this function's local scope.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BrendaSoapFault:
    """A parsed SOAP 1.1 Fault: ``faultcode``/``faultstring``, as BRENDA reports them."""

    fault_code: str | None
    fault_string: str | None


@dataclass(frozen=True, slots=True)
class BrendaSearchHit:
    """The result of looking up an EC number's recommended name.

    Not a general search hit -- see ``BrendaConnector.search()``.
    """

    ec_number: str
    recommended_name: str


@dataclass(frozen=True, slots=True)
class BrendaKineticMeasurement:
    """One BRENDA record for a single kinetic parameter type.

    Never merged with another record, even one for the same EC number and
    parameter type -- BRENDA reports each observation separately, and this
    connector preserves that (``.cursor/rules/01-scientific-integrity.mdc``).
    ``raw`` retains every field BRENDA supplied for this record, including
    ones with no typed slot here (e.g. ``filename``).
    """

    parameter_type: str
    parameter_value: str | None
    parameter_value_maximum: str | None
    unit: str | None
    ec_number: str | None
    organism: str | None
    substrate: str | None
    inhibitor: str | None
    commentary: str | None
    literature_ids: tuple[str, ...]
    raw: dict[str, str]
    source_category_label: str = "database_annotation"


@dataclass(frozen=True, slots=True)
class BrendaRawResult:
    """Source-native parsed result of one kinetic SOAP call: which method, and its records."""

    method: str
    records: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _BrendaMethodSpec:
    parameter_type: str
    unit: str | None
    value_field: str
    value_maximum_field: str
    qualifier_field: str | None  # "substrate", "inhibitor", or None


# Field names verified directly from the live WSDL's complex-type
# definitions (https://www.brenda-enzymes.org/soap/brenda.wsdl), not
# inferred by naming convention. Units verified directly from
# https://www.brenda-enzymes.org/datafields.php.
_METHOD_SPECS: dict[str, _BrendaMethodSpec] = {
    "getKmValue": _BrendaMethodSpec("Km", "mM", "kmValue", "kmValueMaximum", "substrate"),
    "getKiValue": _BrendaMethodSpec("Ki", "mM", "kiValue", "kiValueMaximum", "inhibitor"),
    "getTurnoverNumber": _BrendaMethodSpec(
        "kcat", "1/s", "turnoverNumber", "turnoverNumberMaximum", "substrate"
    ),
    "getKcatKmValue": _BrendaMethodSpec(
        "kcat/Km", "mM/s", "kcatKmValue", "kcatKmValueMaximum", "substrate"
    ),
    "getPhOptimum": _BrendaMethodSpec("pH optimum", None, "phOptimum", "phOptimumMaximum", None),
    "getTemperatureOptimum": _BrendaMethodSpec(
        "temperature optimum", "°C", "temperatureOptimum", "temperatureOptimumMaximum", None
    ),
    "getSpecificActivity": _BrendaMethodSpec(
        "specific activity", "µmol/min/mg", "specificActivity", "specificActivityMaximum", None
    ),
}


def _find_by_local_name(root: ET.Element, local_name: str) -> ET.Element | None:
    """Find the first descendant (or self) element by tag name, ignoring any namespace."""
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == local_name:
            return elem
    return None


def _local_findtext(parent: ET.Element, local_name: str) -> str | None:
    child = _find_by_local_name(parent, local_name)
    if child is None or not child.text:
        return None
    return child.text.strip() or None


def parse_soap_fault(body: str) -> BrendaSoapFault | None:
    """Parse a SOAP 1.1 Fault envelope, if the body is one. Pure: no HTTP or DB access.

    Returns ``None`` for anything that is not a recognizable SOAP fault
    (including non-XML text) rather than raising -- callers decide what a
    non-fault, non-XML body means in their own context.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    fault = _find_by_local_name(root, "Fault")
    if fault is None:
        return None
    return BrendaSoapFault(
        fault_code=_local_findtext(fault, "faultcode"),
        fault_string=_local_findtext(fault, "faultstring"),
    )


def _is_authentication_fault(fault: BrendaSoapFault) -> bool:
    if fault.fault_code == _CONFIRMED_AUTH_FAULT_CODE:
        return True
    text = (fault.fault_string or "").lower()
    return any(marker in text for marker in _AUTH_FAULT_TEXT_MARKERS)


def _is_brenda_permanent_http_failure(status_code: int, body: str) -> bool:
    """``ConnectorHttpClient(is_permanent_failure=...)`` classifier for BRENDA.

    A confirmed BRENDA authentication fault is not transient -- retrying it
    cannot succeed, and BRENDA limits clients to one request per second, so
    wasting several requests on a call that will keep failing identically is
    both pointless and impolite. Only HTTP 500 is examined: BRENDA's
    documented rate limit (429) must still use the shared client's normal
    retry/backoff path, unaffected by this classifier. All SOAP-fault
    parsing stays here, in ``app/connectors/brenda.py`` -- ``ConnectorHttpClient``
    itself never sees this function's body, only its boolean result.
    """
    if status_code != 500:
        return False
    fault = parse_soap_fault(body)
    return fault is not None and _is_authentication_fault(fault)


def _build_soap_envelope(method: str, parameters: str) -> str:
    """Build a SOAP 1.1 request envelope for one BRENDA RPC call.

    Matches the calling convention documented at
    https://www.brenda-enzymes.org/soap.php and confirmed live this
    increment (as an authentication-fault response -- the server evaluated
    and rejected the credentials, meaning it parsed this envelope shape
    correctly): a single string parameter combining credentials and query
    fields.
    """
    escaped = xml_escape(parameters)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{_SOAP_ENVELOPE_NS}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        "<soap:Body>"
        f'<ns1:{method} xmlns:ns1="{_BRENDA_METHOD_NS}">'
        f'<parameters xsi:type="xsd:string">{escaped}</parameters>'
        f"</ns1:{method}>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _extract_soap_return_value(body: str) -> str:
    """Extract a successful call's ``<return>`` text. Pure: no HTTP or DB access."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ConnectorParseError(f"malformed BRENDA SOAP response: not valid XML: {exc}") from exc

    fault_elem = _find_by_local_name(root, "Fault")
    if fault_elem is not None:
        fault_code = _local_findtext(fault_elem, "faultcode")
        fault_string = _local_findtext(fault_elem, "faultstring")
        raise ConnectorParseError(
            f"unexpected BRENDA SOAP fault (faultcode={fault_code!r}): {fault_string!r}"
        )

    return_elem = _find_by_local_name(root, "return")
    if return_elem is None:
        raise ConnectorParseError("malformed BRENDA SOAP response: missing <return> element")
    return "".join(return_elem.itertext())


def parse_delimited_records(text: str) -> list[dict[str, str]]:
    """Parse BRENDA's ``!``/``#``/``*``-delimited kinetic-record format.

    An empty (or all-blank) return value is a legitimate empty result and
    returns ``[]`` -- it is not an error. Documented shape
    (https://www.brenda-enzymes.org/soap.php); not independently observed
    with real data this increment (see module docstring).
    """
    if not text.strip():
        return []

    records: list[dict[str, str]] = []
    for record_text in text.split("!"):
        if not record_text.strip():
            continue
        fields: dict[str, str] = {}
        for field_text in record_text.split("#"):
            if not field_text.strip():
                continue
            if "*" not in field_text:
                raise ConnectorParseError(f"malformed BRENDA record field: {field_text!r}")
            key, _, value = field_text.partition("*")
            fields[key.strip()] = value.strip()
        if fields:
            records.append(fields)
    return records


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_kinetic_records(
    records: Iterable[Mapping[str, str]], *, method: str
) -> list[BrendaKineticMeasurement]:
    """Map generic parsed BRENDA records onto typed, schema-shaped measurements.

    ``method`` selects the field-name/parameter-type/unit mapping (see
    ``_METHOD_SPECS``) -- the raw records themselves carry no self-describing
    parameter type or unit.
    """
    if method not in _METHOD_SPECS:
        raise ConnectorParseError(f"unrecognized BRENDA kinetic method: {method!r}")
    spec = _METHOD_SPECS[method]

    measurements: list[BrendaKineticMeasurement] = []
    for record in records:
        literature_ids = tuple(
            part.strip() for part in record.get("literature", "").split(",") if part.strip()
        )
        substrate = None
        inhibitor = None
        if spec.qualifier_field == "substrate":
            substrate = _none_if_blank(record.get("substrate"))
        elif spec.qualifier_field == "inhibitor":
            inhibitor = _none_if_blank(record.get("inhibitor"))

        measurements.append(
            BrendaKineticMeasurement(
                parameter_type=spec.parameter_type,
                parameter_value=_none_if_blank(record.get(spec.value_field)),
                parameter_value_maximum=_none_if_blank(record.get(spec.value_maximum_field)),
                unit=spec.unit,
                ec_number=_none_if_blank(record.get("ecNumber")),
                organism=_none_if_blank(record.get("organism")),
                substrate=substrate,
                inhibitor=inhibitor,
                commentary=_none_if_blank(record.get("commentary")),
                literature_ids=literature_ids,
                raw=dict(record),
            )
        )
    return measurements


def build_brenda_http_client(
    client: httpx.Client,
    *,
    cache: ResponseCache | None = None,
    max_retries: int = 3,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ConnectorHttpClient:
    """Build a ``ConnectorHttpClient`` using BRENDA's documented rate-limit guidance
    (https://www.brenda-enzymes.org/soap.php: "do not send more than one request
    per second") and BRENDA's permanent-authentication-failure classifier
    (``_is_brenda_permanent_http_failure`` -- so a confirmed auth fault costs
    exactly one request instead of being retried up to ``max_retries`` times
    first). A convenience only -- constructing ``ConnectorHttpClient``
    directly and passing it to ``BrendaConnector`` always works too, just
    without that retry-avoidance optimization (``BrendaConnector._call()``
    still correctly reclassifies the eventual ``ConnectorHTTPError`` either
    way; the classifier only saves the wasted requests).
    """
    rate_limiter = IntervalRateLimiter(_BRENDA_MIN_INTERVAL_SECONDS, clock=clock, sleep=sleep)
    return ConnectorHttpClient(
        client,
        max_retries=max_retries,
        rate_limiter=rate_limiter,
        cache=cache,
        sleep=sleep,
        is_permanent_failure=_is_brenda_permanent_http_failure,
    )


class BrendaConnector:
    """Retrieval and parsing for BRENDA. No curation policy, no persistence.

    Unlike KEGG/PubMed/SGD, credentials are **required**: BRENDA's SOAP
    interface rejects every call without a registered account (confirmed
    live -- see ``ConnectorAuthenticationError``), so there is no
    unauthenticated fallback mode to support.
    """

    source: SourceType = SourceType.BRENDA

    def __init__(
        self,
        http_client: ConnectorHttpClient,
        *,
        email: str,
        password: str,
        server_url: str = _DEFAULT_SERVER_URL,
    ) -> None:
        if not email.strip():
            raise ValueError("email must not be empty")
        if not password:
            raise ValueError("password must not be empty")
        if not server_url.strip():
            raise ValueError("server_url must not be empty")
        self._http = http_client
        self._server_url = server_url
        self._credential_prefix = f"{email.strip()},{hash_brenda_password(password)},"

    @classmethod
    def from_settings(
        cls,
        http_client: ConnectorHttpClient,
        settings: Settings | None = None,
        *,
        server_url: str = _DEFAULT_SERVER_URL,
    ) -> BrendaConnector:
        """Build a connector using the existing ``brenda_username``/``brenda_password`` settings.

        BRENDA's account identifier is an email address
        (https://www.brenda-enzymes.org/soap.php: "you need a valid email
        address and password"); ``brenda_username``'s configured value is
        treated as that email -- no new setting was added for this (see the
        module docstring for the reasoning).
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.brenda_username or resolved_settings.brenda_password is None:
            raise ValueError(
                "Settings.brenda_username and Settings.brenda_password must both be configured"
            )
        return cls(
            http_client,
            email=resolved_settings.brenda_username,
            password=resolved_settings.brenda_password.get_secret_value(),
            server_url=server_url,
        )

    def _call(self, method: str, params: str) -> str:
        """Perform one SOAP RPC call, returning the raw ``<return>`` text content.

        Raises ``ConnectorAuthenticationError`` for a confirmed
        authentication fault. Any other SOAP fault, or a response that is
        not a recognizable SOAP envelope, raises ``ConnectorParseError``.
        Network/rate-limit/server failures at the transport level propagate
        as raised by ``ConnectorHttpClient`` (``ConnectorNetworkError``/
        ``ConnectorRateLimitError``/``ConnectorHTTPError``).
        """
        envelope = _build_soap_envelope(method, self._credential_prefix + params)
        try:
            response = self._http.post(
                self._server_url,
                content=envelope.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            )
        except ConnectorHTTPError as exc:
            fault = parse_soap_fault(exc.body) if exc.body else None
            if fault is not None and _is_authentication_fault(fault):
                raise ConnectorAuthenticationError(
                    fault.fault_string or "BRENDA rejected the configured credentials"
                ) from exc
            raise
        return _extract_soap_return_value(response.text)

    def search(self, query: str) -> BrendaSearchHit | None:
        """Look up the recommended name for an EC number.

        Not a general search: BRENDA's public SOAP interface has no
        enzyme-name-to-EC-number search operation (confirmed by inspecting
        the live WSDL -- ``getEcNumber``/``getEnzymeNames``/
        ``getRecommendedName``/``getSystematicName``/``getSynonyms`` all
        take an EC number as *input* and return name information, never the
        reverse). ``query`` is therefore an EC number, not free text. This
        is a deliberately minimal, honest implementation of
        ``SourceConnector.search()`` rather than an invented endpoint that
        does not exist.

        Returns ``None`` when BRENDA has no recommended name for the EC
        number (an empty return value) -- a legitimate empty result, not an
        error.
        """
        identifier = query.strip()
        if not identifier:
            raise ValueError("query must not be empty")
        name = self._call("getRecommendedName", f"ecNumber*{identifier}#").strip()
        if not name:
            return None
        return BrendaSearchHit(ec_number=identifier, recommended_name=name)

    def _fetch_kinetic(
        self, method: str, ec_number: str, *, organism: str | None
    ) -> list[BrendaKineticMeasurement]:
        identifier = ec_number.strip()
        if not identifier:
            raise ValueError("ec_number must not be empty")

        query = f"ecNumber*{identifier}#"
        if organism:
            query += f"organism*{organism}#"

        body = self._call(method, query)
        records = parse_delimited_records(body)
        return self.normalize(BrendaRawResult(method=method, records=tuple(records)))

    def fetch_km(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """Km values for an EC number (``getKmValue``). ``[]`` if BRENDA has none."""
        return self._fetch_kinetic("getKmValue", ec_number, organism=organism)

    def fetch_ki(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """Ki values for an EC number (``getKiValue``). ``[]`` if BRENDA has none."""
        return self._fetch_kinetic("getKiValue", ec_number, organism=organism)

    def fetch_turnover_number(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """kcat/turnover-number values (``getTurnoverNumber``). ``[]`` if BRENDA has none."""
        return self._fetch_kinetic("getTurnoverNumber", ec_number, organism=organism)

    def fetch_kcat_km(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """kcat/Km (catalytic efficiency) values (``getKcatKmValue``). ``[]`` if none."""
        return self._fetch_kinetic("getKcatKmValue", ec_number, organism=organism)

    def fetch_ph_optimum(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """pH optimum values (``getPhOptimum``). ``[]`` if BRENDA has none."""
        return self._fetch_kinetic("getPhOptimum", ec_number, organism=organism)

    def fetch_temperature_optimum(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """Temperature optimum values (``getTemperatureOptimum``). ``[]`` if none."""
        return self._fetch_kinetic("getTemperatureOptimum", ec_number, organism=organism)

    def fetch_specific_activity(
        self, ec_number: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """Specific activity values (``getSpecificActivity``). ``[]`` if BRENDA has none."""
        return self._fetch_kinetic("getSpecificActivity", ec_number, organism=organism)

    def fetch(
        self, external_id: str, *, organism: str | None = None
    ) -> list[BrendaKineticMeasurement]:
        """``SourceConnector.fetch()``: BRENDA's most fundamental kinetic parameter, Km.

        ``external_id`` is an EC number. ``SourceConnector``'s three-method
        contract has no room to name all seven confirmed kinetic operations,
        so ``fetch()`` picks the most fundamental one (Km) and the other six
        are additional, explicitly-named methods (``fetch_ki()``,
        ``fetch_turnover_number()``, ``fetch_kcat_km()``,
        ``fetch_ph_optimum()``, ``fetch_temperature_optimum()``,
        ``fetch_specific_activity()``) -- the same pattern
        ``app.connectors.sgd.SgdConnector.fetch_go_details()`` established.
        Returns ``[]`` for a legitimate empty result, never ``None`` --
        unlike KEGG/SGD/PubMed's single-record ``fetch()``, this is
        inherently a collection-returning operation.
        """
        return self.fetch_km(external_id, organism=organism)

    def normalize(self, raw: BrendaRawResult) -> list[BrendaKineticMeasurement]:
        """Map a parsed BRENDA kinetic result onto BRENDA-scoped normalized records."""
        return normalize_kinetic_records(raw.records, method=raw.method)


__all__ = [
    "BrendaConnector",
    "BrendaKineticMeasurement",
    "BrendaRawResult",
    "BrendaSearchHit",
    "BrendaSoapFault",
    "build_brenda_http_client",
    "hash_brenda_password",
    "normalize_kinetic_records",
    "parse_delimited_records",
    "parse_soap_fault",
]
