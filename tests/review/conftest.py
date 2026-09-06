"""Shared factories for ``tests/review``.

Mirrors ``tests/persistence/conftest.py``'s plain-function factory pattern
-- these create and flush a minimal valid ``Claim`` row directly against
``db_session``, standing in for "a claim already persisted by
``app.persistence.claim``" (this package never re-tests Increment 19's own
persistence behavior).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.confidence.aggregate_policy import (
    ConflictSeverity,
    ExperimentalRelevance,
    OrganismRelevance,
)
from app.confidence.aggregate_types import AggregateClaimConfidence, ContributionBreakdown
from app.models.claim import Claim
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType


def make_claim(session: Session, *, status: ClaimStatus = ClaimStatus.UNKNOWN) -> Claim:
    claim = Claim(
        subject_type="GENE",
        predicate="activates",
        object_type=None,
        claim_category="regulation",
        status=status,
    )
    session.add(claim)
    session.flush()
    return claim


def _breakdown(**overrides) -> ContributionBreakdown:
    merged = {
        "source_identifier": "PMID:1",
        "publication_identifier": "PMID:1",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "evidence_base_score": 45,
        "organism_relevance": OrganismRelevance.UNKNOWN,
        "experimental_relevance": ExperimentalRelevance.UNKNOWN,
        "is_duplicate": False,
        "numerically_eligible": True,
        "ineligibility_reasons": (),
        "independence_group": "PMID:1",
        "counted_for_replication": True,
        "conflict_status": ConflictSeverity.NONE,
        "adjusted_contribution": Decimal(45),
        "weight": Decimal(1),
        "weighted_contribution": Decimal(45),
        "cumulative_score": Decimal(45),
    } | overrides
    return ContributionBreakdown(**merged)


def make_confidence(
    confidence_class: ConfidenceClass, *, score: int | None = None
) -> AggregateClaimConfidence:
    """A minimal, directly-constructed ``AggregateClaimConfidence`` for one ``confidence_class``.

    Constructed directly (not via ``aggregate_claim_confidence``) since
    ``tests/review`` only needs a value carrying a specific
    ``confidence_class`` to exercise ``machine_review_claim``'s policy --
    the same direct-construction convention
    ``tests/confidence/test_aggregate_types.py`` already uses for its own
    unit tests.
    """
    if confidence_class is ConfidenceClass.UNKNOWN:
        return AggregateClaimConfidence(
            score=None,
            confidence_class=ConfidenceClass.UNKNOWN,
            base_evidence_total=None,
            replication_bonus=0,
            conflict_penalty=0,
            organism_modifier_summary=(),
            experimental_modifier_summary=(),
            evidence_count=1,
            independent_evidence_count=0,
            dependent_evidence_count=0,
            reason_codes=("AGGREGATE_NO_ELIGIBLE_EVIDENCE",),
            contribution_breakdown=(
                _breakdown(
                    numerically_eligible=False,
                    ineligibility_reasons=("EVIDENCE_TYPE_UNSCORED",),
                    adjusted_contribution=None,
                    weight=None,
                    weighted_contribution=None,
                    cumulative_score=None,
                    evidence_base_score=None,
                ),
            ),
        )

    default_scores = {
        ConfidenceClass.LOW: 30,
        ConfidenceClass.MODERATE: 60,
        ConfidenceClass.HIGH: 80,
        ConfidenceClass.VERY_HIGH: 95,
    }
    resolved_score = score if score is not None else default_scores[confidence_class]
    return AggregateClaimConfidence(
        score=resolved_score,
        confidence_class=confidence_class,
        base_evidence_total=resolved_score,
        replication_bonus=0,
        conflict_penalty=0,
        organism_modifier_summary=(),
        experimental_modifier_summary=(),
        evidence_count=1,
        independent_evidence_count=1,
        dependent_evidence_count=0,
        reason_codes=("AGGREGATE_BASE_EVIDENCE_APPLIED",),
        contribution_breakdown=(_breakdown(),),
    )


__all__ = ["make_claim", "make_confidence"]
