"""Tests for ``app.claim_generation.generator.generate_candidate_claims``."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.claim_generation.errors import ClaimGenerationError
from app.claim_generation.generator import generate_candidate_claims
from app.claim_generation.mapping import NormalizationLookups
from app.claim_generation.types import EntityKind, EntityTypingHint
from app.extraction.types import Directness
from app.models.enums import EvidenceType
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.types import NormalizationStatus
from tests.claim_generation.fakes import FakeGeneLookup, FakeOrganismLookup
from tests.claim_generation.fixtures import (
    ACTIVATION_EXTRACTION,
    AMBIGUOUS_ENTITY_EXTRACTION,
    COMPOUND_MEASUREMENT_EXTRACTION,
    INHIBITION_EXTRACTION,
    LITERAL_VALUE_EXTRACTION,
    LOCALIZATION_EXTRACTION,
    MISSING_OBJECT_EXTRACTION,
    MISSING_ORGANISM_EXTRACTION,
    MULTIPLE_PREDICATES_EXTRACTIONS,
    NEGATIVE_FINDING_EXTRACTION,
    NUMERIC_VALUE_EXTRACTION,
    REVIEW_EXTRACTION,
)

ORGANISM_ID = uuid4()


# --- basic input validation --------------------------------------------------


def test_rejects_non_sequence_input():
    with pytest.raises(ClaimGenerationError):
        generate_candidate_claims("not a sequence")  # type: ignore[arg-type]


def test_rejects_non_extraction_items():
    with pytest.raises(ClaimGenerationError):
        generate_candidate_claims([{"not": "an extraction"}])  # type: ignore[list-item]


def test_typing_hints_length_mismatch_raises():
    with pytest.raises(ClaimGenerationError):
        generate_candidate_claims([ACTIVATION_EXTRACTION], typing_hints=[])


def test_empty_extractions_yields_empty_list():
    assert generate_candidate_claims([]) == []


# --- default (no lookups) behavior -------------------------------------------


def test_no_lookups_leaves_everything_unresolved():
    [claim] = generate_candidate_claims([ACTIVATION_EXTRACTION])
    assert claim.subject.normalization_result is None
    assert claim.subject.normalized_id is None
    assert claim.subject.entity_kind is EntityKind.UNKNOWN  # default hint


# --- predicate / grounding preservation --------------------------------------


def test_predicate_preserved_literally():
    [claim] = generate_candidate_claims([ACTIVATION_EXTRACTION])
    assert claim.predicate == "activates"

    [inhibition_claim] = generate_candidate_claims([INHIBITION_EXTRACTION])
    assert inhibition_claim.predicate == "inhibits"


def test_grounding_preserved():
    [claim] = generate_candidate_claims([ACTIVATION_EXTRACTION])
    assert claim.supporting_span == ACTIVATION_EXTRACTION.span
    assert claim.evidence_extraction is ACTIVATION_EXTRACTION
    assert claim.source == ACTIVATION_EXTRACTION.source
    assert claim.source_identifier == ACTIVATION_EXTRACTION.source_identifier


def test_directness_carried_unchanged():
    [claim] = generate_candidate_claims([REVIEW_EXTRACTION])
    assert claim.directness == REVIEW_EXTRACTION.directness == Directness.REVIEW_SUMMARIZES


# --- negative statements -------------------------------------------------------


def test_negative_statement_preserved_not_converted_to_positive():
    [claim] = generate_candidate_claims([NEGATIVE_FINDING_EXTRACTION])
    assert claim.predicate == "did not bind"
    assert "not" in claim.predicate


# --- review handling -----------------------------------------------------------


def test_review_statement_still_generates_a_claim_with_review_directness():
    [claim] = generate_candidate_claims([REVIEW_EXTRACTION])
    assert claim.evidence_type is EvidenceType.REVIEW
    assert claim.directness is Directness.REVIEW_SUMMARIZES


# --- measurements ----------------------------------------------------------------


def test_numeric_value_parsed_and_original_text_preserved():
    [claim] = generate_candidate_claims([NUMERIC_VALUE_EXTRACTION])
    assert claim.value_numeric == Decimal("12.3")
    assert claim.value_text == "12.3"
    assert claim.value_unit == "nmol/min/mg"
    assert claim.object is None


def test_literal_non_numeric_value_kept_as_text_only():
    [claim] = generate_candidate_claims([LITERAL_VALUE_EXTRACTION])
    assert claim.value_text == "5.4-fold"
    assert claim.value_numeric is None


def test_compound_measurement_has_no_object_entity():
    [claim] = generate_candidate_claims([COMPOUND_MEASUREMENT_EXTRACTION])
    assert claim.object is None
    assert claim.value_numeric == Decimal("3.2")
    assert claim.value_unit == "mM"


# --- missing organism / missing object ------------------------------------------


def test_missing_organism_leaves_organism_field_none():
    [claim] = generate_candidate_claims([MISSING_ORGANISM_EXTRACTION])
    assert claim.organism is None


def test_missing_object_leaves_object_field_none():
    [claim] = generate_candidate_claims([MISSING_OBJECT_EXTRACTION])
    assert claim.object is None
    assert claim.subject.original_text == "FadD"


# --- localization / compartment -------------------------------------------------


def test_localization_captures_compartment_reference():
    [claim] = generate_candidate_claims([LOCALIZATION_EXTRACTION])
    assert claim.compartment is not None
    assert claim.compartment.original_text == "peroxisomal membrane"
    assert claim.compartment.entity_kind is EntityKind.COMPARTMENT


# --- multiple claims -------------------------------------------------------------


def test_multiple_extractions_yield_multiple_claims_in_order():
    claims = generate_candidate_claims(list(MULTIPLE_PREDICATES_EXTRACTIONS))
    assert len(claims) == 2
    assert claims[0].predicate == "activates"
    assert claims[0].object.original_text == "fabA"
    assert claims[1].predicate == "inhibits"
    assert claims[1].object.original_text == "fabB"


# --- ambiguity ---------------------------------------------------------------------


def test_ambiguous_subject_normalization_is_preserved_not_resolved():
    # ACTIVATION_EXTRACTION.subject_text is "FadR" -- candidates must share
    # that exact symbol to be surfaced as ambiguous.
    gene_a, gene_b = uuid4(), uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_a,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadR",
                aliases=(),
                description=None,
            ),
            GeneCandidate(
                id=gene_b,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadR",
                aliases=(),
                description=None,
            ),
        ]
    )
    organism_lookup = FakeOrganismLookup(
        organisms=[
            OrganismCandidate(
                id=ORGANISM_ID,
                scientific_name="Escherichia coli",
                strain=None,
                ncbi_taxonomy_id=None,
                kegg_code=None,
                biocyc_id=None,
            )
        ]
    )
    lookups = NormalizationLookups(gene=lookup, organism=organism_lookup)
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE)],
    )
    assert claim.subject.normalization_result.status is NormalizationStatus.AMBIGUOUS
    assert claim.subject.normalized_id is None
    assert len(claim.subject.normalization_result.candidate_entity_ids) == 2


def test_unresolved_normalization_preserved():
    [claim] = generate_candidate_claims([AMBIGUOUS_ENTITY_EXTRACTION])
    assert claim.subject.entity_kind is EntityKind.UNKNOWN
    assert claim.subject.normalization_result is None


# --- typing hints applied per extraction ----------------------------------------


def test_typing_hints_apply_positionally():
    claims = generate_candidate_claims(
        [ACTIVATION_EXTRACTION, INHIBITION_EXTRACTION],
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE), None],
    )
    assert claims[0].subject.entity_kind is EntityKind.GENE
    assert claims[1].subject.entity_kind is EntityKind.UNKNOWN


# --- determinism -----------------------------------------------------------------


def test_repeated_generation_is_identical():
    first = generate_candidate_claims([ACTIVATION_EXTRACTION, INHIBITION_EXTRACTION])
    second = generate_candidate_claims([ACTIVATION_EXTRACTION, INHIBITION_EXTRACTION])
    assert first == second


def test_output_order_mirrors_input_order():
    claims = generate_candidate_claims([INHIBITION_EXTRACTION, ACTIVATION_EXTRACTION])
    assert claims[0].predicate == "inhibits"
    assert claims[1].predicate == "activates"


# --- no persistence / no confidence / no UUID invention -------------------------


def test_no_persistence_or_confidence_fields_exist():
    import dataclasses

    from app.claim_generation.types import CandidateClaim

    field_names = {f.name for f in dataclasses.fields(CandidateClaim)}
    forbidden = {
        "claim_id",
        "evidence_id",
        "confidence_score",
        "confidence_class",
        "status",
        "persistence_status",
    }
    assert field_names.isdisjoint(forbidden)


def test_no_uuid_is_invented_for_unresolved_entities():
    [claim] = generate_candidate_claims([ACTIVATION_EXTRACTION])
    assert claim.subject.normalized_id is None
    assert claim.object is None or claim.object.normalized_id is None
    assert claim.organism is None or claim.organism.normalized_id is None


def test_generate_candidate_claims_never_touches_a_database():
    """Structural check: the generator module imports no SQLAlchemy Session type."""
    import inspect

    import app.claim_generation.generator as generator_module

    source = inspect.getsource(generator_module)
    assert "Session" not in source
    assert "session" not in source
