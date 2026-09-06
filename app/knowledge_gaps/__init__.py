"""Knowledge Gap Detection (Increment 21): deterministic, read-only analysis.

Identifies missing, weak, conflicting, or disconnected knowledge that can be
**demonstrated from the current curated database** -- never a speculative
biological hypothesis, never a suggested experiment, never an LLM-generated
idea. See ``app.knowledge_gaps.analysis``'s module docstring for the full
review-eligibility policy and ``app.knowledge_gaps.rules`` for every
detection rule's exact behavior, and
``docs/17_knowledge_gap_detection_contract.md`` for the complete contract.

This increment produces an immutable in-memory
``KnowledgeGapAnalysisResult`` only -- it does **not** persist
``KnowledgeGap`` rows, does not generate suggested experiments, and does
not use an LLM.
"""

from __future__ import annotations

from app.knowledge_gaps.analysis import analyze_knowledge_gaps
from app.knowledge_gaps.errors import (
    InvalidGapCandidateError,
    KnowledgeGapAnalysisError,
    UnsupportedGapRuleError,
)
from app.knowledge_gaps.types import (
    GapSeverity,
    GapType,
    KnowledgeGapAnalysisResult,
    KnowledgeGapCandidate,
)

__all__ = [
    "GapSeverity",
    "GapType",
    "InvalidGapCandidateError",
    "KnowledgeGapAnalysisError",
    "KnowledgeGapAnalysisResult",
    "KnowledgeGapCandidate",
    "UnsupportedGapRuleError",
    "analyze_knowledge_gaps",
]
