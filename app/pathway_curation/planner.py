"""The public, read-only planning API: ``plan_pathway_curation``.

Builds the deterministic **initial** plan skeleton for one request --
never a connector call, never a database read/write (Step 42 of the
original increment instructions: a human must be able to call this and
inspect the plan before anything executes). The concrete, data-dependent
steps that only become knowable once a connector has actually answered a
query (which specific reactions a discovered pathway contains, which
specific genes a request's seed texts resolve to, ...) cannot be listed
here without violating that same read-only constraint -- they are
recorded instead, per iteration, in ``execute_pathway_curation``'s own
``CurationIterationRecord.plan_steps_executed`` audit trail. This module
therefore plans the *fixed* backbone every run always follows (organism
first, then structural discovery, then gap analysis, then completion
assessment) -- never a guess at downstream connector results.
"""

from __future__ import annotations

from app.claim_generation.types import EntityKind
from app.models.enums import SourceType
from app.pathway_curation.policy import apply_mode_defaults
from app.pathway_curation.types import (
    PATHWAY_CURATION_POLICY_VERSION,
    CurationPlanStep,
    PathwayCurationPlan,
    PathwayCurationRequest,
    PlannerAction,
)
from app.pathway_curation.validation import require_valid_request


def plan_pathway_curation(request: PathwayCurationRequest) -> PathwayCurationPlan:
    """Deterministically plan the fixed backbone of one pathway-curation run.

    Pure: no connector, no database session, no side effect of any kind.
    Calling this twice with an identical ``request`` always returns an
    identical ``PathwayCurationPlan`` (``tests/pathway_curation
    /test_planner.py::test_plan_is_deterministic`` verifies this
    directly).
    """
    require_valid_request(request)
    effective_request = apply_mode_defaults(request)

    steps: list[CurationPlanStep] = []

    resolve_organism_step = CurationPlanStep(
        step_id="resolve-organism",
        action_type=PlannerAction.RESOLVE_ORGANISM,
        target_entity_kind=EntityKind.ORGANISM,
        priority=1,
        rationale=(
            "Organism context must be resolved before any organism-scoped discovery "
            "(reactions, genes, proteins) can be attempted."
        ),
        expected_output_kind="Organism",
        query_text=effective_request.organism_text,
    )
    steps.append(resolve_organism_step)

    discover_pathway_step = CurationPlanStep(
        step_id="discover-pathway",
        action_type=PlannerAction.DISCOVER_PATHWAY,
        target_entity_kind=None,
        dependencies=(resolve_organism_step.step_id,),
        priority=2,
        rationale="Seed structural discovery from a curated pathway database (KEGG).",
        expected_output_kind="KEGG pathway id(s)",
        query_text=effective_request.biological_process,
        source=SourceType.KEGG,
    )
    steps.append(discover_pathway_step)

    discover_reactions_step = CurationPlanStep(
        step_id="discover-reactions",
        action_type=PlannerAction.DISCOVER_REACTIONS,
        target_entity_kind=EntityKind.REACTION,
        dependencies=(discover_pathway_step.step_id,),
        priority=3,
        rationale="Expand the discovered pathway's own reaction membership.",
        expected_output_kind="KEGG reaction id(s)",
        source=SourceType.KEGG,
    )
    steps.append(discover_reactions_step)

    if effective_request.seed_entity_texts:
        discover_enzymes_step = CurationPlanStep(
            step_id="discover-enzymes",
            action_type=PlannerAction.DISCOVER_ENZYMES,
            target_entity_kind=EntityKind.GENE,
            dependencies=(resolve_organism_step.step_id,),
            priority=4,
            rationale=(
                "Resolve every explicitly seeded gene symbol (request.seed_entity_texts) via "
                "SGD, then its encoded protein via UniProt."
            ),
            expected_output_kind="Gene, Protein",
            source=SourceType.SGD,
        )
        steps.append(discover_enzymes_step)

    if effective_request.include_publications:
        discover_publications_step = CurationPlanStep(
            step_id="discover-publications",
            action_type=PlannerAction.DISCOVER_PUBLICATIONS,
            target_entity_kind=EntityKind.PUBLICATION,
            dependencies=(discover_reactions_step.step_id,),
            priority=5,
            rationale="Discover supporting literature for resolved reactions/enzymes.",
            expected_output_kind="Publication",
            source=SourceType.PUBMED,
        )
        steps.append(discover_publications_step)

    if effective_request.include_kinetics:
        discover_kinetics_step = CurationPlanStep(
            step_id="discover-kinetics",
            action_type=PlannerAction.DISCOVER_KINETICS,
            target_entity_kind=EntityKind.PROTEIN,
            dependencies=(discover_reactions_step.step_id,),
            priority=6,
            rationale="Discover kinetic measurements for resolved catalytic contexts.",
            expected_output_kind="KineticMeasurement",
            source=SourceType.SABIORK,
        )
        steps.append(discover_kinetics_step)

    analyze_gaps_step = CurationPlanStep(
        step_id="analyze-gaps",
        action_type=PlannerAction.ANALYZE_GAPS,
        target_entity_kind=None,
        dependencies=(discover_reactions_step.step_id,),
        priority=7,
        rationale="Run existing deterministic knowledge-gap detection over everything curated.",
        expected_output_kind="KnowledgeGapCandidate",
    )
    steps.append(analyze_gaps_step)

    assess_completion_step = CurationPlanStep(
        step_id="assess-completion",
        action_type=PlannerAction.ASSESS_COMPLETION,
        target_entity_kind=None,
        dependencies=(analyze_gaps_step.step_id,),
        priority=8,
        rationale="Classify the run's terminal status under the requested completion policy.",
        expected_output_kind="CompletionStatus",
    )
    steps.append(assess_completion_step)

    return PathwayCurationPlan(
        plan_id=f"plan::{effective_request.request_id}",
        request_id=effective_request.request_id,
        policy_version=PATHWAY_CURATION_POLICY_VERSION,
        completion_policy=effective_request.completion_policy,
        mode=effective_request.mode,
        steps=tuple(steps),
        initial_frontier=(),
        notes=(
            "This plan lists the fixed backbone only -- the concrete reactions/genes/"
            "proteins/publications a real run discovers are inherently data-dependent and "
            "are recorded per iteration in PathwayCurationResult.iterations instead, never "
            "guessed here.",
        ),
    )


__all__ = ["plan_pathway_curation"]
