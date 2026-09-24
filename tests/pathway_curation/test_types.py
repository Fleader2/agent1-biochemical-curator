"""Tests for ``app.pathway_curation.types`` contract types."""

from __future__ import annotations

import dataclasses

import pytest

from app.claim_generation.types import EntityKind
from app.pathway_curation.types import (
    CompletionPolicy,
    CompletionStatus,
    CurationFrontierItem,
    CurationIterationRecord,
    CurationMode,
    CurationPlanStep,
    FrontierReason,
    PathwayCurationPlan,
    PathwayCurationRequest,
    PlannerAction,
    PlanStepStatus,
)


def _request(**overrides) -> PathwayCurationRequest:
    merged = {
        "request_id": "req-1",
        "organism_text": "Saccharomyces cerevisiae",
        "biological_process": "fatty acid biosynthesis",
    } | overrides
    return PathwayCurationRequest(**merged)


# --- PathwayCurationRequest --------------------------------------------------------------------


def test_request_requires_non_empty_organism_text():
    with pytest.raises(ValueError):
        _request(organism_text="")


def test_request_requires_non_empty_biological_process():
    with pytest.raises(ValueError):
        _request(biological_process="   ")


def test_request_defaults_are_conservative():
    request = _request()
    assert request.include_kinetics is False
    assert request.include_regulation is False
    assert request.include_enzyme_states is False
    assert request.include_publications is True
    assert request.completion_policy is CompletionPolicy.STRUCTURAL_COVERAGE
    assert request.mode is CurationMode.PILOT


def test_request_rejects_non_positive_budgets():
    with pytest.raises(ValueError):
        _request(max_iterations=0)
    with pytest.raises(ValueError):
        _request(max_connector_calls=-1)
    with pytest.raises(ValueError):
        _request(max_publications=0)


def test_request_rejects_seed_and_exclusion_overlap():
    with pytest.raises(ValueError):
        _request(seed_entity_texts=("ACC1",), exclusions=("ACC1",))


def test_request_rejects_invalid_ncbi_taxonomy_id():
    with pytest.raises(ValueError):
        _request(organism_ncbi_taxonomy_id=0)
    with pytest.raises(ValueError):
        _request(organism_ncbi_taxonomy_id=-4932)


def test_request_accepts_valid_ncbi_taxonomy_id():
    request = _request(organism_ncbi_taxonomy_id=4932)
    assert request.organism_ncbi_taxonomy_id == 4932


# --- F3: strain_text (Increment C.1) ------------------------------------------------------------


def test_request_accepts_strain_text():
    request = _request(strain_text="S288C")
    assert request.strain_text == "S288C"


def test_request_strain_text_defaults_to_none():
    assert _request().strain_text is None


def test_request_strips_whitespace_from_strain_text():
    assert _request(strain_text="  S288C  ").strain_text == "S288C"


# --- F4: source_pathway_id (Increment C.1) ------------------------------------------------------


def test_request_accepts_valid_kegg_pathway_ids():
    assert _request(source_pathway_id="sce00061").source_pathway_id == "sce00061"
    assert _request(source_pathway_id="map00061").source_pathway_id == "map00061"
    assert _request(source_pathway_id="hsa00061").source_pathway_id == "hsa00061"
    assert _request(source_pathway_id="ko00061").source_pathway_id == "ko00061"


def test_request_source_pathway_id_defaults_to_none():
    assert _request().source_pathway_id is None


def test_request_rejects_malformed_source_pathway_id():
    with pytest.raises(ValueError):
        _request(source_pathway_id="fatty acid biosynthesis")
    with pytest.raises(ValueError):
        _request(source_pathway_id="sce")
    with pytest.raises(ValueError):
        _request(source_pathway_id="00061")


def test_request_blank_source_pathway_id_is_none_not_an_error():
    """A blank string is treated as "not supplied," consistent with every other
    optional string field on this dataclass -- never a validation error."""
    assert _request(source_pathway_id="   ").source_pathway_id is None


def test_request_is_frozen():
    request = _request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.request_id = "other"  # type: ignore[misc]


# --- CurationPlanStep / PathwayCurationPlan --------------------------------------------------


def _step(**overrides) -> CurationPlanStep:
    merged = {
        "step_id": "s1",
        "action_type": PlannerAction.RESOLVE_ORGANISM,
        "target_entity_kind": EntityKind.ORGANISM,
    } | overrides
    return CurationPlanStep(**merged)


def test_plan_step_requires_positive_priority():
    with pytest.raises(ValueError):
        _step(priority=0)


def test_plan_step_defaults_to_pending():
    assert _step().status is PlanStepStatus.PENDING


def test_plan_rejects_duplicate_step_ids():
    with pytest.raises(ValueError):
        PathwayCurationPlan(
            plan_id="p1",
            request_id="req-1",
            policy_version="pathway-curation-v1",
            completion_policy=CompletionPolicy.STRUCTURAL_COVERAGE,
            mode=CurationMode.PILOT,
            steps=(_step(step_id="s1"), _step(step_id="s1")),
        )


def test_plan_rejects_unknown_dependency():
    with pytest.raises(ValueError):
        PathwayCurationPlan(
            plan_id="p1",
            request_id="req-1",
            policy_version="pathway-curation-v1",
            completion_policy=CompletionPolicy.STRUCTURAL_COVERAGE,
            mode=CurationMode.PILOT,
            steps=(_step(step_id="s1", dependencies=("missing",)),),
        )


# --- CurationFrontierItem --------------------------------------------------------------------


def test_frontier_item_requires_text_or_id():
    with pytest.raises(ValueError):
        CurationFrontierItem(
            frontier_id="f1",
            entity_kind=EntityKind.REACTION,
            reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
            priority=1,
        )


def test_frontier_item_accepts_entity_text():
    item = CurationFrontierItem(
        frontier_id="f1",
        entity_kind=EntityKind.REACTION,
        reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
        priority=1,
        entity_text="R00742",
    )
    assert item.resolved is False


# --- CurationIterationRecord / PathwayCurationResult -------------------------------------------


def test_iteration_record_requires_positive_iteration_number():
    with pytest.raises(ValueError):
        CurationIterationRecord(
            iteration_number=0,
            frontier_before=(),
            plan_steps_executed=(),
            new_entity_ids=(),
            new_reaction_ids=(),
            new_publication_ids=(),
            frontier_after=(),
            connector_calls_made=0,
        )


def test_completion_status_vocabulary_is_exactly_five_values():
    assert {member.value for member in CompletionStatus} == {
        "COMPLETE",
        "COMPLETE_WITH_GAPS",
        "BUDGET_EXHAUSTED",
        "BLOCKED",
        "FAILED",
    }
