"""Tests for ``app.knowledge_gaps.types``: self-validation of the data contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.knowledge_gaps.errors import InvalidGapCandidateError
from app.knowledge_gaps.types import (
    GapSeverity,
    GapType,
    KnowledgeGapAnalysisResult,
    KnowledgeGapCandidate,
)


def _candidate(**overrides) -> KnowledgeGapCandidate:
    merged = {
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "severity": GapSeverity.HIGH,
        "entity_type": "reaction",
        "entity_id": uuid4(),
        "explanation": "Reaction X has no associated ReactionParticipant rows.",
    } | overrides
    return KnowledgeGapCandidate(**merged)


def test_valid_candidate_construction():
    candidate = _candidate()
    assert candidate.gap_type is GapType.REACTION_WITHOUT_PARTICIPANTS
    assert candidate.subject_text is None
    assert candidate.supporting_claim_ids == ()


def test_candidate_rejects_wrong_gap_type():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(gap_type="REACTION_WITHOUT_PARTICIPANTS")


def test_candidate_rejects_wrong_severity():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(severity="HIGH")


def test_candidate_rejects_blank_entity_type():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(entity_type="   ")


def test_candidate_rejects_non_uuid_entity_id():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(entity_id="not-a-uuid")


def test_candidate_accepts_none_entity_id():
    candidate = _candidate(entity_id=None)
    assert candidate.entity_id is None


def test_candidate_rejects_empty_explanation():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(explanation="")


def test_candidate_rejects_blank_explanation():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(explanation="   ")


def test_candidate_rejects_non_uuid_in_supporting_ids():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(supporting_claim_ids=("not-a-uuid",))


def test_candidate_rejects_blank_reason_code():
    with pytest.raises(InvalidGapCandidateError):
        _candidate(reason_codes=("",))


def test_candidate_reason_codes_are_deterministic_tuple():
    codes = ("REACTION_HAS_ZERO_PARTICIPANTS",)
    candidate = _candidate(reason_codes=codes)
    assert candidate.reason_codes == codes
    assert isinstance(candidate.reason_codes, tuple)


def test_candidate_blank_optional_text_becomes_none():
    candidate = _candidate(subject_text="  ", predicate="  ", object_text="  ")
    assert candidate.subject_text is None
    assert candidate.predicate is None
    assert candidate.object_text is None


def test_candidate_is_frozen():
    candidate = _candidate()
    with pytest.raises(AttributeError):
        candidate.explanation = "changed"  # type: ignore[misc]


def test_identity_key_excludes_explanation():
    a = _candidate(explanation="Explanation A", supporting_claim_ids=(uuid4(),))
    key_before = a.identity_key()
    b = _candidate(
        explanation="A completely different explanation",
        entity_id=a.entity_id,
        supporting_claim_ids=a.supporting_claim_ids,
    )
    assert key_before == b.identity_key()


def test_identity_key_shape():
    entity_id = uuid4()
    claim_ids = (uuid4(),)
    candidate = _candidate(entity_id=entity_id, supporting_claim_ids=claim_ids)
    assert candidate.identity_key() == (
        GapType.REACTION_WITHOUT_PARTICIPANTS,
        "reaction",
        entity_id,
        claim_ids,
    )


# --- KnowledgeGapAnalysisResult -------------------------------------------------------


def _result(**overrides) -> KnowledgeGapAnalysisResult:
    merged = {
        "gaps": (_candidate(),),
        "analyzed_claim_count": 0,
        "analyzed_evidence_count": 0,
        "analyzed_entity_count": 1,
        "summary_statistics": {"REACTION_WITHOUT_PARTICIPANTS": 1},
    } | overrides
    return KnowledgeGapAnalysisResult(**merged)


def test_valid_result_construction():
    result = _result()
    assert len(result.gaps) == 1
    assert result.summary_statistics["REACTION_WITHOUT_PARTICIPANTS"] == 1


def test_result_rejects_non_candidate_gaps():
    with pytest.raises(InvalidGapCandidateError):
        _result(gaps=("not a candidate",))


def test_result_rejects_negative_counts():
    with pytest.raises(InvalidGapCandidateError):
        _result(analyzed_claim_count=-1)


def test_result_rejects_non_int_counts():
    with pytest.raises(InvalidGapCandidateError):
        _result(analyzed_claim_count=1.5)


def test_result_summary_statistics_is_immutable():
    result = _result()
    with pytest.raises(TypeError):
        result.summary_statistics["NEW_KEY"] = 5  # type: ignore[index]


def test_result_rejects_negative_summary_value():
    with pytest.raises(InvalidGapCandidateError):
        _result(summary_statistics={"REACTION_WITHOUT_PARTICIPANTS": -1})


def test_result_is_frozen():
    result = _result()
    with pytest.raises(AttributeError):
        result.analyzed_claim_count = 5  # type: ignore[misc]


# --- Enums -----------------------------------------------------------------------------


def test_gap_type_has_no_unresolved_entity_member():
    """Unresolved-entity identity is not reconstructible after persistence
    (see docs/17 §5) -- confirmed absent from the implemented taxonomy."""
    assert not hasattr(GapType, "UNRESOLVED_ENTITY")


def test_gap_severity_includes_reserved_critical():
    assert GapSeverity.CRITICAL.value == "CRITICAL"
