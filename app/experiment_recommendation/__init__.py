"""Deterministic Experiment Recommendation (Increment 23).

Converts an already-detected, deterministic ``KnowledgeGapCandidate``/
``KnowledgeGap`` into a structured, auditable ``ExperimentRecommendation``
using explicit, versioned, rule-based templates -- never an LLM, never a
free-form biological hypothesis. See
``app.experiment_recommendation.recommender``'s module docstring for the
two public entry points, ``app.experiment_recommendation.rules`` for the
gap-type-by-gap-type decision policy, and
``docs/19_experiment_recommendation_contract.md`` for the complete
contract, including the full ``GapType`` policy table.

**Gap-driven, not imagination-driven** (the core architectural rule this
package is built around): a recommendation may propose *how* to measure or
validate information the knowledge base has already demonstrated is
missing. It may never invent a new biological mechanism, pathway, protein
function, regulatory relationship, compound, or interaction not already
implied by the gap itself.

This increment writes nothing to ``KnowledgeGap.suggested_experiment`` (or
anywhere else) -- it produces an immutable in-memory
``ExperimentRecommendation`` only. Persistence remains a deliberately
deferred, later increment.
"""

from __future__ import annotations

from app.experiment_recommendation.errors import (
    ExperimentRecommendationError,
    InvalidRecommendationContextError,
    TemplateApplicationError,
    UnsupportedGapTypeError,
)
from app.experiment_recommendation.recommender import (
    build_context_from_session,
    compute_recommendation_identity,
    recommend_experiment_for_gap,
    recommend_experiment_for_persisted_gap,
)
from app.experiment_recommendation.types import (
    ExperimentClass,
    ExperimentRecommendation,
    ExperimentRecommendationContext,
    RecommendationStatus,
)

__all__ = [
    "ExperimentClass",
    "ExperimentRecommendation",
    "ExperimentRecommendationContext",
    "ExperimentRecommendationError",
    "InvalidRecommendationContextError",
    "RecommendationStatus",
    "TemplateApplicationError",
    "UnsupportedGapTypeError",
    "build_context_from_session",
    "compute_recommendation_identity",
    "recommend_experiment_for_gap",
    "recommend_experiment_for_persisted_gap",
]
