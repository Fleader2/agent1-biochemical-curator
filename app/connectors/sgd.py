"""SGD connector: retrieval and parsing only, no persistence or curation policy.

**Verified against the live SGD API.** Unlike the initial version of this
module, every endpoint, identifier-lookup behavior, and field name below was
confirmed with live requests to https://www.yeastgenome.org (CDC28 /
YBR160W / S000000364) during this increment, not assumed. The official
webservice documentation at
https://github.com/yeastgenome/SGDBackend-Nex2/blob/master/docs/webservice.MD
describes an older ``/webservice/`` base path that no longer resolves (a live
request to it returned SGD's 404 page); the live, currently-working base
path is ``/backend/``, discovered via SGD's own site navigation
(``/api/doc``) and confirmed working. If SGD changes its API again, this
module's assumptions will need re-verification the same way -- there is no
guarantee of stability beyond what was directly observed.

Endpoints used:

* ``GET {base_url}/locus/{identifier}`` -- fetch a single locus/gene record
  (``fetch()``). Confirmed to accept an SGD ID (``S000000364``), a
  systematic/ORF name (``YBR160W``), and a standard gene name (``CDC28``)
  interchangeably -- all three resolved to the identical record in live
  testing.
* ``GET {base_url}/get_search_results?q={query}&category=locus`` -- search
  (``search()``), scoped to the ``locus`` category so results are genes/
  features rather than references, phenotypes, GO terms, etc. Confirmed live:
  a real query returns ``{"total": {...}, "results": [...], "aggregations":
  [...]}``; an unmatched query returns ``{"total": {"value": 0, ...},
  "results": []}`` with HTTP 200, not an error.
* ``GET {base_url}/locus/{identifier}/go_details`` -- GO annotations for a
  locus, including subcellular localization (``fetch_go_details()``).
  Confirmed live: a JSON array of annotation objects, each carrying a
  ``go.go_aspect`` of ``"cellular component"`` (localization), ``"molecular
  function"``, or ``"biological process"``, plus ``annotation_type`` and an
  ``experiment.display_name`` evidence code (e.g. ``"IBA"``, ``"IDA"``).
  Localization is **not** present on the basic locus record at all -- it
  lives only here, confirmed by inspecting a real locus response and finding
  no such field.
* A locus identifier SGD does not recognize returns HTTP 404 (confirmed
  live against a deliberately invalid identifier) -- not a 200 with an empty
  or error-shaped body.

``sgd_base_url`` (``app.config.settings.Settings``) still has no default,
consistent with ``kegg_base_url`` in this same codebase: this is a
deliberate consistency choice, not an oversight -- see ``SgdConnector``'s
docstring for the reasoning, which applies even now that the URL is a
verified fact rather than an assumption. SGD's public API requires no
credential, and none is invented here. SGD does not publish a specific
numeric rate limit; this module still does not invent one.

All HTTP retrieval goes through ``app.connectors.http.ConnectorHttpClient``,
which owns every retry, backoff, timeout, rate-limit, and cache concern.
Nothing in this module retries a request, waits out a rate limit, or reads/
writes a database.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``/``fetch_go_details()``): network I/O
  only, via ``ConnectorHttpClient``.
* parsing (``parse_locus_response()``/``parse_search_response()``/
  ``parse_go_details_response()``): raw SGD JSON -> a source-native
  structure. Pure functions, no I/O, nothing discarded (the complete
  original locus JSON is kept on ``SgdLocusRecord.raw``).
* normalization (``normalize()``, ``normalize_locus()``): source-native
  structure -> an SGD-scoped typed record (``SgdNormalizedRecord``) shaped
  close to ``app.models.gene.Gene``'s columns -- still SGD-only; no
  cross-source entity resolution, no ``Gene``/``Protein`` row is written.
* persistence: not implemented here (a later increment).

A scientific-integrity note on ``SgdGoAnnotation`` (used for GO annotations,
including localization): an SGD GO annotation is a curated database
statement, not direct experimental evidence in this connector's hands
(``.cursor/rules/01-scientific-integrity.mdc``: "Do not treat a database
annotation as direct experimental evidence") -- regardless of what the
underlying GO evidence code says. ``source_category_label`` is always
``"database_annotation"`` for exactly this reason. This connector preserves
``annotation_type`` and the evidence code (e.g. ``"IDA"``, ``"IEA"``)
faithfully, since a later evidence-extraction phase may need them, but it
never assigns an ``app.models.enums.EvidenceType`` itself and never lets a
GO annotation look like an undifferentiated, unlabeled fact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.config.settings import Settings, get_settings
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

# Well-established, stable yeast genome nomenclature (not SGD-API-specific):
# SGD IDs are "S" followed by nine digits; systematic ("ORF") names are a
# chromosome letter (A-P) and arm (L/R) followed by a three-digit ORF number
# and a strand (W/C), with an optional dubious/alternate-ORF letter suffix.
_SGD_ID_PATTERN = re.compile(r"^S\d{9}$")
_SYSTEMATIC_NAME_PATTERN = re.compile(r"^Y[A-P][LR]\d{3}[WC](-[A-Z])?$", re.IGNORECASE)

_LOCALIZATION_ASPECT = "cellular component"


def classify_sgd_identifier(identifier: str) -> str:
    """Classify an identifier string as ``"sgd_id"``, ``"systematic_name"``, or
    ``"standard_name"``, by shape alone. Live-verified: SGD's locus endpoint
    accepts all three forms interchangeably, so this classification is
    informational, not a gate on what ``fetch()`` will accept.
    """
    stripped = identifier.strip()
    if not stripped:
        raise ValueError("identifier must not be empty")
    if _SGD_ID_PATTERN.match(stripped):
        return "sgd_id"
    if _SYSTEMATIC_NAME_PATTERN.match(stripped):
        return "systematic_name"
    return "standard_name"


@dataclass(frozen=True, slots=True)
class SgdSearchHit:
    """One hit from an SGD locus search.

    ``sgd_id`` is extracted from the result's ``href`` field (e.g.
    ``"/locus/S000000364"``) -- live-verified search results carry no direct
    ``sgdid`` key, only ``href`` and an internal search-index ``id`` (a UUID,
    confirmed live, unrelated to the SGD ID).
    """

    sgd_id: str
    systematic_name: str | None
    standard_name: str | None
    description: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SgdAliasEntry:
    """One entry from a locus record's ``aliases`` array.

    Live-verified: this array mixes genuine alternate gene names
    (``category == "Alias"``) with cross-references to other resources
    (``"PomBase ID"``, ``"UniProtKB ID"``, ``"EC number"`` were observed for
    CDC28) under the same key. This connector preserves that mixture as-is
    rather than guessing which entries are "real" aliases --
    ``normalize_locus()`` is where the ``"Alias"``-category entries are
    picked out for the schema-shaped ``aliases`` field.
    """

    display_name: str
    category: str
    link: str | None = None


@dataclass(frozen=True, slots=True)
class SgdExternalLink:
    """One entry from a locus record's ``urls`` array: an external cross-reference.

    Live-verified example: ``{"category": "LOCUS_LSP_RESOURCES",
    "display_name": "Entrez Gene", "link": "http://www.ncbi.nlm.nih.gov/gene/852457"}``.
    """

    category: str
    display_name: str
    link: str | None


@dataclass(frozen=True, slots=True)
class SgdGoAnnotation:
    """One GO annotation from ``/locus/{id}/go_details``, including localization.

    ``aspect`` is one of ``"cellular component"`` (subcellular localization),
    ``"molecular function"``, or ``"biological process"`` (live-verified
    values). ``source_category_label`` is always ``"database_annotation"`` --
    see the module docstring's scientific-integrity note.
    """

    go_id: str
    term: str
    aspect: str
    annotation_type: str | None
    evidence_code: str | None
    source_category_label: str = "database_annotation"

    @property
    def is_localization(self) -> bool:
        return self.aspect == _LOCALIZATION_ASPECT


@dataclass(frozen=True, slots=True)
class SgdLocusRecord:
    """Source-native parsed record for one SGD locus -- nothing discarded.

    ``raw`` retains the complete original JSON object, so a field this
    increment has no specific typed representation for (``qualifier``,
    ``bioent_status``, ``paralogs``, ``complements``, ``references``, ...)
    is still reachable. There is no ``chromosome``/location field: live
    verification found none on this endpoint (SGD may expose genomic
    coordinates via a separate ``sequence_details`` endpoint, not
    implemented here).
    """

    sgd_id: str
    systematic_name: str | None
    standard_name: str | None
    locus_type: str | None
    description: str | None
    aliases: tuple[SgdAliasEntry, ...]
    uniprot_id: str | None
    external_links: tuple[SgdExternalLink, ...]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SgdNormalizedRecord:
    """SGD-scoped normalized view, shaped close to the ``gene`` table's columns.

    ``aliases`` here is narrowed to the locus record's ``"Alias"``-category
    entries only (genuine alternate gene names), as plain strings -- the
    full mixed alias/cross-reference list is still available via
    ``raw.aliases``. ``raw`` retains the complete parsed record, so nothing
    is lost by normalizing. This is not a ``Gene`` row and nothing here is
    persisted or matched against another source's identifiers.
    """

    sgd_id: str
    systematic_name: str | None
    standard_name: str | None
    description: str | None
    aliases: tuple[str, ...]
    uniprot_id: str | None
    external_links: tuple[SgdExternalLink, ...]
    raw: SgdLocusRecord


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_aliases(value: Any) -> tuple[SgdAliasEntry, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConnectorParseError(
            f"malformed SGD response: 'aliases' must be a list, got {type(value).__name__}"
        )
    aliases: list[SgdAliasEntry] = []
    for entry in value:
        if not isinstance(entry, dict) or "display_name" not in entry or "category" not in entry:
            raise ConnectorParseError(f"malformed SGD aliases entry: {entry!r}")
        aliases.append(
            SgdAliasEntry(
                display_name=str(entry["display_name"]),
                category=str(entry["category"]),
                link=_optional_str(entry.get("link")),
            )
        )
    return tuple(aliases)


def _parse_external_links(value: Any) -> tuple[SgdExternalLink, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConnectorParseError(
            f"malformed SGD response: 'urls' must be a list, got {type(value).__name__}"
        )
    links: list[SgdExternalLink] = []
    for entry in value:
        if not isinstance(entry, dict) or "category" not in entry or "display_name" not in entry:
            raise ConnectorParseError(f"malformed SGD urls entry: {entry!r}")
        links.append(
            SgdExternalLink(
                category=str(entry["category"]),
                display_name=str(entry["display_name"]),
                link=_optional_str(entry.get("link")),
            )
        )
    return tuple(links)


def parse_locus_response(data: Any) -> SgdLocusRecord:
    """Parse a single SGD locus JSON object. Pure: no HTTP or DB access."""
    if not isinstance(data, dict):
        raise ConnectorParseError(
            f"malformed SGD locus response: expected a JSON object, got {type(data).__name__}"
        )

    sgd_id = data.get("sgdid")
    if not isinstance(sgd_id, str) or not sgd_id.strip():
        raise ConnectorParseError("malformed SGD locus response: missing or empty 'sgdid'")

    return SgdLocusRecord(
        sgd_id=sgd_id.strip(),
        systematic_name=_optional_str(data.get("format_name")),
        standard_name=_optional_str(data.get("display_name")),
        locus_type=_optional_str(data.get("locus_type")),
        description=_optional_str(data.get("description")),
        aliases=_parse_aliases(data.get("aliases")),
        uniprot_id=_optional_str(data.get("uniprot_id")),
        external_links=_parse_external_links(data.get("urls")),
        raw=data,
    )


def _split_search_name(name: str) -> tuple[str | None, str | None]:
    """Split a search result's ``"STANDARD / SYSTEMATIC"`` name field.

    Live-verified shape: ``"CDC28 / YBR160W"``. Many yeast ORFs have no
    standard name and appear as just the systematic name alone with no
    slash; which form a lone name is is decided mechanically by matching
    the well-established systematic-name pattern, not by guessing.
    """
    parts = [part.strip() for part in name.split("/")]
    if len(parts) == 2:
        return (parts[0] or None), (parts[1] or None)
    if len(parts) == 1 and parts[0]:
        single = parts[0]
        if _SYSTEMATIC_NAME_PATTERN.match(single):
            return None, single
        return single, None
    return None, None


def _sgd_id_from_href(href: Any) -> str | None:
    if not isinstance(href, str) or not href.strip():
        return None
    segment = href.strip().rstrip("/").rsplit("/", maxsplit=1)[-1]
    return segment or None


def parse_search_response(data: Any) -> list[SgdSearchHit]:
    """Parse a ``get_search_results`` response's locus hits.

    An empty ``results`` array is a legitimate empty result and returns
    ``[]`` -- it is not an error.
    """
    if not isinstance(data, dict):
        raise ConnectorParseError(
            f"malformed SGD search response: expected a JSON object, got {type(data).__name__}"
        )

    results = data.get("results")
    if results is None:
        raise ConnectorParseError("malformed SGD search response: missing 'results'")
    if not isinstance(results, list):
        raise ConnectorParseError(
            f"malformed SGD search response: 'results' must be a list, got {type(results).__name__}"
        )

    hits: list[SgdSearchHit] = []
    for entry in results:
        if not isinstance(entry, dict):
            raise ConnectorParseError(
                f"malformed SGD search hit: expected a JSON object, got {type(entry).__name__}"
            )
        sgd_id = _sgd_id_from_href(entry.get("href"))
        if not sgd_id:
            raise ConnectorParseError(f"malformed SGD search hit: unusable 'href': {entry!r}")

        standard_name, systematic_name = None, None
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            standard_name, systematic_name = _split_search_name(name)

        alias_values = entry.get("aliases")
        aliases = (
            tuple(a.strip() for a in alias_values if isinstance(a, str) and a.strip())
            if isinstance(alias_values, list)
            else ()
        )

        hits.append(
            SgdSearchHit(
                sgd_id=sgd_id,
                systematic_name=systematic_name,
                standard_name=standard_name,
                description=_optional_str(entry.get("description")),
                aliases=aliases,
            )
        )
    return hits


def parse_go_details_response(data: Any) -> list[SgdGoAnnotation]:
    """Parse a ``/locus/{id}/go_details`` response into GO annotations."""
    if not isinstance(data, list):
        raise ConnectorParseError(
            f"malformed SGD go_details response: expected a JSON array, got {type(data).__name__}"
        )

    annotations: list[SgdGoAnnotation] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ConnectorParseError(
                "malformed SGD go_details entry: expected a JSON object, "
                f"got {type(entry).__name__}"
            )
        go = entry.get("go")
        if not isinstance(go, dict) or "go_id" not in go or "go_aspect" not in go:
            raise ConnectorParseError(f"malformed SGD go_details entry: {entry!r}")

        experiment = entry.get("experiment")
        evidence_code = (
            _optional_str(experiment.get("display_name")) if isinstance(experiment, dict) else None
        )

        annotations.append(
            SgdGoAnnotation(
                go_id=str(go["go_id"]),
                term=str(go.get("display_name", "")),
                aspect=str(go["go_aspect"]),
                annotation_type=_optional_str(entry.get("annotation_type")),
                evidence_code=evidence_code,
            )
        )
    return annotations


def normalize_locus(record: SgdLocusRecord) -> SgdNormalizedRecord:
    """Map a generic parsed record onto SGD-scoped, schema-shaped fields.

    ``aliases`` is narrowed to ``"Alias"``-category entries -- the full
    mixed list (including cross-reference-shaped entries like "UniProtKB
    ID") stays reachable via ``raw.aliases``.
    """
    return SgdNormalizedRecord(
        sgd_id=record.sgd_id,
        systematic_name=record.systematic_name,
        standard_name=record.standard_name,
        description=record.description,
        aliases=tuple(a.display_name for a in record.aliases if a.category == "Alias"),
        uniprot_id=record.uniprot_id,
        external_links=record.external_links,
        raw=record,
    )


def _parse_json(text: str, *, context: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(
            f"malformed SGD {context} response: not valid JSON: {exc}"
        ) from exc


class SgdConnector:
    """Retrieval and parsing for SGD. No curation policy, no persistence.

    SGD does not publish a specific numeric request-rate limit the way NCBI
    does for E-utilities, so this connector does not invent one: there is no
    ``build_sgd_http_client`` proposing a specific interval. Callers should
    still configure a conservative rate limiter (an
    ``app.connectors.ratelimit.IntervalRateLimiter``, or any
    ``app.connectors.ratelimit.RateLimiter``) on the ``ConnectorHttpClient``
    they construct, as ordinary good API citizenship -- this connector uses
    whatever it is given and never bypasses it.

    ``sgd_base_url`` has no built-in default, even though the current base
    URL is now a verified fact (``https://www.yeastgenome.org/backend``),
    for consistency with ``app.connectors.kegg.KeggConnector`` in this same
    codebase, which treats its base URL the same way on the same reasoning:
    the base URL is environment-dependent configuration, not a constant --
    and SGD's own API has already moved once (the officially documented
    ``/webservice/`` path no longer resolves; live traffic now goes through
    ``/backend/``), so hard-coding today's value as an unconditional default
    would trade one unverifiable assumption for another.
    """

    source: SourceType = SourceType.SGD

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> SgdConnector:
        """Build a connector using the existing ``sgd_base_url`` setting.

        No default base URL is invented if the setting is unset -- consistent
        with ``app.connectors.kegg.KeggConnector.from_settings``.
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.sgd_base_url:
            raise ValueError("Settings.sgd_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.sgd_base_url)

    def search(self, query: str) -> list[SgdSearchHit]:
        """Search SGD for matching genes/features (``category=locus``).

        Returns ``[]`` for a legitimate empty result. A retrieval failure
        (timeout, rate limit exhausted, HTTP error) raises the corresponding
        ``app.connectors.exceptions.ConnectorError`` instead -- the two are
        never confused. Full locus records are not fetched implicitly;
        callers that want them call ``fetch()`` per SGD ID.
        """
        if not query.strip():
            raise ValueError("query must not be empty")

        response = self._http.get(
            f"{self._base_url}/get_search_results", params={"q": query, "category": "locus"}
        )
        data = _parse_json(response.text, context="search")
        return parse_search_response(data)

    def fetch(self, external_id: str) -> SgdLocusRecord | None:
        """Retrieve a single SGD locus by SGD ID, systematic name, or standard name.

        Live-verified: SGD's locus endpoint accepts all three forms
        interchangeably, and this connector passes the identifier through
        unmodified rather than trying to classify or convert it first
        (``classify_sgd_identifier`` is available separately for callers who
        want to know which form a string looks like). Returns ``None`` when
        SGD reports the identifier does not exist (confirmed live: HTTP
        404) -- a legitimate "no such record" outcome, not invented as an
        empty/default record. Any other retrieval failure still raises.
        """
        identifier = external_id.strip()
        if not identifier:
            raise ValueError("external_id must not be empty")

        url = f"{self._base_url}/locus/{quote(identifier, safe='')}"
        try:
            response = self._http.get(url)
        except ConnectorHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = _parse_json(response.text, context="locus")
        return parse_locus_response(data)

    def fetch_go_details(self, sgd_id: str) -> list[SgdGoAnnotation] | None:
        """Retrieve GO annotations (including localization) for a locus.

        Localization is not part of the basic locus record (``fetch()``) --
        it is only available here, filtered to ``aspect == "cellular
        component"`` (``SgdGoAnnotation.is_localization``). Returns ``None``
        when the locus itself does not exist (HTTP 404, same convention as
        ``fetch()``); returns ``[]`` when the locus exists but has no GO
        annotations -- the two are not the same thing.
        """
        identifier = sgd_id.strip()
        if not identifier:
            raise ValueError("sgd_id must not be empty")

        url = f"{self._base_url}/locus/{quote(identifier, safe='')}/go_details"
        try:
            response = self._http.get(url)
        except ConnectorHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = _parse_json(response.text, context="go_details")
        return parse_go_details_response(data)

    def normalize(self, raw: SgdLocusRecord) -> SgdNormalizedRecord:
        """Map a parsed SGD locus record onto an SGD-scoped normalized shape."""
        return normalize_locus(raw)


__all__ = [
    "SgdAliasEntry",
    "SgdConnector",
    "SgdExternalLink",
    "SgdGoAnnotation",
    "SgdLocusRecord",
    "SgdNormalizedRecord",
    "SgdSearchHit",
    "classify_sgd_identifier",
    "normalize_locus",
    "parse_go_details_response",
    "parse_locus_response",
    "parse_search_response",
]
