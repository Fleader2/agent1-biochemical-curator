"""Tests for ``app.confidence.policy``: the authoritative EvidenceType base-score table.

No ``ConfidenceClass``/final-score policy is tested here -- Increment 17
produces a categorical assessment only (see
``docs/13_single_evidence_confidence_contract.md``); final-score/class
policy belongs to a future aggregation increment.
"""

from __future__ import annotations

import pytest

from app.confidence.policy import EVIDENCE_TYPE_BASE_SCORES, EVIDENCE_TYPE_UNSCORED
from app.models.enums import EvidenceType

# --- EvidenceType base scores (authoritative, docs/03_agent_behavior.md) --------


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
def test_evidence_type_base_score_exact(evidence_type, expected_score):
    assert EVIDENCE_TYPE_BASE_SCORES[evidence_type] == expected_score


def test_every_evidence_type_enum_value_is_either_scored_or_explicitly_unscored():
    """No ``EvidenceType`` member may be silently omitted from either table."""
    covered = set(EVIDENCE_TYPE_BASE_SCORES) | set(EVIDENCE_TYPE_UNSCORED)
    assert covered == set(EvidenceType)


def test_unscored_evidence_types_are_exactly_the_documented_gap():
    assert frozenset(
        {
            EvidenceType.LOCALIZATION,
            EvidenceType.PROTEOMICS,
            EvidenceType.METABOLOMICS,
            EvidenceType.FLUXOMICS,
            EvidenceType.TRANSCRIPTOMICS,
            EvidenceType.STRUCTURAL,
            EvidenceType.REVIEW,
            EvidenceType.OTHER,
        }
    ) == EVIDENCE_TYPE_UNSCORED


def test_base_scores_and_unscored_set_are_disjoint():
    assert set(EVIDENCE_TYPE_BASE_SCORES).isdisjoint(EVIDENCE_TYPE_UNSCORED)


def test_policy_module_defines_no_confidence_class_thresholds():
    """Structural: final-score/class policy does not exist in Increment 17."""
    import app.confidence.policy as policy_module

    assert not hasattr(policy_module, "CONFIDENCE_CLASS_THRESHOLDS")
    assert not hasattr(policy_module, "classify_confidence_class")
    assert not hasattr(policy_module, "DIRECTNESS_MULTIPLIERS")
    assert not hasattr(policy_module, "ORGANISM_MULTIPLIERS")
    assert not hasattr(policy_module, "ENTITY_RESOLUTION_CAPS")
