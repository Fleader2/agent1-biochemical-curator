"""Open Enzyme Database (OED) connector: retrieval and parsing only.

**Verified live, this increment (Agent 1.x Increment A), via direct HTTP
calls -- not from documentation alone.** OED's real, live public API root
is ``http://openenzymedb-api.platform.moleculemaker.org/api/v1`` (the
project's marketing/browse site,
``https://openenzymedb.platform.moleculemaker.org``, is a separate web UI,
not this API, and this connector never scrapes it). Two endpoints exist,
confirmed live this increment:

* ``GET /api/v1/data`` -- paginated kinetic-parameter records. Supports
  ``ec_number``/``organism``/``uniprot_id``/``parameter_type`` query
  filters (confirmed live: an ``ec_number=1.1.1.1`` query returned real,
  matching records) plus ``page``/``page_size`` pagination.
* ``GET /api/v1/metadata`` -- summary counts/enumerations, not used by
  this connector (no kinetic data).

**No prediction endpoint exists.** OED's own site describes itself as an
aggregator of experimentally-reported measurements (drawn from
BRENDA/SABIO-RK, among others) plus a separate ML-based *prediction
tool*, but that prediction tool is a client-side/notebook feature, not a
data API endpoint -- ``/api/v1/data`` was confirmed live this increment to
return only rows carrying a ``pubmedid`` (a literature citation), which is
inherent to a reported measurement, never present on a model-predicted
value. Step 11's "never ingest AI-predicted data" requirement is therefore
satisfied structurally: there is no predicted-data endpoint to accidentally
call, and ``tests/connectors/test_open_enzyme_database.py`` asserts this
connector calls only ``/data``/``/metadata``, never any other path.

**No source-lineage field is exposed.** OED's own site states that its
records originate from BRENDA/SABIO-RK, but the live ``/api/v1/data``
response (confirmed this increment) carries no field identifying which
underlying database, or which original record within it, any given row
came from -- only a ``pubmedid`` and a self-contained kinetic value. This
is a genuine, disclosed limitation, not a design shortfall: derivative
detection (Step 25 of the Increment A specification) requires exactly this
kind of explicit lineage, so it structurally cannot fire for OED records
in this increment. See ``docs/24_kinetic_data_curation_and_handoff.md``
§7 for the concrete follow-up when/if OED's API adds such a field.

**No native per-parameter record ID is exposed either.** A single OED
``/data`` row commonly reports up to three parameter values at once (e.g.
``kcat``, ``km``, ``kcat_km``) as sibling fields on one JSON object, not
as separate rows with their own IDs. This connector's ``normalize()``
therefore splits one row into up to three independent
``OedKineticParameter`` records (one per non-null parameter value,
consistent with this repository's "every measurement is independent, one
row per constant" policy -- see ``app/connectors/sabiork.py`` for the same
policy applied to SABIO-RK's own multi-parameter entries) and computes a
deterministic identity for each from the row's own already-specific
fields (``ec_number``, ``substrate``, ``organism``, ``uniprot_id``,
``enzyme_type``, ``parameter_type``, ``pubmed_id``) via SHA-256 of a
canonical sorted-keys JSON encoding -- never Python's built-in ``hash()``,
per this repository's established convention (``app.normalization``).

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient.get()``.
* parsing (``parse_data_response()``): raw OED JSON -> ``OedDataRow``. Pure,
  no I/O, nothing discarded (``raw`` on every row retains the full parsed
  JSON object).
* normalization (``normalize()``): one ``OedDataRow`` -> a tuple of
  ``OedKineticParameter`` (0-3 entries, one per reported parameter value).
  Still OED-only; no cross-source entity resolution, no
  ``KineticMeasurement`` row is written.
* persistence: not implemented here (``app.persistence.kinetic_measurement``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.config.settings import Settings, get_settings
from app.connectors.exceptions import ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

# One JSON key per reported kinetic parameter value that a single OED
# ``/data`` row may carry as a sibling field. Each present, non-null value
# becomes its own independent ``OedKineticParameter`` record.
_PARAMETER_FIELDS: tuple[str, ...] = ("kcat", "km", "kcat_km")


@dataclass(frozen=True, slots=True)
class OedDataRow:
    """One raw record from OED's ``/api/v1/data``, parsed but not yet split.

    ``raw`` retains the complete parsed JSON object, so nothing OED
    reported is lost by parsing.
    """

    ec_number: str | None
    substrate: str | None
    organism: str | None
    uniprot_id: str | None
    enzyme_type: str | None
    pubmed_id: str | None
    kcat: str | None
    km: str | None
    kcat_km: str | None
    unit_kcat: str | None
    unit_km: str | None
    unit_kcat_km: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OedKineticParameter:
    """One independent reported kinetic constant, split out of one ``OedDataRow``.

    ``source_identifier`` is this connector's own deterministically-computed
    identity (see module docstring) -- OED exposes no native per-parameter
    record ID. ``original_source``/``original_source_identifier`` are always
    ``None``: OED's live API (confirmed this increment) exposes no
    source-lineage field, so this connector never invents one.
    """

    source_identifier: str
    parameter_type: str
    value: str
    unit: str | None
    ec_number: str | None
    substrate: str | None
    organism: str | None
    uniprot_id: str | None
    enzyme_type: str | None
    pubmed_id: str | None
    original_source: SourceType | None
    original_source_identifier: str | None
    raw: dict[str, Any]


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _canonical_identity_digest(fields: dict[str, str | None]) -> str:
    """SHA-256 of a canonical, sorted-keys JSON encoding of ``fields``.

    Never Python's built-in ``hash()`` (unstable across processes/versions,
    and not cryptographically deterministic) -- this repository's
    established convention for deterministic identity when a source
    provides no native stable ID (mirrored from BRENDA's own connector).
    """
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_data_response(text: str) -> list[OedDataRow]:
    """Parse an OED ``/api/v1/data`` JSON response into its rows.

    Accepts either a bare JSON array of row objects or an envelope object
    with a ``"results"``/``"data"`` list -- OED's live response (confirmed
    this increment) is a bare array, but this connector tolerates either
    shape defensively rather than assuming one specific envelope forever.
    An empty result is a legitimate ``[]``, not an error.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(f"malformed OED response: not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        for key in ("results", "data"):
            if key in payload and isinstance(payload[key], list):
                rows_payload = payload[key]
                break
        else:
            raise ConnectorParseError(
                "malformed OED response: object payload has no results/data list"
            )
    elif isinstance(payload, list):
        rows_payload = payload
    else:
        raise ConnectorParseError("malformed OED response: not a JSON array or object")

    rows: list[OedDataRow] = []
    for entry in rows_payload:
        if not isinstance(entry, dict):
            raise ConnectorParseError(f"malformed OED response row: not an object: {entry!r}")
        rows.append(
            OedDataRow(
                ec_number=_as_str(entry.get("ec_number")),
                substrate=_as_str(entry.get("substrate")),
                organism=_as_str(entry.get("organism")),
                uniprot_id=_as_str(entry.get("uniprot_id")),
                enzyme_type=_as_str(entry.get("enzyme_type")),
                pubmed_id=_as_str(entry.get("pubmedid") or entry.get("pubmed_id")),
                kcat=_as_str(entry.get("kcat")),
                km=_as_str(entry.get("km")),
                kcat_km=_as_str(entry.get("kcat_km")),
                unit_kcat=_as_str(entry.get("kcat_unit")),
                unit_km=_as_str(entry.get("km_unit")),
                unit_kcat_km=_as_str(entry.get("kcat_km_unit")),
                raw=entry,
            )
        )
    return rows


class OpenEnzymeDatabaseConnector:
    """Retrieval and parsing for Open Enzyme Database. No curation policy, no persistence."""

    source: SourceType = SourceType.OED

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> OpenEnzymeDatabaseConnector:
        """Build a connector using the existing ``oed_base_url`` setting.

        No default base URL is invented if the setting is unset --
        consistent with ``KeggConnector.from_settings``/the rest of
        ``app.config.settings``.
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.oed_base_url:
            raise ValueError("Settings.oed_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.oed_base_url)

    def search(
        self,
        *,
        ec_number: str | None = None,
        organism: str | None = None,
        uniprot_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[OedDataRow]:
        """Search ``/api/v1/data`` by EC number/organism/UniProt ID (all optional, AND-combined).

        Returns ``[]`` for a legitimate empty result. At least one filter
        must be given -- an unfiltered full-table scan is not a supported
        use of this connector.
        """
        if not (ec_number or organism or uniprot_id):
            raise ValueError("at least one of ec_number, organism, uniprot_id must be given")

        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if ec_number:
            params["ec_number"] = ec_number
        if organism:
            params["organism"] = organism
        if uniprot_id:
            params["uniprot_id"] = uniprot_id

        url = f"{self._base_url}/data"
        response = self._http.get(url, params=params)
        return parse_data_response(response.text)

    def fetch(self, external_id: str) -> OedDataRow | None:
        """OED exposes no single-record-by-ID endpoint; use ``search()`` instead.

        Kept for ``SourceConnector`` protocol conformance. Always raises:
        callers must retrieve OED rows via ``search()``, since OED assigns
        no native per-record ID this connector could fetch by (see module
        docstring: identity is computed downstream, in ``normalize()``, from
        the row's own content, not looked up by ID upstream).
        """
        raise NotImplementedError(
            "Open Enzyme Database has no fetch-by-id endpoint; use search() and "
            "normalize() instead"
        )

    def normalize(self, raw: OedDataRow) -> tuple[OedKineticParameter, ...]:
        """Split one OED row into 0-3 independent kinetic-parameter records.

        Never merged or averaged. Each result's ``source_identifier`` is a
        deterministic digest of this row's own identifying fields plus the
        specific parameter type, so re-normalizing the identical row always
        yields the identical identity (idempotent), and two rows that
        happen to report the same numeric value for genuinely different
        conditions still get distinct identities.
        """
        results: list[OedKineticParameter] = []
        parameter_values = {
            "kcat": (raw.kcat, raw.unit_kcat),
            "km": (raw.km, raw.unit_km),
            "kcat_km": (raw.kcat_km, raw.unit_kcat_km),
        }
        for parameter_type, (value, unit) in parameter_values.items():
            if value is None:
                continue
            identity_fields = {
                "ec_number": raw.ec_number,
                "substrate": raw.substrate,
                "organism": raw.organism,
                "uniprot_id": raw.uniprot_id,
                "enzyme_type": raw.enzyme_type,
                "parameter_type": parameter_type,
                "pubmed_id": raw.pubmed_id,
            }
            source_identifier = _canonical_identity_digest(identity_fields)
            results.append(
                OedKineticParameter(
                    source_identifier=source_identifier,
                    parameter_type=parameter_type,
                    value=value,
                    unit=unit,
                    ec_number=raw.ec_number,
                    substrate=raw.substrate,
                    organism=raw.organism,
                    uniprot_id=raw.uniprot_id,
                    enzyme_type=raw.enzyme_type,
                    pubmed_id=raw.pubmed_id,
                    original_source=None,
                    original_source_identifier=None,
                    raw=raw.raw,
                )
            )
        return tuple(results)


__all__ = [
    "OedDataRow",
    "OedKineticParameter",
    "OpenEnzymeDatabaseConnector",
    "parse_data_response",
]
