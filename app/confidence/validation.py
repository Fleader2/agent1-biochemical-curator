"""Pure validation helpers for the Single-Evidence Assessment data contract.

Mirrors ``app.claim_generation.validation``/``app.extraction.validation``'s
role: small, dependency-free functions the frozen dataclasses in
``app.confidence.types`` call from their own ``__post_init__``. Nothing
here performs I/O, calls an LLM, calls into ``app.normalization.*`` or
``app.entity_resolution.*``, or reads a database. Nothing here computes or
validates a final Claim confidence score or ``ConfidenceClass`` -- neither
concept exists in this package (Increment 17 produces a categorical
assessment only; see ``docs/13_single_evidence_confidence_contract.md``).
"""

from __future__ import annotations

from app.confidence.policy import EVIDENCE_SCORE_MAX, EVIDENCE_SCORE_MIN, ScoringStatus


def validate_evidence_score_range(value: int, *, field_name: str) -> None:
    """Require ``evidence_base_score`` to fall within ``[EVIDENCE_SCORE_MIN, EVIDENCE_SCORE_MAX]``.

    This is the authoritative ``docs/03_agent_behavior.md`` base-score
    range -- not a final claim-confidence range, which this package does
    not compute at all.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an int, got {value!r}")
    if value < EVIDENCE_SCORE_MIN or value > EVIDENCE_SCORE_MAX:
        raise ValueError(
            f"{field_name} must be between {EVIDENCE_SCORE_MIN} and {EVIDENCE_SCORE_MAX}, "
            f"got {value!r}"
        )


def validate_scoring_status_consistency(
    *, evidence_base_score: int | None, scoring_status: ScoringStatus
) -> None:
    """Require ``evidence_base_score is None`` iff ``scoring_status is UNSCORED_EVIDENCE_TYPE``.

    A fabricated base score is never allowed to hide behind
    ``SCORED_BASE``, and a real base score is never allowed to be silently
    reported as unscored.
    """
    if evidence_base_score is None:
        if scoring_status is not ScoringStatus.UNSCORED_EVIDENCE_TYPE:
            raise ValueError(
                "scoring_status must be UNSCORED_EVIDENCE_TYPE when evidence_base_score is "
                f"None, got {scoring_status!r}"
            )
        return
    if scoring_status is not ScoringStatus.SCORED_BASE:
        raise ValueError(
            "scoring_status must be SCORED_BASE when evidence_base_score is not None, got "
            f"{scoring_status!r}"
        )


def require_non_blank_string_tuple(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    """Require every element of a string tuple to be a non-blank string, trimmed.

    Used for ``reason_codes`` -- a plain tuple of short, non-empty,
    controlled, human-and-machine-readable strings (Increment 17
    instructions, Step 16: "Keep deterministic reason codes").
    """
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of str, got {value!r}")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must contain only non-empty strings, got {item!r}")
        cleaned.append(item.strip())
    return tuple(cleaned)


__all__ = [
    "require_non_blank_string_tuple",
    "validate_evidence_score_range",
    "validate_scoring_status_consistency",
]
