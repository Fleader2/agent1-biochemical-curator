"""Tests for ``app.entity_resolution.types``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.claim_generation.types import EntityKind
from app.entity_resolution.types import (
    EntityMention,
    IdentifierCandidate,
    MentionResolutionResult,
    MentionResolutionStatus,
)
from app.models.enums import SourceType
from app.normalization.gene import GeneIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from tests.claim_generation.fixtures import ACTIVATION_EXTRACTION

ORGANISM_ID = uuid4()


def _mention(**overrides) -> EntityMention:
    merged = {
        "original_text": "ACC1",
        "entity_kind": EntityKind.GENE,
        "source_context": SourceType.PUBMED,
        "source_context_identifier": "PMID:1",
    } | overrides
    return EntityMention(**merged)


def _gene_identity(**overrides) -> GeneIdentity:
    merged = {
        "source": SourceType.SGD,
        "source_identifier": "S000000001",
        "sgd_id": "S000000001",
    } | overrides
    return GeneIdentity(**merged)


def _normalization_result(**overrides) -> NormalizationResult:
    merged = {
        "status": NormalizationStatus.MATCHED,
        "source": SourceType.SGD,
        "source_identifier": "S000000001",
        "entity_type": "gene",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "matched_entity_id": uuid4(),
    } | overrides
    return NormalizationResult(**merged)


def _candidate(**overrides) -> IdentifierCandidate:
    merged = {
        "entity_kind": EntityKind.GENE,
        "source": SourceType.SGD,
        "source_identifier": "S000000001",
        "original_mention": "ACC1",
        "search_term": "ACC1",
        "source_record_identifier": "S000000001",
        "normalization_input": _gene_identity(),
        "normalization_result": _normalization_result(),
    } | overrides
    return IdentifierCandidate(**merged)


# --- EntityMention -------------------------------------------------------------


def test_mention_requires_non_empty_text():
    with pytest.raises(ValueError, match="original_text"):
        _mention(original_text="")


def test_mention_requires_source_context_identifier():
    with pytest.raises(ValueError, match="source_context_identifier"):
        _mention(source_context_identifier="")


def test_mention_unknown_kind_is_a_valid_construction():
    mention = _mention(entity_kind=EntityKind.UNKNOWN)
    assert mention.entity_kind is EntityKind.UNKNOWN


def test_mention_organism_context_preserved():
    mention = _mention(organism_context_text="Saccharomyces cerevisiae", organism_id=ORGANISM_ID)
    assert mention.organism_context_text == "Saccharomyces cerevisiae"
    assert mention.organism_id == ORGANISM_ID


def test_mention_evidence_extraction_must_agree_with_source_context():
    with pytest.raises(ValueError, match="source_context"):
        EntityMention(
            original_text="FadR",
            entity_kind=EntityKind.GENE,
            source_context=SourceType.KEGG,  # disagrees with ACTIVATION_EXTRACTION.source
            source_context_identifier=ACTIVATION_EXTRACTION.source_identifier,
            evidence_extraction=ACTIVATION_EXTRACTION,
        )


def test_mention_evidence_extraction_consistent_is_accepted():
    mention = EntityMention(
        original_text="FadR",
        entity_kind=EntityKind.GENE,
        source_context=ACTIVATION_EXTRACTION.source,
        source_context_identifier=ACTIVATION_EXTRACTION.source_identifier,
        evidence_extraction=ACTIVATION_EXTRACTION,
    )
    assert mention.evidence_extraction is ACTIVATION_EXTRACTION


# --- IdentifierCandidate ---------------------------------------------------------


def test_candidate_requires_matching_identity_source():
    with pytest.raises(ValueError, match=r"normalization_input\.source"):
        _candidate(source=SourceType.KEGG)


def test_candidate_requires_matching_identity_source_identifier():
    with pytest.raises(ValueError, match=r"normalization_input\.source_identifier"):
        _candidate(source_identifier="S000000999")


def test_candidate_requires_matching_result_source_identifier():
    mismatched_result = _normalization_result(source_identifier="S000000999")
    with pytest.raises(ValueError, match=r"normalization_result\.source_identifier"):
        _candidate(normalization_result=mismatched_result)


def test_candidate_rejects_non_identity_normalization_input():
    with pytest.raises(TypeError):
        _candidate(normalization_input="not an identity")


def test_candidate_valid_construction():
    candidate = _candidate()
    assert candidate.entity_kind is EntityKind.GENE
    assert candidate.source_record_identifier == "S000000001"


# --- MentionResolutionResult -----------------------------------------------------


def test_result_resolved_requires_matching_entity_id():
    matched_id = uuid4()
    candidate = _candidate(normalization_result=_normalization_result(matched_entity_id=matched_id))
    with pytest.raises(ValueError, match="RESOLVED"):
        MentionResolutionResult(
            mention=_mention(),
            status=MentionResolutionStatus.RESOLVED,
            candidates=(candidate,),
            resolved_entity_id=uuid4(),  # does not match
        )


def test_result_resolved_accepts_agreeing_id():
    matched_id = uuid4()
    candidate = _candidate(normalization_result=_normalization_result(matched_entity_id=matched_id))
    result = MentionResolutionResult(
        mention=_mention(),
        status=MentionResolutionStatus.RESOLVED,
        candidates=(candidate,),
        resolved_entity_id=matched_id,
    )
    assert result.resolved_entity_id == matched_id


def test_result_non_resolved_rejects_entity_id():
    with pytest.raises(ValueError, match="resolved_entity_id"):
        MentionResolutionResult(
            mention=_mention(),
            status=MentionResolutionStatus.NO_CANDIDATE,
            resolved_entity_id=uuid4(),
        )


def test_result_source_failure_requires_failed_source_and_category():
    with pytest.raises(ValueError, match="failed_source"):
        MentionResolutionResult(mention=_mention(), status=MentionResolutionStatus.SOURCE_FAILURE)


def test_result_source_failure_valid():
    result = MentionResolutionResult(
        mention=_mention(),
        status=MentionResolutionStatus.SOURCE_FAILURE,
        failed_source=SourceType.SGD,
        error_category="ConnectorNetworkError",
    )
    assert result.failed_source is SourceType.SGD


def test_result_non_source_failure_rejects_failed_source():
    with pytest.raises(ValueError, match="failed_source"):
        MentionResolutionResult(
            mention=_mention(),
            status=MentionResolutionStatus.NO_CANDIDATE,
            failed_source=SourceType.SGD,
        )
