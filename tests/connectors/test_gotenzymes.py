"""Tests for the GotEnzymes2 connector: retrieval, JSON parsing, and normalization.

No test makes a real network call: every request is served by an
``httpx.MockTransport``. ``enzymes_acc1.json``/``gene_acc1.json`` are real, live-captured
responses (Agent 1.x Increment C.11) for the real, already-curated ACC1 gene
(``YNR016C``, EC 6.4.1.2, KEGG reaction ``R00742``) -- not synthetic. ``enzymes_empty.json``/
``enzymes_page1.json``/``enzymes_page2.json`` are deliberately synthetic (out-of-range
``SYNTH1``/``9.9.9.1``/``C9999x`` identifiers, ``docs/05_testing.md``'s "Synthetic
Scientific Fixtures" convention), used only to exercise pagination-following and the
empty-result path.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.connectors.exceptions import ConnectorParseError
from app.connectors.gotenzymes import (
    GOTENZYMES2_CATALYTIC_MODEL,
    GotEnzymesConnector,
    GotEnzymesRecord,
    gotenzymes_prediction_source_id,
    normalize_enzymes_record,
    parse_enzymes_response,
    parse_gene_cross_references,
)
from app.connectors.http import ConnectorHttpClient
from app.models.enums import SourceType

pytestmark = pytest.mark.connector

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gotenzymes"
_BASE_URL = "https://example.invalid/gotenzymes"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class _RecordingHandler:
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


def _connector(handler: _RecordingHandler) -> GotEnzymesConnector:
    return GotEnzymesConnector(_client_for(handler), base_url=_BASE_URL)


# --- source identity -----------------------------------------------------------------------


def test_gotenzymes_connector_source_is_gotenzymes() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("enzymes_empty.json")))
    assert _connector(handler).source is SourceType.GOTENZYMES


# --- search(): real, live-captured ACC1 data ------------------------------------------------


def test_gotenzymes_search_real_acc1_data() -> None:
    """Real, live-captured response for the real, already-curated ACC1 gene."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("enzymes_acc1.json")))
    connector = _connector(handler)

    records = connector.search(gene="YNR016C", organism="sce")

    assert len(handler.requests) == 1
    assert handler.requests[0].url.path == "/gotenzymes/gotenzymes/enzymes"
    assert len(records) == 10
    first = records[0]
    assert isinstance(first, GotEnzymesRecord)
    assert first.gene == "YNR016C"
    assert first.organism == "sce"
    assert first.ec_number == "6.4.1.2"
    assert first.reaction_id == "R00742"
    assert first.compound == "C00024"
    assert first.kcat_value == 3.9841
    assert first.km_value == 0.043
    assert first.kcat_km_value == 72.14


def test_gotenzymes_search_null_values_preserved_not_coerced() -> None:
    """A real reactant with no usable prediction reports null -- never coerced to 0."""
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)

    null_record = next(r for r in records if r.reaction_id == "R04385" and r.compound == "C06250")
    assert null_record.kcat_value is None
    assert null_record.km_value is None
    assert null_record.kcat_km_value is None


def test_gotenzymes_search_requires_at_least_one_filter() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("enzymes_empty.json")))
    connector = _connector(handler)

    with pytest.raises(ValueError):
        connector.search()


def test_gotenzymes_search_empty_result_is_empty_list() -> None:
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("enzymes_empty.json")))
    connector = _connector(handler)

    assert connector.search(gene="NONEXISTENT", organism="sce") == []


def test_gotenzymes_search_follows_pagination_until_total_count_reached() -> None:
    handler = _RecordingHandler(
        [
            httpx.Response(200, text=_fixture("enzymes_page1.json")),
            httpx.Response(200, text=_fixture("enzymes_page2.json")),
        ]
    )
    connector = _connector(handler)

    records = connector.search(gene="SYNTH1", organism="sce", page_size=2)

    assert len(handler.requests) == 2
    assert "pagination%5Bpage%5D=1" in str(handler.requests[0].url)
    assert "pagination%5Bpage%5D=2" in str(handler.requests[1].url)
    assert len(records) == 3
    assert {r.compound for r in records} == {"C99991", "C99992", "C99993"}


def test_gotenzymes_malformed_response_raises_parse_error() -> None:
    handler = _RecordingHandler(httpx.Response(200, text='{"unexpected": true}'))
    connector = _connector(handler)

    with pytest.raises(ConnectorParseError):
        connector.search(gene="X", organism="sce")


# --- normalize(): splitting one row into 0-3 typed predictions ------------------------------


def test_gotenzymes_normalize_splits_row_into_three_predictions() -> None:
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    full_record = records[0]  # kcat, Km, and kcat/Km all present

    predictions = normalize_enzymes_record(full_record)

    assert {p.parameter_type for p in predictions} == {"kcat", "Km", "kcat/Km"}
    assert all(p.source_category_label == "ai_predicted" for p in predictions)
    assert all(p.model == GOTENZYMES2_CATALYTIC_MODEL for p in predictions)


def test_gotenzymes_normalize_never_invents_a_null_parameter() -> None:
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    null_record = next(r for r in records if r.reaction_id == "R04385" and r.compound == "C06250")

    predictions = normalize_enzymes_record(null_record)

    assert predictions == ()


def test_gotenzymes_normalize_units() -> None:
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    predictions = normalize_enzymes_record(records[0])

    units = {p.parameter_type: p.unit for p in predictions}
    assert units == {"kcat": "1/s", "Km": "mM", "kcat/Km": "mM^-1 s^-1"}


def test_gotenzymes_never_labeled_experimental() -> None:
    """The single most important guarantee: every prediction is unambiguously labeled
    ai_predicted, regardless of parameter type."""
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    for record in records:
        for prediction in normalize_enzymes_record(record):
            assert prediction.source_category_label == "ai_predicted"


# --- fetch(): real gene cross-references ----------------------------------------------------


def test_gotenzymes_fetch_real_acc1_uniprot_cross_reference() -> None:
    """Real, live-captured cross-reference for the real, already-curated ACC1 protein."""
    handler = _RecordingHandler(httpx.Response(200, text=_fixture("gene_acc1.json")))
    connector = _connector(handler)

    result = connector.fetch("YNR016C", organism="sce")

    assert handler.requests[0].url.path == "/gotenzymes/gotenzymes/genes/YNR016C"
    assert result.uniprot_ids == ("Q00955",)
    assert result.uniprot_id == "Q00955"


def test_gotenzymes_parse_gene_cross_references_multiple_uniprot_ids_no_single_pick() -> None:
    """More than one UniProtKB cross-reference never has one arbitrarily chosen."""
    data = {
        "info": {"kegg": "X"},
        "crossReferences": {
            "UniProtKB": [{"id": "P00001", "url": "..."}, {"id": "P00002", "url": "..."}]
        },
    }
    result = parse_gene_cross_references("X", data)

    assert result.uniprot_ids == ("P00001", "P00002")
    assert result.uniprot_id is None


def test_gotenzymes_parse_gene_cross_references_no_uniprot_entry() -> None:
    data = {"info": {"kegg": "X"}, "crossReferences": {}}
    result = parse_gene_cross_references("X", data)

    assert result.uniprot_ids == ()
    assert result.uniprot_id is None


# --- deterministic source id -----------------------------------------------------------------


def test_gotenzymes_prediction_source_id_is_deterministic() -> None:
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    prediction = normalize_enzymes_record(records[0])[0]

    first = gotenzymes_prediction_source_id(prediction)
    second = gotenzymes_prediction_source_id(prediction)

    assert first == second
    assert len(first) == 64  # sha256 hex digest


def test_gotenzymes_prediction_source_id_differs_for_different_parameter_types() -> None:
    data = json.loads(_fixture("enzymes_acc1.json"))
    records, _ = parse_enzymes_response(data)
    predictions = normalize_enzymes_record(records[0])

    ids = {gotenzymes_prediction_source_id(p) for p in predictions}
    assert len(ids) == len(predictions)
