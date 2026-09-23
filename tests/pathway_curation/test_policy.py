"""Tests for ``app.pathway_curation.policy``."""

from __future__ import annotations

from app.claim_generation.types import EntityKind
from app.models.enums import SourceType
from app.pathway_curation.policy import (
    FRONTIER_PRIORITY_ORDER,
    apply_mode_defaults,
    assess_completion,
    build_frontier_id,
    frontier_priority_rank,
    pilot_defaults,
    query_identity,
    required_frontier_reasons,
    sort_frontier,
)
from app.pathway_curation.types import (
    CompletionPolicy,
    CompletionStatus,
    CurationFrontierItem,
    CurationMode,
    FrontierReason,
    PathwayCurationRequest,
)


def _request(**overrides) -> PathwayCurationRequest:
    merged = {
        "request_id": "req-1",
        "organism_text": "Saccharomyces cerevisiae",
        "biological_process": "fatty acid biosynthesis",
    } | overrides
    return PathwayCurationRequest(**merged)


def _item(
    reason: FrontierReason, *, resolved: bool = False, text: str = "x"
) -> CurationFrontierItem:
    return CurationFrontierItem(
        frontier_id=build_frontier_id(entity_kind=EntityKind.REACTION, reason=reason, anchor=text),
        entity_kind=EntityKind.REACTION,
        reason=reason,
        priority=frontier_priority_rank(reason),
        entity_text=text,
        resolved=resolved,
    )


# --- Priority --------------------------------------------------------------------------------


def test_frontier_priority_order_is_exhaustive_over_the_enum():
    assert set(FRONTIER_PRIORITY_ORDER) == set(FrontierReason)


def test_structural_reasons_rank_above_enrichment_reasons():
    structural_rank = frontier_priority_rank(FrontierReason.UNRESOLVED_REACTION_IDENTITY)
    kinetics_rank = frontier_priority_rank(FrontierReason.MISSING_KINETICS)
    assert structural_rank < kinetics_rank


def test_sort_frontier_is_deterministic_regardless_of_input_order():
    a = _item(FrontierReason.MISSING_KINETICS, text="a")
    b = _item(FrontierReason.UNRESOLVED_REACTION_IDENTITY, text="b")
    c = _item(FrontierReason.UNRESOLVED_CATALYST, text="c")
    forward = sort_frontier((a, b, c))
    backward = sort_frontier((c, b, a))
    assert forward == backward
    assert forward[0] is b  # highest priority (lowest rank) first


# --- Query identity/dedup ----------------------------------------------------------------------


def test_query_identity_is_order_independent():
    first = query_identity(
        connector=SourceType.KEGG, action="search", query="a", database="reaction"
    )
    second = query_identity(
        connector=SourceType.KEGG, action="search", database="reaction", query="a"
    )
    assert first == second


def test_query_identity_distinguishes_different_actions():
    a = query_identity(connector=SourceType.KEGG, action="search", query="x")
    b = query_identity(connector=SourceType.KEGG, action="fetch", query="x")
    assert a != b


# --- Pilot mode defaults -----------------------------------------------------------------------


def test_pilot_mode_lowers_default_budgets():
    request = _request(mode=CurationMode.PILOT)
    effective = apply_mode_defaults(request)
    defaults = pilot_defaults()
    assert effective.max_iterations == defaults["max_iterations"]
    assert effective.max_connector_calls == defaults["max_connector_calls"]
    assert effective.max_publications == defaults["max_publications"]


def test_pilot_mode_never_overrides_an_explicit_budget():
    request = _request(mode=CurationMode.PILOT, max_iterations=2)
    effective = apply_mode_defaults(request)
    assert effective.max_iterations == 2


def test_standard_mode_is_unchanged():
    request = _request(mode=CurationMode.STANDARD)
    assert apply_mode_defaults(request) is request


# --- required_frontier_reasons -----------------------------------------------------------------


def test_structural_coverage_requires_only_structural_reasons():
    reasons = required_frontier_reasons(CompletionPolicy.STRUCTURAL_COVERAGE)
    assert FrontierReason.MISSING_PUBLICATION not in reasons
    assert FrontierReason.MISSING_KINETICS not in reasons
    assert FrontierReason.UNRESOLVED_REACTION_IDENTITY in reasons


def test_structural_and_evidence_adds_publication_reason():
    reasons = required_frontier_reasons(CompletionPolicy.STRUCTURAL_AND_EVIDENCE)
    assert FrontierReason.MISSING_PUBLICATION in reasons
    assert FrontierReason.MISSING_KINETICS not in reasons


def test_kinetics_policy_adds_kinetics_and_regulation_reasons():
    reasons = required_frontier_reasons(CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS)
    assert FrontierReason.MISSING_KINETICS in reasons
    assert FrontierReason.MISSING_REGULATION in reasons
    assert FrontierReason.MISSING_PUBLICATION in reasons


# --- assess_completion -------------------------------------------------------------------------


def test_assess_completion_complete_when_frontier_empty():
    request = _request()
    status, reasons = assess_completion(
        request=request,
        frontier=(),
        connector_calls_made=1,
        iterations_completed=1,
        hard_blocker=None,
        no_progress=False,
    )
    assert status is CompletionStatus.COMPLETE
    assert reasons


def test_assess_completion_complete_with_gaps_for_low_priority_frontier():
    request = _request()
    frontier = (_item(FrontierReason.MISSING_KINETICS),)
    status, _ = assess_completion(
        request=request,
        frontier=frontier,
        connector_calls_made=1,
        iterations_completed=1,
        hard_blocker=None,
        no_progress=False,
    )
    assert status is CompletionStatus.COMPLETE_WITH_GAPS


def test_assess_completion_complete_with_gaps_for_unresolved_structural_frontier():
    """STRUCTURAL_COVERAGE never claims COMPLETE while a structural item remains, but it is
    still COMPLETE_WITH_GAPS (never a failure) unless the budget is exhausted."""
    request = _request()
    frontier = (_item(FrontierReason.UNRESOLVED_REACTION_IDENTITY),)
    status, _ = assess_completion(
        request=request,
        frontier=frontier,
        connector_calls_made=1,
        iterations_completed=1,
        hard_blocker=None,
        no_progress=False,
    )
    assert status is CompletionStatus.COMPLETE_WITH_GAPS


def test_assess_completion_budget_exhausted_with_unresolved_structural_frontier():
    request = _request(max_connector_calls=5)
    frontier = (_item(FrontierReason.UNRESOLVED_REACTION_IDENTITY),)
    status, reasons = assess_completion(
        request=request,
        frontier=frontier,
        connector_calls_made=5,
        iterations_completed=1,
        hard_blocker=None,
        no_progress=False,
    )
    assert status is CompletionStatus.BUDGET_EXHAUSTED
    assert reasons


def test_assess_completion_blocked_on_hard_blocker():
    request = _request()
    status, reasons = assess_completion(
        request=request,
        frontier=(),
        connector_calls_made=0,
        iterations_completed=0,
        hard_blocker="organism resolution failed",
        no_progress=False,
    )
    assert status is CompletionStatus.BLOCKED
    assert reasons == ("organism resolution failed",)


def test_assess_completion_never_requires_zero_gaps_for_complete_with_gaps():
    request = _request(completion_policy=CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS)
    frontier = (
        _item(FrontierReason.MISSING_KINETICS),
        _item(FrontierReason.MISSING_REGULATION, text="y"),
    )
    status, _ = assess_completion(
        request=request,
        frontier=frontier,
        connector_calls_made=1,
        iterations_completed=1,
        hard_blocker=None,
        no_progress=False,
    )
    assert status is CompletionStatus.COMPLETE_WITH_GAPS
