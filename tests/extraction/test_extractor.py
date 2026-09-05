"""Tests for ``app.extraction.extractor.extract_evidence``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.extraction.errors import GroundingError, UnsupportedInputError
from app.extraction.extractor import extract_evidence
from app.extraction.types import CandidateStatement, Directness
from app.models.enums import EvidenceType, SourceType
from tests.extraction.fixtures import (
    AMBIGUOUS_WORDING_CANDIDATE,
    AMBIGUOUS_WORDING_TEXT,
    COMPARTMENT_CANDIDATE,
    COMPARTMENT_TEXT,
    FIGURE_REFERENCE_CANDIDATE,
    FIGURE_REFERENCE_TEXT,
    MEASUREMENT_CANDIDATE,
    MEASUREMENT_TEXT,
    MISSING_ORGANISM_CANDIDATE,
    MISSING_ORGANISM_TEXT,
    MULTIPLE_FINDINGS_CANDIDATES,
    MULTIPLE_FINDINGS_TEXT,
    MUTATION_CANDIDATE,
    MUTATION_TEXT,
    NEGATIVE_RESULT_CANDIDATE,
    NEGATIVE_RESULT_TEXT,
    POSITIVE_FINDING_CANDIDATE,
    POSITIVE_FINDING_TEXT,
    REVIEW_DISCUSSION_CANDIDATE,
    REVIEW_DISCUSSION_TEXT,
)

del Decimal  # imported for readers checking measurement fields are strings, not Decimal


# --- basic input validation --------------------------------------------------


def test_rejects_empty_text():
    with pytest.raises(UnsupportedInputError):
        extract_evidence(
            source=SourceType.PUBMED, source_identifier="PMID:1", text="   ", candidates=[]
        )


def test_rejects_empty_source_identifier():
    with pytest.raises(UnsupportedInputError):
        extract_evidence(
            source=SourceType.PUBMED, source_identifier="", text="some text", candidates=[]
        )


def test_rejects_non_candidate_items():
    with pytest.raises(UnsupportedInputError):
        extract_evidence(
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            text="some text",
            candidates=[{"subject": "not a CandidateStatement"}],
        )


def test_empty_candidates_yields_empty_list():
    assert (
        extract_evidence(
            source=SourceType.PUBMED, source_identifier="PMID:1", text="some text", candidates=[]
        )
        == []
    )


# --- grounding ---------------------------------------------------------------


def test_positive_finding_grounds_successfully():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=POSITIVE_FINDING_TEXT,
        candidates=[POSITIVE_FINDING_CANDIDATE],
    )
    assert extraction.span.quoted_text == POSITIVE_FINDING_CANDIDATE.quoted_text
    start, end = extraction.span.character_start, extraction.span.character_end
    assert POSITIVE_FINDING_TEXT[start:end] == extraction.span.quoted_text


def test_grounding_fails_when_quote_not_present():
    bogus = CandidateStatement(
        quoted_text="this text does not appear anywhere",
        subject_text="X",
        predicate_text="does",
        evidence_type=EvidenceType.OTHER,
        directness=Directness.AUTHORS_OBSERVED,
    )
    with pytest.raises(GroundingError):
        extract_evidence(
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            text=POSITIVE_FINDING_TEXT,
            candidates=[bogus],
        )


def test_grounding_fails_when_quote_is_ambiguous_without_explicit_offsets():
    text = "FadD acted on ester A. Later, FadD acted on ester A again in a repeat trial."
    ambiguous = CandidateStatement(
        quoted_text="FadD acted on ester A",
        subject_text="FadD",
        predicate_text="acted on",
        object_text="ester A",
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        directness=Directness.AUTHORS_OBSERVED,
    )
    with pytest.raises(GroundingError, match="appears"):
        extract_evidence(
            source=SourceType.PUBMED, source_identifier="PMID:1", text=text, candidates=[ambiguous]
        )


def test_explicit_offsets_disambiguate_repeated_quote():
    text = "FadD acted on ester A. Later, FadD acted on ester A again in a repeat trial."
    second_occurrence_start = text.rindex("FadD acted on ester A")
    quoted = "FadD acted on ester A"
    candidate = CandidateStatement(
        quoted_text=quoted,
        subject_text="FadD",
        predicate_text="acted on",
        object_text="ester A",
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        directness=Directness.AUTHORS_OBSERVED,
        character_start=second_occurrence_start,
        character_end=second_occurrence_start + len(quoted),
    )
    [extraction] = extract_evidence(
        source=SourceType.PUBMED, source_identifier="PMID:1", text=text, candidates=[candidate]
    )
    assert extraction.span.character_start == second_occurrence_start


def test_explicit_offsets_that_do_not_match_quoted_text_are_rejected():
    # Same length as the quote (21 chars) so CandidateStatement's own
    # text-independent length check passes -- the mismatch this test wants
    # to exercise is ground_candidate's real-text comparison, which must
    # fail because the source text at [0:21] does not actually read "FadD
    # acted on ester B" (it reads "...ester A").
    text = "FadD acted on ester A."
    candidate = CandidateStatement(
        quoted_text="FadD acted on ester B",
        subject_text="FadD",
        predicate_text="acted on",
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        directness=Directness.AUTHORS_OBSERVED,
        character_start=0,
        character_end=21,
    )
    with pytest.raises(GroundingError):
        extract_evidence(
            source=SourceType.PUBMED, source_identifier="PMID:1", text=text, candidates=[candidate]
        )


def test_one_span_per_extraction_no_merged_passages():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=POSITIVE_FINDING_TEXT,
        candidates=[POSITIVE_FINDING_CANDIDATE],
    )
    # Structural guarantee: exactly one SourceSpan object, not a list of spans.
    assert not isinstance(extraction.span, (list, tuple))


def test_quotation_preserved_exactly_including_punctuation():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MEASUREMENT_TEXT,
        candidates=[MEASUREMENT_CANDIDATE],
    )
    assert extraction.span.quoted_text == MEASUREMENT_TEXT


# --- statement decomposition --------------------------------------------------


def test_subject_predicate_object_qualifier_extracted_separately():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=POSITIVE_FINDING_TEXT,
        candidates=[POSITIVE_FINDING_CANDIDATE],
    )
    assert extraction.subject_text == "FadD"
    assert extraction.predicate_text == "hydrolyzed"
    assert extraction.object_text == "long-chain acyl-CoA esters"
    assert extraction.qualifier_text == "in vitro"
    # Decomposed, not concatenated back into one sentence:
    assert extraction.subject_text != POSITIVE_FINDING_TEXT


# --- observation vs interpretation -------------------------------------------


def test_observation_and_interpretation_are_distinguished_by_directness():
    [observed] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=POSITIVE_FINDING_TEXT,
        candidates=[POSITIVE_FINDING_CANDIDATE],
    )
    [discussed] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:2",
        text=REVIEW_DISCUSSION_TEXT,
        candidates=[REVIEW_DISCUSSION_CANDIDATE],
    )
    assert observed.directness is Directness.AUTHORS_OBSERVED
    assert discussed.directness is Directness.REVIEW_SUMMARIZES
    assert observed.directness != discussed.directness


# --- measurements --------------------------------------------------------------


def test_measurement_fields_preserved_literally_no_conversion():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MEASUREMENT_TEXT,
        candidates=[MEASUREMENT_CANDIDATE],
    )
    assert extraction.measurement_value == "12.3 +/- 0.4"
    assert extraction.measurement_units == "nmol/min/mg"
    assert extraction.statistical_support == "p < 0.01, n = 3"
    assert isinstance(extraction.measurement_value, str)  # never coerced to float/Decimal


# --- context: organism, strain, mutation, assay, compartment -----------------


def test_mutation_context_captured():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MUTATION_TEXT,
        candidates=[MUTATION_CANDIDATE],
    )
    assert extraction.perturbation == "fadD1 point mutation"


def test_compartment_context_captured():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=COMPARTMENT_TEXT,
        candidates=[COMPARTMENT_CANDIDATE],
    )
    assert extraction.compartment_text == "peroxisomal membrane"


def test_missing_organism_is_left_none_not_guessed():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MISSING_ORGANISM_TEXT,
        candidates=[MISSING_ORGANISM_CANDIDATE],
    )
    assert extraction.organism_text is None


def test_negative_result_is_a_normal_extraction_not_a_special_case():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=NEGATIVE_RESULT_TEXT,
        candidates=[NEGATIVE_RESULT_CANDIDATE],
    )
    assert "No detectable" in extraction.span.quoted_text
    assert extraction.evidence_type is EvidenceType.GENETIC


# --- figures/tables ------------------------------------------------------------


def test_figure_reference_captured():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=FIGURE_REFERENCE_TEXT,
        candidates=[FIGURE_REFERENCE_CANDIDATE],
    )
    assert extraction.figure_reference == "Figure 3B"


# --- multiple statements -------------------------------------------------------


def test_one_paragraph_yields_multiple_extractions_in_source_order():
    extractions = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MULTIPLE_FINDINGS_TEXT,
        candidates=list(reversed(MULTIPLE_FINDINGS_CANDIDATES)),  # supplied out of source order
    )
    assert len(extractions) == 2
    assert extractions[0].subject_text == "FadD"
    assert extractions[1].subject_text == "FadL"
    assert extractions[0].span.character_start < extractions[1].span.character_start


# --- ambiguity preserved --------------------------------------------------------


def test_unresolved_pronoun_ambiguity_is_preserved_not_resolved():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=AMBIGUOUS_WORDING_TEXT,
        candidates=[AMBIGUOUS_WORDING_CANDIDATE],
    )
    assert extraction.subject_text == "it"  # pronoun left unresolved, not guessed as FadD or FadK
    assert "ambiguous" in (extraction.notes or "").lower()


# --- determinism -----------------------------------------------------------------


def test_repeated_extraction_is_identical():
    first = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MULTIPLE_FINDINGS_TEXT,
        candidates=list(MULTIPLE_FINDINGS_CANDIDATES),
    )
    second = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MULTIPLE_FINDINGS_TEXT,
        candidates=list(MULTIPLE_FINDINGS_CANDIDATES),
    )
    assert first == second


def test_output_order_depends_only_on_source_position_not_input_order():
    forward = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MULTIPLE_FINDINGS_TEXT,
        candidates=list(MULTIPLE_FINDINGS_CANDIDATES),
    )
    backward = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=MULTIPLE_FINDINGS_TEXT,
        candidates=list(reversed(MULTIPLE_FINDINGS_CANDIDATES)),
    )
    assert [e.subject_text for e in forward] == [e.subject_text for e in backward]


# --- no inference: structural guarantee ----------------------------------------


def test_evidence_extraction_has_no_normalized_entity_uuid_fields():
    """Verify no field on EvidenceExtraction could hold a fabricated/normalized

    entity identifier (gene id, protein id, compound id, reaction id, EC
    number, pathway) -- only free text and the one explicitly-optional,
    always-None-from-this-package publication_id.
    """
    import dataclasses

    from app.extraction.types import EvidenceExtraction

    field_names = {f.name for f in dataclasses.fields(EvidenceExtraction)}
    forbidden = {
        "gene_id",
        "protein_id",
        "compound_id",
        "reaction_id",
        "organism_id",
        "compartment_id",
        "ec_number",
        "pathway_id",
    }
    assert field_names.isdisjoint(forbidden)


def test_extraction_never_invents_a_publication_id():
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        text=POSITIVE_FINDING_TEXT,
        candidates=[POSITIVE_FINDING_CANDIDATE],
    )
    assert extraction.publication_id is None
