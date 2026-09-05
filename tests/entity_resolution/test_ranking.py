"""Tests for ``app.entity_resolution.ranking``."""

from __future__ import annotations

from uuid import uuid4

from app.claim_generation.types import EntityKind
from app.entity_resolution.ranking import classify_outcome, sort_candidates
from app.entity_resolution.types import IdentifierCandidate, MentionResolutionStatus
from app.models.enums import SourceType
from app.normalization.gene import GeneIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus


def _candidate(
    source_record_identifier: str, status: NormalizationStatus, **overrides
) -> IdentifierCandidate:
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier=source_record_identifier,
        sgd_id=source_record_identifier,
    )
    result_kwargs = {
        "status": status,
        "source": SourceType.SGD,
        "source_identifier": source_record_identifier,
        "entity_type": "gene",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    }
    if status is NormalizationStatus.MATCHED:
        result_kwargs["matched_entity_id"] = overrides.pop("matched_entity_id", uuid4())
    elif status is NormalizationStatus.AMBIGUOUS:
        result_kwargs["candidate_entity_ids"] = overrides.pop("candidate_entity_ids", (uuid4(),))
    elif status is NormalizationStatus.CONFLICTED:
        result_kwargs["matched_entity_id"] = overrides.pop("matched_entity_id", uuid4())
    result = NormalizationResult(**result_kwargs)
    merged = {
        "entity_kind": EntityKind.GENE,
        "source": SourceType.SGD,
        "source_identifier": source_record_identifier,
        "original_mention": "ACC1",
        "search_term": "ACC1",
        "source_record_identifier": source_record_identifier,
        "normalization_input": identity,
        "normalization_result": result,
    } | overrides
    return IdentifierCandidate(**merged)


def test_empty_candidates_is_no_candidate():
    status, resolved_id, _ = classify_outcome(())
    assert status is MentionResolutionStatus.NO_CANDIDATE
    assert resolved_id is None


def test_single_matched_is_resolved():
    matched_id = uuid4()
    candidate = _candidate("S1", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    status, resolved_id, _ = classify_outcome([candidate])
    assert status is MentionResolutionStatus.RESOLVED
    assert resolved_id == matched_id


def test_two_candidates_same_matched_id_is_resolved():
    matched_id = uuid4()
    a = _candidate("S1", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    b = _candidate("S2", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    status, resolved_id, reason = classify_outcome([a, b])
    assert status is MentionResolutionStatus.RESOLVED
    assert resolved_id == matched_id
    assert "agree" in reason


def test_two_candidates_different_matched_ids_is_conflicted():
    a = _candidate("S1", NormalizationStatus.MATCHED, matched_entity_id=uuid4())
    b = _candidate("S2", NormalizationStatus.MATCHED, matched_entity_id=uuid4())
    status, resolved_id, _ = classify_outcome([a, b])
    assert status is MentionResolutionStatus.CONFLICTED
    assert resolved_id is None


def test_normalizer_conflicted_status_propagates_as_conflicted():
    a = _candidate("S1", NormalizationStatus.CONFLICTED, matched_entity_id=uuid4())
    status, resolved_id, _ = classify_outcome([a])
    assert status is MentionResolutionStatus.CONFLICTED
    assert resolved_id is None


def test_normalizer_ambiguous_with_no_matched_is_ambiguous():
    a = _candidate("S1", NormalizationStatus.AMBIGUOUS)
    status, resolved_id, _ = classify_outcome([a])
    assert status is MentionResolutionStatus.AMBIGUOUS
    assert resolved_id is None


def test_matched_plus_ambiguous_is_still_resolved():
    """A MATCHED candidate is not overridden by a weaker, non-conflicting

    AMBIGUOUS candidate from another source -- ambiguity that does not
    assert a competing canonical id is not a conflict.
    """
    matched_id = uuid4()
    matched = _candidate("S1", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    ambiguous = _candidate("S2", NormalizationStatus.AMBIGUOUS)
    status, resolved_id, _ = classify_outcome([matched, ambiguous])
    assert status is MentionResolutionStatus.RESOLVED
    assert resolved_id == matched_id


def test_new_only_is_new_candidate():
    a = _candidate("S1", NormalizationStatus.NEW)
    status, resolved_id, _ = classify_outcome([a])
    assert status is MentionResolutionStatus.NEW_CANDIDATE
    assert resolved_id is None


def test_unresolved_only_is_no_candidate():
    a = _candidate("S1", NormalizationStatus.UNRESOLVED)
    status, _resolved_id, _reason = classify_outcome([a])
    assert status is MentionResolutionStatus.NO_CANDIDATE


def test_sort_candidates_is_stable_and_source_keyed():
    a = _candidate("S2", NormalizationStatus.NEW)
    b = _candidate("S1", NormalizationStatus.NEW)
    ordered = sort_candidates([a, b])
    assert [c.source_record_identifier for c in ordered] == ["S1", "S2"]


def test_sort_candidates_does_not_depend_on_input_order():
    matched_id = uuid4()
    a = _candidate("S1", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    b = _candidate("S2", NormalizationStatus.MATCHED, matched_entity_id=matched_id)
    forward = sort_candidates([a, b])
    backward = sort_candidates([b, a])
    assert forward == backward
