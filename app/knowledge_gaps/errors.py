"""Knowledge-gap-analysis exceptions.

``KnowledgeGapAnalysisError`` is the base class; nothing raises it directly.
"""

from __future__ import annotations


class KnowledgeGapAnalysisError(Exception):
    """Base class for knowledge-gap-analysis exceptions."""


class UnsupportedGapRuleError(KnowledgeGapAnalysisError):
    """A gap type has no entry in a required policy table (e.g. severity).

    Defensive only: every ``GapType`` member implemented in
    ``app.knowledge_gaps.rules`` has a severity mapping, so this should be
    unreachable in practice -- it exists to fail loudly rather than silently
    if that invariant is ever violated, instead of falling back to a guessed
    severity.
    """


class InvalidGapCandidateError(KnowledgeGapAnalysisError):
    """A ``KnowledgeGapCandidate``/``KnowledgeGapAnalysisResult`` failed self-validation."""


__all__ = ["InvalidGapCandidateError", "KnowledgeGapAnalysisError", "UnsupportedGapRuleError"]
