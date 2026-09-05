"""Tests for ``app.extraction.types``: self-validation of the data contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.extraction.errors import ExtractionValidationError, MalformedSpanError
from app.extraction.types import CandidateStatement, Directness, EvidenceExtraction, SourceSpan
from app.models.enums import EvidenceType, SourceType


def _span(**overrides) -> SourceSpan:
    merged = {
        "document_identifier": "PMID:1",
        "character_start": 0,
        "character_end": 5,
        "quoted_text": "hello",
    } | overrides
    return SourceSpan(**merged)


def _candidate(**overrides) -> CandidateStatement:
    merged = {
        "quoted_text": "FadD hydrolyzed esters.",
        "subject_text": "FadD",
        "predicate_text": "hydrolyzed",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": Directness.AUTHORS_OBSERVED,
    } | overrides
    return CandidateStatement(**merged)


def _extraction(**overrides) -> EvidenceExtraction:
    merged = {
        "source": SourceType.PUBMED,
        "source_identifier": "PMID:1",
        "span": _span(),
        "subject_text": "FadD",
        "predicate_text": "hydrolyzed",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": Directness.AUTHORS_OBSERVED,
    } | overrides
    return EvidenceExtraction(**merged)


# --- SourceSpan: required fields / offsets / consistent spans --------------


def test_span_requires_document_identifier():
    with pytest.raises(ExtractionValidationError):
        _span(document_identifier="")


def test_span_rejects_empty_quoted_text():
    with pytest.raises(ExtractionValidationError):
        _span(quoted_text="", character_start=0, character_end=0)


def test_span_rejects_negative_start():
    with pytest.raises(MalformedSpanError):
        _span(character_start=-1, character_end=4, quoted_text="hell")


def test_span_rejects_end_not_after_start():
    with pytest.raises(MalformedSpanError):
        _span(character_start=5, character_end=5, quoted_text="x")


def test_span_rejects_inconsistent_length():
    with pytest.raises(MalformedSpanError):
        _span(character_start=0, character_end=10, quoted_text="hello")  # length 5 != 10


def test_span_quoted_text_is_never_trimmed():
    span = _span(character_start=0, character_end=7, quoted_text=" hello ")
    assert span.quoted_text == " hello "


def test_span_optional_fields_default_none_and_blank_becomes_none():
    span = _span(section="  ", paragraph_index=None, sentence_index=None)
    assert span.section is None


def test_span_rejects_negative_paragraph_index():
    with pytest.raises(ExtractionValidationError):
        _span(paragraph_index=-1)


# --- CandidateStatement -----------------------------------------------------


def test_candidate_requires_subject_text():
    with pytest.raises(ExtractionValidationError):
        _candidate(subject_text="")


def test_candidate_requires_predicate_text():
    with pytest.raises(ExtractionValidationError):
        _candidate(predicate_text="   ")


def test_candidate_requires_quoted_text():
    with pytest.raises(ExtractionValidationError):
        _candidate(quoted_text="")


def test_candidate_rejects_wrong_evidence_type_type():
    with pytest.raises(TypeError):
        _candidate(evidence_type="DIRECT_BIOCHEMICAL")  # must be the enum, not a string


def test_candidate_rejects_wrong_directness_type():
    with pytest.raises(TypeError):
        _candidate(directness="AUTHORS_OBSERVED")


def test_candidate_rejects_units_without_value():
    with pytest.raises(ExtractionValidationError):
        _candidate(measurement_units="nmol/min/mg", measurement_value=None)


def test_candidate_allows_value_without_units():
    candidate = _candidate(measurement_value="3-fold", measurement_units=None)
    assert candidate.measurement_value == "3-fold"


def test_candidate_requires_both_offsets_or_neither():
    with pytest.raises(ValueError, match="both character_start and character_end"):
        _candidate(quoted_text="hi", character_start=0, character_end=None)


def test_candidate_offsets_must_match_quoted_text_length():
    with pytest.raises(MalformedSpanError):
        _candidate(quoted_text="hi", character_start=0, character_end=10)


def test_candidate_blank_optional_fields_become_none():
    candidate = _candidate(object_text="   ")
    assert candidate.object_text is None


# --- EvidenceExtraction ------------------------------------------------------


def test_extraction_requires_matching_document_identifier():
    mismatched_span = _span(document_identifier="PMID:999")
    with pytest.raises(ValueError, match="document_identifier"):
        _extraction(source_identifier="PMID:1", span=mismatched_span)


def test_extraction_rejects_non_span():
    with pytest.raises(TypeError):
        _extraction(span="not a span")


def test_extraction_requires_subject_and_predicate():
    with pytest.raises(ExtractionValidationError):
        _extraction(subject_text="")


def test_extraction_publication_id_defaults_to_none():
    extraction = _extraction()
    assert extraction.publication_id is None


def test_extraction_publication_id_may_be_explicitly_supplied():
    pub_id = uuid4()
    extraction = _extraction(publication_id=pub_id)
    assert extraction.publication_id == pub_id


def test_extraction_is_frozen():
    extraction = _extraction()
    with pytest.raises(AttributeError):
        extraction.subject_text = "something else"  # type: ignore[misc]


def test_extraction_rejects_measurement_units_without_value():
    with pytest.raises(ExtractionValidationError):
        _extraction(measurement_units="nmol/min/mg", measurement_value=None)
