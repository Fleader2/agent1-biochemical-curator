"""Deterministic ordering and outcome classification for a set of ``IdentifierCandidate``\\ s.

Pure functions only -- no I/O, no connector calls, no calls into
``app.normalization.*`` (every candidate handed to these functions has
already been normalized by ``app.entity_resolution.adapters``). This is
where Steps 9-13 and 27 of this increment's instructions are implemented:
every candidate is preserved (never "pick the first"), candidates are
never collapsed just because their names match, and the final
classification depends only on each candidate's own already-established
``NormalizationResult`` -- never on search/HTTP response order.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.entity_resolution.types import IdentifierCandidate, MentionResolutionStatus
from app.normalization.types import NormalizationStatus


def sort_candidates(
    candidates: Sequence[IdentifierCandidate],
) -> tuple[IdentifierCandidate, ...]:
    """Order candidates by a stable, source-defined key -- never by retrieval order.

    Sorted by ``(source, source_record_identifier)``: both are intrinsic
    to what was actually retrieved (a connector's own stable record id),
    never dependent on HTTP response ordering, dict/set iteration, or
    which candidate happened to be constructed first (Step 27:
    "Never depend on HTTP response ordering when it is not guaranteed.").
    """

    def _key(candidate: IdentifierCandidate) -> tuple[str, str]:
        return (candidate.source.value, candidate.source_record_identifier)

    return tuple(sorted(candidates, key=_key))


def classify_outcome(
    candidates: Sequence[IdentifierCandidate],
) -> tuple[MentionResolutionStatus, UUID | None, str]:
    """Classify a fully-normalized candidate set into one ``MentionResolutionStatus``.

    Returns ``(status, resolved_entity_id, reason)``. Depends only on each
    candidate's own ``normalization_result.status``/``matched_entity_id`` --
    never on candidate order, count beyond what each status legitimately
    requires, or any signal outside what normalization itself already
    established (Step 10: "Candidates may be collapsed only if their
    existing normalization results establish that they resolve to the
    same canonical entity ID.").
    """
    if not candidates:
        return (
            MentionResolutionStatus.NO_CANDIDATE,
            None,
            "no candidate records were retrieved from any queried source",
        )

    statuses = {candidate.normalization_result.status for candidate in candidates}
    matched_ids = {
        candidate.normalization_result.matched_entity_id
        for candidate in candidates
        if candidate.normalization_result.status is NormalizationStatus.MATCHED
    }

    if NormalizationStatus.CONFLICTED in statuses:
        return (
            MentionResolutionStatus.CONFLICTED,
            None,
            "at least one candidate's own normalization result was CONFLICTED",
        )

    if len(matched_ids) > 1:
        return (
            MentionResolutionStatus.CONFLICTED,
            None,
            f"different candidates normalized to {len(matched_ids)} different MATCHED "
            "canonical entity ids -- never chosen arbitrarily",
        )

    if len(matched_ids) == 1:
        return (
            MentionResolutionStatus.RESOLVED,
            next(iter(matched_ids)),
            "every MATCHED candidate agrees on one canonical entity id",
        )

    if NormalizationStatus.AMBIGUOUS in statuses:
        return (
            MentionResolutionStatus.AMBIGUOUS,
            None,
            "at least one candidate's own normalization result was AMBIGUOUS, and no "
            "candidate reached MATCHED",
        )

    if NormalizationStatus.NEW in statuses:
        return (
            MentionResolutionStatus.NEW_CANDIDATE,
            None,
            "a verified external record was found with no corresponding existing entity -- "
            "safe to create, but not yet resolved to an existing one",
        )

    return (
        MentionResolutionStatus.NO_CANDIDATE,
        None,
        "candidate records were retrieved but none carried enough information to normalize",
    )


__all__ = ["classify_outcome", "sort_candidates"]
