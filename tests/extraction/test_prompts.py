"""Tests for ``app.extraction.prompts``."""

from __future__ import annotations

import pytest

from app.extraction.errors import LLMFormattingError
from app.extraction.prompts import (
    EVIDENCE_EXTRACTION_PROMPT,
    candidate_statement_from_prompt_fields,
)
from app.extraction.types import Directness
from app.models.enums import EvidenceType


def _raw(**overrides) -> dict[str, str]:
    merged = {
        "SUBJECT": "FadD",
        "PREDICATE": "hydrolyzed",
        "OBJECT_OR_VALUE": "long-chain acyl-CoA esters",
        "EVIDENCE_TYPE": "DIRECT_BIOCHEMICAL",
        "DIRECTNESS": "AUTHORS_OBSERVED",
        "QUOTED_TEXT": "FadD hydrolyzed long-chain acyl-CoA esters in vitro.",
    } | overrides
    return merged


def test_parses_a_well_formed_payload():
    candidate = candidate_statement_from_prompt_fields(_raw())
    assert candidate.subject_text == "FadD"
    assert candidate.predicate_text == "hydrolyzed"
    assert candidate.object_text == "long-chain acyl-CoA esters"
    assert candidate.evidence_type is EvidenceType.DIRECT_BIOCHEMICAL
    assert candidate.directness is Directness.AUTHORS_OBSERVED


def test_not_stated_literal_becomes_none():
    candidate = candidate_statement_from_prompt_fields(_raw(QUALIFIER="NOT STATED"))
    assert candidate.qualifier_text is None


@pytest.mark.parametrize("missing_key", ["SUBJECT", "PREDICATE", "QUOTED_TEXT"])
def test_missing_required_field_raises_llm_formatting_error(missing_key):
    raw = _raw()
    del raw[missing_key]
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(raw)


def test_missing_evidence_type_raises_llm_formatting_error():
    raw = _raw()
    del raw["EVIDENCE_TYPE"]
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(raw)


def test_missing_directness_raises_llm_formatting_error():
    raw = _raw()
    del raw["DIRECTNESS"]
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(raw)


def test_unrecognized_evidence_type_value_raises_llm_formatting_error():
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(_raw(EVIDENCE_TYPE="TOTALLY_MADE_UP"))


def test_unrecognized_directness_value_raises_llm_formatting_error():
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(_raw(DIRECTNESS="VERY_SURE"))


def test_unrecognized_field_key_raises_llm_formatting_error():
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(_raw(SOME_MADE_UP_FIELD="x"))


@pytest.mark.parametrize("ignored_key", ["AUTHOR_CERTAINTY", "SOURCE_LOCATION"])
def test_deliberately_ignored_fields_do_not_raise(ignored_key):
    candidate = candidate_statement_from_prompt_fields(_raw(**{ignored_key: "some value"}))
    assert candidate.subject_text == "FadD"


def test_historical_alias_fields_map_correctly():
    candidate = candidate_statement_from_prompt_fields(
        _raw(
            EXPERIMENTAL_METHOD="spectrophotometric assay",
            CURATOR_SUMMARY="FadD shows acyl-CoA synthetase activity in vitro",
            EXPERIMENTAL_CONDITIONS="30C, pH 7.5",
            ORGANISM="Escherichia coli",
            STRAIN="K-12",
            COMPARTMENT="cytoplasm",
            QUALIFIER="in vitro",
        )
    )
    assert candidate.experimental_system == "spectrophotometric assay"
    assert candidate.normalized_text == "FadD shows acyl-CoA synthetase activity in vitro"
    assert candidate.notes == "30C, pH 7.5"
    assert candidate.organism_text == "Escherichia coli"
    assert candidate.strain_text == "K-12"
    assert candidate.compartment_text == "cytoplasm"
    assert candidate.qualifier_text == "in vitro"


def test_non_integer_offset_raises_llm_formatting_error():
    with pytest.raises(LLMFormattingError):
        candidate_statement_from_prompt_fields(_raw(CHARACTER_START="not-a-number"))


def test_integer_offset_fields_are_coerced_to_int():
    candidate = candidate_statement_from_prompt_fields(
        _raw(
            QUOTED_TEXT="FadD",
            CHARACTER_START="0",
            CHARACTER_END="4",
        )
    )
    assert candidate.character_start == 0
    assert candidate.character_end == 4


def test_content_validation_errors_propagate_unwrapped():
    """A payload that parses fine but fails CandidateStatement's own content

    validation (units without a value) must raise ExtractionValidationError,
    not LLMFormattingError -- the payload's *shape* was fine; its *content*
    was not.
    """
    from app.extraction.errors import ExtractionValidationError

    with pytest.raises(ExtractionValidationError):
        candidate_statement_from_prompt_fields(_raw(MEASUREMENT_UNITS="nmol/min/mg"))


# --- prompt text content -------------------------------------------------------


@pytest.mark.parametrize(
    "expected_substring",
    [
        "QUOTED_TEXT",
        "verbatim",
        "NOT STATED",
        "AUTHOR_HYPOTHESIS",
        "may",
        "might",
        "suggests",
        "could",
        "possibly",
        "appears to",
        "is consistent with",
        "Do not combine information",
        "Do not infer",
    ],
)
def test_prompt_requires_key_constraints(expected_substring):
    normalized = " ".join(EVIDENCE_EXTRACTION_PROMPT.split())
    assert expected_substring in normalized


def test_prompt_is_a_fixed_string_not_a_template():
    assert "{" not in EVIDENCE_EXTRACTION_PROMPT
    assert "}" not in EVIDENCE_EXTRACTION_PROMPT
