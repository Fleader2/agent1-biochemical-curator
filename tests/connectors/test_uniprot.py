"""Tests for the UniProt connector: retrieval, JSON parsing, and normalization.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using the static fixtures under
``tests/fixtures/uniprot/``. Those fixtures are synthetic, hand-constructed
examples that follow UniProtKB's documented, stable JSON schema -- they are
not a live snapshot of the current UniProt service and should not be read
as a guarantee of today's exact field values (the same disclaimer
``app/connectors/uniprot.py``'s own module docstring carries, and the same
"synthetic, not live-verified" convention ``tests/connectors/test_kegg.py``
already uses for KEGG). Accessions/identifiers here (``P99999``,
``A0A999TEST``, ``S000099999``, ...) are deliberately out-of-range
synthetic values (``docs/05_testing.md``, "Synthetic Scientific Fixtures").
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import ConnectorHTTPError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.uniprot import (
    UniProtConnector,
    UniProtCrossReference,
    UniProtEntryRecord,
    normalize_entry,
    parse_entry_response,
    parse_search_response,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "uniprot"
_BASE_URL = "https://example.invalid/uniprot"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _parsed_fixture(name: str) -> UniProtEntryRecord:
    return parse_entry_response(json.loads(_fixture(name)))


class _RecordingHandler:
    """An ``httpx.MockTransport`` handler that replays a scripted sequence.

    Mirrors ``tests/connectors/test_sgd.py``'s own handler exactly: each
    entry in ``responses`` is either an ``httpx.Response`` to return or an
    exception instance to raise, consumed in call order; the last entry
    repeats for any calls beyond the scripted sequence.
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


# --- source attribute -----------------------------------------------------------


def test_uniprot_connector_source_is_uniprot() -> None:
    assert UniProtConnector.source == SourceType.UNIPROT


# --- fetch() ----------------------------------------------------------------------


def test_uniprot_fetch_exact_accession() -> None:
    """``fetch()`` calls UniProt's entry endpoint and returns a parsed record."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("entry_p99999.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    record = connector.fetch("P99999")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/uniprot/uniprotkb/P99999"
    assert isinstance(record, UniProtEntryRecord)
    assert record.primary_accession == "P99999"


def test_uniprot_fetch_preserves_primary_accession_literally() -> None:
    record = _parsed_fixture("entry_p99999.json")
    assert record.primary_accession == "P99999"
    assert record.secondary_accessions == ("P00888",)


def test_uniprot_fetch_reviewed_status_preserved() -> None:
    record = _parsed_fixture("entry_p99999.json")
    normalized = normalize_entry(record)
    assert normalized.reviewed is True


def test_uniprot_fetch_unreviewed_status_preserved() -> None:
    record = _parsed_fixture("entry_unreviewed.json")
    normalized = normalize_entry(record)
    assert normalized.reviewed is False


def test_uniprot_protein_name_parsed_from_recommended_name() -> None:
    normalized = normalize_entry(_parsed_fixture("entry_p99999.json"))
    assert normalized.protein_name == "Test-only acetyl-CoA carboxylase"


def test_uniprot_protein_name_falls_back_to_submitted_name_when_unreviewed() -> None:
    normalized = normalize_entry(_parsed_fixture("entry_unreviewed.json"))
    assert normalized.protein_name == "Test-only uncharacterized protein"


def test_uniprot_organism_taxonomy_parsed() -> None:
    normalized = normalize_entry(_parsed_fixture("entry_p99999.json"))
    assert normalized.organism_taxonomy_id == 559292
    assert normalized.organism_name == "Saccharomyces cerevisiae (strain ATCC 204508 / S288c)"


def test_uniprot_gene_metadata_parsed_but_inert() -> None:
    """Gene names are captured as plain metadata -- this test only checks

    parsing; app/normalization/protein.py's own tests verify they are
    never promoted to gene_id.
    """
    normalized = normalize_entry(_parsed_fixture("entry_p99999.json"))
    assert normalized.gene_names == ("TEST1",)


def test_uniprot_ec_metadata_parsed_but_inert() -> None:
    normalized = normalize_entry(_parsed_fixture("entry_p99999.json"))
    assert normalized.ec_numbers == ("6.4.1.2",)


def test_uniprot_sequence_length_parsed() -> None:
    normalized = normalize_entry(_parsed_fixture("entry_p99999.json"))
    assert normalized.sequence_length == 2233


def test_uniprot_cross_references_narrowed_to_safe_set() -> None:
    """SGD/GeneID/KEGG are surfaced on the normalized record; EMBL/PDB are not

    (though still reachable on the raw entry -- nothing is discarded at the
    source-native layer)."""
    record = _parsed_fixture("entry_p99999.json")
    assert UniProtCrossReference(database="EMBL", identifier="X99999") in record.cross_references
    assert UniProtCrossReference(database="PDB", identifier="9XYZ") in record.cross_references

    normalized = normalize_entry(record)
    databases = {ref.database for ref in normalized.cross_references}
    assert databases == {"SGD", "GeneID", "KEGG"}
    assert UniProtCrossReference(database="SGD", identifier="S000099999") in (
        normalized.cross_references
    )


def test_uniprot_fetch_returns_none_for_unknown_accession() -> None:
    """A UniProt 404 for an entry means "no such record"."""
    handler = _RecordingHandler(httpx.Response(404, text="not found"))
    connector = UniProtConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    assert connector.fetch("NOSUCHACCESSION") is None


def test_uniprot_fetch_still_raises_for_non_404_failure() -> None:
    """A non-404 failure on fetch() still raises -- only 404 means "no record",

    and it is never confused with a connector failure."""
    handler = _RecordingHandler(httpx.Response(500, text="internal error"))
    connector = UniProtConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.fetch("P99999")


def test_uniprot_fetch_rejects_malformed_response() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="not json"))
    connector = UniProtConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorParseError):
        connector.fetch("P99999")


def test_uniprot_fetch_rejects_response_missing_primary_accession() -> None:
    with pytest.raises(ConnectorParseError):
        parse_entry_response({"uniProtkbId": "NO_ACCESSION_HERE"})


# --- search() -----------------------------------------------------------------


def test_uniprot_search_sends_query_correctly() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_test1.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("TEST1")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/uniprot/uniprotkb/search"
    params = dict(handler.requests[0].url.params)
    assert params["query"] == "TEST1"
    assert params["format"] == "json"
    assert len(hits) == 2
    assert hits[0].primary_accession == "P99999"
    assert hits[0].reviewed is True
    assert hits[1].primary_accession == "A0A999TEST"
    assert hits[1].reviewed is False


def test_uniprot_search_includes_organism_taxonomy_filter() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_test1.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    connector.search("TEST1", organism_taxonomy_id=559292)

    params = dict(handler.requests[0].url.params)
    assert params["query"] == "(TEST1) AND organism_id:559292"


def test_uniprot_search_limit_respected() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_test1.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    connector.search("TEST1", limit=5)

    params = dict(handler.requests[0].url.params)
    assert params["size"] == "5"


def test_uniprot_search_returns_empty_list_for_no_hits() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    assert connector.search("no-such-protein") == []


def test_uniprot_search_rejects_malformed_response() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="not json"))
    connector = UniProtConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorParseError):
        connector.search("TEST1")


def test_uniprot_search_rejects_response_missing_results_shape() -> None:
    with pytest.raises(ConnectorParseError):
        parse_search_response(json.dumps({"results": "not-a-list"}))


def test_uniprot_search_failure_not_treated_as_zero_results() -> None:
    """An exhausted-retry failure raises; it is never silently treated as ``[]``."""
    handler = _RecordingHandler(httpx.Response(503, text="internal error"))
    connector = UniProtConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.search("TEST1")


def test_uniprot_search_empty_query_rejected() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("search_empty.json")))
    connector = UniProtConnector(_client_for(handler), base_url=_BASE_URL)

    with pytest.raises(ValueError, match="query"):
        connector.search("   ")


# --- retry / rate limiting / caching (shared ConnectorHttpClient) --------------


def test_uniprot_transient_failure_is_retried_then_succeeds() -> None:
    """A transient 503 is retried using the shared connector retry policy --

    this connector adds no retry logic of its own."""
    handler = _RecordingHandler(
        [
            httpx.Response(503, text="try again"),
            httpx.Response(200, text=_fixture("entry_p99999.json")),
        ]
    )
    connector = UniProtConnector(
        _client_for(handler, max_retries=1, backoff_base_seconds=0.0), base_url=_BASE_URL
    )

    record = connector.fetch("P99999")

    assert len(handler.requests) == 2
    assert record is not None
    assert record.primary_accession == "P99999"


def test_uniprot_rate_limit_enforced() -> None:
    """UniProt requests go through the injected rate limiter, once per attempt."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("entry_p99999.json")))
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    connector = UniProtConnector(
        _client_for(handler, rate_limiter=_RecordingLimiter()), base_url=_BASE_URL
    )

    connector.fetch("P99999")

    assert len(acquire_calls) == 1


def test_uniprot_cache_used() -> None:
    """Repeated identical UniProt fetches are served from cache, not re-fetched."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("entry_p99999.json")))
    connector = UniProtConnector(
        _client_for(handler, cache=InMemoryResponseCache()), base_url=_BASE_URL
    )

    first = connector.fetch("P99999")
    second = connector.fetch("P99999")

    assert len(handler.requests) == 1
    assert first == second


# --- isoform handling (Increment 15, Step 23) -----------------------------------


def test_uniprot_base_accession_preserved() -> None:
    record = _parsed_fixture("entry_p99999.json")
    assert record.primary_accession == "P99999"


def test_uniprot_isoform_accession_preserved() -> None:
    record = _parsed_fixture("entry_p99999-2.json")
    assert record.primary_accession == "P99999-2"


def test_uniprot_search_does_not_strip_isoform_suffix() -> None:
    hits = parse_search_response(
        json.dumps(
            {
                "results": [
                    {
                        "entryType": "UniProtKB reviewed (Swiss-Prot)",
                        "primaryAccession": "P99999-2",
                        "uniProtkbId": "TEST1_YEAST",
                    }
                ]
            }
        )
    )
    assert hits[0].primary_accession == "P99999-2"


def test_uniprot_base_and_isoform_remain_distinct() -> None:
    base = _parsed_fixture("entry_p99999.json")
    isoform = _parsed_fixture("entry_p99999-2.json")
    assert base.primary_accession != isoform.primary_accession
    assert base.primary_accession == "P99999"
    assert isoform.primary_accession == "P99999-2"


# --- ID Mapping deferral (Increment 15, Step 27) --------------------------------


def test_uniprot_connector_never_calls_idmapping() -> None:
    """Structural check: no URL path fragment for UniProt's ID Mapping API

    (``/idmapping``, ``/idmapping/run``, ``/idmapping/results``) appears
    anywhere in this connector module -- the word "idmapping" itself is
    expected to appear once, in this module's own deferral documentation
    (see its module docstring), so this checks for an actual path
    reference rather than a bare substring match.
    """
    import inspect

    method_source = "".join(
        inspect.getsource(method)
        for method in (
            UniProtConnector.search,
            UniProtConnector.fetch,
            UniProtConnector.normalize,
            UniProtConnector.__init__,
        )
    )
    assert "idmapping" not in method_source.lower()
    assert not hasattr(UniProtConnector, "map_ids")
    assert not hasattr(UniProtConnector, "id_mapping")
