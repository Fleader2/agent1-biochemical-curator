"""The Review Workflow (Increment 20): deterministic curation state transitions.

Not an AI increment -- a workflow increment around records already
persisted by ``app.persistence.claim``. See ``app.review.workflow``'s
module docstring for the full state machine, the central
``Claim``-has-no-``curation_state``-column schema finding this package is
built around, and the deliberate limitations of machine review's own
policy. See ``docs/16_review_workflow_contract.md`` for the full contract.

No prior review workflow existed anywhere in this repository before this
increment (verified by inspection: no ``ReviewEvent(`` construction call
anywhere in ``app/`` prior to this package).
"""

from __future__ import annotations

from app.review.errors import InvalidReviewTransitionError, ReviewError
from app.review.history import get_review_history
from app.review.types import ReviewDecision, ReviewerType, ReviewWorkflowResult
from app.review.workflow import get_current_curation_state, human_review_claim, machine_review_claim

__all__ = [
    "InvalidReviewTransitionError",
    "ReviewDecision",
    "ReviewError",
    "ReviewWorkflowResult",
    "ReviewerType",
    "get_current_curation_state",
    "get_review_history",
    "human_review_claim",
    "machine_review_claim",
]
