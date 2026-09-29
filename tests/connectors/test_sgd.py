"""Tests for the SGD connector: retrieval, JSON parsing, and normalization.

Includes the SGD connector tests required by ``docs/05_testing.md`` ("SGD
Connector Tests"): ``test_sgd_gene_lookup``, ``test_sgd_systematic_name_parsed``,
``test_sgd_aliases_preserved``, ``test_sgd_external_identifiers_preserved``,
``test_sgd_localization_annotation_labeled_as_database_annotation``.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using the static fixtures under
``tests/fixtures/sgd/``. **Unlike this connector's first draft, these
fixtures' field names, nesting, and response shape are verified against live
requests to https://www.yeastgenome.org made during Phase 3 Increment 4's
verification pass** (see ``app/connectors/sgd.py``'s module docstring for
the endpoints and exact requests used). ``sgdid``, ``format_name``,
``display_name``, ``locus_type``, and ``uniprot_id`` for CDC28 are the real,
confirmed values (SGD ID ``S000000364``); description text is
shortened/paraphrased rather than reproduced verbatim, and numeric/internal
IDs (``aliases[].id``, ``bioentity_id``, GO annotation ``id``) are synthetic
filler where the real value was not part of what this pass needed to
confirm. ``S000099999``/``YNL999W``/``TEST1`` are deliberately out-of-range
synthetic identifiers (``docs/05_testing.md``, "Synthetic Scientific
Fixtures").
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.config.settings import Settings
from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.sgd import (
    SgdConnector,
    SgdExternalLink,
    SgdLocusRecord,
    SgdProteinAbundance,
    SgdReferenceRecord,
    classify_sgd_identifier,
    normalize_locus,
    parse_go_details_response,
    parse_locus_response,
    parse_protein_abundance_details_response,
    parse_reference_response,
    parse_search_response,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sgd"
_BASE_URL = "https://example.invalid/sgd"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _parsed_fixture(name: str) -> SgdLocusRecord:
    return parse_locus_response(json.loads(_fixture(name)))


class _RecordingHandler:
    """An ``httpx.MockTransport`` handler that replays a scripted sequence.

    Each entry in ``responses`` is either an ``httpx.Response`` to return or
    an exception instance to raise, consumed in call order; the last entry
    repeats for any calls beyond the scripted sequence. Every request is
    recorded so tests can inspect the path/query the connector built.
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


# --- SGD Connector Tests (docs/05_testing.md) --------------------------------


def test_sgd_gene_lookup() -> None:
    """``fetch()`` calls SGD's verified locus endpoint and returns a parsed record."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("locus_cdc28.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    record = connector.fetch("CDC28")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/sgd/locus/CDC28"
    assert isinstance(record, SgdLocusRecord)
    assert record.sgd_id == "S000000364"
    assert record.standard_name == "CDC28"


def test_sgd_systematic_name_parsed() -> None:
    """The systematic name (``format_name``) is parsed distinctly from the standard name."""
    record = _parsed_fixture("locus_cdc28.json")

    assert record.systematic_name == "YBR160W"
    assert record.standard_name == "CDC28"
    assert record.systematic_name != record.standard_name


def test_sgd_aliases_preserved() -> None:
    """All alias entries are preserved, including their category, not truncated.

    Live-verified: SGD's ``aliases`` array mixes genuine alternate names
    (category "Alias") with cross-reference-shaped entries ("UniProtKB ID",
    "EC number"). The raw parsed record keeps all of them.
    """
    record = _parsed_fixture("locus_cdc28.json")

    categories = {a.category for a in record.aliases}
    assert categories == {"Alias", "UniProtKB ID", "EC number"}
    alias_names = {a.display_name for a in record.aliases if a.category == "Alias"}
    assert alias_names == {"CDK1", "HSL5"}

    normalized = normalize_locus(record)
    assert normalized.aliases == ("CDK1", "HSL5")


def test_sgd_external_identifiers_preserved() -> None:
    """External identifiers SGD supplies (``uniprot_id``, ``urls``) are preserved."""
    record = _parsed_fixture("locus_cdc28.json")

    assert record.uniprot_id == "P00546"
    assert record.external_links == (
        SgdExternalLink(
            category="LOCUS_LSP_RESOURCES",
            display_name="Entrez Gene",
            link="http://www.ncbi.nlm.nih.gov/gene/852457",
        ),
        SgdExternalLink(
            category="LOCUS_LSP_RESOURCES",
            display_name="E.C.2.7.11.22",
            link="http://www.expasy.org/enzyme/2.7.11.22",
        ),
    )


def test_sgd_localization_annotation_labeled_as_database_annotation() -> None:
    """SGD localization data (from go_details) is labeled as a database annotation.

    Live-verified: localization is not present on the basic locus record at
    all -- it comes only from ``/locus/{id}/go_details``, filtered to
    ``go_aspect == "cellular component"``. An SGD GO annotation is a curated
    database statement, not direct experimental evidence
    (``.cursor/rules/01-scientific-integrity.mdc``), regardless of what its
    underlying evidence code says. This connector never assigns an
    ``EvidenceType`` -- that belongs to a later evidence-extraction phase --
    but it must also never let a database-derived annotation look like an
    undifferentiated fact.
    """
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("go_details_cdc28.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    annotations = connector.fetch_go_details("S000000364")

    assert handler.requests[0].url.path == "/sgd/locus/S000000364/go_details"
    assert annotations is not None
    localization = [a for a in annotations if a.is_localization]
    assert {a.term for a in localization} == {
        "cyclin-dependent protein kinase holoenzyme complex",
        "cytoplasm",
    }
    assert all(a.aspect == "cellular component" for a in localization)
    assert all(a.source_category_label == "database_annotation" for a in annotations)
    # Non-localization GO aspects are still present and distinguishable.
    aspects = {a.aspect for a in annotations}
    assert aspects == {"cellular component", "molecular function", "biological process"}


# --- search() -----------------------------------------------------------------


def test_sgd_search() -> None:
    """``search()`` calls SGD's verified search endpoint and returns structured hits."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_cdc28.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("CDC28")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/sgd/get_search_results"
    params = dict(handler.requests[0].url.params)
    assert params["q"] == "CDC28"
    assert params["category"] == "locus"
    assert len(hits) == 2
    assert hits[0].sgd_id == "S000000364"
    assert hits[0].standard_name == "CDC28"
    assert hits[0].systematic_name == "YBR160W"
    assert hits[0].aliases == ("CDK1", "HSL5")


def test_sgd_search_returns_empty_list_for_no_hits() -> None:
    """A legitimate zero-result search (SGD's real empty-results shape) returns ``[]``."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    assert connector.search("no-such-gene") == []


def test_sgd_search_hit_sgd_id_extracted_from_href() -> None:
    """The SGD ID comes from ``href`` (e.g. "/locus/S000000364"), not a direct field.

    Live-verified: search result entries carry no direct ``sgdid`` key.
    """
    hits = parse_search_response(json.loads(_fixture("search_cdc28.json")))
    assert hits[1].sgd_id == "S000000339"


# --- Missing record vs. failure -------------------------------------------------


def test_sgd_fetch_returns_none_for_unknown_identifier() -> None:
    """An SGD 404 for locus (confirmed live) means "no such record"."""
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    assert connector.fetch("NOSUCHGENE") is None


def test_sgd_fetch_still_raises_for_non_404_failure() -> None:
    """A non-404 failure on fetch() still raises -- only 404 means "no record"."""
    handler = _RecordingHandler(httpx.Response(500, text="internal error"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.fetch("CDC28")


def test_sgd_search_failure_not_treated_as_zero_results() -> None:
    """An exhausted-retry failure raises; it is never silently treated as ``[]``."""
    handler = _RecordingHandler(httpx.Response(503, text="internal error"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.search("CDC28")


def test_sgd_fetch_go_details_returns_none_for_unknown_locus() -> None:
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    assert connector.fetch_go_details("NOSUCHGENE") is None


def test_sgd_fetch_go_details_empty_list_when_no_annotations() -> None:
    """A locus that exists but has no GO annotations returns ``[]``, distinct from ``None``."""
    handler = _RecordingHandler(httpx.Response(200, text="[]"))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    annotations = connector.fetch_go_details("S000000364")

    assert annotations == []


# --- Protein abundance (Agent 1.x Increment "SGD Reference Protein Abundance
# Integration") --------------------------------------------------------------------------------


def test_sgd_locus_with_abundance_parses_protein_overview() -> None:
    """Real, live-confirmed CDC28 median abundance figure (6670 molecules/cell,
    MAD 1539) is parsed from the locus record's own ``protein_overview`` object."""
    record = _parsed_fixture("locus_cdc28_with_abundance.json")

    assert record.protein_abundance == SgdProteinAbundance(
        value=Decimal("6670"), median_absolute_deviation=Decimal("1539")
    )


def test_sgd_locus_without_protein_overview_has_no_abundance() -> None:
    """A real, common, non-error case: SGD reports no abundance data for this locus at
    all -- never fabricated as zero or any other placeholder value."""
    record = _parsed_fixture("locus_cdc28.json")

    assert record.protein_abundance is None


def test_sgd_minimal_locus_has_no_abundance() -> None:
    record = _parsed_fixture("locus_minimal.json")

    assert record.protein_abundance is None


def test_sgd_fetch_returns_abundance_via_full_connector_call() -> None:
    """End-to-end: ``fetch()`` through a mocked HTTP transport surfaces the same
    parsed abundance figure as the pure parser."""
    handler = _RecordingHandler(
        httpx.Response(200, text=_fixture("locus_cdc28_with_abundance.json"))
    )
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    record = connector.fetch("CDC28")

    assert record is not None
    assert record.protein_abundance == SgdProteinAbundance(
        value=Decimal("6670"), median_absolute_deviation=Decimal("1539")
    )


def test_sgd_reference_response_parses_pubmed_id() -> None:
    """Real, live-confirmed: SGD reference S000207593 (Ho et al. 2018) carries
    ``pubmed_id`` 29361465 -- the meta-reference for SGD's own abundance aggregate."""
    reference = parse_reference_response(json.loads(_fixture("reference_ho2018.json")))

    assert reference == SgdReferenceRecord(
        sgd_id="S000207593",
        citation=(
            "Ho B, et al. (2018) Unification of Protein Abundance Datasets Yields a "
            "Quantitative Saccharomyces cerevisiae Proteome. Cell Syst 6(2):192-205.e3"
        ),
        pubmed_id="29361465",
        year=2018,
    )


def test_sgd_fetch_reference_returns_none_for_unknown_reference() -> None:
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    assert connector.fetch_reference("S099999999") is None


def test_sgd_fetch_reference_calls_verified_endpoint() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("reference_ho2018.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    reference = connector.fetch_reference("S000207593")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/sgd/reference/S000207593"
    assert reference is not None
    assert reference.pubmed_id == "29361465"


def test_sgd_reference_response_missing_sgdid_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_reference_response({"pubmed_id": 123})


def test_sgd_reference_response_no_pubmed_id_is_none_never_fabricated() -> None:
    reference = parse_reference_response({"sgdid": "S000000001"})

    assert reference.pubmed_id is None


def test_sgd_protein_abundance_details_extracts_shared_meta_reference() -> None:
    """Every one of CDC28's real per-study rows shares the identical meta-reference
    (Ho et al. 2018, S000207593) -- confirmed live; only the first row is read."""
    reference_sgd_id = parse_protein_abundance_details_response(
        json.loads(_fixture("protein_abundance_details_cdc28.json"))
    )

    assert reference_sgd_id == "S000207593"


def test_sgd_protein_abundance_details_empty_list_is_none() -> None:
    """A locus with no abundance-detail rows at all -- a real, disclosed case, never
    an error."""
    assert parse_protein_abundance_details_response([]) is None


def test_sgd_protein_abundance_details_not_a_list_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_protein_abundance_details_response({"not": "a list"})


def test_sgd_protein_abundance_details_missing_reference_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_protein_abundance_details_response([{"data_value": 100}])


def test_sgd_fetch_protein_abundance_reference_id_returns_none_for_unknown_locus() -> None:
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = SgdConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    assert connector.fetch_protein_abundance_reference_id("NOSUCHGENE") is None


def test_sgd_fetch_protein_abundance_reference_id_returns_none_for_empty_details() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="[]"))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    assert connector.fetch_protein_abundance_reference_id("S000000364") is None


def test_sgd_fetch_protein_abundance_reference_id_calls_verified_endpoint() -> None:
    handler = _RecordingHandler(
        httpx.Response(200, text=_fixture("protein_abundance_details_cdc28.json"))
    )
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    reference_sgd_id = connector.fetch_protein_abundance_reference_id("S000000364")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/sgd/locus/S000000364/protein_abundance_details"
    assert reference_sgd_id == "S000207593"


# --- Malformed content ----------------------------------------------------------


def test_sgd_fetch_malformed_json_raises_parse_error() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="this is not json {{{"))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    with pytest.raises(ConnectorParseError):
        connector.fetch("CDC28")


def test_sgd_locus_response_missing_sgdid_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_locus_response({"format_name": "YBR160W", "display_name": "CDC28"})


def test_sgd_locus_response_not_an_object_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_locus_response(["not", "an", "object"])


def test_sgd_search_response_missing_results_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_search_response({"total": {"value": 0}})


def test_sgd_search_hit_unusable_href_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_search_response({"results": [{"name": "CDC28 / YBR160W", "href": None}]})


def test_sgd_aliases_wrong_type_raises_parse_error() -> None:
    """Aliases present but with the wrong JSON type is malformed, not gracefully empty."""
    with pytest.raises(ConnectorParseError):
        parse_locus_response({"sgdid": "S000000364", "aliases": "CDK1"})


def test_sgd_alias_entry_missing_required_keys_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_locus_response({"sgdid": "S000000364", "aliases": [{"display_name": "CDK1"}]})


def test_sgd_go_details_missing_go_field_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_go_details_response([{"annotation_type": "computational"}])


def test_sgd_go_details_not_an_array_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_go_details_response({"go_id": "GO:0000307"})


# --- Missing optional fields handled gracefully ---------------------------------


def test_sgd_minimal_record_missing_optional_fields() -> None:
    """A locus record with only 'sgdid' plus names parses fine; optional fields are None/empty."""
    record = _parsed_fixture("locus_minimal.json")

    assert record.sgd_id == "S000099999"
    assert record.description is None
    assert record.aliases == ()
    assert record.uniprot_id is None
    assert record.external_links == ()


# --- Identifier classification (task C) ------------------------------------------


def test_classify_sgd_identifier_recognizes_sgd_id() -> None:
    assert classify_sgd_identifier("S000000364") == "sgd_id"


def test_classify_sgd_identifier_recognizes_systematic_name() -> None:
    assert classify_sgd_identifier("YBR160W") == "systematic_name"
    assert classify_sgd_identifier("YNL999W") == "systematic_name"


def test_classify_sgd_identifier_treats_standard_name_as_fallback() -> None:
    assert classify_sgd_identifier("CDC28") == "standard_name"


def test_classify_sgd_identifier_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="empty"):
        classify_sgd_identifier("   ")


def test_sgd_fetch_passes_through_any_identifier_form_unmodified() -> None:
    """fetch() does not classify or convert the identifier -- it is sent as given.

    Live-verified: SGD ID, systematic name, and standard name all resolve
    to the same record, so no local conversion is needed or attempted.
    """
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("locus_cdc28.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)

    connector.fetch("YBR160W")

    assert handler.requests[0].url.path == "/sgd/locus/YBR160W"


# --- Shared cache / rate-limit integration ---------------------------------------


def test_sgd_response_cached() -> None:
    """Repeated identical SGD requests are served from cache, not re-fetched."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("locus_cdc28.json")))
    connector = SgdConnector(
        _client_for(handler, cache=InMemoryResponseCache()), base_url=_BASE_URL
    )

    first = connector.fetch("CDC28")
    second = connector.fetch("CDC28")

    assert len(handler.requests) == 1
    assert first == second


def test_sgd_rate_limit_enforced() -> None:
    """SGD requests go through the injected rate limiter, once per attempt."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("locus_cdc28.json")))
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    connector = SgdConnector(
        _client_for(handler, rate_limiter=_RecordingLimiter()), base_url=_BASE_URL
    )

    connector.fetch("CDC28")

    assert len(acquire_calls) == 1


def test_sgd_429_retried() -> None:
    """A 429 is retried by the shared HTTP client, not reimplemented locally."""
    handler = _RecordingHandler(
        [httpx.Response(429), httpx.Response(200, text=_fixture("locus_cdc28.json"))]
    )
    connector = SgdConnector(
        _client_for(handler, sleep=lambda _seconds: None), base_url=_BASE_URL
    )

    record = connector.fetch("CDC28")

    assert len(handler.requests) == 2
    assert record is not None


# --- normalize() ------------------------------------------------------------------


def test_sgd_normalize_preserves_raw_record() -> None:
    record = _parsed_fixture("locus_cdc28.json")
    normalized = normalize_locus(record)

    assert normalized.raw is record
    assert normalized.sgd_id == record.sgd_id


# --- Construction / configuration / declared source -------------------------------


def test_sgd_connector_declares_sgd_source() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))
    connector = SgdConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.source == SourceType.SGD


def test_sgd_connector_rejects_empty_base_url() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))
    with pytest.raises(ValueError, match="base_url"):
        SgdConnector(_client_for(handler), base_url="   ")


def test_sgd_connector_from_settings_uses_configured_base_url() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("locus_cdc28.json")))
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        sgd_base_url=_BASE_URL,
    )

    connector = SgdConnector.from_settings(_client_for(handler), settings=settings)
    record = connector.fetch("CDC28")

    assert record is not None
    assert record.sgd_id == "S000000364"


def test_sgd_connector_from_settings_raises_when_unconfigured() -> None:
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        sgd_base_url=None,
    )
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))

    with pytest.raises(ValueError, match="sgd_base_url"):
        SgdConnector.from_settings(_client_for(handler), settings=settings)
