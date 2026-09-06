"""Review-workflow exceptions.

Mirrors ``app.persistence.errors``'s own philosophy: raised only for a
condition a caller should never have reached at all -- a request for a
state transition the schema/state machine does not represent. An ordinary
"this transition was already applied, nothing to do" outcome is reported as
data (``app.review.types.ReviewWorkflowResult`` with ``changed=False``),
never raised.
"""

from __future__ import annotations


class ReviewError(Exception):
    """Base class for review-workflow exceptions."""


class InvalidReviewTransitionError(ReviewError):
    """A requested ``CurationState`` transition is not represented by the state machine.

    Raised whenever the requested ``(old_state, new_state)`` pair is not one
    of the explicit edges in ``app.review.workflow``'s transition table, or
    the requested ``new_state`` is not one the calling actor (machine vs.
    human review) is permitted to reach. Never silently ignored, never
    auto-corrected to the nearest legal state -- see
    ``docs/16_review_workflow_contract.md`` for the full transition table.
    """


class InvalidRecommendationTransitionError(ReviewError):
    """A requested ``RecommendationLifecycleStatus`` transition is not represented.

    Not represented by the lifecycle state machine, specifically.

    The identical philosophy as ``InvalidReviewTransitionError``, applied
    to ``app.review.experiment_recommendation_workflow``'s own lifecycle
    state machine -- see
    ``docs/20_experiment_recommendation_persistence_contract.md`` for the
    full transition table.
    """


__all__ = [
    "InvalidRecommendationTransitionError",
    "InvalidReviewTransitionError",
    "ReviewError",
]
