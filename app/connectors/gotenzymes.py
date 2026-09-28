"""GotEnzymes2 connector: retrieval and parsing only, no persistence or curation policy.

**Verified against the live Metabolic Atlas API.** GotEnzymes2
(https://metabolicatlas.org/gotenzymes) is an AI-predicted (never
experimentally measured) enzyme kinetic-parameter database -- Km, kcat,
kcat/Km, plus thermal properties this module never imports (Agent 1.x
Increment C.11 instructions: only Km/kcat/kcat-over-Km are in scope).
Programmatic access is a real, documented REST API at
``https://metabolicatlas.org/api/v2`` (confirmed live this increment: its
Swagger UI at ``/api/v2/swagger/`` embeds a real OpenAPI 2.0 document
listing a dedicated ``GotEnzymes`` tag). No authentication is required.

Endpoints used (both confirmed live this increment, not assumed from the
Swagger description alone, which specifies no response schema):

* ``GET {base_url}/gotenzymes/enzymes`` -- bulk, paginated search
  (``search()``), filterable by ``filters[gene]``/``filters[ec_number]``/
  ``filters[compound]``/``filters[organism]``/``filters[reaction_id]``/
  ``filters[domain]``. Confirmed live (``filters[organism]=sce``,
  ``filters[gene]=YNR016C``): a JSON object
  ``{"enzymes": [...], "totalCount": "<string int>"}``, each row shaped
  ``{"gene": "YNR016C", "organism": "sce", "domain": "E", "reaction_id":
  "R00742", "ec_number": "6.4.1.2", "compound": "C00024", "kcat_values":
  3.9841, "km_values": 0.043, "kcat_km_values": 72.14, "topt": 28.1826,
  "tm": 40.6314}`` -- any of ``kcat_values``/``km_values``/
  ``kcat_km_values`` may independently be JSON ``null`` (a real reactant
  the model produced no usable prediction for, confirmed live: e.g. EC
  6.3.4.14 against compound C06250 for real gene YNR016C). ``gene`` is a
  KEGG-style systematic locus name (confirmed real for yeast: matches
  ``Gene.kegg_gene_id``/``Gene.systematic_name`` already curated by this
  repository, e.g. ``"YNR016C"`` for ACC1); ``compound``/``reaction_id``
  are real KEGG compound/reaction ids (``"C00024"`` = Acetyl-CoA,
  ``"C00083"`` = Malonyl-CoA, ``"R00742"`` = the same real acetyl-CoA
  carboxylase reaction this repository has tracked since Pilot 1);
  ``ec_number`` is occasionally a ``;``-separated list, mirroring BRENDA's
  own occasional multi-EC reporting. **No per-record confidence/quality
  score, no model name, and no stable per-record id are present in this
  response** -- confirmed by direct inspection, not merely undocumented;
  see ``GotEnzymesRecord``'s own docstring for how this module compensates
  (a database-wide, not per-record, model name; a computed, deterministic
  ``source_id``).
* ``GET {base_url}/gotenzymes/genes/{geneId}`` -- gene cross-references
  (``fetch()``). Confirmed live (``YNR016C``): a JSON object
  ``{"info": {"kegg": "YNR016C"}, "crossReferences": {"NCBI Protein": [...],
  "UniProtKB": [{"id": "Q00955", "url": "..."}]}}`` -- the real, structured
  UniProt accession this module's protein-identity resolution anchors on
  (confirmed: ``Q00955`` is this repository's own already-curated real
  ACC1 protein's ``uniprot_id``). Returns an HTTP error for an unrecognized
  gene id (never independently reconfirmed live against a deliberately
  invalid id this increment -- unlike SGD's own 404 confirmation -- so this
  module treats any non-2xx response the shared HTTP layer raises as an
  ordinary ``ConnectorError``, never assumes a specific status code).

**Units, corrected and re-confirmed in Agent 1.x Increment C.12.**
``kcat_values`` is confirmed, from the original GotEnzymes publication
(Kerkhoven lab, NAR 2023), to be reported in ``1/s`` (turnover number).
GotEnzymes2's own live API exposes no unit metadata for any field, and its
2026 publication is partially paywalled -- but GotEnzymes2's own abstract
names its real underlying model for catalytic parameters
(``ProtT5&MolGen&ExtraTrees``, benchmarked against, among others, UniKP),
and **UniKP's own publication (Nature Communications, 2023) explicitly
reports Km in ``mM`` and kcat/Km in ``mM⁻¹s⁻¹``/``s⁻¹·mM⁻¹``**
(Table 1: e.g. "0.36 mM", "327.2 s⁻¹⋅mM⁻¹") -- confirmed via that
publication, not merely assumed by analogy to BRENDA. This module
accordingly uses ``mM`` for Km (unchanged from C.11) and the unambiguous
``"mM^-1 s^-1"`` spelling for kcat/Km -- **corrected this increment from
C.11's own ``"mM/s"``**, which borrowed BRENDA's own official (if
unconventional) documented idiom for that field
(https://www.brenda-enzymes.org/datafields.php: "The unit of this value is
mM/s", confirmed live, meaning "per mM per second") without independent
justification for GotEnzymes2 specifically, whose real underlying model
does not use that spelling. See
``app.normalization.kinetic_units``'s own module docstring for how both
spellings are still recognized, correctly, for whichever source actually
uses each one.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient.get()``.
* parsing (``parse_enzymes_response()``/``parse_gene_cross_references()``):
  raw JSON -> a source-native structure. Pure, no I/O.
* normalization (``normalize()``): one raw enzyme row -> zero to three
  ``GotEnzymesPrediction`` records (one per non-``null`` parameter type)
  -- still GotEnzymes-only; no cross-source entity resolution, no
  ``KineticMeasurement`` row written here.
* persistence: not implemented here (this increment's own
  ``app.normalization``/``app.pathway_curation`` layers, mirroring
  BRENDA/SABIO-RK).

A scientific-integrity note, identical in spirit to BRENDA's/SGD's own:
every ``GotEnzymesPrediction`` is an AI-model output, never a curated
experimental measurement, regardless of how confident the underlying model
might be -- ``source_category_label`` is always ``"ai_predicted"`` for
exactly this reason, and this connector never assigns an
``app.models.enums.EvidenceType`` or otherwise implies experimental
provenance.
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

# Confirmed live this increment: the original GotEnzymes publication (NAR 2023) states
# turnover numbers are reported in s^-1. GotEnzymes2's own Km/kcat-over-Km units are not
# stated anywhere source-confirmed -- see module docstring.
_KCAT_UNIT = "1/s"
_KM_UNIT = "mM"
_KCAT_KM_UNIT = "mM^-1 s^-1"

# The single, database-wide model name GotEnzymes2's own publication names for its
# catalytic-parameter predictions (ProtT5&MolGen&ExtraTrees) -- a real, confirmed fact
# about the database as a whole, not a per-record value the API itself reports.
GOTENZYMES2_CATALYTIC_MODEL = "ProtT5&MolGen&ExtraTrees"


@dataclass(frozen=True, slots=True)
class GotEnzymesRecord:
    """One raw GotEnzymes2 ``/gotenzymes/enzymes`` row -- source-native, nothing discarded.

    Any of ``kcat_value``/``km_value``/``kcat_km_value`` may be ``None`` --
    a real reactant/gene/EC combination the underlying model produced no
    usable prediction for (confirmed live), never coerced to zero or
    dropped from the response. ``ec_number`` is preserved exactly as
    reported, including a ``;``-separated multi-EC string when GotEnzymes2
    reports one -- this module never picks one arbitrarily.
    """

    gene: str
    organism: str
    domain: str | None
    reaction_id: str | None
    ec_number: str | None
    compound: str | None
    kcat_value: float | None
    km_value: float | None
    kcat_km_value: float | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GotEnzymesPrediction:
    """One GotEnzymes2 record for a single kinetic parameter type -- never merged with
    another prediction, even one for the same gene/EC/compound, mirroring BRENDA's own
    "never average, never pick the best" policy (``.cursor/rules/01-scientific-
    integrity.mdc``).
    """

    parameter_type: str  # "kcat" | "Km" | "kcat/Km" -- BRENDA's own controlled labels,
    # reused verbatim so app.normalization.kinetic_measurement.map_parameter_type already
    # recognizes them with no new mapping entries.
    parameter_value: float
    unit: str
    gene: str
    organism: str
    ec_number: str | None
    reaction_id: str | None
    compound: str | None
    model: str
    source_category_label: str = "ai_predicted"


@dataclass(frozen=True, slots=True)
class GotEnzymesGeneCrossReferences:
    """Source-native parsed ``/gotenzymes/genes/{geneId}`` response -- nothing discarded.

    ``uniprot_id`` is ``None`` when GotEnzymes2 reports no ``UniProtKB`` cross-reference
    for this gene, or reports more than one (never guessed which one is "the" protein --
    see ``uniprot_ids`` for the full, unfiltered list).
    """

    gene: str
    uniprot_ids: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def uniprot_id(self) -> str | None:
        return self.uniprot_ids[0] if len(self.uniprot_ids) == 1 else None


def parse_enzymes_response(data: Any) -> tuple[list[GotEnzymesRecord], int]:
    """Parse a ``/gotenzymes/enzymes`` response. Pure: no HTTP or DB access.

    Returns ``([], 0)`` for a legitimate empty result (a real, confirmed shape: a filter
    combination with no matching predictions still returns HTTP 200 with an empty
    ``enzymes`` array) -- never an error.
    """
    if not isinstance(data, dict) or "enzymes" not in data:
        raise ConnectorParseError(
            f"malformed GotEnzymes2 enzymes response: expected an object with an "
            f"'enzymes' key, got {data!r}"
        )
    rows = data["enzymes"]
    if not isinstance(rows, list):
        raise ConnectorParseError(
            f"malformed GotEnzymes2 enzymes response: 'enzymes' must be a list, "
            f"got {type(rows).__name__}"
        )
    total = data.get("totalCount")
    try:
        total_count = int(total)
    except (TypeError, ValueError) as exc:
        raise ConnectorParseError(
            f"malformed GotEnzymes2 enzymes response: 'totalCount' must be an integer-like "
            f"string, got {total!r}"
        ) from exc

    records: list[GotEnzymesRecord] = []
    for row in rows:
        if not isinstance(row, dict) or "gene" not in row or "organism" not in row:
            raise ConnectorParseError(f"malformed GotEnzymes2 enzyme row: {row!r}")
        records.append(
            GotEnzymesRecord(
                gene=str(row["gene"]),
                organism=str(row["organism"]),
                domain=row.get("domain"),
                reaction_id=row.get("reaction_id"),
                ec_number=row.get("ec_number"),
                compound=row.get("compound"),
                kcat_value=row.get("kcat_values"),
                km_value=row.get("km_values"),
                kcat_km_value=row.get("kcat_km_values"),
                raw=row,
            )
        )
    return records, total_count


def parse_gene_cross_references(gene_id: str, data: Any) -> GotEnzymesGeneCrossReferences:
    """Parse a ``/gotenzymes/genes/{geneId}`` response. Pure: no HTTP or DB access."""
    if not isinstance(data, dict):
        raise ConnectorParseError(
            f"malformed GotEnzymes2 gene response: expected an object, got {data!r}"
        )
    cross_refs = data.get("crossReferences")
    uniprot_entries: list[Any] = []
    if isinstance(cross_refs, dict):
        uniprot_entries = cross_refs.get("UniProtKB") or []
    uniprot_ids = tuple(
        str(entry["id"])
        for entry in uniprot_entries
        if isinstance(entry, dict) and "id" in entry
    )
    return GotEnzymesGeneCrossReferences(gene=gene_id, uniprot_ids=uniprot_ids, raw=data)


def normalize_enzymes_record(record: GotEnzymesRecord) -> tuple[GotEnzymesPrediction, ...]:
    """Split one raw row into zero to three typed predictions, one per non-``None``
    parameter value -- never invents a value for a ``None`` field."""
    predictions: list[GotEnzymesPrediction] = []
    for parameter_type, value, unit in (
        ("kcat", record.kcat_value, _KCAT_UNIT),
        ("Km", record.km_value, _KM_UNIT),
        ("kcat/Km", record.kcat_km_value, _KCAT_KM_UNIT),
    ):
        if value is None:
            continue
        predictions.append(
            GotEnzymesPrediction(
                parameter_type=parameter_type,
                parameter_value=value,
                unit=unit,
                gene=record.gene,
                organism=record.organism,
                ec_number=record.ec_number,
                reaction_id=record.reaction_id,
                compound=record.compound,
                model=GOTENZYMES2_CATALYTIC_MODEL,
            )
        )
    return tuple(predictions)


def gotenzymes_prediction_source_id(prediction: GotEnzymesPrediction) -> str:
    """Deterministic identity for a GotEnzymes2 prediction, which has no native record id.

    SHA-256 of a canonical, sorted-keys JSON encoding of the record's own already-specific
    fields -- never Python's built-in ``hash()`` -- mirrors
    ``app.normalization.kinetic_measurement._brenda_source_id``'s identical technique.
    """
    fields = {
        "gene": prediction.gene,
        "organism": prediction.organism,
        "ec_number": prediction.ec_number,
        "reaction_id": prediction.reaction_id,
        "compound": prediction.compound,
        "parameter_type": prediction.parameter_type,
        "parameter_value": prediction.parameter_value,
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_DEFAULT_PAGE_SIZE = 500


class GotEnzymesConnector:
    """Retrieval and parsing for GotEnzymes2. No curation policy, no persistence, no
    authentication (GotEnzymes2's public API requires none)."""

    source: SourceType = SourceType.GOTENZYMES

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> GotEnzymesConnector:
        """Build a connector using the existing ``gotenzymes_base_url`` setting.

        No default base URL is invented if the setting is unset -- consistent with
        ``app.connectors.sgd.SgdConnector.from_settings``.
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.gotenzymes_base_url:
            raise ValueError("Settings.gotenzymes_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.gotenzymes_base_url)

    def search(
        self,
        *,
        gene: str | None = None,
        ec_number: str | None = None,
        compound: str | None = None,
        organism: str | None = None,
        reaction_id: str | None = None,
        domain: str | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> list[GotEnzymesRecord]:
        """Search GotEnzymes2, following pagination until every matching row is retrieved.

        At least one filter must be supplied -- an entirely unfiltered search would
        attempt to page through the whole database (tens of millions of rows), which no
        caller of this repository should ever need or intend.
        """
        if not any((gene, ec_number, compound, organism, reaction_id, domain)):
            raise ValueError(
                "at least one of gene, ec_number, compound, organism, reaction_id, "
                "domain must be given"
            )
        params: dict[str, str] = {}
        if gene:
            params["filters[gene]"] = gene
        if ec_number:
            params["filters[ec_number]"] = ec_number
        if compound:
            params["filters[compound]"] = compound
        if organism:
            params["filters[organism]"] = organism
        if reaction_id:
            params["filters[reaction_id]"] = reaction_id
        if domain:
            params["filters[domain]"] = domain
        params["pagination[pageSize]"] = str(page_size)

        all_records: list[GotEnzymesRecord] = []
        page = 1
        while True:
            response = self._http.get(
                f"{self._base_url}/gotenzymes/enzymes",
                params={**params, "pagination[page]": str(page)},
            )
            data = _parse_json(response.text, context="enzymes")
            records, total_count = parse_enzymes_response(data)
            all_records.extend(records)
            if not records or len(all_records) >= total_count:
                break
            page += 1
        return all_records

    def fetch(self, gene_id: str, *, organism: str) -> GotEnzymesGeneCrossReferences:
        """Retrieve one gene's own cross-references (notably its UniProt accession)."""
        identifier = gene_id.strip()
        if not identifier:
            raise ValueError("gene_id must not be empty")
        response = self._http.get(
            f"{self._base_url}/gotenzymes/genes/{identifier}", params={"organism": organism}
        )
        data = _parse_json(response.text, context="gene")
        return parse_gene_cross_references(identifier, data)

    def normalize(self, raw: GotEnzymesRecord) -> tuple[GotEnzymesPrediction, ...]:
        """Map one parsed GotEnzymes2 row onto zero to three typed predictions."""
        return normalize_enzymes_record(raw)


def _parse_json(text: str, *, context: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(
            f"malformed GotEnzymes2 {context} response: not valid JSON: {exc}"
        ) from exc


__all__ = [
    "GOTENZYMES2_CATALYTIC_MODEL",
    "GotEnzymesConnector",
    "GotEnzymesGeneCrossReferences",
    "GotEnzymesPrediction",
    "GotEnzymesRecord",
    "gotenzymes_prediction_source_id",
    "normalize_enzymes_record",
    "parse_enzymes_response",
    "parse_gene_cross_references",
]
