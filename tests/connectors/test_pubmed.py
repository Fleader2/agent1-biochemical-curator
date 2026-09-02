"""Tests for the PubMed connector: retrieval, XML parsing, and normalization.

Includes the PubMed connector tests required by ``docs/05_testing.md``
("PubMed Connector Tests"): ``test_pubmed_search_builds_valid_request``,
``test_pubmed_fetch_parses_metadata``, ``test_pubmed_pmid_preserved``,
``test_pubmed_empty_result_valid``, ``test_pubmed_timeout_handled``,
``test_pubmed_429_retried``, ``test_pubmed_500_retried``,
``test_pubmed_rate_limit_enforced``, ``test_pubmed_response_cached``,
``test_pubmed_failed_request_not_treated_as_zero_results``.

No test makes a real network call: every request is served by an
``httpx.MockTransport``, using the static fixtures under
``tests/fixtures/publications/``. Those fixtures use synthetic, clearly
out-of-range PMIDs (90000001, 90000002 -- see ``docs/05_testing.md``,
"Synthetic Scientific Fixtures": "If a field requires a syntactically valid
numeric PMID, use a test-only namespace ... rather than silently
representing it as a real PubMed record"). They follow NCBI's documented
E-utilities XML shape (https://www.ncbi.nlm.nih.gov/books/NBK25501/) but are
hand-constructed, not a live snapshot of the real service.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from app.config.settings import Settings
from app.connectors.cache import InMemoryResponseCache
from app.connectors.exceptions import ConnectorHTTPError, ConnectorNetworkError, ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.pubmed import (
    PubMedArticleRecord,
    PubMedConnector,
    build_pubmed_http_client,
    normalize_pubmed_article,
    parse_efetch_response,
    parse_esearch_response,
    parse_pubmed_article,
)
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "publications"
_BASE_URL = "https://example.invalid/eutils"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


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


# --- PubMed Connector Tests (docs/05_testing.md) -----------------------------


def test_pubmed_search_builds_valid_request() -> None:
    """``search()`` calls esearch with the expected db/term/retmode parameters."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    connector.search("acetyl-CoA carboxylase", max_results=5)

    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request.url.path == "/eutils/esearch.fcgi"
    params = dict(request.url.params)
    assert params["db"] == "pubmed"
    assert params["term"] == "acetyl-CoA carboxylase"
    assert params["retmax"] == "5"
    assert params["retmode"] == "xml"


def test_pubmed_fetch_parses_metadata() -> None:
    """``fetch()`` calls efetch and returns a fully parsed article record."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("efetch_article.xml")))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    article = connector.fetch("90000001")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/eutils/efetch.fcgi"
    assert isinstance(article, PubMedArticleRecord)
    assert article.pmid == "90000001"
    assert article.journal_title == "Journal of Synthetic Test Data"
    assert article.year == "2024"
    assert article.publication_types == ("Journal Article",)


def test_pubmed_pmid_preserved() -> None:
    """PMIDs from a search result are preserved exactly, as strings."""
    hits = parse_esearch_response(_fixture("esearch_two_hits.xml"))
    assert [hit.pmid for hit in hits] == ["90000001", "90000002"]


def test_pubmed_empty_result_valid() -> None:
    """A successful search with zero IDs returns ``[]``, not an error."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_empty.xml")))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    hits = connector.search("a query with no matches")

    assert hits == []


def test_pubmed_timeout_handled() -> None:
    """A timeout is retried by the shared HTTP client, not swallowed or ignored."""
    handler = _RecordingHandler(
        [
            httpx.TimeoutException("simulated timeout"),
            httpx.Response(200, text=_fixture("esearch_two_hits.xml")),
        ]
    )
    connector = PubMedConnector(
        _client_for(handler, sleep=lambda _seconds: None), base_url=_BASE_URL
    )

    hits = connector.search("glucose")

    assert len(handler.requests) == 2
    assert len(hits) == 2


def test_pubmed_429_retried() -> None:
    """A 429 is retried by the shared HTTP client."""
    handler = _RecordingHandler(
        [httpx.Response(429), httpx.Response(200, text=_fixture("esearch_two_hits.xml"))]
    )
    connector = PubMedConnector(
        _client_for(handler, sleep=lambda _seconds: None), base_url=_BASE_URL
    )

    hits = connector.search("glucose")

    assert len(handler.requests) == 2
    assert len(hits) == 2


def test_pubmed_500_retried() -> None:
    """A 500 is retried by the shared HTTP client."""
    handler = _RecordingHandler(
        [httpx.Response(500), httpx.Response(200, text=_fixture("efetch_article.xml"))]
    )
    connector = PubMedConnector(
        _client_for(handler, sleep=lambda _seconds: None), base_url=_BASE_URL
    )

    article = connector.fetch("90000001")

    assert len(handler.requests) == 2
    assert article is not None
    assert article.pmid == "90000001"


def test_pubmed_rate_limit_enforced() -> None:
    """PubMed requests go through the injected rate limiter, once per attempt."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    acquire_calls: list[None] = []

    class _RecordingLimiter:
        def acquire(self) -> None:
            acquire_calls.append(None)

    connector = PubMedConnector(
        _client_for(handler, rate_limiter=_RecordingLimiter()), base_url=_BASE_URL
    )

    connector.search("glucose")

    assert len(acquire_calls) == 1


def test_pubmed_response_cached() -> None:
    """Repeated identical PubMed requests are served from cache, not re-fetched."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("efetch_article.xml")))
    connector = PubMedConnector(
        _client_for(handler, cache=InMemoryResponseCache()), base_url=_BASE_URL
    )

    first = connector.fetch("90000001")
    second = connector.fetch("90000001")

    assert len(handler.requests) == 1
    assert first == second


def test_pubmed_failed_request_not_treated_as_zero_results() -> None:
    """An exhausted-retry failure raises; it is never silently treated as ``[]``."""
    handler = _RecordingHandler(httpx.Response(503, text="internal error"))
    connector = PubMedConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.search("glucose")


# --- fetch(): empty result vs. failure, distinguished --------------------------


def test_pubmed_fetch_returns_none_for_unknown_pmid() -> None:
    """An efetch response with no PubmedArticle elements means "no such record"."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("efetch_not_found.xml")))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    article = connector.fetch("99999999")

    assert article is None


def test_pubmed_fetch_still_raises_for_non_empty_failure() -> None:
    """A genuine upstream failure on fetch() still raises -- not treated as "not found"."""
    handler = _RecordingHandler(httpx.Response(500, text="internal error"))
    connector = PubMedConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorHTTPError):
        connector.fetch("90000001")


def test_pubmed_network_failure_raises_network_error() -> None:
    handler = _RecordingHandler(httpx.ConnectError("simulated connection error"))
    connector = PubMedConnector(_client_for(handler, max_retries=0), base_url=_BASE_URL)

    with pytest.raises(ConnectorNetworkError):
        connector.search("glucose")


# --- Title / abstract / author parsing -----------------------------------------


def test_pubmed_title_parsing_preserves_nested_inline_markup() -> None:
    """ArticleTitle text inside nested elements (<i>, <sup>) is preserved, not lost."""
    root = ET.fromstring(_fixture("efetch_article.xml"))
    article = parse_pubmed_article(root.find(".//PubmedArticle"))

    assert article.title == (
        "Regulation of ACC1 activity by 32P-labeled phosphorylation in a synthetic test fixture"
    )


def test_pubmed_abstract_parsing_multiple_labeled_sections() -> None:
    """Multiple labeled AbstractText sections are all preserved, in order."""
    articles = parse_efetch_response(_fixture("efetch_article.xml"))
    article = articles[0]

    assert len(article.abstract_sections) == 2
    assert article.abstract_sections[0].label == "BACKGROUND"
    assert "nested emphasis" in article.abstract_sections[0].text
    assert article.abstract_sections[1].label == "RESULTS"

    normalized = normalize_pubmed_article(article)
    assert normalized.abstract is not None
    assert "BACKGROUND: This is synthetic background text" in normalized.abstract
    assert "RESULTS: This is synthetic results text" in normalized.abstract


def test_pubmed_missing_abstract_is_none_not_an_error() -> None:
    """An article with no <Abstract> element at all parses fine, with abstract=None."""
    articles = parse_efetch_response(_fixture("efetch_minimal.xml"))
    article = articles[0]

    assert article.abstract_sections == ()
    normalized = normalize_pubmed_article(article)
    assert normalized.abstract is None


def test_pubmed_author_parsing_named_and_collective() -> None:
    """Both individually-named authors and a CollectiveName are captured."""
    articles = parse_efetch_response(_fixture("efetch_article.xml"))
    article = articles[0]

    assert article.authors == ("Smith Jane", "Synthetic Test Consortium")


def test_pubmed_missing_author_list_is_empty_not_an_error() -> None:
    articles = parse_efetch_response(_fixture("efetch_minimal.xml"))
    assert articles[0].authors == ()


# --- DOI / PMCID extraction by IdType, not position ------------------------------


def test_pubmed_doi_extraction_when_present() -> None:
    articles = parse_efetch_response(_fixture("efetch_article.xml"))
    normalized = normalize_pubmed_article(articles[0])

    assert normalized.doi == "10.9999/test.90000001"


def test_pubmed_pmcid_extraction_when_present() -> None:
    articles = parse_efetch_response(_fixture("efetch_article.xml"))
    normalized = normalize_pubmed_article(articles[0])

    assert normalized.pmcid == "PMC9000001"


def test_pubmed_doi_and_pmcid_none_when_absent() -> None:
    """No DOI/PMCID is ever invented when NCBI did not supply one."""
    articles = parse_efetch_response(_fixture("efetch_minimal.xml"))
    normalized = normalize_pubmed_article(articles[0])

    assert normalized.doi is None
    assert normalized.pmcid is None


def test_pubmed_article_ids_distinguished_by_type_not_position() -> None:
    """DOI and PMCID are picked out by IdType, independent of their order in the XML."""
    root = ET.fromstring(
        """<PubmedArticle>
            <MedlineCitation><PMID>1</PMID></MedlineCitation>
            <PubmedData>
                <ArticleIdList>
                    <ArticleId IdType="pmc">PMC1</ArticleId>
                    <ArticleId IdType="pubmed">1</ArticleId>
                    <ArticleId IdType="doi">10.1/x</ArticleId>
                </ArticleIdList>
            </PubmedData>
        </PubmedArticle>"""
    )
    normalized = normalize_pubmed_article(parse_pubmed_article(root))

    assert normalized.doi == "10.1/x"
    assert normalized.pmcid == "PMC1"


# --- Malformed XML --------------------------------------------------------------


def test_pubmed_malformed_esearch_xml_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_esearch_response("<eSearchResult><IdList><Id>1</Id></IdList>")  # unclosed


def test_pubmed_malformed_efetch_xml_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_efetch_response("<PubmedArticleSet><PubmedArticle>")  # unclosed


def test_pubmed_article_missing_pmid_raises_parse_error() -> None:
    with pytest.raises(ConnectorParseError):
        parse_efetch_response(
            "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            "<Article><ArticleTitle>No PMID here</ArticleTitle></Article>"
            "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
        )


def test_pubmed_fetch_malformed_response_raises_parse_error() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="this is not xml at all <<<"))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    with pytest.raises(ConnectorParseError):
        connector.fetch("90000001")


# --- NCBI tool/email/api_key parameters -----------------------------------------


def test_pubmed_includes_ncbi_credential_params_when_configured() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    connector = PubMedConnector(
        _client_for(handler),
        base_url=_BASE_URL,
        tool="agent1-test-tool",
        email="agent1-test@example.invalid",
        api_key="test-only-fake-ncbi-key",
    )

    connector.search("glucose")

    params = dict(handler.requests[0].url.params)
    assert params["tool"] == "agent1-test-tool"
    assert params["email"] == "agent1-test@example.invalid"
    assert params["api_key"] == "test-only-fake-ncbi-key"


def test_pubmed_omits_ncbi_credential_params_when_not_configured() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)

    connector.search("glucose")

    params = dict(handler.requests[0].url.params)
    assert "tool" not in params
    assert "email" not in params
    assert "api_key" not in params


def test_pubmed_connector_from_settings_reads_ncbi_configuration() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        ncbi_tool_name="agent1-test-tool",
        ncbi_email="agent1-test@example.invalid",
        ncbi_api_key="test-only-fake-ncbi-key",  # type: ignore[arg-type]
    )

    connector = PubMedConnector.from_settings(
        _client_for(handler), settings=settings, base_url=_BASE_URL
    )
    connector.search("glucose")

    params = dict(handler.requests[0].url.params)
    assert params["tool"] == "agent1-test-tool"
    assert params["email"] == "agent1-test@example.invalid"
    assert params["api_key"] == "test-only-fake-ncbi-key"


def test_pubmed_from_settings_omits_credentials_when_unset() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("esearch_two_hits.xml")))
    # ncbi_api_key must be overridden explicitly to None: the test session's
    # conftest.py sets a NCBI_API_KEY environment variable (a fake test-only
    # value) for the whole run, which Settings() would otherwise pick up.
    settings = Settings(
        database_url="postgresql://user:pass@localhost/db",  # type: ignore[call-arg]
        ncbi_tool_name=None,
        ncbi_email=None,
        ncbi_api_key=None,
    )

    connector = PubMedConnector.from_settings(
        _client_for(handler), settings=settings, base_url=_BASE_URL
    )
    connector.search("glucose")

    params = dict(handler.requests[0].url.params)
    assert "tool" not in params
    assert "email" not in params
    assert "api_key" not in params


def test_pubmed_api_key_never_appears_in_connector_repr() -> None:
    """The configured API key must not leak through the connector's own repr."""
    connector = PubMedConnector(
        _client_for(_RecordingHandler(httpx.Response(200, text="<eSearchResult/>"))),
        base_url=_BASE_URL,
        api_key="test-only-fake-ncbi-key-should-not-leak",
    )

    assert "test-only-fake-ncbi-key-should-not-leak" not in repr(connector)


# --- Rate-limit defaults (build_pubmed_http_client) -----------------------------


def test_build_pubmed_http_client_uses_faster_interval_with_api_key() -> None:
    """NCBI's documented guidance: a faster allowed rate when an API key is configured.

    Two back-to-back requests through a fake, fully controlled clock: both
    clients see the same (short) simulated elapsed time between requests, so
    the resulting sleep duration directly reflects each client's configured
    minimum interval -- no real wall-clock timing is involved.
    """
    handler = _RecordingHandler(httpx.Response(200, text="<eSearchResult/>"))

    sleeps_without_key: list[float] = []
    times_without_key = iter([0.0, 0.05, 0.05])
    client_without_key = build_pubmed_http_client(
        httpx.Client(transport=httpx.MockTransport(handler)),
        api_key=None,
        clock=lambda: next(times_without_key),
        sleep=sleeps_without_key.append,
    )

    sleeps_with_key: list[float] = []
    times_with_key = iter([0.0, 0.05, 0.05])
    client_with_key = build_pubmed_http_client(
        httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-only-fake-ncbi-key",
        clock=lambda: next(times_with_key),
        sleep=sleeps_with_key.append,
    )

    client_without_key.get("https://example.invalid/x")
    client_without_key.get("https://example.invalid/x")
    client_with_key.get("https://example.invalid/x")
    client_with_key.get("https://example.invalid/x")

    assert sleeps_without_key == [pytest.approx(1 / 3 - 0.05)]
    assert sleeps_with_key == [pytest.approx(1 / 10 - 0.05)]
    assert sleeps_without_key[0] > sleeps_with_key[0]


# --- Stable identifier / declared source ----------------------------------------


def test_pubmed_connector_declares_pubmed_source() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="<eSearchResult/>"))
    connector = PubMedConnector(_client_for(handler), base_url=_BASE_URL)
    assert connector.source == SourceType.PUBMED


def test_pubmed_connector_rejects_empty_base_url() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="<eSearchResult/>"))
    with pytest.raises(ValueError, match="base_url"):
        PubMedConnector(_client_for(handler), base_url="   ")
