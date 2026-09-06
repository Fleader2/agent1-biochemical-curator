"""KnowledgeGap persistence (Increment 22).

Persists Increment 21's deterministic ``KnowledgeGapCandidate`` output
faithfully -- never reinterpreting ``gap_type``, ``severity``, supporting
Claim/Evidence/Entity ids, reason codes, or ``explanation``. This module
performs no gap detection of any kind (it never imports or calls
``app.knowledge_gaps.analysis.analyze_knowledge_gaps``), no confidence
scoring, no normalization, no entity resolution, and no LLM call --
``KnowledgeGapCandidate``/``KnowledgeGapAnalysisResult`` are read-only
inputs here, never mutated.

**Identity/deduplication.** Reuses
``KnowledgeGapCandidate.identity_key()`` exactly -- ``(gap_type,
entity_type, entity_id, supporting_claim_ids)`` -- rather than inventing a
second, conflicting identity rule. ``compute_identity_key`` below turns
that tuple into a deterministic, versioned string
(``"kg-v1:" + sha256(...)``) suitable for a database unique constraint:
supporting claim ids are sorted before hashing (order-independence,
Increment 22 instructions Step 17/18), the serialization is a compact,
sorted-keys JSON document (never Python's built-in ``hash()``, which is
process-randomized for strings and therefore not stable across runs), and
the ``kg-v1`` prefix versions the policy so a future change to the identity
algorithm cannot silently collide with historically-computed keys
(Step 19).

**Idempotency.** ``knowledge_gap.identity_key`` carries a partial unique
index (``WHERE identity_key IS NOT NULL``, migration
``0010_knowledge_gap_hardening``). Persisting the same deterministic gap
twice reuses the existing row (``REUSED_EXISTING``) rather than creating a
duplicate -- explanation text and timestamps are irrelevant to identity by
construction, since they are never part of ``identity_key`` at all.

**Existing-open-gap policy** (Step 26): a ``REUSED_EXISTING`` row's
structured fields (``gap_type``/``severity``/``reason_codes_json``/
``supporting_*_ids_json``/``missing_information``) are never rewritten --
this module only ever recognizes that the identical gap was already
recorded, it does not reconcile or update it.

**Terminal-gap recurrence** (Step 27): if a row with the same
``identity_key`` already exists but its ``status`` is ``RESOLVED``/
``DISMISSED``, this module returns ``REQUIRES_REVIEW`` (carrying that
row's id) rather than silently reopening it or creating a duplicate. No
automatic reopen workflow exists in this increment.

**Transaction handling.** Exactly mirrors ``app.persistence.reaction``: one
``SAVEPOINT`` (``session.begin_nested()``) around the insert attempt, an
``IntegrityError`` caught and resolved via a post-failure recheck query
(the identity-key unique index is the real concurrency authority -- see
"Concurrency" below), every other exception left to propagate. Never
commits or rolls back the session it is given.

**Concurrency.** Two sessions racing to persist the identical deterministic
gap: the loser's ``INSERT`` raises ``IntegrityError`` against the partial
unique index; this module catches it, rolls back only its own ``SAVEPOINT``,
re-queries by ``identity_key``, and returns ``REUSED_EXISTING`` for the
row the winner created. No duplicate row is possible. Without the unique
index (a row whose ``identity_key`` legitimately stays ``NULL`` -- never
produced by this module's own callers, since ``compute_identity_key``
always returns a real string) this guarantee would not hold; every row
this module creates always has a non-null ``identity_key``, so this
residual case does not arise from normal use.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.knowledge_gaps.types import KnowledgeGapAnalysisResult, KnowledgeGapCandidate
from app.models.knowledge_gap import GAP_SEVERITY_VALUES, GAP_TYPE_VALUES, KnowledgeGap
from app.persistence.knowledge_gap_types import (
    TERMINAL_KNOWLEDGE_GAP_STATUSES,
    KnowledgeGapPersistenceResult,
    KnowledgeGapStatus,
)
from app.persistence.types import PersistenceAction

_IDENTITY_KEY_VERSION = "kg-v1"

_VALID_STATUSES = frozenset(status.value for status in KnowledgeGapStatus)


def compute_identity_key(candidate: KnowledgeGapCandidate) -> str:
    """A deterministic, versioned digest of ``candidate.identity_key()``.

    Never Python's built-in ``hash()`` (process-randomized, not stable
    across runs/processes) -- a canonical, sorted-keys JSON serialization
    fed through SHA-256 instead. Supporting claim ids are sorted so input
    ordering never changes the result.
    """
    gap_type, entity_type, entity_id, supporting_claim_ids = candidate.identity_key()
    canonical = {
        "gap_type": gap_type.value,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else None,
        "supporting_claim_ids": sorted(str(claim_id) for claim_id in supporting_claim_ids),
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{_IDENTITY_KEY_VERSION}:{digest}"


def _existing_by_identity_key(session: Session, identity_key: str) -> KnowledgeGap | None:
    return session.execute(
        select(KnowledgeGap).where(KnowledgeGap.identity_key == identity_key)
    ).scalar_one_or_none()


def persist_knowledge_gap(
    candidate: KnowledgeGapCandidate,
    *,
    session: Session,
    status: str = KnowledgeGapStatus.OPEN.value,
    priority: int | None = None,
    model_impact: str | None = None,
) -> KnowledgeGapPersistenceResult:
    """Persist one ``KnowledgeGapCandidate`` faithfully. See module docstring.

    ``status``/``priority``/``model_impact`` are the only research-adjacent
    fields this function ever writes, and only when the caller explicitly
    supplies them -- ``suggested_experiment``/``importance`` are never
    written by this function at all (always left ``NULL``, Increment 22
    instructions Steps 13/32).
    """
    if not isinstance(candidate, KnowledgeGapCandidate):
        raise TypeError(
            f"persist_knowledge_gap requires a KnowledgeGapCandidate, got {candidate!r}"
        )
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"persist_knowledge_gap requires status to be one of {sorted(_VALID_STATUSES)}, "
            f"got {status!r}"
        )
    if priority is not None and (not isinstance(priority, int) or isinstance(priority, bool)):
        raise TypeError(
            f"persist_knowledge_gap requires priority to be an int or None, got {priority!r}"
        )
    if model_impact is not None and not isinstance(model_impact, str):
        raise TypeError(
            f"persist_knowledge_gap requires model_impact to be a str or None, got {model_impact!r}"
        )
    assert candidate.gap_type.value in GAP_TYPE_VALUES  # structurally guaranteed, defense-in-depth
    assert candidate.severity.value in GAP_SEVERITY_VALUES

    identity_key = compute_identity_key(candidate)

    existing = _existing_by_identity_key(session, identity_key)
    if existing is not None:
        return _result_for_existing(existing, identity_key)

    try:
        with session.begin_nested():
            row = KnowledgeGap(
                subject_type=candidate.entity_type,
                subject_id=candidate.entity_id,
                missing_information=candidate.explanation,
                gap_type=candidate.gap_type.value,
                severity=candidate.severity.value,
                reason_codes_json=list(candidate.reason_codes),
                supporting_claim_ids_json=[str(i) for i in candidate.supporting_claim_ids],
                supporting_evidence_ids_json=[str(i) for i in candidate.supporting_evidence_ids],
                supporting_entity_ids_json=[str(i) for i in candidate.supporting_entity_ids],
                identity_key=identity_key,
                status=status,
                priority=priority,
                model_impact=model_impact,
                importance=None,
                suggested_experiment=None,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_by_identity_key(session, identity_key)
        if raced_existing is not None:
            return _result_for_existing(raced_existing, identity_key)
        return KnowledgeGapPersistenceResult(
            action=PersistenceAction.FAILED,
            knowledge_gap_id=None,
            created=False,
            reused_existing=False,
            identity_key=identity_key,
            reason=(
                "knowledge gap creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return KnowledgeGapPersistenceResult(
        action=PersistenceAction.CREATED,
        knowledge_gap_id=row.id,
        created=True,
        reused_existing=False,
        identity_key=identity_key,
        reason="created new knowledge gap",
    )


def _result_for_existing(
    existing: KnowledgeGap, identity_key: str
) -> KnowledgeGapPersistenceResult:
    if existing.status in TERMINAL_KNOWLEDGE_GAP_STATUSES:
        return KnowledgeGapPersistenceResult(
            action=PersistenceAction.REQUIRES_REVIEW,
            knowledge_gap_id=existing.id,
            created=False,
            reused_existing=False,
            identity_key=identity_key,
            review_required=True,
            reason=(
                f"an existing knowledge gap {existing.id} with the identical identity_key "
                f"already has terminal status {existing.status!r}; not reopened automatically"
            ),
        )
    return KnowledgeGapPersistenceResult(
        action=PersistenceAction.REUSED_EXISTING,
        knowledge_gap_id=existing.id,
        created=False,
        reused_existing=True,
        identity_key=identity_key,
        reason=f"reused existing knowledge gap {existing.id} with the identical identity_key",
    )


def persist_knowledge_gap_analysis(
    result: KnowledgeGapAnalysisResult,
    *,
    session: Session,
    status: str = KnowledgeGapStatus.OPEN.value,
) -> tuple[KnowledgeGapPersistenceResult, ...]:
    """Persist every candidate in ``result.gaps``, in order.

    A thin wrapper: each candidate gets its own ``persist_knowledge_gap``
    call (its own ``SAVEPOINT``), so one candidate's ``IntegrityError`` never
    aborts the rest of the batch. Returned results are in exactly the same
    order as ``result.gaps`` -- never re-sorted.
    """
    if not isinstance(result, KnowledgeGapAnalysisResult):
        raise TypeError(
            f"persist_knowledge_gap_analysis requires a KnowledgeGapAnalysisResult, got {result!r}"
        )
    return tuple(
        persist_knowledge_gap(candidate, session=session, status=status)
        for candidate in result.gaps
    )


def get_knowledge_gap(session: Session, knowledge_gap_id: UUID) -> KnowledgeGap | None:
    """One ``KnowledgeGap`` row by id, or ``None``. Read-only."""
    return session.get(KnowledgeGap, knowledge_gap_id)


def list_open_knowledge_gaps(
    session: Session, *, limit: int | None = None
) -> tuple[KnowledgeGap, ...]:
    """Every ``OPEN`` ``KnowledgeGap`` row, oldest first. Read-only.

    Ordered by ``created_at`` ascending with ``id`` as a stable tiebreaker
    (the same "stable, not necessarily chronological" tiebreak convention
    ``app.review.history.get_review_history`` already uses).
    """
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise TypeError(
            "list_open_knowledge_gaps requires limit to be a non-negative int or None, "
            f"got {limit!r}"
        )
    statement = (
        select(KnowledgeGap)
        .where(KnowledgeGap.status == KnowledgeGapStatus.OPEN.value)
        .order_by(KnowledgeGap.created_at.asc(), KnowledgeGap.id.asc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return tuple(session.execute(statement).scalars().all())


__all__ = [
    "compute_identity_key",
    "get_knowledge_gap",
    "list_open_knowledge_gaps",
    "persist_knowledge_gap",
    "persist_knowledge_gap_analysis",
]
