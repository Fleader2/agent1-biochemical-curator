"""The final Agent 1 output contract (Increment 27, scope freeze).

Two top-level, frozen container types:

1. ``Agent1KnowledgePackage`` -- the complete, coherent Agent 1 knowledge
   product for a scope (an organism, or the whole database): every category
   of output Agent 1 v1 is responsible for producing (Agent 1's own final
   scope, Steps 1-12: curated entities, reactions, enzymes, regulation,
   provenance, confidence, review state, knowledge gaps, experiment
   recommendations, execution/results), plus a deterministic summary of
   provenance completeness and known limitations.
2. ``Agent1CuratedKnowledgeView`` -- the deliberately narrower, model-
   relevant subset handed to Agent 2 (the Antimony Builder): reactions,
   participants, compounds, compartments, enzyme associations, regulation,
   provenance, and confidence -- data only. It carries no reaction-graph
   optimization, no kinetic-law selection, no parameter selection, no
   Antimony syntax, and no validation: those are Agent 2/3 responsibilities
   (``docs/23_agent1_v1_scope_and_completion.md`` §23).

Every field on both types holds either an already-persisted ORM row
(returned exactly as ``app.persistence.*``'s own existing read APIs already
return them elsewhere in this repository -- see, for example,
``app.persistence.experiment_recommendation.get_experiment_recommendation``)
or a small, local summary dataclass built purely by reading already-
computed columns. Nothing here recomputes normalization, confidence,
review state, knowledge-gap detection, or experiment-recommendation logic
-- ``app.agent1.service`` only ever calls into the existing package that
owns each of those computations.

``AGENT1_CONTRACT_VERSION`` marks this contract's own version -- a single
string constant, not a semantic-versioning subsystem (Increment 27
instructions, Step 30).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.models.claim import Claim, Evidence
from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import ClaimStatus, ConfidenceClass, CurationState
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.gene import Gene
from app.models.knowledge_gap import KnowledgeGap
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.models.review_event import ReviewEvent

#: This contract's own version. Bump only when ``Agent1KnowledgePackage``/
#: ``Agent1CuratedKnowledgeView``'s field shape changes.
AGENT1_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ClaimReviewState:
    """One claim's derived curation state and its full audit history.

    ``curation_state`` is always computed by
    ``app.review.workflow.get_current_curation_state`` -- this module never
    reimplements that derivation (Increment 27 instructions, Step 6: "must
    not duplicate ... review logic").
    """

    claim_id: UUID
    curation_state: CurationState
    history: tuple[ReviewEvent, ...]


@dataclass(frozen=True, slots=True)
class ClaimConfidenceSummary:
    """One claim's already-persisted confidence, reported verbatim.

    Never recomputed -- ``app.confidence`` is the sole authority on these
    two values (Increment 27 instructions, Step 12: "Expose existing
    confidence. Do not recompute.").
    """

    claim_id: UUID
    confidence_score: Decimal | None
    confidence_class: ConfidenceClass | None
    status: ClaimStatus


@dataclass(frozen=True, slots=True)
class ProvenanceSummary:
    """A deterministic count-based summary of provenance completeness.

    Every count is a plain ``len()`` over already-loaded rows -- no new
    query logic, no judgment about *which* claims deserve provenance.
    """

    total_claims: int
    claims_with_evidence: int
    claims_without_evidence: int
    total_evidence: int
    evidence_with_publication: int
    evidence_with_quoted_support: int
    publications_referenced: int


@dataclass(frozen=True, slots=True)
class Agent1KnowledgePackage:
    """The complete, coherent Agent 1 v1 knowledge product for one scope.

    Excludes, by construction (no field could hold one): Antimony, SBML,
    ODEs, kinetic model objects, simulation results, or model-validation
    output (Increment 27 instructions, Step 5). Those belong to Agents 2-5.
    """

    contract_version: str

    organism_id: UUID | None

    organisms: tuple[Organism, ...]
    genes: tuple[Gene, ...]
    proteins: tuple[Protein, ...]
    compounds: tuple[Compound, ...]
    compartments: tuple[Compartment, ...]
    reactions: tuple[Reaction, ...]
    reaction_participants: tuple[ReactionParticipant, ...]
    reaction_enzyme_associations: tuple[ReactionEnzyme, ...]
    regulatory_interactions: tuple[RegulatoryInteraction, ...]
    publications: tuple[Publication, ...]

    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    confidence_summaries: tuple[ClaimConfidenceSummary, ...]
    review_states: tuple[ClaimReviewState, ...]

    knowledge_gaps: tuple[KnowledgeGap, ...]
    experiment_recommendations: tuple[ExperimentRecommendationRecord, ...]
    experiment_executions: tuple[ExperimentExecution, ...]
    experiment_results: tuple[ExperimentResult, ...]

    provenance_summary: ProvenanceSummary
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Agent1CuratedKnowledgeView:
    """The minimal, model-relevant, curated-only subset handed to Agent 2.

    Data only -- see module docstring. Only ``HUMAN_ACCEPTED`` claims
    contribute to this view's ``claims``/``evidence`` (Increment 27
    instructions, Step 10's eligibility policy) -- an uncurated or rejected
    claim is never exposed here as though it were accepted knowledge.
    """

    contract_version: str

    organism_id: UUID | None

    compartments: tuple[Compartment, ...]
    compounds: tuple[Compound, ...]
    reactions: tuple[Reaction, ...]
    reaction_participants: tuple[ReactionParticipant, ...]
    reaction_enzyme_associations: tuple[ReactionEnzyme, ...]
    regulatory_interactions: tuple[RegulatoryInteraction, ...]

    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    confidence_summaries: tuple[ClaimConfidenceSummary, ...]


__all__ = [
    "AGENT1_CONTRACT_VERSION",
    "Agent1CuratedKnowledgeView",
    "Agent1KnowledgePackage",
    "ClaimConfidenceSummary",
    "ClaimReviewState",
    "ProvenanceSummary",
]
