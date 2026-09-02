"""PubMed connector: retrieval and parsing only, no persistence or curation policy.

Uses NCBI E-utilities (https://www.ncbi.nlm.nih.gov/books/NBK25501/):

* ``GET {base_url}/esearch.fcgi`` -- search (``search()``), returns matching PMIDs.
* ``GET {base_url}/efetch.fcgi`` -- fetch a single article by PMID (``fetch()``).

Both are unauthenticated in the sense that no credential is *required*, but
NCBI's usage guidelines ask callers to identify themselves (``tool``,
``email``) and offer a higher request rate to callers with a registered API
key (``api_key``). This module includes those parameters when configured
(``PubMedConnector.from_settings``, reading ``app.config.settings.Settings``'s
``ncbi_tool_name``/``ncbi_email``/``ncbi_api_key``) and omits them otherwise --
it never invents a value for an unset one.

All HTTP retrieval goes through ``app.connectors.http.ConnectorHttpClient``,
which owns every retry, backoff, timeout, rate-limit, and cache concern.
Nothing in this module retries a request, waits out a rate limit, or reads/
writes a database. ``build_pubmed_http_client`` is an optional convenience
for constructing a ``ConnectorHttpClient`` with NCBI's documented rate-limit
guidance (3 requests/second without an API key, 10 with one) -- using it is
never required, and ``PubMedConnector`` itself never constructs a rate
limiter on its own.

Four separate transformations, per ``app/connectors/base.py``:

* retrieval (``search()``/``fetch()``): network I/O only, via
  ``ConnectorHttpClient``.
* parsing (``parse_esearch_response()``/``parse_efetch_response()``/
  ``parse_pubmed_article()``): raw PubMed XML -> a source-native structure
  (``PubMedSearchHit``, ``PubMedArticleRecord``). Pure functions, no I/O,
  nothing discarded.
* normalization (``normalize()``, ``normalize_pubmed_article()``):
  source-native structure -> a PubMed-scoped typed record
  (``PubMedNormalizedRecord``) shaped closer to ``app.models.publication.
  Publication`` -- still PubMed-only; no cross-source entity resolution, no
  claim/evidence generation, no publication-row persistence, which are all
  later phases.
* persistence: not implemented here (a later increment).

A credential-handling note: ``api_key`` (like ``tool``/``email``) is a
required *query parameter* in NCBI's own API design -- there is no
header-based alternative -- so it necessarily appears in the request URL
this module builds and in ``ConnectorHttpClient``'s resulting
``RawResponse.url``. Nothing in this module logs, and no exception message
built here includes, the full URL or the credential parameters; the
``ConnectorHttpClient``-level error messages use only the caller-supplied
base path (without query parameters). Still, any future code that persists
or logs ``RawResponse.url`` for a PubMed request would need to redact
``api_key`` first -- this module cannot do that on its own since the shared
``ConnectorHttpClient``/``RawResponse`` types have no redaction concept, and
introducing one is out of this increment's scope.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config.settings import Settings, get_settings
from app.connectors.cache import ResponseCache
from app.connectors.exceptions import ConnectorParseError
from app.connectors.http import ConnectorHttpClient
from app.connectors.ratelimit import IntervalRateLimiter
from app.models.enums import SourceType

_DEFAULT_EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_DEFAULT_MAX_RESULTS = 20

# NCBI E-utilities usage guidelines: up to 3 requests/second without a
# registered API key, up to 10 requests/second with one.
_UNAUTHENTICATED_MIN_INTERVAL_SECONDS = 1.0 / 3.0
_API_KEY_MIN_INTERVAL_SECONDS = 1.0 / 10.0


@dataclass(frozen=True, slots=True)
class PubMedSearchHit:
    """One PMID returned by an ``esearch`` call."""

    pmid: str


@dataclass(frozen=True, slots=True)
class PubMedAbstractSection:
    """One ``AbstractText`` element. ``label`` is ``None`` when unlabeled."""

    label: str | None
    text: str


@dataclass(frozen=True, slots=True)
class PubMedArticleId:
    """One ``ArticleId`` element, keyed by its ``IdType`` (``"doi"``, ``"pmc"``, ...)."""

    id_type: str
    value: str


@dataclass(frozen=True, slots=True)
class PubMedArticleRecord:
    """Source-native parsed record for one ``PubmedArticle`` -- nothing discarded.

    ``year`` is the raw text found in the XML (from ``Year`` or, failing
    that, a leading 4-digit prefix of ``MedlineDate``), not yet coerced to
    ``int`` -- that coercion, and picking DOI/PMCID out of ``article_ids``,
    is ``normalize()``'s job.
    """

    pmid: str
    title: str | None
    abstract_sections: tuple[PubMedAbstractSection, ...]
    journal_title: str | None
    year: str | None
    authors: tuple[str, ...]
    article_ids: tuple[PubMedArticleId, ...]
    publication_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PubMedNormalizedRecord:
    """PubMed-scoped normalized view, shaped close to the ``publication`` table.

    ``raw`` retains the complete parsed record, so nothing is lost by
    normalizing -- only additional, cleaned, typed fields are added. This is
    not a ``Publication`` row and nothing here is persisted.
    """

    pmid: str
    title: str | None
    abstract: str | None
    journal: str | None
    year: int | None
    authors: tuple[str, ...]
    doi: str | None
    pmcid: str | None
    raw: PubMedArticleRecord


def _element_text(element: ET.Element) -> str:
    """Flatten an element's text, including text inside nested inline markup.

    PubMed XML text nodes (``ArticleTitle``, ``AbstractText``, ...) may
    contain nested elements such as ``<i>``/``<sub>``/``<sup>`` for
    formatting. Reading only ``element.text`` would silently drop everything
    inside and after those children; ``itertext()`` walks the whole subtree.
    """
    return "".join(element.itertext()).strip()


def _extract_year(pub_date: ET.Element | None) -> str | None:
    if pub_date is None:
        return None
    year_elem = pub_date.find("Year")
    if year_elem is not None and (year_elem.text or "").strip():
        return year_elem.text.strip()
    # Older/irregular records use a free-text MedlineDate instead of a clean
    # Year, e.g. "2020 Jan-Feb". Extract a leading 4-digit year if present;
    # this is a mechanical parse, not an inference about what the date means.
    medline_date_elem = pub_date.find("MedlineDate")
    if medline_date_elem is not None and medline_date_elem.text:
        match = re.match(r"(\d{4})", medline_date_elem.text.strip())
        if match:
            return match.group(1)
    return None


def _format_author_name(author: ET.Element) -> str | None:
    collective = author.find("CollectiveName")
    if collective is not None and (collective.text or "").strip():
        return collective.text.strip()

    last_name_elem = author.find("LastName")
    last_name = (last_name_elem.text or "").strip() if last_name_elem is not None else ""
    if not last_name:
        return None

    fore_name_elem = author.find("ForeName")
    initials_elem = author.find("Initials")
    given = ""
    if fore_name_elem is not None and (fore_name_elem.text or "").strip():
        given = fore_name_elem.text.strip()
    elif initials_elem is not None and (initials_elem.text or "").strip():
        given = initials_elem.text.strip()

    return f"{last_name} {given}".strip() if given else last_name


def parse_pubmed_article(article: ET.Element) -> PubMedArticleRecord:
    """Parse one ``<PubmedArticle>`` element. Pure: no HTTP or DB access."""
    medline = article.find("MedlineCitation")
    if medline is None:
        raise ConnectorParseError("malformed PubmedArticle: missing MedlineCitation")

    pmid_elem = medline.find("PMID")
    if pmid_elem is None or not (pmid_elem.text or "").strip():
        raise ConnectorParseError("malformed PubmedArticle: missing PMID")
    pmid = pmid_elem.text.strip()

    title: str | None = None
    journal_title: str | None = None
    year: str | None = None
    abstract_sections: list[PubMedAbstractSection] = []
    authors: list[str] = []
    publication_types: list[str] = []

    article_elem = medline.find("Article")
    if article_elem is not None:
        title_elem = article_elem.find("ArticleTitle")
        if title_elem is not None:
            title = _element_text(title_elem) or None

        journal_elem = article_elem.find("Journal")
        if journal_elem is not None:
            journal_title_elem = journal_elem.find("Title")
            if journal_title_elem is not None:
                journal_title = _element_text(journal_title_elem) or None
            year = _extract_year(journal_elem.find("JournalIssue/PubDate"))

        abstract_elem = article_elem.find("Abstract")
        if abstract_elem is not None:
            for text_elem in abstract_elem.findall("AbstractText"):
                abstract_sections.append(
                    PubMedAbstractSection(
                        label=text_elem.get("Label"), text=_element_text(text_elem)
                    )
                )

        author_list_elem = article_elem.find("AuthorList")
        if author_list_elem is not None:
            for author_elem in author_list_elem.findall("Author"):
                name = _format_author_name(author_elem)
                if name:
                    authors.append(name)

        pub_type_list_elem = article_elem.find("PublicationTypeList")
        if pub_type_list_elem is not None:
            for pub_type_elem in pub_type_list_elem.findall("PublicationType"):
                text = _element_text(pub_type_elem)
                if text:
                    publication_types.append(text)

    article_ids: list[PubMedArticleId] = []
    id_list_elem = article.find("PubmedData/ArticleIdList")
    if id_list_elem is not None:
        for id_elem in id_list_elem.findall("ArticleId"):
            id_type = id_elem.get("IdType", "")
            value = (id_elem.text or "").strip()
            if id_type and value:
                article_ids.append(PubMedArticleId(id_type=id_type, value=value))

    return PubMedArticleRecord(
        pmid=pmid,
        title=title,
        abstract_sections=tuple(abstract_sections),
        journal_title=journal_title,
        year=year,
        authors=tuple(authors),
        article_ids=tuple(article_ids),
        publication_types=tuple(publication_types),
    )


def parse_esearch_response(text: str) -> list[PubMedSearchHit]:
    """Parse an ``esearch`` response into PMIDs.

    A response with no ``IdList``/``Id`` elements is a legitimate empty
    result and returns ``[]`` -- it is not an error.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ConnectorParseError(f"malformed PubMed esearch XML: {exc}") from exc

    id_list = root.find("IdList")
    if id_list is None:
        return []

    hits: list[PubMedSearchHit] = []
    for id_elem in id_list.findall("Id"):
        pmid = (id_elem.text or "").strip()
        if pmid:
            hits.append(PubMedSearchHit(pmid=pmid))
    return hits


def parse_efetch_response(text: str) -> list[PubMedArticleRecord]:
    """Parse an ``efetch`` response into zero or more article records.

    An article set with no ``PubmedArticle`` elements (as returned for an
    unknown/invalid PMID) yields ``[]`` -- it is not an error.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ConnectorParseError(f"malformed PubMed efetch XML: {exc}") from exc

    return [parse_pubmed_article(article) for article in root.findall(".//PubmedArticle")]


def _combine_abstract_sections(sections: tuple[PubMedAbstractSection, ...]) -> str | None:
    if not sections:
        return None
    parts = [f"{s.label}: {s.text}" if s.label else s.text for s in sections]
    return "\n\n".join(parts)


def normalize_pubmed_article(record: PubMedArticleRecord) -> PubMedNormalizedRecord:
    """Map a generic parsed record onto PubMed-scoped, schema-shaped fields."""
    doi = next((a.value for a in record.article_ids if a.id_type == "doi"), None)
    pmcid = next((a.value for a in record.article_ids if a.id_type == "pmc"), None)

    year: int | None = None
    if record.year is not None and record.year.isdigit():
        year = int(record.year)

    return PubMedNormalizedRecord(
        pmid=record.pmid,
        title=record.title,
        abstract=_combine_abstract_sections(record.abstract_sections),
        journal=record.journal_title,
        year=year,
        authors=record.authors,
        doi=doi,
        pmcid=pmcid,
        raw=record,
    )


def build_pubmed_http_client(
    client: httpx.Client,
    *,
    api_key: str | None = None,
    cache: ResponseCache | None = None,
    max_retries: int = 3,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ConnectorHttpClient:
    """Build a ``ConnectorHttpClient`` using NCBI's documented rate-limit guidance.

    A convenience only: constructing ``ConnectorHttpClient`` directly (with
    any rate limiter, or none) and passing it to ``PubMedConnector`` always
    works too -- ``PubMedConnector`` itself never constructs a rate limiter.
    ``clock``/``sleep`` are injectable, matching ``IntervalRateLimiter``, so
    the choice of interval can be tested deterministically.
    """
    interval = _API_KEY_MIN_INTERVAL_SECONDS if api_key else _UNAUTHENTICATED_MIN_INTERVAL_SECONDS
    rate_limiter = IntervalRateLimiter(interval, clock=clock, sleep=sleep)
    return ConnectorHttpClient(
        client, max_retries=max_retries, rate_limiter=rate_limiter, cache=cache, sleep=sleep
    )


class PubMedConnector:
    """Retrieval and parsing for PubMed. No curation policy, no persistence."""

    source: SourceType = SourceType.PUBMED

    def __init__(
        self,
        http_client: ConnectorHttpClient,
        *,
        base_url: str = _DEFAULT_EUTILS_BASE_URL,
        tool: str | None = None,
        email: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._tool = tool
        self._email = email
        self._api_key = api_key

    @classmethod
    def from_settings(
        cls,
        http_client: ConnectorHttpClient,
        settings: Settings | None = None,
        *,
        base_url: str = _DEFAULT_EUTILS_BASE_URL,
    ) -> PubMedConnector:
        """Build a connector using the existing ``ncbi_*`` settings.

        Every credential is optional -- NCBI allows unauthenticated access at
        a lower request rate -- so nothing here raises for an unset value;
        it is simply omitted from requests.
        """
        resolved_settings = settings or get_settings()
        api_key = (
            resolved_settings.ncbi_api_key.get_secret_value()
            if resolved_settings.ncbi_api_key is not None
            else None
        )
        return cls(
            http_client,
            base_url=base_url,
            tool=resolved_settings.ncbi_tool_name,
            email=resolved_settings.ncbi_email,
            api_key=api_key,
        )

    def _credential_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._tool:
            params["tool"] = self._tool
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    def search(
        self, query: str, *, max_results: int = _DEFAULT_MAX_RESULTS
    ) -> list[PubMedSearchHit]:
        """Search PubMed for matching PMIDs.

        Returns ``[]`` for a legitimate empty result. A retrieval failure
        (timeout, rate limit exhausted, HTTP error) raises the corresponding
        ``app.connectors.exceptions.ConnectorError`` instead -- the two are
        never confused. Article details are not fetched implicitly; callers
        that want them call ``fetch()`` per PMID.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if max_results < 1:
            raise ValueError("max_results must be at least 1")

        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(max_results),
            "retmode": "xml",
            **self._credential_params(),
        }
        response = self._http.get(f"{self._base_url}/esearch.fcgi", params=params)
        return parse_esearch_response(response.text)

    def fetch(self, external_id: str) -> PubMedArticleRecord | None:
        """Retrieve a single PubMed article by PMID.

        Returns ``None`` when PubMed's response contains no article for the
        requested PMID -- efetch does not use a distinct HTTP status for an
        unknown PMID the way KEGG's ``get`` does; it returns an empty
        article set instead. This is treated as the same "no such record"
        outcome, never as an invented empty/default record. Any other
        retrieval failure still raises.
        """
        pmid = external_id.strip()
        if not pmid:
            raise ValueError("external_id must not be empty")

        params = {
            "db": "pubmed",
            "id": pmid,
            "rettype": "abstract",
            "retmode": "xml",
            **self._credential_params(),
        }
        response = self._http.get(f"{self._base_url}/efetch.fcgi", params=params)
        articles = parse_efetch_response(response.text)
        return articles[0] if articles else None

    def normalize(self, raw: PubMedArticleRecord) -> PubMedNormalizedRecord:
        """Map a parsed PubMed article onto a PubMed-scoped normalized shape."""
        return normalize_pubmed_article(raw)


__all__ = [
    "PubMedAbstractSection",
    "PubMedArticleId",
    "PubMedArticleRecord",
    "PubMedConnector",
    "PubMedNormalizedRecord",
    "PubMedSearchHit",
    "build_pubmed_http_client",
    "normalize_pubmed_article",
    "parse_efetch_response",
    "parse_esearch_response",
    "parse_pubmed_article",
]
