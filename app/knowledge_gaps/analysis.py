"""The public Knowledge Gap Detection API: ``analyze_knowledge_gaps``.

Read-only. No write, no commit, no rollback, no LLM call, no connector
call, no confidence recomputation, no normalization -- this module only
issues ``SELECT`` statements and assembles their results into an immutable
``KnowledgeGapAnalysisResult``. See ``app.knowledge_gaps.rules`` for the
detection policy itself; this module owns only query construction,
review-state eligibility, deduplication, and deterministic ordering.

**Review eligibility (Increment 20's review workflow, reused, not
reinvented).** ``Claim`` has no ``curation_state`` column (see
``app.review.workflow``'s own module docstring for the full finding) --
its current curation state is derived from its ``ReviewEvent`` history,
exactly as ``app.review.workflow._current_curation_state`` already does for
one claim at a time. This module needs the state of *every* claim at once,
so it re-expresses the identical "latest ``new_state`` per entity, oldest-
row-wins-are-overwritten-by-newer-rows" algorithm as a single batched query
(``_batch_current_curation_states``) instead of calling that per-claim
function in a loop (which would be one query per claim -- see Step 26,
"avoid N+1"). The ordering convention (``created_at`` ascending, ``id`` as a
stable-but-not-chronological tiebreaker) is identical to
``app.review.workflow``'s own, including its identical limitation
(disclosed there and in ``docs/17_knowledge_gap_detection_contract.md``).

Eligible claims are exactly ``HUMAN_ACCEPTED``, plus ``MACHINE_REVIEWED``
when ``include_machine_reviewed=True``. A claim with no ``ReviewEvent`` at
all is implicitly ``PROPOSED`` (the same implicit default
``app.review.workflow`` uses) and is therefore never eligible.
``REJECTED``/``NEEDS_REVIEW``/``PROPOSED`` claims are never treated as
accepted scientific knowledge (Increment 21 instructions, Step 9) --
``NEEDS_REVIEW`` is deliberately excluded even when
``include_machine_reviewed=True``: it already signals "awaiting human
attention" through the review workflow itself, so this module does not
duplicate that signal as a knowledge gap.

Entity-connectivity rules (``Reaction``/``Protein``/``Gene``/``Compound``)
are **not** scoped by any review state -- those tables have no analogous
curation-eligibility concept in this increment's scope, and every
persisted row is examined.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.knowledge_gaps import rules
from app.knowledge_gaps.types import (
    GapSeverity,
    GapType,
    KnowledgeGapAnalysisResult,
    KnowledgeGapCandidate,
)
from app.models.claim import Claim, Evidence
from app.models.compound import Compound
from app.models.enums import CurationState
from app.models.gene import Gene
from app.models.protein import Protein
from app.models.reaction import Reaction
from app.models.review_event import ReviewEvent

_CLAIM_ENTITY_TYPE = "claim"

_SEVERITY_ORDER: dict[GapSeverity, int] = {
    GapSeverity.CRITICAL: 0,
    GapSeverity.HIGH: 1,
    GapSeverity.MODERATE: 2,
    GapSeverity.LOW: 3,
    GapSeverity.INFO: 4,
}


def analyze_knowledge_gaps(
    session: Session, *, include_machine_reviewed: bool = False
) -> KnowledgeGapAnalysisResult:
    """Run every implemented knowledge-gap rule over the current database state.

    Read-only: issues only ``SELECT`` statements, never commits or rolls
    back the session it is given, and never mutates any loaded row.
    """
    if not isinstance(session, Session):
        raise TypeError(f"analyze_knowledge_gaps requires a Session, got {session!r}")
    if not isinstance(include_machine_reviewed, bool):
        raise TypeError(
            f"analyze_knowledge_gaps requires include_machine_reviewed to be a bool, "
            f"got {include_machine_reviewed!r}"
        )

    all_claims = (
        session.execute(
            select(Claim).options(
                selectinload(Claim.evidence_records).selectinload(Evidence.conditions)
            )
        )
        .scalars()
        .all()
    )
    eligible_claims = _eligible_claims(session, all_claims, include_machine_reviewed)

    gaps: list[KnowledgeGapCandidate] = []
    gaps.extend(rules.detect_conflicting_claims(eligible_claims))
    gaps.extend(rules.detect_low_confidence_claims(eligible_claims))
    gaps.extend(rules.detect_single_source_claims(eligible_claims))
    gaps.extend(rules.detect_no_primary_experimental_evidence(eligible_claims))
    gaps.extend(rules.detect_missing_publication(eligible_claims))
    gaps.extend(rules.detect_missing_experimental_context(eligible_claims))

    reactions = (
        session.execute(
            select(Reaction).options(
                selectinload(Reaction.participants), selectinload(Reaction.enzymes)
            )
        )
        .scalars()
        .all()
    )
    gaps.extend(rules.detect_reactions_without_participants(reactions))
    gaps.extend(rules.detect_reactions_without_enzyme(reactions))

    proteins = (
        session.execute(select(Protein).options(selectinload(Protein.reaction_enzymes)))
        .scalars()
        .all()
    )
    gaps.extend(rules.detect_proteins_without_reaction(proteins))

    genes = session.execute(select(Gene).options(selectinload(Gene.proteins))).scalars().all()
    gaps.extend(rules.detect_genes_without_protein(genes))

    compounds = (
        session.execute(select(Compound).options(selectinload(Compound.reaction_participants)))
        .scalars()
        .all()
    )
    gaps.extend(rules.detect_isolated_compounds(compounds))

    deduped = _dedupe(gaps)
    ordered = tuple(sorted(deduped, key=_sort_key))

    evidence_count = sum(len(claim.evidence_records) for claim in eligible_claims)
    entity_count = len(reactions) + len(proteins) + len(genes) + len(compounds)

    summary_statistics = {gap_type.value: 0 for gap_type in GapType}
    for gap in ordered:
        summary_statistics[gap.gap_type.value] += 1

    return KnowledgeGapAnalysisResult(
        gaps=ordered,
        analyzed_claim_count=len(eligible_claims),
        analyzed_evidence_count=evidence_count,
        analyzed_entity_count=entity_count,
        summary_statistics=summary_statistics,
    )


def _eligible_claims(
    session: Session, all_claims: Sequence[Claim], include_machine_reviewed: bool
) -> list[Claim]:
    eligible_states = {CurationState.HUMAN_ACCEPTED}
    if include_machine_reviewed:
        eligible_states.add(CurationState.MACHINE_REVIEWED)

    states = _batch_current_curation_states(session, [claim.id for claim in all_claims])
    return [
        claim
        for claim in all_claims
        if states.get(claim.id, CurationState.PROPOSED) in eligible_states
    ]


def _batch_current_curation_states(
    session: Session, claim_ids: Sequence[UUID]
) -> dict[UUID, CurationState]:
    """The latest ``new_state`` per claim id, for every id in ``claim_ids``.

    A claim id with no ``ReviewEvent`` row is simply absent from the
    returned mapping (the caller treats that as the implicit ``PROPOSED``
    starting state) -- see module docstring for the exact algorithm and its
    parity with ``app.review.workflow._current_curation_state``.
    """
    if not claim_ids:
        return {}
    rows = session.execute(
        select(ReviewEvent.entity_id, ReviewEvent.new_state)
        .where(
            ReviewEvent.entity_type == _CLAIM_ENTITY_TYPE, ReviewEvent.entity_id.in_(claim_ids)
        )
        .order_by(ReviewEvent.entity_id, ReviewEvent.created_at.asc(), ReviewEvent.id.asc())
    ).all()

    states: dict[UUID, CurationState] = {}
    for entity_id, new_state in rows:
        states[entity_id] = new_state
    return states


def _dedupe(gaps: Sequence[KnowledgeGapCandidate]) -> list[KnowledgeGapCandidate]:
    """First-occurrence-wins deduplication by ``KnowledgeGapCandidate.identity_key()``.

    Never dedupes by ``explanation`` text (Increment 21 instructions, Step
    22).
    """
    seen: dict[tuple, KnowledgeGapCandidate] = {}
    for gap in gaps:
        key = gap.identity_key()
        if key not in seen:
            seen[key] = gap
    return list(seen.values())


def _sort_key(gap: KnowledgeGapCandidate) -> tuple:
    """Deterministic ordering: severity (most severe first), gap_type, entity_type, entity_id.

    A ``None`` ``entity_id`` sorts after every real id, at a given
    severity/gap_type/entity_type.
    """
    return (
        _SEVERITY_ORDER[gap.severity],
        gap.gap_type.value,
        gap.entity_type,
        gap.entity_id is None,
        str(gap.entity_id) if gap.entity_id is not None else "",
    )


__all__ = ["analyze_knowledge_gaps"]
