"""Pathway Curation Planner (Agent 1.x Increment C).

Public API: ``plan_pathway_curation`` (read-only), ``execute_pathway_curation``
(bounded, auditable execution), and ``validate_agent2_readiness`` (a
deterministic, read-only structural-readiness assessment of that execution's
own export, always attached to its result as ``PathwayCurationResult
.agent2_readiness``). See ``docs/26_autonomous_pathway_curation_planner.md``
for the full contract, including the pre-commit revision that added reaction-
participant resolution (``app.pathway_curation.equation_parser``),
reaction-enzyme catalyst association, and this readiness assessment.

The planner orchestrates Agent 1's existing curation capabilities --
connectors, normalization, entity resolution, persistence, knowledge-gap
detection, and the existing ``Agent1CuratedKnowledgeView`` export chain.
It never invents biological facts, model structure, or kinetic
assumptions, and it never implements Agent 2/3/4/5 behavior.
"""

from __future__ import annotations

from app.pathway_curation.errors import (
    CurationExecutionError,
    InvalidCurationRequestError,
    PathwayCurationError,
    PlannerConfigurationError,
    UnsupportedConnectorCapabilityError,
)
from app.pathway_curation.executor import PathwayConnectorBundle, execute_pathway_curation
from app.pathway_curation.planner import plan_pathway_curation
from app.pathway_curation.readiness import (
    Agent2ReadinessAssessment,
    Agent2ReadinessIssue,
    Agent2ReadinessIssueCode,
    validate_agent2_readiness,
)
from app.pathway_curation.types import (
    PATHWAY_CURATION_POLICY_VERSION,
    CompletionPolicy,
    CompletionStatus,
    CurationFrontierItem,
    CurationIterationRecord,
    CurationMode,
    CurationPlanStep,
    FrontierReason,
    PathwayCurationPlan,
    PathwayCurationRequest,
    PathwayCurationResult,
    PlannerAction,
    PlanStepStatus,
)

__all__ = [
    "PATHWAY_CURATION_POLICY_VERSION",
    "Agent2ReadinessAssessment",
    "Agent2ReadinessIssue",
    "Agent2ReadinessIssueCode",
    "CompletionPolicy",
    "CompletionStatus",
    "CurationExecutionError",
    "CurationFrontierItem",
    "CurationIterationRecord",
    "CurationMode",
    "CurationPlanStep",
    "FrontierReason",
    "InvalidCurationRequestError",
    "PathwayConnectorBundle",
    "PathwayCurationError",
    "PathwayCurationPlan",
    "PathwayCurationRequest",
    "PathwayCurationResult",
    "PlanStepStatus",
    "PlannerAction",
    "PlannerConfigurationError",
    "UnsupportedConnectorCapabilityError",
    "execute_pathway_curation",
    "plan_pathway_curation",
    "validate_agent2_readiness",
]
