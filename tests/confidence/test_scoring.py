"""Tests for ``app.confidence.scoring.assess_single_evidence_claim``."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.claim_generation.types import CandidateClaim, EntityKind
from app.confidence.policy import EntityResolutionQuality, OrganismAssessment, ScoringStatus
from app.confidence.scoring import assess_single_evidence_claim
from app.entity_resolution.types import MentionResolutionStatus
from app.extraction.types import Directness
from app.models.enums import EvidenceType, SourceType
from tests.confidence.helpers import (
    make_candidate,
    make_claim,
    make_conflicted_result,
    make_extraction,
    make_matched_result,
    make_mention_result,
    make_new_result,
    make_reference,
    make_unresolved_result,
)

# --- Evidence base score ----------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence_type", "expected_score"),
    [
        (EvidenceType.DIRECT_BIOCHEMICAL, 45),
        (EvidenceType.DIRECT_IN_VIVO, 40),
        (EvidenceType.GENETIC, 25),
        (EvidenceType.CURATED_DATABASE, 20),
        (EvidenceType.COMPUTATIONAL, 10),
        (EvidenceType.HOMOLOGY, 5),
        (EvidenceType.AUTHOR_HYPOTHESIS, 0),
    ],
)
def test_evidence_base_score_exact_for_every_authoritative_type(evidence_type, expected_score):
    claim = make_claim(evidence_type=evidence_type, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.evidence_base_score == expected_score
    assert result.scoring_status is ScoringStatus.SCORED_BASE


@pytest.mark.parametrize(
    "evidence_type",
    [
        EvidenceType.LOCALIZATION,
        EvidenceType.PROTEOMICS,
        EvidenceType.METABOLOMICS,
        EvidenceType.FLUXOMICS,
        EvidenceType.TRANSCRIPTOMICS,
        EvidenceType.STRUCTURAL,
        EvidenceType.REVIEW,
        EvidenceType.OTHER,
    ],
)
def test_unmapped_evidence_type_has_no_base_score(evidence_type):
    claim = make_claim(evidence_type=evidence_type)
    result = assess_single_evidence_claim(claim)
    assert result.evidence_base_score is None
    assert result.scoring_status is ScoringStatus.UNSCORED_EVIDENCE_TYPE
    assert f"EVIDENCE_UNSCORED_{evidence_type.value}" in result.reason_codes


def test_evidence_type_is_preserved_exactly():
    claim = make_claim(evidence_type=EvidenceType.GENETIC)
    result = assess_single_evidence_claim(claim)
    assert result.evidence_type is EvidenceType.GENETIC


# --- Directness: preserved exactly, no numeric modifier -------------------------


@pytest.mark.parametrize(
    "directness",
    [
        Directness.AUTHORS_OBSERVED,
        Directness.AUTHORS_INFERRED,
        Directness.AUTHORS_PROPOSED,
        Directness.AUTHORS_DISCUSSED,
        Directness.REVIEW_SUMMARIZES,
        Directness.DATABASE_ANNOTATES,
    ],
)
def test_every_directness_value_preserved_exactly(directness):
    claim = make_claim(directness=directness, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.directness is directness
    assert f"DIRECTNESS_{directness.value}" in result.reason_codes


def test_directness_carries_no_numeric_modifier():
    """Two claims that differ only in Directness must have identical evidence_base_score."""
    observed = assess_single_evidence_claim(
        make_claim(directness=Directness.AUTHORS_OBSERVED, organism_text=None)
    )
    proposed = assess_single_evidence_claim(
        make_claim(directness=Directness.AUTHORS_PROPOSED, organism_text=None)
    )
    assert observed.evidence_base_score == proposed.evidence_base_score == 45


def test_review_summarizes_preserved_categorically_not_penalized_numerically():
    result = assess_single_evidence_claim(
        make_claim(
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
            directness=Directness.REVIEW_SUMMARIZES,
            organism_text=None,
        )
    )
    assert result.directness is Directness.REVIEW_SUMMARIZES
    assert result.evidence_base_score == 45  # untouched by directness
    assert "DIRECTNESS_REVIEW_SUMMARIZES" in result.reason_codes


def test_database_annotates_preserved_categorically():
    result = assess_single_evidence_claim(
        make_claim(
            evidence_type=EvidenceType.CURATED_DATABASE,
            directness=Directness.DATABASE_ANNOTATES,
            organism_text=None,
        )
    )
    assert result.directness is Directness.DATABASE_ANNOTATES
    assert result.evidence_base_score == 20


# --- Subject resolution -------------------------------------------------------------


def test_subject_resolved_direct_matched_path():
    gene_id = uuid4()
    subject = make_reference(
        normalization_result=make_matched_result(matched_entity_id=gene_id), normalized_id=gene_id
    )
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.RESOLVED
    assert "SUBJECT_RESOLVED" in result.reason_codes


def test_subject_ambiguous_preserves_evidence_dimension():
    subject = make_reference(
        normalization_result=None,
        mention_resolution_result=make_mention_result(
            status=MentionResolutionStatus.AMBIGUOUS,
            candidates=(
                make_candidate(
                    source_record_identifier="S1",
                    normalization_result=make_matched_result(source_identifier="S1"),
                ),
            ),
        ),
    )
    claim = make_claim(
        subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, organism_text=None
    )
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.AMBIGUOUS
    assert result.evidence_base_score == 45  # evidence dimension entirely untouched
    assert "SUBJECT_AMBIGUOUS" in result.reason_codes


def test_subject_conflicted():
    subject = make_reference(normalization_result=make_conflicted_result())
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.CONFLICTED
    assert "SUBJECT_CONFLICTED" in result.reason_codes


def test_subject_unresolved():
    subject = make_reference(normalization_result=make_unresolved_result())
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.UNRESOLVED


def test_subject_no_candidate():
    mention_result = make_mention_result(status=MentionResolutionStatus.NO_CANDIDATE, candidates=())
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(
        subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, organism_text=None
    )
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.NO_CANDIDATE
    assert result.evidence_base_score == 45  # not converted to negative evidence


def test_subject_source_failure():
    mention_result = make_mention_result(
        status=MentionResolutionStatus.SOURCE_FAILURE,
        failed_source=SourceType.SGD,
        error_category="ConnectorNetworkError",
    )
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(
        subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, organism_text=None
    )
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.SOURCE_FAILURE
    assert result.evidence_base_score == 45  # not treated as counter-evidence
    assert "SUBJECT_SOURCE_FAILURE" in result.reason_codes


def test_subject_unsupported_no_lookup_available():
    subject = make_reference(normalization_result=None, mention_resolution_result=None)
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.UNSUPPORTED


def test_subject_unsupported_entity_kind_via_entity_resolution():
    mention_result = make_mention_result(status=MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND)
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.UNSUPPORTED


def test_subject_unknown_kind_still_computes_evidence_dimension():
    claim = make_claim(
        subject=make_reference(entity_kind=EntityKind.UNKNOWN, normalization_result=None)
    )
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.UNKNOWN_KIND
    assert result.evidence_base_score == 45
    assert "SUBJECT_UNKNOWN_KIND" in result.reason_codes


def test_subject_never_none_is_always_assessed():
    claim = make_claim()
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is not None
    assert any(code.startswith("SUBJECT_") for code in result.reason_codes)


def test_subject_does_not_select_a_candidate():
    """Structural: the module never reads candidate_entity_ids to pick one."""
    import inspect

    import app.confidence.scoring as scoring_module

    source = inspect.getsource(scoring_module)
    assert "candidate_entity_ids[0]" not in source
    assert ".candidate_entity_ids[" not in source


# --- VERIFIED_NEW -------------------------------------------------------------------


def test_verified_new_direct_path_not_penalized_for_not_being_persisted():
    subject = make_reference(normalization_result=make_new_result())
    claim = make_claim(
        subject=subject, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, organism_text=None
    )
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.VERIFIED_NEW
    assert result.evidence_base_score == 45
    assert "SUBJECT_VERIFIED_NEW" in result.reason_codes


def test_verified_new_via_entity_resolution_single_candidate():
    candidate = make_candidate(normalization_result=make_new_result(source_identifier="S000000001"))
    mention_result = make_mention_result(
        status=MentionResolutionStatus.NEW_CANDIDATE, candidates=(candidate,)
    )
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.VERIFIED_NEW


def test_verified_new_distinct_from_ambiguous():
    verified = assess_single_evidence_claim(
        make_claim(
            subject=make_reference(normalization_result=make_new_result()), organism_text=None
        )
    )
    ambiguous = assess_single_evidence_claim(
        make_claim(
            subject=make_reference(
                mention_resolution_result=make_mention_result(
                    status=MentionResolutionStatus.AMBIGUOUS,
                    candidates=(make_candidate(),),
                ),
                normalization_result=None,
            ),
            organism_text=None,
        )
    )
    assert verified.subject_resolution is EntityResolutionQuality.VERIFIED_NEW
    assert ambiguous.subject_resolution is EntityResolutionQuality.AMBIGUOUS
    assert verified.subject_resolution is not ambiguous.subject_resolution


def test_new_candidate_multiple_candidates_treated_as_ambiguous():
    candidate_a = make_candidate(
        source_record_identifier="S1", normalization_result=make_new_result(source_identifier="S1")
    )
    candidate_b = make_candidate(
        source_record_identifier="S2", normalization_result=make_new_result(source_identifier="S2")
    )
    mention_result = make_mention_result(
        status=MentionResolutionStatus.NEW_CANDIDATE, candidates=(candidate_a, candidate_b)
    )
    subject = make_reference(mention_resolution_result=mention_result, normalization_result=None)
    claim = make_claim(subject=subject, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.subject_resolution is EntityResolutionQuality.AMBIGUOUS


# --- Object resolution -------------------------------------------------------------


def test_object_entity_resolved_is_assessed():
    compound_id = uuid4()
    object_ref = make_reference(
        original_text="acyl-CoA",
        entity_kind=EntityKind.COMPOUND,
        normalization_result=make_matched_result(matched_entity_id=compound_id),
        normalized_id=compound_id,
    )
    claim = make_claim(object_=object_ref, object_text="acyl-CoA", organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.object_resolution is EntityResolutionQuality.RESOLVED
    assert "OBJECT_RESOLVED" in result.reason_codes


def test_object_entity_conflicted_is_assessed():
    object_ref = make_reference(
        original_text="acyl-CoA",
        entity_kind=EntityKind.COMPOUND,
        normalization_result=make_conflicted_result(),
    )
    claim = make_claim(object_=object_ref, object_text="acyl-CoA", organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.object_resolution is EntityResolutionQuality.CONFLICTED


def test_literal_value_claim_with_no_object_is_not_applicable():
    """``Km = 0.42 mM`` may legitimately have no object entity."""
    claim = make_claim(
        object_=None,
        object_text=None,
        value_text="0.42",
        value_numeric=Decimal("0.42"),
        value_unit="mM",
        organism_text=None,
    )
    result = assess_single_evidence_claim(claim)
    assert result.object_resolution is None
    assert not any(code.startswith("OBJECT_") for code in result.reason_codes)


def test_object_present_but_untyped_is_not_applicable():
    """An object mention exists but was never typed as an entity (UNKNOWN kind)."""
    untyped_object = make_reference(
        original_text="fabA transcription", entity_kind=EntityKind.UNKNOWN
    )
    claim = make_claim(object_=untyped_object, object_text="fabA transcription", organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.object_resolution is EntityResolutionQuality.NOT_APPLICABLE
    assert "OBJECT_NOT_APPLICABLE" in result.reason_codes


# --- Organism -----------------------------------------------------------------------


def test_organism_explicit_resolved():
    organism_id = uuid4()
    organism_ref = make_reference(
        original_text="Escherichia coli",
        entity_kind=EntityKind.ORGANISM,
        normalization_result=make_matched_result(matched_entity_id=organism_id),
        normalized_id=organism_id,
    )
    claim = make_claim(organism=organism_ref, organism_text="Escherichia coli")
    result = assess_single_evidence_claim(claim)
    assert result.organism_resolution is OrganismAssessment.EXPLICIT_RESOLVED
    assert "ORGANISM_EXPLICIT_RESOLVED" in result.reason_codes


def test_organism_not_stated():
    claim = make_claim(organism=None, organism_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.organism_resolution is OrganismAssessment.NOT_STATED
    assert "ORGANISM_NOT_STATED" in result.reason_codes


def test_organism_explicit_unresolved():
    organism_ref = make_reference(
        original_text="Escherichia coli", entity_kind=EntityKind.ORGANISM, normalization_result=None
    )
    claim = make_claim(organism=organism_ref, organism_text="Escherichia coli")
    result = assess_single_evidence_claim(claim)
    assert result.organism_resolution is OrganismAssessment.EXPLICIT_UNRESOLVED
    assert "ORGANISM_EXPLICIT_UNRESOLVED" in result.reason_codes


def test_organism_conflicted():
    organism_ref = make_reference(
        original_text="Escherichia coli",
        entity_kind=EntityKind.ORGANISM,
        normalization_result=make_conflicted_result(),
    )
    claim = make_claim(organism=organism_ref, organism_text="Escherichia coli")
    result = assess_single_evidence_claim(claim)
    assert result.organism_resolution is OrganismAssessment.CONFLICTED


def test_organism_source_failure():
    mention_result = make_mention_result(
        status=MentionResolutionStatus.SOURCE_FAILURE,
        entity_kind=EntityKind.ORGANISM,
        original_text="Escherichia coli",
        failed_source=SourceType.SGD,
        error_category="ConnectorNetworkError",
    )
    organism_ref = make_reference(
        original_text="Escherichia coli",
        entity_kind=EntityKind.ORGANISM,
        mention_resolution_result=mention_result,
        normalization_result=None,
    )
    claim = make_claim(organism=organism_ref, organism_text="Escherichia coli")
    result = assess_single_evidence_claim(claim)
    assert result.organism_resolution is OrganismAssessment.SOURCE_FAILURE


def test_organism_no_taxonomic_distance_modifier_exists():
    """Structural: no genus/eukaryote/bacterium taxonomic vocabulary exists."""
    import app.confidence.policy as policy_module

    source_names = dir(policy_module)
    for forbidden in ("genus", "eukaryote", "bacterium", "taxonomic"):
        assert not any(forbidden in name.lower() for name in source_names)


# --- Compartment ---------------------------------------------------------------------


def test_compartment_absent_is_none():
    claim = make_claim(compartment=None, compartment_text=None)
    result = assess_single_evidence_claim(claim)
    assert result.compartment_resolution is None
    assert not any(code.startswith("COMPARTMENT_") for code in result.reason_codes)


def test_compartment_explicit_state_preserved():
    compartment_id = uuid4()
    compartment_ref = make_reference(
        original_text="peroxisome",
        entity_kind=EntityKind.COMPARTMENT,
        normalization_result=make_matched_result(matched_entity_id=compartment_id),
        normalized_id=compartment_id,
    )
    claim = make_claim(
        compartment=compartment_ref, compartment_text="peroxisome", organism_text=None
    )
    result = assess_single_evidence_claim(claim)
    assert result.compartment_resolution is EntityResolutionQuality.RESOLVED
    assert "COMPARTMENT_RESOLVED" in result.reason_codes


# --- Negative claims ------------------------------------------------------------------


def test_negative_predicate_receives_no_special_handling():
    positive = assess_single_evidence_claim(make_claim(predicate="binds", organism_text=None))
    negative = assess_single_evidence_claim(
        make_claim(predicate="did not bind", organism_text=None)
    )
    assert positive.evidence_base_score == negative.evidence_base_score
    assert positive.subject_resolution == negative.subject_resolution


# --- Measurements ---------------------------------------------------------------------


def test_numeric_measurement_presence_does_not_alter_base_score():
    no_value = assess_single_evidence_claim(
        make_claim(object_text=None, value_text=None, organism_text=None)
    )
    with_value = assess_single_evidence_claim(
        make_claim(
            object_text=None,
            value_text="12.3",
            value_numeric=Decimal("12.3"),
            value_unit="nmol/min/mg",
            organism_text=None,
        )
    )
    assert no_value.evidence_base_score == with_value.evidence_base_score


# --- Determinism ------------------------------------------------------------------------


def test_repeated_assessment_is_identical():
    claim = make_claim()
    first = assess_single_evidence_claim(claim)
    second = assess_single_evidence_claim(claim)
    assert first == second


def test_assessment_does_not_mutate_claim():
    claim = make_claim()
    before = repr(claim)
    assess_single_evidence_claim(claim)
    assert repr(claim) == before


def test_assessment_does_not_mutate_subject_reference():
    gene_id = uuid4()
    subject = make_reference(
        normalization_result=make_matched_result(matched_entity_id=gene_id), normalized_id=gene_id
    )
    claim = make_claim(subject=subject)
    before = repr(subject)
    assess_single_evidence_claim(claim)
    assert repr(subject) == before


# --- No final confidence -------------------------------------------------------------


def test_result_has_no_final_score_or_confidence_class():
    claim = make_claim()
    result = assess_single_evidence_claim(claim)
    assert not hasattr(result, "score")
    assert not hasattr(result, "confidence_class")


def test_single_evidence_layer_exports_no_confidence_class_mapping():
    """Increment 17's own modules (policy/scoring/types) never map a score

    to a ConfidenceClass -- that concept was moved entirely to Increment
    18's aggregation layer (``app.confidence.aggregate_policy``), which
    the top-level ``app.confidence`` package re-exports for aggregate use
    only.
    """
    import app.confidence.policy as single_evidence_policy_module
    import app.confidence.scoring as scoring_module
    import app.confidence.types as types_module

    for module in (single_evidence_policy_module, scoring_module, types_module):
        assert not hasattr(module, "classify_confidence_class")
        assert not hasattr(module, "CONFIDENCE_CLASS_THRESHOLDS")


# --- No persistence / no network / no connectors -----------------------------------


def test_scoring_module_imports_no_database_session_or_connector():
    import inspect

    import app.confidence.scoring as scoring_module

    source = inspect.getsource(scoring_module)
    for forbidden in ("Session", "httpx", "ConnectorHttpClient", "requests.", "import connectors"):
        assert forbidden not in source


def test_confidence_package_never_imports_connectors_module():
    import app.confidence.policy as policy_module
    import app.confidence.scoring as scoring_module
    import app.confidence.types as types_module

    for module in (scoring_module, policy_module, types_module):
        assert not hasattr(module, "ConnectorHttpClient")


# --- Structural safety: single-evidence boundary ------------------------------------


def test_assessment_function_signature_takes_only_one_claim():
    import inspect

    signature = inspect.signature(assess_single_evidence_claim)
    params = list(signature.parameters)
    assert params == ["claim"]


def test_llm_only_hypothesis_cannot_masquerade_as_scored_evidence():
    """AUTHOR_HYPOTHESIS is the closest thing to an unvalidated speculative

    claim this schema permits -- it always reports no authoritative base
    score, for every Directness. A CandidateClaim can never be constructed
    without a genuine EvidenceExtraction at all (TypeError), so a truly
    LLM-only, ungrounded hypothesis is structurally impossible to assess
    in the first place.
    """
    for directness in Directness:
        claim = make_claim(
            evidence_type=EvidenceType.AUTHOR_HYPOTHESIS, directness=directness, organism_text=None
        )
        result = assess_single_evidence_claim(claim)
        assert result.evidence_base_score == 0
        assert result.scoring_status is ScoringStatus.SCORED_BASE

    with pytest.raises(TypeError):
        CandidateClaim(
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            evidence_extraction="not an EvidenceExtraction",  # type: ignore[arg-type]
            subject=make_reference(),
            predicate="activates",
            supporting_span=make_extraction().span,
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
            directness=Directness.AUTHORS_OBSERVED,
        )


def test_rejects_non_candidate_claim_input():
    with pytest.raises(TypeError):
        assess_single_evidence_claim("not a claim")  # type: ignore[arg-type]
