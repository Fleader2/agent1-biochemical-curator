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

**Agent 1.x Increment A** added ``kinetic_measurements`` to both
container types (raw ``KineticMeasurement`` rows on
``Agent1KnowledgePackage``, the curated, faithfully-reshaped
``CuratedKineticMeasurement`` on ``Agent1CuratedKnowledgeView``) and bumped
``AGENT1_CONTRACT_VERSION`` to ``"1.1"`` -- an additive field, not a
breaking reshape of any existing field, so a minor bump (see
``docs/24_kinetic_data_curation_and_handoff.md``). Kinetic measurements
carry no ``Claim``/``CurationState`` of their own (verified directly
against ``app/models/kinetic_measurement.py``: no ``curation_state``
column exists on that table), so ``Agent1CuratedKnowledgeView`` includes
every ingested ``KineticMeasurement`` unfiltered, exactly the same
"exists = curated" policy already applied to
``reactions``/``compounds``/``compartments`` above -- never gated on
``HUMAN_ACCEPTED`` the way ``claims``/``evidence`` are, since that gate
does not exist for this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.models.claim import Claim, Evidence
from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import ClaimStatus, ConfidenceClass, CurationState, SourceType
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.gene import Gene
from app.models.kinetic_measurement import KineticMeasurement
from app.models.knowledge_gap import KnowledgeGap
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.models.review_event import ReviewEvent

#: This contract's own version. Bump only when ``Agent1KnowledgePackage``/
#: ``Agent1CuratedKnowledgeView``'s field shape changes.
AGENT1_CONTRACT_VERSION = "1.1"


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
class CuratedKineticMeasurement:
    """Agent 2-facing view of one curated kinetic measurement (Agent 1.x Increment A).

    A faithful reshaping of one ``KineticMeasurement`` row -- never a
    reinterpretation, and never a decision about model usage. Agent 1's own
    architectural boundary for this increment: Agent 1 curates *reported*
    kinetic facts; Agent 2 decides model usage, kinetic-law mapping, and
    parameter declaration; Agent 4 performs parameter fitting/calibration.
    This type therefore carries no ``ParameterSpecification``-shaped field,
    no kinetic-law assignment, and no boundary/module decision of any kind.

    Every entity reference (``reaction_id``, ``protein_id``, ...) is exactly
    what ``KineticMeasurement`` itself stores -- ``None`` when Agent 1 could
    not resolve that reference, never guessed or defaulted here.

    ``value``/``unit`` are ``KineticMeasurement.parameter_value``/``.unit``
    (the as-reported figures; see ``app.persistence.kinetic_measurement``'s
    module docstring for why ``original_value``/``original_unit`` currently
    always equal them). ``normalized_value``/``normalized_unit`` are always
    ``None`` in this increment -- no unit-conversion framework exists yet
    (``docs/24_kinetic_data_curation_and_handoff.md`` §11).
    """

    kinetic_measurement_id: UUID

    reaction_id: UUID | None
    protein_id: UUID | None
    complex_id: UUID | None
    substrate_id: UUID | None
    organism_id: UUID | None
    publication_id: UUID | None

    parameter_type: str
    reported_parameter_type: str | None
    value: Decimal
    unit: str
    normalized_value: Decimal | None
    normalized_unit: str | None

    strain: str | None
    temperature_c: Decimal | None
    ph: Decimal | None
    reported_rate_law: str | None

    source: SourceType | None
    source_id: str | None

    confidence_score: Decimal | None
    confidence_class: ConfidenceClass | None

    notes: str | None


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
    kinetic_measurements: tuple[KineticMeasurement, ...]

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
    kinetic_measurements: tuple[CuratedKineticMeasurement, ...]

    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    confidence_summaries: tuple[ClaimConfidenceSummary, ...]


__all__ = [
    "AGENT1_CONTRACT_VERSION",
    "Agent1CuratedKnowledgeView",
    "Agent1KnowledgePackage",
    "ClaimConfidenceSummary",
    "ClaimReviewState",
    "CuratedKineticMeasurement",
    "ProvenanceSummary",
]
