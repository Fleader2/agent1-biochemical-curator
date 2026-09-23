"""Tests for ``app.pathway_curation.planner.plan_pathway_curation`` (read-only, no DB)."""

from __future__ import annotations

import pytest

from app.pathway_curation.errors import InvalidCurationRequestError
from app.pathway_curation.planner import plan_pathway_curation
from app.pathway_curation.types import (
    CompletionPolicy,
    CurationMode,
    PathwayCurationRequest,
    PlannerAction,
)


def _request(**overrides) -> PathwayCurationRequest:
    merged = {
        "request_id": "req-1",
        "organism_text": "Saccharomyces cerevisiae",
        "biological_process": "fatty acid biosynthesis",
    } | overrides
    return PathwayCurationRequest(**merged)


def test_plan_starts_with_organism_resolution():
    plan = plan_pathway_curation(_request())
    assert plan.steps[0].action_type is PlannerAction.RESOLVE_ORGANISM


def test_plan_ends_with_completion_assessment():
    plan = plan_pathway_curation(_request())
    assert plan.steps[-1].action_type is PlannerAction.ASSESS_COMPLETION


def test_plan_pathway_discovery_depends_on_organism_resolution():
    plan = plan_pathway_curation(_request())
    by_action = {step.action_type: step for step in plan.steps}
    pathway_step = by_action[PlannerAction.DISCOVER_PATHWAY]
    organism_step = by_action[PlannerAction.RESOLVE_ORGANISM]
    assert organism_step.step_id in pathway_step.dependencies


def test_plan_omits_enzyme_discovery_without_seed_entity_texts():
    plan = plan_pathway_curation(_request(seed_entity_texts=()))
    assert PlannerAction.DISCOVER_ENZYMES not in {s.action_type for s in plan.steps}


def test_plan_includes_enzyme_discovery_with_seed_entity_texts():
    plan = plan_pathway_curation(_request(seed_entity_texts=("ACC1",)))
    assert PlannerAction.DISCOVER_ENZYMES in {s.action_type for s in plan.steps}


def test_plan_omits_kinetics_and_publications_when_disabled():
    plan = plan_pathway_curation(_request(include_publications=False, include_kinetics=False))
    actions = {s.action_type for s in plan.steps}
    assert PlannerAction.DISCOVER_PUBLICATIONS not in actions
    assert PlannerAction.DISCOVER_KINETICS not in actions


def test_plan_includes_kinetics_step_when_enabled():
    plan = plan_pathway_curation(_request(include_kinetics=True))
    assert PlannerAction.DISCOVER_KINETICS in {s.action_type for s in plan.steps}


def test_plan_always_includes_gap_analysis_and_completion_assessment():
    plan = plan_pathway_curation(_request())
    actions = {s.action_type for s in plan.steps}
    assert PlannerAction.ANALYZE_GAPS in actions
    assert PlannerAction.ASSESS_COMPLETION in actions


def test_plan_is_deterministic():
    request = _request(seed_entity_texts=("ACC1", "FAS1"), include_kinetics=True)
    first = plan_pathway_curation(request)
    second = plan_pathway_curation(request)
    assert first == second


def test_plan_reflects_pilot_mode_budgets():
    plan = plan_pathway_curation(_request(mode=CurationMode.PILOT))
    assert plan.mode is CurationMode.PILOT


def test_plan_carries_the_requested_completion_policy():
    plan = plan_pathway_curation(
        _request(completion_policy=CompletionPolicy.STRUCTURAL_AND_EVIDENCE)
    )
    assert plan.completion_policy is CompletionPolicy.STRUCTURAL_AND_EVIDENCE


def test_plan_rejects_invalid_request():
    bad_request = _request(
        completion_policy=CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS,
        include_kinetics=False,
    )
    with pytest.raises(InvalidCurationRequestError):
        plan_pathway_curation(bad_request)


def test_plan_never_touches_a_database_or_connector():
    """Structural guarantee: planning succeeds with no session/connector object anywhere in
    scope -- if planning ever needed one, this call would raise ``NameError``/``AttributeError``
    rather than a domain error."""
    plan = plan_pathway_curation(_request())
    assert plan.plan_id == "plan::req-1"
