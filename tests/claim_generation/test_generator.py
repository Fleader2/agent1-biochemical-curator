"""Tests for ``app.claim_generation.generator.generate_candidate_claims``."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.claim_generation.errors import ClaimGenerationError
from app.claim_generation.generator import generate_candidate_claims
from app.claim_generation.mapping import NormalizationLookups
from app.claim_generation.types import EntityKind, EntityTypingHint
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit
from app.entity_resolution.resolver import ConnectorBundle
from app.entity_resolution.types import MentionResolutionStatus
from app.extraction.types import Directness
from app.models.enums import EvidenceType
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.protein import ProteinCandidate
from app.normalization.types import NormalizationStatus
from tests.claim_generation.fakes import FakeGeneLookup, FakeOrganismLookup, FakeProteinLookup
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
from tests.entity_resolution.fakes import FakeSgdConnector, FakeUniProtConnector

ORGANISM_ID = uuid4()


def _sgd_locus(
    sgd_id="S000000001", standard_name="FadR", systematic_name="YHR123W"
) -> SgdLocusRecord:
    return SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name=systematic_name,
        standard_name=standard_name,
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw={},
    )


def _sgd_normalized(locus: SgdLocusRecord) -> SgdNormalizedRecord:
    return SgdNormalizedRecord(
        sgd_id=locus.sgd_id,
        systematic_name=locus.systematic_name,
        standard_name=locus.standard_name,
        description=locus.description,
        aliases=(),
        uniprot_id=locus.uniprot_id,
        external_links=(),
        raw=locus,
    )


def _organism_lookup(scientific_name: str = "Escherichia coli"):
    return FakeOrganismLookup(
        organisms=[
            OrganismCandidate(
                id=ORGANISM_ID,
                scientific_name=scientific_name,
                strain=None,
                ncbi_taxonomy_id=None,
                kegg_code=None,
                biocyc_id=None,
            )
        ]
    )


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


# --- Entity Resolution integration (Increment 16) --------------------------------


def test_without_connectors_behavior_is_unchanged():
    """Backward compatibility: omitting connectors reproduces the exact

    pre-Increment-16 result (bare symbol match stays AMBIGUOUS, never
    MATCHED, and mention_resolution_result is never populated).
    """
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
    lookups = NormalizationLookups(gene=lookup, organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE)],
    )
    assert claim.subject.mention_resolution_result is None
    assert claim.subject.normalization_result.status is NormalizationStatus.AMBIGUOUS


def test_gene_subject_prefers_entity_resolution_when_connectors_supplied():
    locus = _sgd_locus()
    normalized = _sgd_normalized(locus)
    connector = FakeSgdConnector(
        hits_by_query={
            "FadR": [
                SgdSearchHit(
                    sgd_id="S000000001",
                    systematic_name="YHR123W",
                    standard_name="FadR",
                    description=None,
                    aliases=(),
                )
            ]
        },
        locus_by_sgd_id={"S000000001": locus},
        normalized_by_sgd_id={"S000000001": normalized},
    )
    gene_id = uuid4()
    gene_lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id="S000000001",
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR123W",
                symbol="FadR",
                aliases=(),
                description=None,
            )
        ]
    )
    lookups = NormalizationLookups(gene=gene_lookup, organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE)],
        connectors=ConnectorBundle(sgd=connector),
    )
    assert claim.subject.mention_resolution_result is not None
    assert claim.subject.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert claim.subject.normalized_id == gene_id


def test_protein_subject_uses_uniprot_enrichment_end_to_end():
    """Key acceptance case: typed PROTEIN + resolved organism + UniProt

    connector produces a RESOLVED CandidateEntityReference with the full
    MentionResolutionResult preserved, and no Gene inference occurs.
    """
    entry = UniProtEntryRecord(
        primary_accession="P99999",
        entry_name="TEST1_ECOLI",
        entry_type="UniProtKB reviewed (Swiss-Prot)",
        secondary_accessions=(),
        recommended_name="Test-only regulatory protein",
        submitted_names=(),
        gene_names=("fadR",),
        organism_name="Escherichia coli",
        organism_taxonomy_id=511145,
        ec_numbers=(),
        sequence_length=239,
        cross_references=(),
        raw={},
    )
    normalized = UniProtProteinRecord(
        primary_accession=entry.primary_accession,
        entry_name=entry.entry_name,
        reviewed=True,
        protein_name=entry.recommended_name,
        gene_names=entry.gene_names,
        organism_name=entry.organism_name,
        organism_taxonomy_id=entry.organism_taxonomy_id,
        ec_numbers=entry.ec_numbers,
        sequence_length=entry.sequence_length,
        secondary_accessions=entry.secondary_accessions,
        cross_references=entry.cross_references,
        raw=entry,
    )
    connector = FakeUniProtConnector(
        hits_by_query={
            'FadR AND organism_name:"Escherichia coli"': [
                UniProtSearchHit(
                    primary_accession="P99999", entry_name="TEST1_ECOLI", reviewed=True
                )
            ]
        },
        entry_by_accession={"P99999": entry},
        normalized_by_accession={"P99999": normalized},
    )
    protein_id = uuid4()
    protein_lookup = FakeProteinLookup(
        proteins=[
            ProteinCandidate(
                id=protein_id,
                organism_id=ORGANISM_ID,
                uniprot_id="P99999",
                name="Test-only regulatory protein",
                gene_id=None,
                ec_number=None,
            )
        ]
    )
    lookups = NormalizationLookups(protein=protein_lookup, organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.PROTEIN)],
        connectors=ConnectorBundle(uniprot=connector),
    )
    assert claim.subject.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert claim.subject.normalized_id == protein_id
    [candidate] = claim.subject.mention_resolution_result.candidates
    assert candidate.normalization_input.gene_id is None  # no Gene inference


def test_protein_subject_missing_organism_stays_unresolved_not_direct():
    """ACTIVATION_EXTRACTION's organism_text resolves fine, but if organism

    normalization itself is unavailable (no OrganismLookup supplied), the
    UniProt path must report UNRESOLVED rather than silently falling back.
    """
    connector = FakeUniProtConnector()
    protein_lookup = FakeProteinLookup(proteins=[])
    lookups = NormalizationLookups(protein=protein_lookup)  # no organism lookup
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.PROTEIN)],
        connectors=ConnectorBundle(uniprot=connector),
    )
    assert claim.subject.mention_resolution_result.status is MentionResolutionStatus.UNRESOLVED
    assert claim.subject.normalized_id is None


def test_source_failure_preserved_in_claim_not_masked():
    class _FailingSgd:
        def search(self, query):
            from app.connectors.exceptions import ConnectorNetworkError

            raise ConnectorNetworkError("simulated timeout")

        def fetch(self, external_id):  # pragma: no cover - not reached
            return None

        def normalize(self, raw):  # pragma: no cover - not reached
            raise AssertionError

    lookups = NormalizationLookups(gene=FakeGeneLookup(genes=[]), organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE)],
        connectors=ConnectorBundle(sgd=_FailingSgd()),
    )
    assert claim.subject.mention_resolution_result.status is MentionResolutionStatus.SOURCE_FAILURE
    assert claim.subject.normalized_id is None


def test_organism_reference_never_uses_entity_resolution():
    """Even with a full connectors bundle, organism resolution is always

    the pre-existing direct-normalization path (no dedicated organism
    enrichment connector exists -- docs/12_entity_resolution_architecture.md
    §11).
    """
    lookups = NormalizationLookups(organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        connectors=ConnectorBundle(sgd=FakeSgdConnector(), uniprot=FakeUniProtConnector()),
    )
    assert claim.organism.mention_resolution_result is None
    assert claim.organism.normalized_id == ORGANISM_ID


def test_unsupported_kind_connector_bundle_falls_back_to_direct_normalization():
    """A connectors bundle missing the specific connector a kind needs

    falls back to direct bare-text normalization for that kind, exactly
    as if no connectors had been supplied at all.
    """
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadR",
                aliases=(),
                description=None,
            )
        ]
    )
    lookups = NormalizationLookups(gene=lookup, organism=_organism_lookup())
    [claim] = generate_candidate_claims(
        [ACTIVATION_EXTRACTION],
        lookups=lookups,
        typing_hints=[EntityTypingHint(subject_kind=EntityKind.GENE)],
        connectors=ConnectorBundle(uniprot=FakeUniProtConnector()),  # no sgd connector
    )
    assert claim.subject.mention_resolution_result is None
    assert claim.subject.normalization_result.status is NormalizationStatus.AMBIGUOUS


def test_no_persistence_confidence_or_gene_protein_inference_with_connectors():
    """Structural safety check on the Increment 16 integration surface."""
    import inspect

    import app.claim_generation.generator as generator_module
    import app.claim_generation.mapping as mapping_module

    for module in (generator_module, mapping_module):
        source = inspect.getsource(module)
        assert "confidence_score" not in source
        assert "confidence_class" not in source
        assert "Session" not in source
        assert "ExternalRecord" not in source
        assert "SourceCrossReference" not in source
