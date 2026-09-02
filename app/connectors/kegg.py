"""KEGG connector: retrieval and parsing only, no persistence or curation policy.

Uses KEGG's REST API (``https://rest.kegg.jp`` in production, via the
configured ``kegg_base_url`` setting -- see ``KeggConnector.from_settings``):

* ``GET {base_url}/find/{database}/{query}`` -- search (``search()``),
* ``GET {base_url}/get/{external_id}`` -- fetch a single entry (``fetch()``).

Both endpoints are unauthenticated and require no credentials.

All HTTP retrieval goes through ``app.connectors.http.ConnectorHttpClient``,
which owns every retry, backoff, timeout, rate-limit, and cache concern
(``.cursor/rules/02-architecture.mdc``: "Connectors must not contain curation
policy" and should support "search()/fetch()/normalize() ... caching, rate
limiting, retry/backoff" via the shared foundation). Nothing in this module
retries a request, waits out a rate limit, or reads/writes a database.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient``.
* parsing (``parse_find_response()``/``parse_flat_file()``): raw KEGG text ->
  source-native structures (``KeggSearchHit``, ``KeggFlatFileRecord``). Pure
  functions, no I/O, nothing discarded.
* normalization (``normalize()``, ``normalize_compound()``,
  ``normalize_reaction()``): source-native structures -> KEGG-scoped typed
  records (``KeggCompoundRecord``, ``KeggReactionRecord``). Still KEGG-only;
  no merging with ChEBI/UniProt/SGD/etc., which is a later phase
  (``app/normalization/``).
* persistence: not implemented here (a later increment).

KEGG's flat-file ``get`` format is documented at
https://www.kegg.jp/kegg/rest/keggapi.html but that document (and this
module) reflects the long-stable, well-established shape of the public KEGG
REST API. No live request was made while writing this module (out of scope
for this increment), so this is not a verified-against-the-live-service
snapshot -- see the module docstring note in ``tests/connectors/test_kegg.py``
for what that means for the test fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from app.config.settings import Settings, get_settings
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

_ENTRY_FIELD_WIDTH = 12  # KEGG flat-file keyword column width, e.g. "ENTRY       ".


@dataclass(frozen=True, slots=True)
class KeggSearchHit:
    """One line of a KEGG ``find`` result: a stable identifier and description."""

    entry_id: str
    description: str


@dataclass(frozen=True, slots=True)
class KeggFlatFileRecord:
    """A KEGG ``get`` response, parsed into its fields without interpretation.

    ``fields`` maps each flat-file keyword (``NAME``, ``FORMULA``, ``PATHWAY``,
    ...) to every row recorded under it, in order -- the first row and any
    continuation rows alike. Nothing KEGG returned is discarded here, even
    fields this increment has no specific use for.
    """

    entry_id: str
    entry_type: str | None
    fields: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class KeggCompoundRecord:
    """KEGG-scoped normalized view of a ``Compound`` entry.

    ``raw`` retains the complete parsed record, so nothing is lost by
    normalizing -- only additional, cleaned, typed fields are added.
    """

    entry_id: str
    names: tuple[str, ...]
    formula: str | None
    exact_mass: str | None
    mol_weight: str | None
    pathways: tuple[str, ...]
    raw: KeggFlatFileRecord


@dataclass(frozen=True, slots=True)
class KeggReactionRecord:
    """KEGG-scoped normalized view of a ``Reaction`` entry.

    ``enzymes`` preserves each ``ENZYME`` row verbatim: a single row may list
    more than one space-separated EC number, and this is not split further,
    since guessing at a split would risk misreading the source.
    """

    entry_id: str
    names: tuple[str, ...]
    definition: str | None
    equation: str | None
    enzymes: tuple[str, ...]
    pathways: tuple[str, ...]
    raw: KeggFlatFileRecord


def split_kegg_identifier(identifier: str) -> tuple[str | None, str]:
    """Split a KEGG identifier into its database prefix and entry id.

    ``"cpd:C00031"`` -> ``("cpd", "C00031")``; a bare ``"C00031"`` (no
    prefix) -> ``(None, "C00031")``. Whitespace is stripped; an empty
    identifier is rejected rather than silently accepted.
    """
    stripped = identifier.strip()
    if not stripped:
        raise ValueError("identifier must not be empty")
    if ":" in stripped:
        database, _, entry_id = stripped.partition(":")
        return database, entry_id
    return None, stripped


def parse_find_response(text: str) -> list[KeggSearchHit]:
    """Parse a KEGG ``find`` response: tab-separated ``id\\tdescription`` lines.

    An empty (or all-blank) response is a legitimate empty result and
    returns ``[]`` -- it is not an error.
    """
    hits: list[KeggSearchHit] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" not in line:
            raise ConnectorParseError(f"malformed KEGG find response line: {line!r}")
        entry_id, _, description = line.partition("\t")
        hits.append(KeggSearchHit(entry_id=entry_id.strip(), description=description.strip()))
    return hits


def parse_flat_file(text: str) -> KeggFlatFileRecord:
    """Parse a KEGG ``get`` flat-file response into its fields.

    KEGG's flat-file format: each field starts with a keyword in the first
    ``_ENTRY_FIELD_WIDTH`` columns followed by its first line of content;
    further lines belonging to the same field are indented (no keyword) and
    are continuation rows of the field that precedes them. The record ends
    at a line consisting solely of ``///``, which is not itself a field.
    """
    content_lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == "///":
            break
        content_lines.append(line)

    non_blank = [line for line in content_lines if line.strip()]
    if not non_blank or not non_blank[0].startswith("ENTRY"):
        raise ConnectorParseError("malformed KEGG flat-file record: missing ENTRY line")

    fields: dict[str, list[str]] = {}
    current_field: str | None = None
    for line in non_blank:
        if line[0] != " ":
            keyword = line[:_ENTRY_FIELD_WIDTH].strip()
            content = line[_ENTRY_FIELD_WIDTH:].strip()
            current_field = keyword
            fields.setdefault(keyword, []).append(content)
        else:
            if current_field is None:
                raise ConnectorParseError(
                    f"malformed KEGG flat-file record: continuation line before any field: "
                    f"{line!r}"
                )
            fields[current_field].append(line.strip())

    entry_tokens = fields["ENTRY"][0].split()
    if not entry_tokens:
        raise ConnectorParseError("malformed KEGG flat-file record: empty ENTRY line")
    entry_id = entry_tokens[0]
    entry_type = entry_tokens[-1] if len(entry_tokens) > 1 else None

    return KeggFlatFileRecord(
        entry_id=entry_id,
        entry_type=entry_type,
        fields={keyword: tuple(rows) for keyword, rows in fields.items()},
    )


def _join_names(name_rows: tuple[str, ...]) -> tuple[str, ...]:
    """KEGG ``NAME`` rows are ``;``-separated synonyms, possibly spanning rows."""
    joined = " ".join(name_rows)
    return tuple(part.strip() for part in joined.split(";") if part.strip())


def _first_row(record: KeggFlatFileRecord, keyword: str) -> str | None:
    rows = record.fields.get(keyword)
    return rows[0] if rows else None


def normalize_compound(record: KeggFlatFileRecord) -> KeggCompoundRecord:
    """Map a generic parsed record onto KEGG-scoped compound fields."""
    return KeggCompoundRecord(
        entry_id=record.entry_id,
        names=_join_names(record.fields.get("NAME", ())),
        formula=_first_row(record, "FORMULA"),
        exact_mass=_first_row(record, "EXACT_MASS"),
        mol_weight=_first_row(record, "MOL_WEIGHT"),
        pathways=record.fields.get("PATHWAY", ()),
        raw=record,
    )


def normalize_reaction(record: KeggFlatFileRecord) -> KeggReactionRecord:
    """Map a generic parsed record onto KEGG-scoped reaction fields."""
    definition_rows = record.fields.get("DEFINITION", ())
    equation_rows = record.fields.get("EQUATION", ())
    return KeggReactionRecord(
        entry_id=record.entry_id,
        names=_join_names(record.fields.get("NAME", ())),
        definition=" ".join(definition_rows) if definition_rows else None,
        equation=" ".join(equation_rows) if equation_rows else None,
        enzymes=record.fields.get("ENZYME", ()),
        pathways=record.fields.get("PATHWAY", ()),
        raw=record,
    )


class KeggConnector:
    """Retrieval and parsing for KEGG. No curation policy, no persistence."""

    source: SourceType = SourceType.KEGG

    def __init__(self, http_client: ConnectorHttpClient, *, base_url: str) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(
        cls, http_client: ConnectorHttpClient, settings: Settings | None = None
    ) -> KeggConnector:
        """Build a connector using the existing ``kegg_base_url`` setting.

        No default base URL is invented if the setting is unset -- consistent
        with the rest of ``app.config.settings``, unknown configuration stays
        unknown rather than silently falling back to a hard-coded value.
        """
        resolved_settings = settings or get_settings()
        if not resolved_settings.kegg_base_url:
            raise ValueError("Settings.kegg_base_url is not configured")
        return cls(http_client, base_url=resolved_settings.kegg_base_url)

    def search(self, query: str, *, database: str) -> list[KeggSearchHit]:
        """Search a KEGG database (``"compound"``, ``"reaction"``, ``"genes"``, ...).

        Returns ``[]`` for a legitimate empty result. A retrieval failure
        (timeout, rate limit exhausted, HTTP error) raises the corresponding
        ``app.connectors.exceptions.ConnectorError`` instead -- the two are
        never confused.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if not database.strip():
            raise ValueError("database must not be empty")

        url = f"{self._base_url}/find/{quote(database, safe='')}/{quote(query, safe='')}"
        response = self._http.get(url)
        return parse_find_response(response.text)

    def fetch(self, external_id: str) -> KeggFlatFileRecord | None:
        """Retrieve a single KEGG entry by its stable identifier.

        Returns ``None`` when KEGG reports the identifier does not exist
        (HTTP 404) -- a legitimate "no such record" outcome, not invented as
        an empty/default record and not treated as a failure. Any other
        retrieval failure still raises.
        """
        database, entry_id = split_kegg_identifier(external_id)
        identifier = f"{database}:{entry_id}" if database else entry_id

        url = f"{self._base_url}/get/{quote(identifier, safe=':')}"
        try:
            response = self._http.get(url)
        except ConnectorHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        return parse_flat_file(response.text)

    def normalize(
        self, raw: KeggFlatFileRecord
    ) -> KeggCompoundRecord | KeggReactionRecord | KeggFlatFileRecord:
        """Map a parsed KEGG record onto a KEGG-scoped normalized shape.

        Only ``Compound`` and ``Reaction`` entries get a specific typed
        result; any other entry type (genes, pathways, ...) is returned
        unchanged rather than dropped or guessed at, since no normalized
        shape for it has been designed yet.
        """
        entry_type = (raw.entry_type or "").strip().lower()
        if entry_type == "compound":
            return normalize_compound(raw)
        if entry_type == "reaction":
            return normalize_reaction(raw)
        return raw


__all__ = [
    "KeggCompoundRecord",
    "KeggConnector",
    "KeggFlatFileRecord",
    "KeggReactionRecord",
    "KeggSearchHit",
    "normalize_compound",
    "normalize_reaction",
    "parse_find_response",
    "parse_flat_file",
    "split_kegg_identifier",
]
