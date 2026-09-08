"""Agent 1 final scope-freeze and completion contract (Increment 27).

Agent 1 is the biochemical knowledge curator: it determines what
biochemical knowledge is supported by available evidence, how strongly, what
is missing, and what experiments might address those gaps. Agent 1 does not
build, validate, simulate, or critique mathematical models -- those
responsibilities belong to Agents 2 through 5. See
``docs/23_agent1_v1_scope_and_completion.md`` for the full contract this
package implements.

This package is a thin, read-only orchestration layer over already-complete
Agent 1 subsystems (``app.connectors``, ``app.normalization``,
``app.entity_resolution``, ``app.extraction``, ``app.claim_generation``,
``app.confidence``, ``app.persistence``, ``app.review``,
``app.knowledge_gaps``, ``app.experiment_recommendation``,
``app.result_interpretation``). It performs no normalization, no claim
generation, no persistence, no confidence scoring, no review, no
knowledge-gap detection, and no experiment-recommendation logic of its own
-- it only reads, coordinates, and assembles.
"""

from app.agent1.export import get_agent1_curated_knowledge_view
from app.agent1.service import (
    curated_claims,
    get_agent1_knowledge_package,
    machine_reviewed_claims,
    non_curated_claims,
    rejected_claims,
)
from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
    ClaimConfidenceSummary,
    ClaimReviewState,
    CuratedAllostericInteraction,
    CuratedEnzymeModification,
    CuratedEnzymeState,
    CuratedEnzymeStateTransition,
    CuratedKineticMeasurement,
    ProvenanceSummary,
)

__all__ = [
    "AGENT1_CONTRACT_VERSION",
    "Agent1CuratedKnowledgeView",
    "Agent1KnowledgePackage",
    "ClaimConfidenceSummary",
    "ClaimReviewState",
    "CuratedAllostericInteraction",
    "CuratedEnzymeModification",
    "CuratedEnzymeState",
    "CuratedEnzymeStateTransition",
    "CuratedKineticMeasurement",
    "ProvenanceSummary",
    "curated_claims",
    "get_agent1_curated_knowledge_view",
    "get_agent1_knowledge_package",
    "machine_reviewed_claims",
    "non_curated_claims",
    "rejected_claims",
]
