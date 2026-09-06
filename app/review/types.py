"""The Review Workflow data contract.

Three types:

1. ``ReviewerType`` -- the closed vocabulary ``ReviewEvent.reviewer_type``
   is written from. ``app/models/review_event.py`` itself gives only
   "Examples" (``AI_CRITIC``, ``HUMAN``, ``DETERMINISTIC_VALIDATOR``), not a
   database enum -- ``reviewer_type`` is a plain ``VARCHAR``. This is a
   local-only ``StrEnum``, the same "vocabulary exists in code, not in the
   database" pattern ``app.extraction.types.Directness`` already uses for
   ``Evidence.directness``, transcribed verbatim from
   ``docs/02_database_schema.md``'s own "Table: review_event" examples --
   not invented.
2. ``ReviewDecision`` -- one human reviewer's request to transition one
   ``Claim``'s curation state. Immutable, self-validating.
3. ``ReviewWorkflowResult`` -- the outcome of applying one review action
   (machine or human) to one ``Claim``.

Both dataclasses are frozen and validate themselves in ``__post_init__``,
the same pattern every other data-contract module in this repository
already uses. Neither performs I/O, calls an LLM, or reads a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.models.enums import CurationState
from app.review.validation import clean_optional, require_non_empty_str, require_timezone_aware


class ReviewerType(StrEnum):
    """The closed vocabulary ``ReviewEvent.reviewer_type`` is written from. See module docstring."""

    HUMAN = "HUMAN"
    AI_CRITIC = "AI_CRITIC"
    DETERMINISTIC_VALIDATOR = "DETERMINISTIC_VALIDATOR"


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """One human reviewer's request to transition one ``Claim``'s curation state.

    ``decision`` is a ``CurationState`` value directly -- reusing the
    existing schema enum rather than inventing a parallel one.
    ``app.review.workflow.human_review_claim`` is the sole authority on
    which ``CurationState`` values a human decision may actually target
    (``InvalidReviewTransitionError`` for any other); this type only
    requires ``decision`` to be a genuine ``CurationState`` member.

    ``reviewer`` is required and non-blank -- this package never fabricates
    reviewer identity, and a blank reviewer would silently defeat the audit
    trail ``docs/03_agent_behavior.md``'s "Provenance Behavior" requires
    ("who made the change").

    ``timestamp`` is required, with no default -- this module never reads
    the wall clock itself (Increment 20 instructions, Step 14: "No
    timestamps outside the repository's existing conventions"); the caller
    supplies exactly when the decision was made, and
    ``app.review.workflow.human_review_claim`` writes it verbatim to
    ``ReviewEvent.created_at``, overriding that column's ``server_default``
    -- "when the change occurred" (docs/03) is the actual decision moment,
    not merely the moment this code happened to run.

    ``created_by`` has **no destination on ``ReviewEvent``** --
    ``review_event`` has no column for "who invoked this recording", only
    ``reviewer_type``/``reviewer_id`` (who made the *decision*). Accepted
    here (per this increment's own field list) but never persisted; see
    ``docs/16_review_workflow_contract.md`` for this disclosed gap. Kept
    optional since nothing requires it.
    """

    claim_id: UUID
    decision: CurationState
    reviewer: str
    reason: str
    timestamp: datetime
    notes: str | None = None
    created_by: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.claim_id, UUID):
            raise TypeError(f"ReviewDecision.claim_id must be a UUID, got {self.claim_id!r}")
        if not isinstance(self.decision, CurationState):
            raise TypeError(
                f"ReviewDecision.decision must be a CurationState, got {self.decision!r}"
            )
        object.__setattr__(
            self, "reviewer", require_non_empty_str(self.reviewer, field_name="reviewer")
        )
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))
        object.__setattr__(
            self, "timestamp", require_timezone_aware(self.timestamp, field_name="timestamp")
        )
        object.__setattr__(self, "notes", clean_optional(self.notes))
        object.__setattr__(self, "created_by", clean_optional(self.created_by))


@dataclass(frozen=True, slots=True)
class ReviewWorkflowResult:
    """The outcome of applying one review action (machine or human) to one ``Claim``.

    ``changed`` is ``False`` for two distinct cases this type deliberately
    does not distinguish further (both are reported through ``reason``,
    prose-readable): the requested state already equalled the current
    state (a harmless no-op, never re-recorded), or an integrity violation
    rolled the attempt back (see ``app.review.workflow``'s own docstring).
    ``review_event_id`` is populated if and only if ``changed`` is
    ``True`` -- exactly one ``ReviewEvent`` was created only when a real
    transition occurred.
    """

    old_state: CurationState
    new_state: CurationState
    review_event_id: UUID | None
    changed: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.old_state, CurationState):
            raise TypeError(
                f"ReviewWorkflowResult.old_state must be a CurationState, got {self.old_state!r}"
            )
        if not isinstance(self.new_state, CurationState):
            raise TypeError(
                f"ReviewWorkflowResult.new_state must be a CurationState, got {self.new_state!r}"
            )
        if self.changed and self.review_event_id is None:
            raise ValueError("ReviewWorkflowResult: changed=True requires review_event_id")
        if not self.changed and self.review_event_id is not None:
            raise ValueError("ReviewWorkflowResult: changed=False must not carry review_event_id")
        object.__setattr__(self, "reason", require_non_empty_str(self.reason, field_name="reason"))


__all__ = ["ReviewDecision", "ReviewWorkflowResult", "ReviewerType"]
