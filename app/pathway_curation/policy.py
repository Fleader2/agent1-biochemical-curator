"""Deterministic planning/execution policy: priority, budgets, completion, dedup.

Pure functions and constants only -- no connector call, no database
access, no normalization decision. This module is the single place that
answers "what order," "are we done," "have we already asked this," and
"what does pilot mode default to" -- every other module in this package
calls into here rather than re-deciding any of these questions locally.
"""

from __future__ import annotations

from app.models.enums import SourceType
from app.pathway_curation.types import (
    CompletionPolicy,
    CompletionStatus,
    CurationFrontierItem,
    CurationMode,
    FrontierReason,
    PathwayCurationRequest,
)

#: Step 15's deterministic frontier priority order -- structural (modeling-readiness)
#: coverage first, enrichment second. A frontier item's own ``priority`` field (an
#: ``int``, lower is more urgent) is always derived from this order via
#: ``frontier_priority_rank``, never assigned ad hoc by a caller.
#:
#: **Revised in the Increment C pre-commit revision** to reflect Agent-2-blocking
#: severity, not biological importance (see
#: ``docs/26_autonomous_pathway_curation_planner.md``'s "Structural frontier
#: priority" section): a reaction with no participants or an unresolved catalyst
#: association now sorts ahead of missing organism/compartment *context* items,
#: since the former blocks structural network construction and the latter two are
#: already resolved by the time either could appear.
FRONTIER_PRIORITY_ORDER: tuple[FrontierReason, ...] = (
    FrontierReason.UNRESOLVED_REACTION_IDENTITY,
    FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY,
    FrontierReason.REACTION_MISSING_PARTICIPANTS,
    FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
    FrontierReason.MISSING_COMPARTMENT_CONTEXT,
    FrontierReason.UNRESOLVED_CATALYST,
    FrontierReason.REACTION_CATALYST_UNRESOLVED,
    FrontierReason.REACTION_ENZYME_PERSISTENCE_FAILED,
    FrontierReason.MISSING_ORGANISM_CONTEXT,
    FrontierReason.MISSING_PUBLICATION,
    FrontierReason.MISSING_KINETICS,
    FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED,
    FrontierReason.MISSING_REGULATION,
    FrontierReason.REGULATION_REQUESTED_NOT_SUPPORTED,
    FrontierReason.MISSING_ENZYME_STATE,
    FrontierReason.AMBIGUOUS_IDENTITY,
    FrontierReason.CONFLICTED_IDENTITY,
    FrontierReason.SOURCE_FAILURE,
    FrontierReason.NO_CONNECTOR_AVAILABLE,
)

_PRIORITY_RANK: dict[FrontierReason, int] = {
    reason: rank for rank, reason in enumerate(FRONTIER_PRIORITY_ORDER, start=1)
}

#: The highest-urgency frontier reasons (Step 4's "structural coverage" bar):
#: a run may not report ``COMPLETE`` while an unresolved item at this priority
#: tier remains, for any completion policy -- it may still report
#: ``COMPLETE_WITH_GAPS`` (structural frontier never blocks that weaker status;
#: see ``assess_completion``), matching ``Agent2ReadinessAssessment``'s own
#: "catalyst absence never blocks readiness by itself" policy
#: (``app.pathway_curation.readiness``) for ``UNRESOLVED_CATALYST``/
#: ``REACTION_ENZYME_PERSISTENCE_FAILED`` specifically. Extended in the
#: Increment C pre-commit revision with the two new reaction-participant/
#: catalyst-persistence reasons introduced alongside it.
_STRUCTURAL_FRONTIER_REASONS = frozenset(
    {
        FrontierReason.UNRESOLVED_REACTION_IDENTITY,
        FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY,
        FrontierReason.REACTION_MISSING_PARTICIPANTS,
        FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
        FrontierReason.UNRESOLVED_CATALYST,
        FrontierReason.REACTION_CATALYST_UNRESOLVED,
        FrontierReason.REACTION_ENZYME_PERSISTENCE_FAILED,
    }
)

#: Frontier reasons that only matter once a completion policy requires
#: evidence/kinetics enrichment -- never counted against
#: ``STRUCTURAL_COVERAGE`` alone (Step 23: structural progress is never
#: blocked on publication/kinetics retrieval unless the policy requires it).
_EVIDENCE_FRONTIER_REASONS = frozenset({FrontierReason.MISSING_PUBLICATION})
_KINETICS_FRONTIER_REASONS = frozenset(
    {
        FrontierReason.MISSING_KINETICS,
        FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED,
        FrontierReason.MISSING_REGULATION,
        FrontierReason.REGULATION_REQUESTED_NOT_SUPPORTED,
        FrontierReason.MISSING_ENZYME_STATE,
    }
)


def frontier_priority_rank(reason: FrontierReason) -> int:
    """The deterministic priority rank for ``reason`` -- lower is more urgent.

    Every ``FrontierReason`` member has a rank; this can never raise for
    a real member of the enum (``FRONTIER_PRIORITY_ORDER`` is exhaustive
    over it, enforced by ``tests/pathway_curation/test_policy.py``).
    """
    return _PRIORITY_RANK[reason]


def sort_frontier(items: tuple[CurationFrontierItem, ...]) -> tuple[CurationFrontierItem, ...]:
    """Deterministic frontier ordering: priority rank, then a stable tiebreak.

    Tiebreak is ``(entity_kind, entity_text or "", str(entity_id or ""),
    frontier_id)`` -- intrinsic to each item, never dependent on
    connector response order or dict/set iteration.
    """

    def _key(item: CurationFrontierItem) -> tuple[int, str, str, str, str]:
        return (
            frontier_priority_rank(item.reason),
            item.entity_kind.value,
            item.entity_text or "",
            str(item.entity_id or ""),
            item.frontier_id,
        )

    return tuple(sorted(items, key=_key))


def build_frontier_id(*, entity_kind: object, reason: FrontierReason, anchor: str) -> str:
    """One deterministic, stable identity string for a frontier item.

    ``anchor`` is whatever text/id string uniquely names the thing this
    item is about (e.g. a reaction's external id, a gene symbol) --
    deterministic and content-derived, never a random UUID (mirrors
    ``app.persistence.knowledge_gap.compute_identity_key``'s own
    "deterministic, content-derived identity" convention).
    """
    kind_value = getattr(entity_kind, "value", entity_kind)
    return f"frontier::{kind_value}::{reason.value}::{anchor}"


def query_identity(*, connector: SourceType, action: str, **query_args: str | None) -> str:
    """A deterministic identity string for one logical connector query.

    Used for orchestration-level query deduplication (Step 34) -- this is
    independent of, and never a substitute for, each connector's own
    HTTP-level caching (``app.connectors.cache``). Two calls with the
    same ``connector``/``action`` and the same (sorted, normalized)
    keyword arguments always produce the same identity string,
    regardless of argument insertion order.
    """
    normalized_args = ",".join(
        f"{key}={value}" for key, value in sorted(query_args.items()) if value is not None
    )
    return f"{connector.value}::{action}::{normalized_args}"


def pilot_defaults() -> dict[str, object]:
    """The conservative default overrides ``CurationMode.PILOT`` applies.

    A plain factory/helper, not a second parallel request type (Step 36:
    "If adding a mode enum feels unnecessary, implement equivalent
    defaults in a factory/helper" -- this repository keeps the small
    ``CurationMode`` enum for explicitness, but centralizes its concrete
    numeric defaults here rather than scattering magic numbers across
    ``planner``/``executor``). Tight iteration/connector/publication
    budgets, curated-database preference is structural (KEGG/SGD/UniProt
    are always tried before literature enrichment -- see
    ``strategies.py``), and broad pathway expansion is avoided by
    ``executor``'s own frontier-batch sizing, not by a setting here.
    """
    return {
        "max_iterations": 3,
        "max_connector_calls": 25,
        "max_publications": 5,
    }


def apply_mode_defaults(request: PathwayCurationRequest) -> PathwayCurationRequest:
    """Return ``request``, with ``CurationMode.PILOT``'s conservative budgets applied.

    Only lowers a budget that the caller left at this module's own
    *non-pilot* baseline default (5/50/10, ``PathwayCurationRequest``'s
    own field defaults) -- an explicit, deliberately-chosen tighter or
    looser budget the caller already set is never overridden. Idempotent
    and side-effect-free: never mutates ``request``, always returns a
    (possibly identical) new/same value.
    """
    if request.mode is not CurationMode.PILOT:
        return request

    defaults = pilot_defaults()
    overrides: dict[str, object] = {}
    if request.max_iterations == 5:
        overrides["max_iterations"] = defaults["max_iterations"]
    if request.max_connector_calls == 50:
        overrides["max_connector_calls"] = defaults["max_connector_calls"]
    if request.max_publications == 10:
        overrides["max_publications"] = defaults["max_publications"]

    if not overrides:
        return request

    import dataclasses

    return dataclasses.replace(request, **overrides)


def required_frontier_reasons(policy: CompletionPolicy) -> frozenset[FrontierReason]:
    """Which frontier-reason categories actually count against completion under ``policy``.

    ``STRUCTURAL_COVERAGE`` only ever counts the three structural reasons;
    each successive policy adds the categories its own name promises,
    never silently requiring more than it discloses (Step 4/§4).
    """
    reasons = set(_STRUCTURAL_FRONTIER_REASONS)
    evidence_or_higher = (
        CompletionPolicy.STRUCTURAL_AND_EVIDENCE,
        CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS,
    )
    if policy in evidence_or_higher:
        reasons |= _EVIDENCE_FRONTIER_REASONS
    if policy is CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS:
        reasons |= _KINETICS_FRONTIER_REASONS
    return frozenset(reasons)


def assess_completion(
    *,
    request: PathwayCurationRequest,
    frontier: tuple[CurationFrontierItem, ...],
    connector_calls_made: int,
    iterations_completed: int,
    hard_blocker: str | None,
    no_progress: bool,
) -> tuple[CompletionStatus, tuple[str, ...]]:
    """Deterministically classify one run's terminal status. Never requires zero gaps.

    Precedence (Step 32's stable stopping rule, applied in the order a
    caller should trust): a hard blocker always wins, then budget
    exhaustion, then whether unresolved high-priority frontier remains
    under ``request.completion_policy`` -- ``COMPLETE_WITH_GAPS`` is a
    fully legitimate terminal state, never treated as a failure.
    """
    reasons: list[str] = []

    if hard_blocker is not None:
        return CompletionStatus.BLOCKED, (hard_blocker,)

    unresolved = tuple(item for item in frontier if not item.resolved)
    counted_reasons = required_frontier_reasons(request.completion_policy)
    blocking = tuple(item for item in unresolved if item.reason in counted_reasons)

    budget_exhausted = (
        connector_calls_made >= request.max_connector_calls
        or iterations_completed >= request.max_iterations
    )

    if blocking:
        if budget_exhausted:
            reasons.append(
                f"budget exhausted ({connector_calls_made}/{request.max_connector_calls} "
                f"connector calls, {iterations_completed}/{request.max_iterations} iterations) "
                f"with {len(blocking)} unresolved high-priority frontier item(s) remaining under "
                f"{request.completion_policy.value}"
            )
            return CompletionStatus.BUDGET_EXHAUSTED, tuple(reasons)
        if no_progress:
            reasons.append(
                f"no progress in the final iteration with {len(blocking)} unresolved "
                f"high-priority frontier item(s) remaining under {request.completion_policy.value}"
            )
            return CompletionStatus.COMPLETE_WITH_GAPS, tuple(reasons)
        reasons.append(
            f"{len(blocking)} unresolved high-priority frontier item(s) remain under "
            f"{request.completion_policy.value}: "
            f"{sorted({item.reason.value for item in blocking})}"
        )
        return CompletionStatus.COMPLETE_WITH_GAPS, tuple(reasons)

    if unresolved:
        reasons.append(
            f"all high-priority ({request.completion_policy.value}) frontier resolved; "
            f"{len(unresolved)} lower-priority enrichment item(s) remain documented as gaps"
        )
        return CompletionStatus.COMPLETE_WITH_GAPS, tuple(reasons)

    reasons.append(
        f"no unresolved frontier of any priority remains under {request.completion_policy.value}"
    )
    return CompletionStatus.COMPLETE, tuple(reasons)


__all__ = [
    "FRONTIER_PRIORITY_ORDER",
    "apply_mode_defaults",
    "assess_completion",
    "build_frontier_id",
    "frontier_priority_rank",
    "pilot_defaults",
    "query_identity",
    "required_frontier_reasons",
    "sort_frontier",
]
