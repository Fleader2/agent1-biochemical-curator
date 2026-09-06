"""Structured errors for the Confidence layer (Increment 18: aggregation only).

Increment 17's single-evidence assessment had no domain-specific failure
mode worth a custom exception (it always produces a result, even an
``UNSCORED_EVIDENCE_TYPE`` one). Aggregation introduces exactly one new
failure mode this hierarchy exists for: a caller asking to combine
evidence for claims that are not clearly the same logical claim
(Increment 18 instructions, Step 4 -- "If claims are not clearly
compatible: raise a validation error or refuse aggregation").
"""

from __future__ import annotations


class ConfidenceAggregationError(Exception):
    """Base class for Multi-Evidence Aggregation errors."""


class IncompatibleClaimsError(ConfidenceAggregationError):
    """Two or more supplied ``EvidenceContribution``\\ s do not represent the same logical claim.

    Raised instead of silently aggregating claims that merely "sound
    similar" -- see ``app.confidence.aggregation``'s claim-compatibility
    check for the exact dimensions compared (subject, predicate, object,
    value shape, organism, strain, compartment).
    """


__all__ = ["ConfidenceAggregationError", "IncompatibleClaimsError"]
