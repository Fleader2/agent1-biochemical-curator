"""UniProtKB connector: retrieval and parsing only, no persistence or curation policy.

Uses UniProt's REST API (``https://rest.uniprot.org`` in production, via the
configured ``uniprot_base_url`` setting -- see ``UniProtConnector.from_settings``,
which mirrors ``KeggConnector``/``SgdConnector``'s own "no built-in default"
policy: this project's base URLs are environment-dependent configuration, not
constants, and no live request against the real UniProt service was made
while writing this module (out of scope for this increment) -- this is not a
verified-against-the-live-service snapshot, the same caveat
``app/connectors/kegg.py``'s own module docstring already carries for KEGG.
The response shapes below reflect UniProtKB's long-documented, stable JSON
schema, not a live-captured example.

* ``GET {base_url}/uniprotkb/search`` -- search (``search()``), returning
  ``{"results": [...]}`` where each result is a (partial) UniProtKB entry.
* ``GET {base_url}/uniprotkb/{accession}`` -- fetch a single entry
  (``fetch()``), returning one full UniProtKB entry.

Both endpoints are unauthenticated and require no credentials, like SGD's
and KEGG's own public REST APIs in this codebase.

Four separate transformations, per ``app/connectors/base.py`` (identical
structure to every other connector in this repository):

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient`` -- no retry/backoff/rate-limit/cache logic of its
  own (all of that lives in the shared HTTP layer, reused unchanged).
* parsing (``parse_search_response()``/``parse_entry_response()``): raw JSON
  -> source-native structures (``UniProtSearchHit``, ``UniProtEntryRecord``).
  Pure functions, no I/O, nothing discarded from a fetched entry (see
  ``UniProtEntryRecord.raw``).
* normalization (``normalize()``/``normalize_entry()``): source-native
  structures -> a UniProt-scoped, deliberately narrowed typed record
  (``UniProtProteinRecord``) -- still UniProt-only, no merging with SGD/KEGG/
  any other source, which remains ``app/normalization/``'s job.
* persistence: not implemented here (a later increment).

**Reviewed status is preserved, never used as identity or confidence.**
UniProtKB's ``entryType`` field distinguishes "UniProtKB reviewed
(Swiss-Prot)" entries from "UniProtKB unreviewed (TrEMBL)" ones --
``UniProtProteinRecord.reviewed`` carries this through as plain metadata.
This connector never excludes unreviewed entries and never treats reviewed
status as identity or as a confidence signal (Increment 15 instructions,
Step 3) -- that judgment, if it is ever made, belongs to a later evidence/
scoring layer, not to retrieval.

**The primary accession is preserved literally, at every layer.** No case
folding, no isoform-suffix stripping (``P12345`` and ``P12345-2`` are
different strings, always), no substitution of a secondary accession for
the primary one. ``app.normalization.protein``'s own literal-isoform policy
remains the sole authority on what two accessions being "the same" would
even mean -- this connector never makes that judgment itself.

**Cross-references are narrowed, not merged into Agent 1 relationships.**
UniProtKB entries carry many database cross-references (``SGD``,
``GeneID``, ``KEGG``, ``EMBL``, ``PDB``, ...); ``UniProtEntryRecord.raw``
retains everything, but ``UniProtProteinRecord.cross_references`` keeps
only ``SGD``/``GeneID``/``KEGG`` -- and even those are inert connector-
record metadata only. Nothing here uses a cross-reference to populate a
Gene relationship, call Gene normalization, or assert that a UniProt gene
name names a specific Agent 1 ``Gene`` row (Increment 15 instructions,
Step 9 -- mandatory).

**EC numbers are inert metadata, never identity.** Extracted only from the
entry's recommended name (``proteinDescription.recommendedName.ecNumbers``)
-- never used to search, match, or resolve Protein identity, and never used
to infer a Reaction or ReactionEnzyme relationship (Step 10, Step 26).

**UniProt ID Mapping is intentionally not implemented in this increment.**
UniProt's separate ``/idmapping`` API supports mapping accessions across
UniProt/SGD/GeneID/KEGG/many other databases, which could later help
corroborate a Gene<->Protein relationship discovered independently through
several sources -- but adding it now would broaden this increment past
Protein *mention* enrichment (its stated scope). No ``/idmapping`` call
exists anywhere in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.config.settings import Settings, get_settings
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

#: Cross-reference databases this connector surfaces on the normalized
#: record -- deliberately narrow (Increment 15, Step 11). Every other
#: cross-reference UniProt returns is still reachable on
#: ``UniProtEntryRecord.raw``, just not promoted to
#: ``UniProtProteinRecord.cross_references``.
_SAFE_CROSS_REFERENCE_DATABASES = frozenset({"SGD", "GeneID", "KEGG"})


@dataclass(frozen=True, slots=True)
class UniProtCrossReference:
    """One entry from a UniProtKB record's ``uniProtKBCrossReferences`` array."""

    database: str
    identifier: str


@dataclass(frozen=True, slots=True)
class UniProtSearchHit:
    """One hit from a UniProtKB search -- deliberately minimal.

    Never enough on its own to construct a ``ProteinIdentity``: a caller
    must ``fetch()`` the full entry first (Increment 15 instructions, Step
    24 -- the same "verify before normalizing" discipline
    ``app.connectors.pubmed`` already applies, where search returns bare
    PMIDs and only ``fetch()`` retrieves real metadata).
    """

    primary_accession: str
    entry_name: str | None
    reviewed: bool | None


@dataclass(frozen=True, slots=True)
class UniProtEntryRecord:
    """Source-native parsed record for one UniProtKB entry -- nothing discarded.

    ``raw`` retains the complete original JSON object, so a field this
    increment has no specific typed representation for (subcellular
    location comments, sequence features, citations, ...) is still
    reachable. ``recommended_name``/``submitted_names`` and
    ``secondary_accessions`` are kept separate from
    ``UniProtProteinRecord``'s own narrower shape so that the *source's*
    full structure is preserved at this layer, even though normalization
    picks only one protein name (see ``normalize_entry``).
    """

    primary_accession: str
    entry_name: str | None
    entry_type: str | None
    secondary_accessions: tuple[str, ...]
    recommended_name: str | None
    submitted_names: tuple[str, ...]
    gene_names: tuple[str, ...]
    organism_name: str | None
    organism_taxonomy_id: int | None
    ec_numbers: tuple[str, ...]
    sequence_length: int | None
    cross_references: tuple[UniProtCrossReference, ...]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UniProtProteinRecord:
    """UniProt-scoped normalized view of one entry, shaped for Protein enrichment.

    Deliberately small (Increment 15 instructions, Step 2: "Do not include
    every UniProtKB field. Keep the normalized record small and
    auditable."). ``raw`` retains the complete parsed ``UniProtEntryRecord``,
    so nothing is lost by normalizing -- only additional, cleaned, typed
    fields are added. This is not a ``Protein`` row and nothing here is
    persisted or matched against another source's identifiers.
    """

    primary_accession: str
    entry_name: str | None
    reviewed: bool | None
    protein_name: str | None
    gene_names: tuple[str, ...]
    organism_name: str | None
    organism_taxonomy_id: int | None
    ec_numbers: tuple[str, ...]
    sequence_length: int | None
    secondary_accessions: tuple[str, ...]
    cross_references: tuple[UniProtCrossReference, ...]
    raw: UniProtEntryRecord


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _parse_reviewed(entry_type: str | None) -> bool | None:
    """Classify UniProtKB's ``entryType`` string into reviewed/unreviewed.

    ``"unreviewed"`` is checked before ``"reviewed"`` -- the former
    contains the latter as a substring, so a naive single check would
    misclassify every unreviewed (TrEMBL) entry as reviewed.
    """
    if entry_type is None:
        return None
    lowered = entry_type.lower()
    if "unreviewed" in lowered:
        return False
    if "reviewed" in lowered:
        return True
    return None


def _full_name_value(full_name: Any) -> str | None:
    if isinstance(full_name, dict):
        return _optional_str(full_name.get("value"))
    return None


def _parse_protein_names(data: dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    """Extract ``(recommended_name, submitted_names)`` from ``proteinDescription``.

    UniProtKB gives a reviewed (Swiss-Prot) entry exactly one
    ``recommendedName``; an unreviewed (TrEMBL) entry instead carries one or
    more ``submissionNames`` (author-submitted names, not yet curated into
    one recommended name) -- both are preserved here, conservatively, per
    Increment 15 instructions, Step 8.
    """
    description = data.get("proteinDescription")
    if not isinstance(description, dict):
        return None, ()
    recommended_name = _full_name_value((description.get("recommendedName") or {}).get("fullName"))
    submission_entries = description.get("submissionNames")
    submitted_names: tuple[str, ...] = ()
    if isinstance(submission_entries, list):
        submitted_names = tuple(
            name
            for name in (
                _full_name_value(entry.get("fullName"))
                for entry in submission_entries
                if isinstance(entry, dict)
            )
            if name is not None
        )
    return recommended_name, submitted_names


def _parse_ec_numbers(data: dict[str, Any]) -> tuple[str, ...]:
    """EC numbers from the recommended name only -- see module docstring's EC policy."""
    description = data.get("proteinDescription")
    if not isinstance(description, dict):
        return ()
    recommended_name = description.get("recommendedName")
    if not isinstance(recommended_name, dict):
        return ()
    ec_entries = recommended_name.get("ecNumbers")
    if not isinstance(ec_entries, list):
        return ()
    return tuple(
        value
        for value in (
            _optional_str(entry.get("value")) for entry in ec_entries if isinstance(entry, dict)
        )
        if value is not None
    )


def _parse_gene_names(data: dict[str, Any]) -> tuple[str, ...]:
    genes = data.get("genes")
    if not isinstance(genes, list):
        return ()
    names = []
    for gene in genes:
        if not isinstance(gene, dict):
            continue
        name = _full_name_value(gene.get("geneName"))
        if name is not None:
            names.append(name)
    return tuple(names)


def _parse_organism(data: dict[str, Any]) -> tuple[str | None, int | None]:
    organism = data.get("organism")
    if not isinstance(organism, dict):
        return None, None
    return _optional_str(organism.get("scientificName")), _optional_int(organism.get("taxonId"))


def _parse_cross_references(data: dict[str, Any]) -> tuple[UniProtCrossReference, ...]:
    raw_refs = data.get("uniProtKBCrossReferences")
    if not isinstance(raw_refs, list):
        return ()
    refs = []
    for ref in raw_refs:
        if not isinstance(ref, dict):
            continue
        database = _optional_str(ref.get("database"))
        identifier = _optional_str(ref.get("id"))
        if database is not None and identifier is not None:
            refs.append(UniProtCrossReference(database=database, identifier=identifier))
    return tuple(refs)


def _parse_secondary_accessions(data: dict[str, Any]) -> tuple[str, ...]:
    values = data.get("secondaryAccessions")
    if not isinstance(values, list):
        return ()
    return tuple(v.strip() for v in values if isinstance(v, str) and v.strip())


def parse_entry_response(data: Any) -> UniProtEntryRecord:
    """Parse a single UniProtKB entry JSON object. Pure: no HTTP or DB access."""
    if not isinstance(data, dict):
        raise ConnectorParseError(
            f"malformed UniProt entry response: expected a JSON object, got {type(data).__name__}"
        )

    primary_accession = _optional_str(data.get("primaryAccession"))
    if primary_accession is None:
        raise ConnectorParseError(
            "malformed UniProt entry response: missing or empty 'primaryAccession'"
        )

    recommended_name, submitted_names = _parse_protein_names(data)
    organism_name, organism_taxonomy_id = _parse_organism(data)

    return UniProtEntryRecord(
        primary_accession=primary_accession,
        entry_name=_optional_str(data.get("uniProtkbId")),
        entry_type=_optional_str(data.get("entryType")),
        secondary_accessions=_parse_secondary_accessions(data),
        recommended_name=recommended_name,
        submitted_names=submitted_names,
        gene_names=_parse_gene_names(data),
        organism_name=organism_name,
        organism_taxonomy_id=organism_taxonomy_id,
        ec_numbers=_parse_ec_numbers(data),
        sequence_length=_optional_int((data.get("sequence") or {}).get("length")),
        cross_references=_parse_cross_references(data),
        raw=data,
    )


def parse_search_response(text: str) -> list[UniProtSearchHit]:
    """Parse a UniProtKB ``search`` JSON response: ``{"results": [...]}``.

    Returns ``[]`` for a legitimate empty result set (an absent or empty
    ``results`` array) -- a malformed response (not JSON, not an object, a
    ``results`` entry with no ``primaryAccession``) raises
    ``ConnectorParseError`` instead, never silently treated as zero hits.
    """
    data = _parse_json(text, context="search")
    if not isinstance(data, dict):
        raise ConnectorParseError(
            f"malformed UniProt search response: expected a JSON object, got {type(data).__name__}"
        )
    results = data.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise ConnectorParseError("malformed UniProt search response: 'results' is not a list")

    hits = []
    for entry in results:
        if not isinstance(entry, dict):
            raise ConnectorParseError(
                "malformed UniProt search response: a result entry is not a JSON object"
            )
        primary_accession = _optional_str(entry.get("primaryAccession"))
        if primary_accession is None:
            raise ConnectorParseError(
                "malformed UniProt search response: a result is missing 'primaryAccession'"
            )
        hits.append(
            UniProtSearchHit(
                primary_accession=primary_accession,
                entry_name=_optional_str(entry.get("uniProtkbId")),
                reviewed=_parse_reviewed(_optional_str(entry.get("entryType"))),
            )
        )
    return hits


def normalize_entry(record: UniProtEntryRecord) -> UniProtProteinRecord:
    """Map a parsed UniProtKB entry onto the narrow, normalized shape.

    ``protein_name`` prefers ``recommended_name``; when only
    ``submitted_names`` exist (an unreviewed entry), the first is used --
    never invented from the gene name (Increment 15 instructions, Step 8).
    """
    protein_name = record.recommended_name
    if protein_name is None and record.submitted_names:
        protein_name = record.submitted_names[0]

    return UniProtProteinRecord(
        primary_accession=record.primary_accession,
        entry_name=record.entry_name,
        reviewed=_parse_reviewed(record.entry_type),
        protein_name=protein_name,
        gene_names=record.gene_names,
        organism_name=record.organism_name,
        organism_taxonomy_id=record.organism_taxonomy_id,
        ec_numbers=record.ec_numbers,
        sequence_length=record.sequence_length,
        secondary_accessions=record.secondary_accessions,
        cross_references=tuple(
            ref
            for ref in record.cross_references
            if ref.database in _SAFE_CROSS_REFERENCE_DATABASES
        ),
        raw=record,
    )


def _parse_json(text: str, *, context: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(
            f"malformed UniProt {context} response: not valid JSON: {exc}"
        ) from exc


class UniProtConnector:
    """Retrieval and parsing for UniProtKB. No curation policy, no persistence.

    ``uniprot_base_url`` has no built-in default -- consistent with
    ``SgdConnector``/``KeggConnector`` in this same codebase, for the same
    reason: the base URL is environment-dependent configuration, and no
    live verification of the current endpoint was performed while writing
    this module (see module docstring).
    """

    source: SourceType = SourceType.UNIPROT

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> UniProtConnector:
        """Build a connector using the existing ``uniprot_base_url`` setting.

        No default base URL is invented if the setting is unset --
        consistent with ``SgdConnector.from_settings``/
        ``KeggConnector.from_settings``.
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.uniprot_base_url:
            raise ValueError("Settings.uniprot_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.uniprot_base_url)

    def search(
        self, query: str, *, organism_taxonomy_id: int | None = None, limit: int = 25
    ) -> list[UniProtSearchHit]:
        """Search UniProtKB for matching entries.

        ``organism_taxonomy_id``, when supplied, is combined into the query
        using UniProt's own ``organism_id`` field filter -- never invented,
        never applied as a post-hoc client-side filter. Returns ``[]`` for a
        legitimate empty result. A retrieval failure (timeout, rate limit
        exhausted, HTTP error) raises the corresponding
        ``app.connectors.exceptions.ConnectorError`` instead -- the two are
        never confused. Full entries are not fetched implicitly; callers
        that want them call ``fetch()`` per accession (Step 24).
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if limit < 1:
            raise ValueError("limit must be at least 1")

        effective_query = query.strip()
        if organism_taxonomy_id is not None:
            effective_query = f"({effective_query}) AND organism_id:{organism_taxonomy_id}"

        params = {"query": effective_query, "format": "json", "size": str(limit)}
        response = self._http.get(f"{self._base_url}/uniprotkb/search", params=params)
        return parse_search_response(response.text)

    def fetch(self, accession: str) -> UniProtEntryRecord | None:
        """Retrieve a single UniProtKB entry by its exact primary accession.

        The accession is passed through unmodified -- no case-folding, no
        isoform-suffix stripping (module docstring). Returns ``None`` when
        UniProt reports the accession does not exist (HTTP 404) -- a
        legitimate "no such record" outcome, not invented as an empty/
        default record. Any other retrieval failure still raises.
        """
        identifier = accession.strip()
        if not identifier:
            raise ValueError("accession must not be empty")

        url = f"{self._base_url}/uniprotkb/{quote(identifier, safe='')}"
        try:
            response = self._http.get(url, params={"format": "json"})
        except ConnectorHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = _parse_json(response.text, context="entry")
        return parse_entry_response(data)

    def normalize(self, raw: UniProtEntryRecord) -> UniProtProteinRecord:
        """Map a parsed UniProtKB entry onto a UniProt-scoped normalized shape."""
        return normalize_entry(raw)


__all__ = [
    "UniProtConnector",
    "UniProtCrossReference",
    "UniProtEntryRecord",
    "UniProtProteinRecord",
    "UniProtSearchHit",
    "normalize_entry",
    "parse_entry_response",
    "parse_search_response",
]
