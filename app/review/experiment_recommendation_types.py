"""Data contract for experiment-recommendation lifecycle (Increment 24).

Three types:

1. ``RecommendationLifecycleStatus`` -- the human-workflow state of one
   persisted ``ExperimentRecommendationRecord``. Deliberately **not** the
   same vocabulary as Increment 23's own
   ``app.experiment_recommendation.types.RecommendationStatus``
   (``RECOMMENDED``/``NOT_APPLICABLE``/``REQUIRES_HUMAN_DESIGN``/
   ``INSUFFICIENT_INFORMATION``, the deterministic engine's own
   conclusion) -- see
   ``app.review.experiment_recommendation_workflow``'s module docstring
   for the full state machine and why the two must never be conflated
   (``RECOMMENDED`` is not ``ACCEPTED``).
2. ``ExperimentRecommendationDecision`` -- one human reviewer's request to
   transition one persisted recommendation's lifecycle status. Mirrors
   ``app.review.types.ReviewDecision`` closely: immutable, self-validating,
   a required timezone-aware ``timestamp`` (never read from the wall clock
   by this package itself).
3. ``ExperimentRecommendationLifecycleResult`` -- the outcome of one
   ``app.review.experiment_recommendation_workflow
   .transition_experiment_recommendation`` call. Mirrors
   ``app.review.types.ReviewWorkflowResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.review.validation import clean_optional, require_non_empty_str, require_timezone_aware


class RecommendationLifecycleStatus(StrEnum):
    """The human-workflow state of one persisted experiment recommendation.

    ``PROPOSED`` is the state every newly persisted recommendation starts
    in. ``ACCEPTED``/``REJECTED``/``SUPERSEDED`` are terminal (Increment 24
    instructions, Step 5: "Do not implement reopening unless explicitly
    justified" -- nothing here justifies it). ``DEFERRED`` is the one
    non-terminal alternative to an immediate accept/reject decision --
    "not ready to decide yet," distinct from simply leaving a
    recommendation ``PROPOSED`` indefinitely, since it is itself a
    recorded human decision (with its own audit event) rather than an
    absence of one.
    """

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    SUPERSEDED = "SUPERSEDED"


class RecommendationActorType(StrEnum):
    """The only value ``app.review.experiment_recommendation_workflow`` ever writes is ``HUMAN``.

    Increment 24 instructions, Step 24: "Preferred simplest policy: No
    machine lifecycle transitions in Increment 24." This enum exists (and
    ``experiment_recommendation_event.actor_type`` remains an unconstrained
    ``VARCHAR`` at the schema level, exactly like ``review_event
    .reviewer_type``) so a future increment could introduce a machine actor
    without a schema change, but no code anywhere in this package
    constructs one today -- structurally verified by
    ``tests/review/test_experiment_recommendation_lifecycle.py``.
    """

    HUMAN = "HUMAN"


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationDecision:
    """One human reviewer's request to transition one recommendation's lifecycle status.

    ``timestamp`` is required, with no default -- this module never reads
    the wall clock itself, the same discipline
    ``app.review.types.ReviewDecision`` already established. Written
    verbatim to ``ExperimentRecommendationEvent.created_at``.
    """

    recommendation_id: UUID
    new_status: RecommendationLifecycleStatus
    reviewer_id: str
    reason: str
    timestamp: datetime
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.recommendation_id, UUID):
            raise TypeError(
                "ExperimentRecommendationDecision.recommendation_id must be a UUID, "
                f"got {self.recommendation_id!r}"
            )
        if not isinstance(self.new_status, RecommendationLifecycleStatus):
            raise TypeError(
                "ExperimentRecommendationDecision.new_status must be a "
                f"RecommendationLifecycleStatus, got {self.new_status!r}"
            )
        object.__setattr__(
            self, "reviewer_id", require_non_empty_str(self.reviewer_id, field_name="reviewer_id")
        )
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))
        object.__setattr__(
            self, "timestamp", require_timezone_aware(self.timestamp, field_name="timestamp")
        )
        object.__setattr__(self, "notes", clean_optional(self.notes))


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationLifecycleResult:
    """The outcome of one ``transition_experiment_recommendation`` call.

    ``event_id`` is populated if and only if ``changed`` is ``True`` --
    exactly one ``ExperimentRecommendationEvent`` was created only when a
    real transition occurred.
    """

    recommendation_id: UUID
    old_status: RecommendationLifecycleStatus
    new_status: RecommendationLifecycleStatus
    changed: bool
    event_id: UUID | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.recommendation_id, UUID):
            raise TypeError(
                "ExperimentRecommendationLifecycleResult.recommendation_id must be a UUID, "
                f"got {self.recommendation_id!r}"
            )
        if not isinstance(self.old_status, RecommendationLifecycleStatus):
            raise TypeError(
                "ExperimentRecommendationLifecycleResult.old_status must be a "
                f"RecommendationLifecycleStatus, got {self.old_status!r}"
            )
        if not isinstance(self.new_status, RecommendationLifecycleStatus):
            raise TypeError(
                "ExperimentRecommendationLifecycleResult.new_status must be a "
                f"RecommendationLifecycleStatus, got {self.new_status!r}"
            )
        if self.changed and self.event_id is None:
            raise ValueError(
                "ExperimentRecommendationLifecycleResult: changed=True requires event_id"
            )
        if not self.changed and self.event_id is not None:
            raise ValueError(
                "ExperimentRecommendationLifecycleResult: changed=False must not carry event_id"
            )
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))


__all__ = [
    "ExperimentRecommendationDecision",
    "ExperimentRecommendationLifecycleResult",
    "RecommendationActorType",
    "RecommendationLifecycleStatus",
]
