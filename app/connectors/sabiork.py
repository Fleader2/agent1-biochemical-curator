"""SABIO-RK connector: retrieval and parsing only, no persistence or curation policy.

**Verified live, this increment (Agent 1.x Increment A), via direct HTTP
calls -- not from documentation alone.** SABIO-RK's long-documented legacy
SOAP/REST endpoint (``sabioRestWebServices``, offering SBML/BioPAX export)
was retired in 2025; every third-party tutorial or wrapper describing it
now describes a dead API. The current, live interface is a plain Apache
Solr ``/select`` endpoint:

* Base URL: ``https://sabiork.h-its.org/api/ft/proxy-select`` (confirmed
  live this increment: a real ``q=ECNumber:1.1.1.1`` query returned 768
  real matching entries).
* Query language: standard Solr ``q=Field:value`` syntax (quote
  multi-word values, e.g. ``Organism:"Homo sapiens"``); ``wt=json``,
  ``rows``, and ``fl`` (comma-separated field list) are standard Solr
  parameters, also confirmed live. Requesting an unrecognized field name
  in ``fl`` is silently ignored by Solr, not an error.
* Direct single-entry retrieval: ``q=EntryID:<id>``.
* **No authentication required** (confirmed live).
* Each Solr document also carries a field literally named ``Json``,
  containing the complete structured per-entry record (organism, enzyme,
  reaction, experimental conditions, publication, and every kinetic-law
  parameter) that the flat Solr fields alone do not expose cleanly. This
  connector always requests ``fl=EntryID,Json`` and parses ``Json`` --
  never reconstructs a record from the flat Solr fields, which are
  observed to be an incomplete, denormalized projection of the same data.

Only ``EntryID:<id>``/``ECNumber:<ec>`` were independently, live-confirmed
this increment; ``Organism:``/``UniProtID:``/``Substrate:`` field-scoped
filtering is supported per Solr's own query syntax and this connector
passes them through as additional ``AND``-combined clauses, but a
follow-up ``UniprotID:`` query during this increment's own research was
inconclusive (returned the same unfiltered top document), so those
additional filters are implemented conservatively as optional,
independently-combinable clauses rather than assumed correct without
further live verification -- see ``tests/connectors/test_sabiork.py`` for
what is and is not covered by a live-response-shaped fixture versus an
documented-only assumption.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient.get()``.
* parsing (``parse_solr_response()``, ``parse_kinetic_law_json()``): raw
  Solr JSON -> source-native structures. Pure, no I/O, nothing discarded
  that this connector has a typed slot for; ``raw`` on every typed record
  retains the parsed ``Json`` dict in full.
* normalization (``normalize()``): source-native ``SabioKineticRecord`` ->
  a tuple of one ``SabioKineticParameter`` per reported kinetic constant
  (a single SABIO-RK entry commonly reports more than one parameter type,
  e.g. both Km and kcat, for the same assay -- each becomes its own
  record, never merged or averaged). Still SABIO-only; no cross-source
  entity resolution, no ``KineticMeasurement`` row is written.
* persistence: not implemented here (``app.persistence.kinetic_measurement``).

Only kinetic-law parameters whose reported ``role`` is ``"Constant"`` are
treated as measured kinetic parameters (``"Variable"``-role entries are
assay conditions such as a tested substrate concentration, not a reported
kinetic constant, and are not converted into ``SabioKineticParameter``
records here).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config.settings import Settings, get_settings
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

_DEFAULT_BASE_URL = "https://sabiork.h-its.org/api/ft/proxy-select"

# Solr role that marks a kineticlaw.parameter[] entry as a reported kinetic
# constant (Km, kcat, Vmax, Ki, ...) rather than an assay condition
# ("Variable", e.g. a tested substrate concentration).
_CONSTANT_ROLE = "Constant"


@dataclass(frozen=True, slots=True)
class SabioSearchHit:
    """One Solr search result: enough to decide whether to fetch the full entry."""

    entry_id: str
    ec_numbers: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SabioKineticParameter:
    """One reported kinetic constant from one SABIO-RK entry's ``kineticlaw.parameter[]``.

    ``value`` is the reported (not SI-normalized) value: SABIO-RK's
    ``Json`` payload carries both ``start_value``/``unit.name`` (as
    reported) and ``n_start_value``/``unit.n_name`` (SI-normalized) for
    every parameter -- this connector keeps only the reported pair
    (``value``/``unit``) as a plain string (parsed to ``Decimal`` only in
    ``app.normalization.kinetic_measurement``, never here), consistent
    with Increment A's unit policy: no unit conversion in this repository
    yet, and the reported value is Agent 1's curation target, not a
    silently-substituted normalized one.
    """

    name: str | None
    parameter_type: str | None
    value: str | None
    unit: str | None
    species_label: str | None
    comment: str | None


@dataclass(frozen=True, slots=True)
class SabioKineticRecord:
    """One full SABIO-RK entry, parsed from its ``Json`` field.

    ``raw`` retains the complete parsed ``Json`` dict, so nothing SABIO-RK
    reported is lost by parsing -- only additional, typed fields are added.
    """

    entry_id: str
    parameters: tuple[SabioKineticParameter, ...]
    reaction_equation: str | None
    ec_number: str | None
    enzyme_name: str | None
    uniprot_ids: tuple[str, ...]
    is_wildtype: bool | None
    is_recombinant: bool | None
    organism: str | None
    ncbi_taxonomy_id: str | None
    strain: str | None
    tissue: str | None
    buffer: str | None
    ph: str | None
    temperature: str | None
    temperature_unit: str | None
    pubmed_id: str | None
    publication_title: str | None
    raw: dict[str, Any]


def parse_solr_response(text: str) -> list[dict[str, Any]]:
    """Parse a Solr ``/select?wt=json`` response into its ``response.docs`` list.

    An empty result set (``numFound == 0``) returns ``[]`` -- a legitimate
    empty result, not an error.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(
            f"malformed SABIO-RK Solr response: not valid JSON: {exc}"
        ) from exc

    response = payload.get("response")
    if not isinstance(response, dict) or "docs" not in response:
        raise ConnectorParseError(
            "malformed SABIO-RK Solr response: missing response.docs"
        )
    docs = response["docs"]
    if not isinstance(docs, list):
        raise ConnectorParseError("malformed SABIO-RK Solr response: response.docs is not a list")
    return docs


def _first(value: Any) -> Any:
    """Solr multi-valued fields are JSON arrays; take the first entry, if any."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_kinetic_law_json(entry_id: str, raw_json_text: str) -> SabioKineticRecord:
    """Parse one entry's ``Json`` field text into a typed ``SabioKineticRecord``.

    Raises ``ConnectorParseError`` for anything that is not valid JSON or
    is missing the top-level sections this connector expects
    (``kineticlaw``, ``general``, ``reaction``) -- a genuinely malformed or
    unrecognized payload shape, never silently reinterpreted.
    """
    try:
        payload = json.loads(raw_json_text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(
            f"malformed SABIO-RK entry {entry_id}: Json field is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConnectorParseError(
            f"malformed SABIO-RK entry {entry_id}: Json field is not an object"
        )

    kineticlaw = payload.get("kineticlaw") or {}
    general = payload.get("general") or {}
    reaction = payload.get("reaction") or {}
    enzyme = payload.get("enzyme_description") or {}
    conditions = payload.get("experimental_conditions") or {}
    publication = payload.get("publication") or {}

    parameters: list[SabioKineticParameter] = []
    for param in kineticlaw.get("parameter", []) or []:
        if not isinstance(param, dict):
            continue
        if param.get("role") != _CONSTANT_ROLE:
            continue
        parameter_type = param.get("parameter_type") or {}
        unit = param.get("unit") or {}
        species = param.get("species") or {}
        parameters.append(
            SabioKineticParameter(
                name=_as_str(param.get("name")),
                parameter_type=_as_str(parameter_type.get("name")),
                value=_as_str(param.get("start_value")),
                unit=_as_str(unit.get("name")),
                species_label=_as_str(species.get("species_key")),
                comment=_as_str(param.get("comment")),
            )
        )

    organism = general.get("organism") or {}
    proteins = enzyme.get("proteins") or []
    uniprot_ids = tuple(
        _as_str(protein.get("uniprot_id"))
        for protein in proteins
        if isinstance(protein, dict) and protein.get("uniprot_id")
    )
    ph_section = conditions.get("envvar_ph") or {}
    temperature_section = conditions.get("envvar_temperature") or {}
    temperature_unit = temperature_section.get("unit") or {}

    return SabioKineticRecord(
        entry_id=entry_id,
        parameters=tuple(parameters),
        reaction_equation=_as_str(reaction.get("equation")),
        ec_number=_as_str(enzyme.get("ec_number")),
        enzyme_name=_as_str(enzyme.get("enzyme_name")),
        uniprot_ids=tuple(uid for uid in uniprot_ids if uid),
        is_wildtype=bool(enzyme["wildtype"]) if "wildtype" in enzyme else None,
        is_recombinant=bool(enzyme["is_recombinant"]) if "is_recombinant" in enzyme else None,
        organism=_as_str(organism.get("name")),
        ncbi_taxonomy_id=_as_str(organism.get("ncbi_taxonomy_id")),
        strain=_as_str(general.get("strain")),
        tissue=_as_str(general.get("tissue")),
        buffer=_as_str(conditions.get("buffer")),
        ph=_as_str(ph_section.get("start_value")),
        temperature=_as_str(temperature_section.get("start_value")),
        temperature_unit=_as_str(temperature_unit.get("name")),
        pubmed_id=_as_str(publication.get("pubmed_id")),
        publication_title=_as_str(publication.get("title")),
        raw=payload,
    )


class SabiorkConnector:
    """Retrieval and parsing for SABIO-RK. No curation policy, no persistence."""

    source: SourceType = SourceType.SABIORK

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> SabiorkConnector:
        """Build a connector using the existing ``sabiork_base_url`` setting.

        No default base URL is invented if the setting is unset --
        consistent with ``KeggConnector.from_settings``/the rest of
        ``app.config.settings``: unknown configuration stays unknown rather
        than silently falling back to a hard-coded value, even though this
        connector's live endpoint was independently confirmed this
        increment (see module docstring).
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.sabiork_base_url:
            raise ValueError("Settings.sabiork_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.sabiork_base_url)

    def _select(self, query: str, *, fields: str, rows: int) -> list[dict[str, Any]]:
        response = self._http.get(
            self._base_url, params={"q": query, "wt": "json", "fl": fields, "rows": rows}
        )
        return parse_solr_response(response.text)

    def search(
        self,
        query: str,
        *,
        organism: str | None = None,
        uniprot_id: str | None = None,
        substrate: str | None = None,
        rows: int = 50,
    ) -> list[SabioSearchHit]:
        """Search by EC number (``query``), optionally narrowed by organism/UniProt/substrate.

        ``query`` is an EC number (SABIO-RK's ``ECNumber`` field, the one
        field-scoped filter independently confirmed live this increment).
        The optional keyword filters are passed through as additional,
        ``AND``-combined Solr clauses per SABIO-RK's documented field names
        -- not independently re-verified live for every field (see module
        docstring). Returns ``[]`` for a legitimate empty result.
        """
        ec_number = query.strip()
        if not ec_number:
            raise ValueError("query must not be empty")

        clauses = [f"ECNumber:{ec_number}"]
        if organism:
            clauses.append(f'Organism:"{organism}"')
        if uniprot_id:
            clauses.append(f"UniProtID:{uniprot_id}")
        if substrate:
            clauses.append(f'Substrate:"{substrate}"')
        solr_query = " AND ".join(clauses)

        docs = self._select(solr_query, fields="EntryID,ECNumber", rows=rows)
        hits = []
        for doc in docs:
            entry_id = _as_str(_first(doc.get("EntryID")))
            if entry_id is None:
                continue
            ec_numbers = tuple(
                ec for ec in (doc.get("ECNumber") or []) if isinstance(ec, str) and ec.strip()
            )
            hits.append(SabioSearchHit(entry_id=entry_id, ec_numbers=ec_numbers, raw=doc))
        return hits

    def fetch(self, external_id: str) -> SabioKineticRecord | None:
        """Retrieve one full entry by its SABIO-RK ``EntryID``.

        Returns ``None`` when no entry with this ``EntryID`` exists (an
        empty Solr result) -- a legitimate "no such record" outcome, not a
        failure. Any other retrieval failure still raises.
        """
        entry_id = external_id.strip()
        if not entry_id:
            raise ValueError("external_id must not be empty")

        try:
            docs = self._select(f"EntryID:{entry_id}", fields="EntryID,Json", rows=1)
        except ConnectorHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not docs:
            return None

        doc = docs[0]
        raw_json = _first(doc.get("Json"))
        if raw_json is None:
            raise ConnectorParseError(
                f"malformed SABIO-RK entry {entry_id}: no Json field in response"
            )
        raw_json_text = raw_json if isinstance(raw_json, str) else json.dumps(raw_json)
        return parse_kinetic_law_json(entry_id, raw_json_text)

    def normalize(self, raw: SabioKineticRecord) -> tuple[SabioKineticParameter, ...]:
        """Every reported kinetic constant for one entry, as independent records.

        Never merged or averaged -- one SABIO-RK entry commonly reports
        more than one parameter type (e.g. both Km and kcat) for the same
        assay, and each stays its own record.
        """
        return raw.parameters


__all__ = [
    "SabioKineticParameter",
    "SabioKineticRecord",
    "SabioSearchHit",
    "SabiorkConnector",
    "parse_kinetic_law_json",
    "parse_solr_response",
]
