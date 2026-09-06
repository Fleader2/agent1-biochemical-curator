"""ExperimentRecommendation persistence (Increment 24).

Persists Increment 23's deterministic ``ExperimentRecommendation`` output
faithfully -- never reinterpreting its ``status``, ``experiment_class``,
``objective``, ``rationale``, or any supporting id list. This module
performs no recommendation generation of any kind on its own initiative;
``persist_experiment_recommendation`` never calls
``app.experiment_recommendation.recommender`` -- that call is the
caller's responsibility, kept as an explicitly separate, visible step
(``recommend_and_persist_for_gap`` below shows the layering, it does not
hide it).

**Identity, reused exactly.** ``recommendation_identity`` is
``app.experiment_recommendation.recommender.compute_recommendation_identity``'s
own output (``"experiment-rec-v1:<sha256>"``) -- this module never
recomputes it differently. A partial-free (always-non-null, per
``ExperimentRecommendation``'s own construction) unique index on that
column is the database-level idempotency and concurrency authority.

**Identity-content consistency** (Increment 24 instructions, Step 20): an
identity match with differing persisted content is a genuine invariant
violation (the three identity ingredients should always deterministically
produce the same content) and raises
``app.persistence.errors.RecommendationIdentityConflictError`` rather than
silently updating or silently reusing mismatched data.

**Terminal KnowledgeGap behavior** (Step 21): creating a *new* row for a
gap whose persisted ``status`` is ``RESOLVED``/``DISMISSED`` is refused
conservatively (``PersistenceAction.REQUIRES_REVIEW``, no row created) --
but idempotent *reuse* of an already-existing identical recommendation is
never blocked by the gap's current status (nothing new is being created in
that case).

**Transaction handling.** Exactly mirrors ``app.persistence.knowledge_gap``:
one ``SAVEPOINT`` (``session.begin_nested()``) around the insert attempt,
an ``IntegrityError`` caught and resolved via a post-failure recheck query,
every other exception (including ``RecommendationIdentityConflictError``)
left to propagate. Never commits or rolls back the session it is given.

**What this module never writes**: ``KnowledgeGap.suggested_experiment``,
``KnowledgeGap.model_impact``, ``KnowledgeGap.status`` -- accepting or
otherwise transitioning a recommendation's lifecycle never resolves or
otherwise mutates the originating gap (Increment 24 instructions, Steps
32-34).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.experiment_recommendation.recommender import (
    build_context_from_session,
    compute_recommendation_identity,
    recommend_experiment_for_persisted_gap,
)
from app.experiment_recommendation.types import (
    ExperimentRecommendation,
    ExperimentRecommendationContext,
)
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.errors import RecommendationIdentityConflictError
from app.persistence.experiment_recommendation_types import (
    ExperimentRecommendationPersistenceResult,
)
from app.persistence.knowledge_gap_types import TERMINAL_KNOWLEDGE_GAP_STATUSES
from app.persistence.types import PersistenceAction

_OPEN_LIFECYCLE_STATUSES = frozenset({"PROPOSED", "DEFERRED"})


def persist_experiment_recommendation(
    recommendation: ExperimentRecommendation,
    *,
    knowledge_gap_id: UUID,
    session: Session,
) -> ExperimentRecommendationPersistenceResult:
    """Persist one ``ExperimentRecommendation`` faithfully. See module docstring."""
    if not isinstance(recommendation, ExperimentRecommendation):
        raise TypeError(
            "persist_experiment_recommendation requires an ExperimentRecommendation, "
            f"got {recommendation!r}"
        )
    if not isinstance(knowledge_gap_id, UUID):
        raise TypeError(
            f"persist_experiment_recommendation requires a UUID knowledge_gap_id, "
            f"got {knowledge_gap_id!r}"
        )

    identity = compute_recommendation_identity(recommendation)

    existing = _existing_by_identity(session, identity)
    if existing is not None:
        _require_content_matches(existing, recommendation, knowledge_gap_id, identity)
        return ExperimentRecommendationPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            recommendation_id=existing.id,
            recommendation_identity=identity,
            created=False,
            reused_existing=True,
            reason=(
                f"reused existing experiment recommendation {existing.id} with the "
                "identical recommendation_identity"
            ),
        )

    knowledge_gap = session.get(KnowledgeGap, knowledge_gap_id)
    if knowledge_gap is None:
        raise ValueError(f"no KnowledgeGap exists with id {knowledge_gap_id!r}")
    if knowledge_gap.status in TERMINAL_KNOWLEDGE_GAP_STATUSES:
        return ExperimentRecommendationPersistenceResult(
            action=PersistenceAction.REQUIRES_REVIEW,
            recommendation_id=None,
            recommendation_identity=identity,
            created=False,
            reused_existing=False,
            review_required=True,
            reason=(
                f"KnowledgeGap {knowledge_gap_id} has terminal status "
                f"{knowledge_gap.status!r}; a new recommendation is not created for a "
                "terminal gap without an explicit override"
            ),
        )

    try:
        with session.begin_nested():
            row = _build_row(recommendation, knowledge_gap_id, identity)
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = _existing_by_identity(session, identity)
        if raced is not None:
            _require_content_matches(raced, recommendation, knowledge_gap_id, identity)
            return ExperimentRecommendationPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                recommendation_id=raced.id,
                recommendation_identity=identity,
                created=False,
                reused_existing=True,
                reason=(
                    f"reused existing experiment recommendation {raced.id} found after a "
                    "concurrent-insert race"
                ),
            )
        return ExperimentRecommendationPersistenceResult(
            action=PersistenceAction.FAILED,
            recommendation_id=None,
            recommendation_identity=identity,
            created=False,
            reused_existing=False,
            reason=(
                "experiment recommendation creation rolled back due to a database "
                "integrity violation, and no matching row was found on recheck"
            ),
        )

    return ExperimentRecommendationPersistenceResult(
        action=PersistenceAction.CREATED,
        recommendation_id=row.id,
        recommendation_identity=identity,
        created=True,
        reused_existing=False,
        reason="created new experiment recommendation",
    )


def recommend_and_persist_for_gap(
    knowledge_gap: KnowledgeGap,
    *,
    session: Session,
    context: ExperimentRecommendationContext | None = None,
) -> ExperimentRecommendationPersistenceResult:
    """Load context (if not supplied), recommend, then persist -- three visible steps.

    A convenience wrapper only -- it never merges these steps into one
    opaque call. A caller who wants to inspect or modify the
    ``ExperimentRecommendation`` before persisting should call
    ``recommend_experiment_for_persisted_gap``/``persist_experiment_recommendation``
    directly instead.
    """
    resolved_context = context
    if resolved_context is None:
        claim_ids = tuple(_uuid_tuple(knowledge_gap.supporting_claim_ids_json))
        evidence_ids = tuple(_uuid_tuple(knowledge_gap.supporting_evidence_ids_json))
        resolved_context = build_context_from_session(
            session, claim_ids=claim_ids, evidence_ids=evidence_ids
        )
    recommendation = recommend_experiment_for_persisted_gap(knowledge_gap, context=resolved_context)
    return persist_experiment_recommendation(
        recommendation, knowledge_gap_id=knowledge_gap.id, session=session
    )


def get_experiment_recommendation(
    session: Session, recommendation_id: UUID
) -> ExperimentRecommendationRecord | None:
    """One ``ExperimentRecommendationRecord`` by id, or ``None``. Read-only."""
    return session.get(ExperimentRecommendationRecord, recommendation_id)


def list_experiment_recommendations_for_gap(
    session: Session, knowledge_gap_id: UUID
) -> tuple[ExperimentRecommendationRecord, ...]:
    """Every recommendation for one KnowledgeGap, oldest first. Read-only."""
    statement = (
        select(ExperimentRecommendationRecord)
        .where(ExperimentRecommendationRecord.knowledge_gap_id == knowledge_gap_id)
        .order_by(
            ExperimentRecommendationRecord.created_at.asc(), ExperimentRecommendationRecord.id.asc()
        )
    )
    return tuple(session.execute(statement).scalars().all())


def list_open_experiment_recommendations(
    session: Session, *, limit: int | None = None
) -> tuple[ExperimentRecommendationRecord, ...]:
    """Every recommendation whose lifecycle status is ``PROPOSED``/``DEFERRED``. Read-only.

    Ordered by ``created_at`` ascending with ``id`` as a stable tiebreaker
    -- the same "stable, not necessarily chronological" convention
    ``app.review.history.get_review_history``/``app.persistence
    .knowledge_gap.list_open_knowledge_gaps`` already use.
    """
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise TypeError(
            "list_open_experiment_recommendations requires limit to be a non-negative int "
            f"or None, got {limit!r}"
        )
    statement = (
        select(ExperimentRecommendationRecord)
        .where(ExperimentRecommendationRecord.lifecycle_status.in_(_OPEN_LIFECYCLE_STATUSES))
        .order_by(
            ExperimentRecommendationRecord.created_at.asc(), ExperimentRecommendationRecord.id.asc()
        )
    )
    if limit is not None:
        statement = statement.limit(limit)
    return tuple(session.execute(statement).scalars().all())


def _existing_by_identity(
    session: Session, identity: str
) -> ExperimentRecommendationRecord | None:
    return session.execute(
        select(ExperimentRecommendationRecord).where(
            ExperimentRecommendationRecord.recommendation_identity == identity
        )
    ).scalar_one_or_none()


def _build_row(
    recommendation: ExperimentRecommendation, knowledge_gap_id: UUID, identity: str
) -> ExperimentRecommendationRecord:
    return ExperimentRecommendationRecord(
        knowledge_gap_id=knowledge_gap_id,
        recommendation_identity=identity,
        gap_type=recommendation.gap_type.value,
        gap_severity=recommendation.gap_severity.value,
        recommendation_status=recommendation.status.value,
        experiment_class=(
            recommendation.experiment_class.value
            if recommendation.experiment_class is not None
            else None
        ),
        objective=recommendation.objective,
        target_entity_type=recommendation.target_entity_type,
        target_entity_id=recommendation.target_entity_id,
        required_measurement=recommendation.required_measurement,
        required_comparison=recommendation.required_comparison,
        experimental_context_requirements_json=list(
            recommendation.experimental_context_requirements
        ),
        success_criterion=recommendation.success_criterion,
        rationale=recommendation.rationale,
        supporting_claim_ids_json=[str(i) for i in recommendation.supporting_claim_ids],
        supporting_evidence_ids_json=[str(i) for i in recommendation.supporting_evidence_ids],
        reason_codes_json=list(recommendation.reason_codes),
        template_id=recommendation.template_id,
        template_version=recommendation.template_version,
    )


def _require_content_matches(
    existing: ExperimentRecommendationRecord,
    recommendation: ExperimentRecommendation,
    knowledge_gap_id: UUID,
    identity: str,
) -> None:
    if _content_matches(existing, recommendation, knowledge_gap_id):
        return
    raise RecommendationIdentityConflictError(
        f"an ExperimentRecommendationRecord with recommendation_identity {identity!r} "
        f"already exists (id={existing.id}) but its persisted content differs from the "
        "supplied recommendation -- this indicates an invariant violation (a template "
        "changed content without a version bump, or a mismatched recommendation was "
        "supplied), not an ordinary data condition"
    )


def _content_matches(
    existing: ExperimentRecommendationRecord,
    recommendation: ExperimentRecommendation,
    knowledge_gap_id: UUID,
) -> bool:
    expected_experiment_class = (
        recommendation.experiment_class.value
        if recommendation.experiment_class is not None
        else None
    )
    return (
        existing.knowledge_gap_id == knowledge_gap_id
        and existing.gap_type == recommendation.gap_type.value
        and existing.gap_severity == recommendation.gap_severity.value
        and existing.recommendation_status == recommendation.status.value
        and existing.experiment_class == expected_experiment_class
        and existing.objective == recommendation.objective
        and existing.target_entity_type == recommendation.target_entity_type
        and existing.target_entity_id == recommendation.target_entity_id
        and existing.required_measurement == recommendation.required_measurement
        and existing.required_comparison == recommendation.required_comparison
        and set(existing.experimental_context_requirements_json or ())
        == set(recommendation.experimental_context_requirements)
        and existing.success_criterion == recommendation.success_criterion
        and existing.rationale == recommendation.rationale
        and set(existing.supporting_claim_ids_json or ())
        == {str(i) for i in recommendation.supporting_claim_ids}
        and set(existing.supporting_evidence_ids_json or ())
        == {str(i) for i in recommendation.supporting_evidence_ids}
        and set(existing.reason_codes_json or ()) == set(recommendation.reason_codes)
        and existing.template_id == recommendation.template_id
        and existing.template_version == recommendation.template_version
    )


def _uuid_tuple(values: list | None) -> tuple[UUID, ...]:
    if not values:
        return ()
    return tuple(UUID(value) for value in values)


__all__ = [
    "get_experiment_recommendation",
    "list_experiment_recommendations_for_gap",
    "list_open_experiment_recommendations",
    "persist_experiment_recommendation",
    "recommend_and_persist_for_gap",
]
