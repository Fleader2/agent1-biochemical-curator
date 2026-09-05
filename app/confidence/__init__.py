"""Single-Evidence Assessment (Increment 17).

Produces a deterministic, auditable **assessment** of one supporting
``CandidateClaim`` -- never a final Claim confidence:

    single evidence
        |
        v
    SingleEvidenceAssessment (this package)
        |
        v
    future multi-evidence aggregation (Increment 18)
        |
        v
    0-100 Claim confidence / ConfidenceClass

The authoritative 0-100 ``Claim.confidence_score`` and ``ConfidenceClass``
are aggregate-claim concepts: ``docs/03_agent_behavior.md``'s own
confidence formula requires a replication bonus to reach its own MODERATE
floor (50), and its maximum single-evidence base score (45) sits below
that floor -- a single evidence item cannot meaningfully produce a final
score or class on its own. This package therefore records categorical
state only (an ``EvidenceType`` base score where one is authoritatively
specified, ``Directness`` unchanged, and identity-resolution state per
role), for a later increment to aggregate into a final score.

**Core architectural rule** (kept strictly separate throughout this
package):

1. Evidence strength -- what does the source evidence itself say
   (``evidence_type``/``evidence_base_score``/``directness``)?
2. Entity-resolution quality -- how completely and unambiguously were the
   claim's entities resolved (``subject_resolution``/``object_resolution``/
   ``organism_resolution``/``compartment_resolution``, each purely
   categorical -- no numeric weight is attached to any of them here)?

Model applicability (how useful this evidence is for a *particular*
downstream model) is a third, distinct concept this package never touches
at all.

**No persistence of any kind** occurs anywhere in this package: no
database write, no ``Claim`` column update. No network access, no
connector call, no LLM call -- every input this package needs already
exists on the supplied ``CandidateClaim`` and its own already-validated
entity references.

See ``docs/13_single_evidence_confidence_contract.md`` for the full
contract, including the exact ``EvidenceType`` score table and an explicit
list of what is deliberately deferred to a future aggregation increment.
"""

from __future__ import annotations

from app.confidence.policy import (
    EVIDENCE_SCORE_MAX,
    EVIDENCE_SCORE_MIN,
    EVIDENCE_TYPE_BASE_SCORES,
    EVIDENCE_TYPE_UNSCORED,
    EntityResolutionQuality,
    EntityRole,
    OrganismAssessment,
    ScoringStatus,
)
from app.confidence.scoring import assess_single_evidence_claim
from app.confidence.types import SingleEvidenceAssessment

__all__ = [
    "EVIDENCE_SCORE_MAX",
    "EVIDENCE_SCORE_MIN",
    "EVIDENCE_TYPE_BASE_SCORES",
    "EVIDENCE_TYPE_UNSCORED",
    "EntityResolutionQuality",
    "EntityRole",
    "OrganismAssessment",
    "ScoringStatus",
    "SingleEvidenceAssessment",
    "assess_single_evidence_claim",
]
