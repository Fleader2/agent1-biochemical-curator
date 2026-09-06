"""Data contract for ExperimentRecommendation persistence (Increment 24).

One type: ``ExperimentRecommendationPersistenceResult``, the outcome of one
``app.persistence.experiment_recommendation.persist_experiment_recommendation``
call. Reuses ``app.persistence.types.PersistenceAction`` directly
(``CREATED``/``REUSED_EXISTING``/``REQUIRES_REVIEW``/``FAILED``) rather
than inventing a parallel action vocabulary, with its own invariants:
unlike ``app.persistence.knowledge_gap_types.KnowledgeGapPersistenceResult``,
``REQUIRES_REVIEW`` here never carries a ``recommendation_id`` -- it means
"no row was created because the originating KnowledgeGap is terminal," not
"an existing terminal row was found" (see
``app.persistence.experiment_recommendation``'s own module docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationPersistenceResult:
    """The outcome of one ``persist_experiment_recommendation`` call.

    * ``CREATED`` -- a new ``ExperimentRecommendationRecord`` was inserted.
      ``recommendation_id`` is its id; ``created=True``.
    * ``REUSED_EXISTING`` -- a row with the identical
      ``recommendation_identity`` already existed, with matching content.
      ``recommendation_id`` is the existing row's id;
      ``reused_existing=True``. Its content is never overwritten
      (Increment 24 instructions, Step 19).
    * ``REQUIRES_REVIEW`` -- no row exists yet for this identity, and the
      originating ``KnowledgeGap`` has a terminal ``status``
      (``RESOLVED``/``DISMISSED``) -- no row is created.
      ``recommendation_id`` is ``None``; ``review_required=True``.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row
      could be found on the post-failure recheck. ``recommendation_id`` is
      ``None``.
    """

    action: PersistenceAction
    recommendation_id: UUID | None
    recommendation_identity: str
    created: bool
    reused_existing: bool
    review_required: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.REQUIRES_REVIEW,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"ExperimentRecommendationPersistenceResult.action must be one of "
                f"{valid_actions}, got {self.action!r}"
            )
        if (
            not isinstance(self.recommendation_identity, str)
            or not self.recommendation_identity.strip()
        ):
            raise ValueError(
                "ExperimentRecommendationPersistenceResult.recommendation_identity must be a "
                f"non-empty string, got {self.recommendation_identity!r}"
            )

        if self.action is PersistenceAction.CREATED:
            if self.recommendation_id is None:
                raise ValueError("CREATED requires recommendation_id")
            if not self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "CREATED requires created=True, reused_existing=False, review_required=False"
                )
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.recommendation_id is None:
                raise ValueError("REUSED_EXISTING requires recommendation_id")
            if self.created or not self.reused_existing or self.review_required:
                raise ValueError(
                    "REUSED_EXISTING requires created=False, reused_existing=True, "
                    "review_required=False"
                )
        elif self.action is PersistenceAction.REQUIRES_REVIEW:
            if self.recommendation_id is not None:
                raise ValueError("REQUIRES_REVIEW must not carry recommendation_id")
            if self.created or self.reused_existing or not self.review_required:
                raise ValueError(
                    "REQUIRES_REVIEW requires created=False, reused_existing=False, "
                    "review_required=True"
                )
        else:  # FAILED
            if self.recommendation_id is not None:
                raise ValueError("FAILED must not carry recommendation_id")
            if self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "FAILED requires created=False, reused_existing=False, review_required=False"
                )


__all__ = ["ExperimentRecommendationPersistenceResult"]
