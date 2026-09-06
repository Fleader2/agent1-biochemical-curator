"""The public Experiment Recommendation API (Increment 23).

``recommend_experiment_for_gap``/``recommend_experiment_for_persisted_gap``
are the only two entry points. Both convert their respective input shape
(an in-memory ``KnowledgeGapCandidate`` or a persisted ``KnowledgeGap`` ORM
row) into the same internal ``app.experiment_recommendation.rules._GapView``
and dispatch to the identical, shared decision logic -- one gap-type
policy, never two diverging ones for the two entry points.

**No database access in core recommendation logic.** Neither entry point
above touches a ``Session``. ``build_context_from_session`` is the single
exception -- an optional convenience helper that loads the ``Claim``/
``Evidence`` rows a gap's own ``supporting_claim_ids``/
``supporting_evidence_ids`` already name, for a caller who wants
context-dependent recommendations (``LOW_CONFIDENCE_CLAIM``,
``SINGLE_SOURCE_SUPPORT``, ``NO_PRIMARY_EXPERIMENTAL_EVIDENCE``,
``CONFLICTING_CLAIMS``) to actually resolve rather than fall back to
``INSUFFICIENT_INFORMATION``. It issues only ``SELECT`` statements, never
commits, rolls back, or mutates anything it loads.

**Recommendation identity** (Increment 23 instructions, Step 7):
``compute_recommendation_identity`` is a standalone function, not a stored
field on ``ExperimentRecommendation`` -- Step 6's own field list does not
include one, and every ingredient it needs (``knowledge_gap_identity``,
``template_id``, ``template_version``) is already a field on the
recommendation, so storing a fourth, derived field would be redundant. A
future persistence increment can call this function itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experiment_recommendation.errors import InvalidRecommendationContextError
from app.experiment_recommendation.rules import _GapView, build_recommendation
from app.experiment_recommendation.types import (
    ExperimentRecommendation,
    ExperimentRecommendationContext,
)
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim, Evidence
from app.models.knowledge_gap import KnowledgeGap
from app.persistence.knowledge_gap import compute_identity_key

_RECOMMENDATION_IDENTITY_VERSION = "experiment-rec-v1"


def recommend_experiment_for_gap(
    gap: KnowledgeGapCandidate,
    *,
    context: ExperimentRecommendationContext | None = None,
) -> ExperimentRecommendation:
    """Recommend one experiment (or explain why none can be safely recommended) for ``gap``.

    Pure: no query, no write, no mutation of ``gap`` or ``context``.
    """
    if not isinstance(gap, KnowledgeGapCandidate):
        raise TypeError(
            f"recommend_experiment_for_gap requires a KnowledgeGapCandidate, got {gap!r}"
        )
    resolved_context = _resolve_context(context)

    view = _GapView(
        knowledge_gap_identity=compute_identity_key(gap),
        gap_type=gap.gap_type,
        severity=gap.severity,
        entity_type=gap.entity_type,
        entity_id=gap.entity_id,
        supporting_claim_ids=gap.supporting_claim_ids,
        supporting_evidence_ids=gap.supporting_evidence_ids,
        reason_codes=gap.reason_codes,
    )
    return build_recommendation(view, resolved_context)


def recommend_experiment_for_persisted_gap(
    knowledge_gap: KnowledgeGap,
    *,
    context: ExperimentRecommendationContext | None = None,
) -> ExperimentRecommendation:
    """The same recommendation logic, for an already-persisted ``KnowledgeGap`` row.

    Requires ``gap_type``/``severity``/``identity_key`` to be populated --
    all three are nullable on ``KnowledgeGap`` for backward compatibility
    with rows that predate Increment 22's schema hardening (see
    ``docs/18_knowledge_gap_persistence_contract.md``); a row lacking any
    of them cannot be mapped back to a recommendation and raises
    ``InvalidRecommendationContextError`` rather than guessing.
    """
    if not isinstance(knowledge_gap, KnowledgeGap):
        raise TypeError(
            "recommend_experiment_for_persisted_gap requires a KnowledgeGap, "
            f"got {knowledge_gap!r}"
        )
    if knowledge_gap.gap_type is None or knowledge_gap.severity is None:
        raise InvalidRecommendationContextError(
            f"KnowledgeGap {knowledge_gap.id} has no gap_type/severity recorded -- it predates "
            "Increment 22's schema hardening and cannot be mapped to a recommendation"
        )
    if knowledge_gap.identity_key is None:
        raise InvalidRecommendationContextError(
            f"KnowledgeGap {knowledge_gap.id} has no identity_key recorded -- it predates "
            "Increment 22's schema hardening and cannot be mapped to a recommendation"
        )
    resolved_context = _resolve_context(context)

    view = _GapView(
        knowledge_gap_identity=knowledge_gap.identity_key,
        gap_type=GapType(knowledge_gap.gap_type),
        severity=GapSeverity(knowledge_gap.severity),
        entity_type=knowledge_gap.subject_type,
        entity_id=knowledge_gap.subject_id,
        supporting_claim_ids=_uuid_tuple(knowledge_gap.supporting_claim_ids_json),
        supporting_evidence_ids=_uuid_tuple(knowledge_gap.supporting_evidence_ids_json),
        reason_codes=tuple(knowledge_gap.reason_codes_json or ()),
    )
    return build_recommendation(view, resolved_context)


def build_context_from_session(
    session: Session,
    *,
    claim_ids: Sequence[UUID] = (),
    evidence_ids: Sequence[UUID] = (),
) -> ExperimentRecommendationContext:
    """Load exactly the ``Claim``/``Evidence`` rows named by id. Read-only.

    A thin convenience helper -- the only place in this package a
    ``Session`` is ever used. Callers typically pass a gap's own
    ``supporting_claim_ids``/``supporting_evidence_ids`` directly.
    """
    claims: dict[UUID, Claim] = {}
    if claim_ids:
        for claim in session.execute(select(Claim).where(Claim.id.in_(claim_ids))).scalars():
            claims[claim.id] = claim

    evidence: dict[UUID, Evidence] = {}
    if evidence_ids:
        for record in session.execute(
            select(Evidence).where(Evidence.id.in_(evidence_ids))
        ).scalars():
            evidence[record.id] = record

    return ExperimentRecommendationContext(claims=claims, evidence=evidence)


def compute_recommendation_identity(recommendation: ExperimentRecommendation) -> str:
    """A deterministic, versioned digest of one recommendation's stable identity.

    Derived from ``knowledge_gap_identity``/``template_id``/
    ``template_version`` only -- never from ``rationale`` or any other
    generated text (Increment 23 instructions, Step 7). Never Python's
    built-in ``hash()`` (process-randomized for strings); a canonical,
    sorted-keys JSON serialization fed through SHA-256 instead, the same
    convention ``app.persistence.knowledge_gap.compute_identity_key``
    already established.
    """
    canonical = {
        "knowledge_gap_identity": recommendation.knowledge_gap_identity,
        "template_id": recommendation.template_id,
        "template_version": recommendation.template_version,
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{_RECOMMENDATION_IDENTITY_VERSION}:{digest}"


def _resolve_context(
    context: ExperimentRecommendationContext | None,
) -> ExperimentRecommendationContext:
    if context is None:
        return ExperimentRecommendationContext()
    if not isinstance(context, ExperimentRecommendationContext):
        raise InvalidRecommendationContextError(
            f"context must be an ExperimentRecommendationContext or None, got {context!r}"
        )
    return context


def _uuid_tuple(values: list | None) -> tuple[UUID, ...]:
    if not values:
        return ()
    return tuple(UUID(value) for value in values)


__all__ = [
    "build_context_from_session",
    "compute_recommendation_identity",
    "recommend_experiment_for_gap",
    "recommend_experiment_for_persisted_gap",
]
