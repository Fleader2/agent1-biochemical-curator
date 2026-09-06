"""Synthetic, DB-free fixtures for ``tests/experiment_recommendation``.

Every ORM object is constructed in-memory (never added to a session), with
an explicit ``id`` -- the ``default=uuid4`` column default only applies at
flush time. Mirrors ``tests/knowledge_gaps/test_rules.py``'s own
DB-free convention.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.experiment_recommendation.types import ExperimentRecommendationContext
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim, Evidence
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType


def make_claim(**overrides) -> Claim:
    merged = {
        "id": uuid4(),
        "subject_type": "GENE",
        "subject_id": uuid4(),
        "predicate": "activates",
        "organism_id": uuid4(),
        "status": ClaimStatus.UNKNOWN,
        "confidence_class": ConfidenceClass.HIGH,
        "confidence_score": Decimal(80),
    } | overrides
    return Claim(**merged)


def make_evidence(claim: Claim, **overrides) -> Evidence:
    merged = {
        "id": uuid4(),
        "claim_id": claim.id,
        "source_type": SourceType.PUBMED,
        "source_id": "PMID:1",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": "AUTHORS_OBSERVED",
        "curator_summary": "summary",
    } | overrides
    evidence = Evidence(**merged)
    claim.evidence_records.append(evidence)
    return evidence


def make_candidate(**overrides) -> KnowledgeGapCandidate:
    merged = {
        "gap_type": GapType.REACTION_WITHOUT_PARTICIPANTS,
        "severity": GapSeverity.HIGH,
        "entity_type": "reaction",
        "entity_id": uuid4(),
        "explanation": "test-only explanation",
    } | overrides
    return KnowledgeGapCandidate(**merged)


def make_context(*, claims: tuple[Claim, ...] = (), evidence: tuple[Evidence, ...] = ()):
    return ExperimentRecommendationContext(
        claims={claim.id: claim for claim in claims},
        evidence={record.id: record for record in evidence},
    )


__all__ = ["make_candidate", "make_claim", "make_context", "make_evidence"]
