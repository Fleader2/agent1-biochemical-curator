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

**Agent 1.x Increment B** added ``enzyme_states``/``enzyme_modifications``/
``allosteric_interactions``/``enzyme_state_transitions`` to both container
types, and ``enzyme_state_id`` to ``CuratedKineticMeasurement``, bumping
``AGENT1_CONTRACT_VERSION`` to ``"1.2"`` (see
``docs/25_enzyme_regulatory_states_contract.md``). None of the four new
tables carries a ``Claim``/``CurationState`` column either, so the same
"exists = curated" policy applies to all four -- never gated on
``HUMAN_ACCEPTED``. State-specific ``ReactionEnzyme`` associations need no
separate field: ``reaction_enzyme_associations`` is already a tuple of raw
``ReactionEnzyme`` rows, and every such row already carries its own
``enzyme_state_id`` column (added by the same increment) for free.

**Agent 1.x Increment C.6** added ``kinetic_measurement_protein_contexts``
(raw ``KineticMeasurementProteinContext`` join rows) to
``Agent1KnowledgePackage``, and ``protein_ids`` to
``CuratedKineticMeasurement``, bumping ``AGENT1_CONTRACT_VERSION`` to
``"1.3"`` -- both additive fields, no existing field removed or repurposed.
Motivated directly by Real Integration Pilot 1 Run 7: SABIO-RK's own
EC-scoped (not protein-scoped) search let two distinct proteins sharing one
EC number (yeast's real FAS1/FAS2) each independently, legitimately
discover the identical external source record, but
``KineticMeasurement.protein_id``'s own single-value shape could only ever
record one of them -- the second protein's own equally valid discovery left
no trace at all. ``protein_ids`` is always a superset of the legacy
``protein_id`` (kept unchanged, for backward compatibility with the
ordinary single-protein case); no `Claim`/`CurationState` column exists on
the new join table either, so it is included in
``Agent1CuratedKnowledgeView`` unfiltered, per this contract's own
established "exists = curated" policy for structural/schema records that
carry no curation state of their own.

**Agent 1.x Increment "Experimental Context and Quantitative Observation
Framework"** added ``experimental_contexts``/``perturbations``/
``quantitative_observations`` to both container types, bumping
``AGENT1_CONTRACT_VERSION`` to ``"1.4"`` -- three additive fields, no
existing field removed or repurposed. See
``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design. None of the four new tables
(``ExperimentalContext``/``Perturbation``/``QuantitativeObservation``/
``QuantitativeObservationDependency``) carries a ``Claim``/
``CurationState`` column, so this contract's own established "exists =
curated" policy applies to all of them unfiltered, exactly as it already
does for kinetic measurements and enzyme regulatory states.
``CuratedQuantitativeObservation.dependencies`` is computed here (a
faithful reshaping of every ``QuantitativeObservationDependency`` row
naming that observation as its ``derived_observation_id``), never copied
from a single column -- this increment represents derived-value
dependency/provenance only, it never performs a derivation itself
(no code anywhere computes a ``DERIVED`` observation's own value from its
declared inputs).

**Publication Date Handoff increment** added ``publications`` (the minimal
``CuratedPublication`` -- ``id``/``year`` only) to ``Agent1CuratedKnowledgeView``,
bumping ``AGENT1_CONTRACT_VERSION`` to ``"1.5"`` -- one additive field, no existing
field removed or repurposed. ``Agent1KnowledgePackage.publications`` already existed
(``tuple[Publication, ...]``, already scoped by
``app.agent1.service.get_agent1_knowledge_package`` to exactly the publications this
run's own kinetic measurements/experimental contexts/perturbations/quantitative
observations reference) -- this increment only exposes that already-computed,
already-stored data one layer further, reshaped to the minimal subset Agent 2's own
kinetic-evidence recency prioritization needs. No new publication lookup logic. No
year is ever inferred from ``pmid``/``doi``/a database timestamp -- ``Publication
.year`` verbatim, or ``None`` when Agent 1 itself never resolved one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.models.claim import Claim, Evidence
from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import (
    AllostericEffect,
    ClaimStatus,
    ConfidenceClass,
    CurationState,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ExperimentalContextClassification,
    ModificationType,
    QuantitativeEvidenceClass,
    SourceType,
    TimeReferenceBasis,
)
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.experimental_context import ExperimentalContext
from app.models.gene import Gene
from app.models.kinetic_measurement import KineticMeasurement, KineticMeasurementProteinContext
from app.models.knowledge_gap import KnowledgeGap
from app.models.organism import Organism
from app.models.perturbation import Perturbation
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.quantitative_observation import (
    QuantitativeObservation,
    QuantitativeObservationDependency,
)
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.models.review_event import ReviewEvent

#: This contract's own version. Bump only when ``Agent1KnowledgePackage``/
#: ``Agent1CuratedKnowledgeView``'s field shape changes.
AGENT1_CONTRACT_VERSION = "1.5"


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

    **Agent 1.x Increment C.6: ``protein_id`` is a legacy convenience field,
    not the authoritative record of protein applicability -- use
    ``protein_ids`` instead.** ``protein_id`` reflects only whichever
    protein's own resolution happened to persist this measurement's
    ``(source, source_id)`` row first, an accident of processing order; it
    is preserved unchanged solely so pre-C.6 single-protein-context callers
    keep working. ``protein_ids`` (see its own field comment below) is the
    complete, order-independent set and is authoritative going forward.

    ``value``/``unit`` are ``KineticMeasurement.parameter_value``/``.unit``
    (the as-reported figures; see ``app.persistence.kinetic_measurement``'s
    module docstring for why ``original_value``/``original_unit`` currently
    always equal them). ``normalized_value``/``normalized_unit`` (Agent 1.x
    Increment C.12) are the canonical-unit conversion
    ``app.normalization.kinetic_units.convert_to_canonical_unit`` computed
    for this row -- ``nM`` for Km/Ki, ``per_sec`` for kcat, ``nM_per_s``
    for Vmax, ``per_nMs`` for kcat/Km -- or both ``None`` when the reported
    unit could not be recognized or was dimensionally incompatible with
    the parameter type (never a fabricated canonical value).
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

    #: Agent 1.x Increment B. ``None`` unless this measurement was
    #: specifically reported for one defined ``EnzymeState`` -- never
    #: treated as applicable to the parent protein/complex generally, or
    #: to any other state of it, when set (see
    #: ``docs/25_enzyme_regulatory_states_contract.md`` §15).
    enzyme_state_id: UUID | None = None

    #: Agent 1.x Increment C.6. **The authoritative record of every protein
    #: this measurement is biologically applicable to** -- always a superset
    #: of ``protein_id`` (which is a legacy convenience field only, see that
    #: field's own comment above; do not treat it as the source of truth).
    #: Length 0 when no protein context was ever resolved, length 1 for the
    #: ordinary single-protein case, length 2+ only when more than one
    #: distinct protein's own independent query legitimately discovered the
    #: identical external source record (confirmed live, Real Integration
    #: Pilot 1 Run 7: yeast's real FAS1/FAS2). Order is not significant and
    #: must never be read as "who discovered it first" -- that information,
    #: when needed, belongs to
    #: ``Agent1KnowledgePackage.kinetic_measurement_protein_contexts``
    #: (``created_at``), not to this tuple's own ordering.
    protein_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CuratedPublication:
    """Agent 2-facing view of one publication's own primary date, for kinetic-evidence
    recency prioritization only (Publication Date Handoff increment).

    Deliberately the smallest useful subset of ``Publication`` -- ``id`` (so Agent 2 can
    key a ``publication_id -> year`` mapping the same way ``CuratedKineticMeasurement
    .publication_id`` already references it) and ``year`` alone, never title/journal/
    authors/PMID/DOI/abstract or any other bibliographic metadata Agent 2's own kinetic-
    evidence consolidation has no use for. ``year`` is ``Publication.year`` verbatim --
    the primary publication's own year, never a database ``created_at``/``updated_at``
    timestamp and never inferred from ``pmid``/``doi`` -- ``None`` whenever Agent 1 itself
    never resolved a year for this publication (never fabricated here).
    """

    id: UUID
    year: int | None = None


@dataclass(frozen=True, slots=True)
class CuratedEnzymeState:
    """Agent 2-facing view of one curated enzyme regulatory state (Agent 1.x Increment B).

    A faithful reshaping of one ``EnzymeState`` row. Exactly one of
    ``protein_id``/``complex_id`` is set, mirroring the underlying row's
    own database ``CHECK`` constraint -- this type never conflates
    macromolecule identity with state identity (see
    ``docs/25_enzyme_regulatory_states_contract.md`` §4).
    """

    enzyme_state_id: UUID
    protein_id: UUID | None
    complex_id: UUID | None
    state_type: EnzymeStateType
    state_label: str | None
    compartment_id: UUID | None
    active_state: bool | None
    source: SourceType | None
    source_id: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedEnzymeModification:
    """Agent 2-facing view of one curated enzyme modification (Agent 1.x Increment B).

    A faithful reshaping of one ``EnzymeModification`` row, always
    attached to one ``CuratedEnzymeState`` via ``enzyme_state_id``.
    """

    enzyme_modification_id: UUID
    enzyme_state_id: UUID
    modification_type: ModificationType
    residue: str | None
    residue_position: int | None
    site_label: str | None
    modifying_compound_id: UUID | None
    stoichiometry: Decimal | None
    source: SourceType | None
    source_id: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedAllostericInteraction:
    """Agent 2-facing view of one curated allosteric interaction (Agent 1.x Increment B).

    A faithful reshaping of one ``AllostericInteraction`` row. ``effect``
    is the curated *qualitative* regulatory relationship only -- the
    quantitative kinetic consequence, if any, is a separate, state-specific
    ``CuratedKineticMeasurement`` (see that type's own docstring and
    ``docs/25_enzyme_regulatory_states_contract.md`` §11 for why the two
    are never conflated).
    """

    allosteric_interaction_id: UUID
    enzyme_state_id: UUID
    ligand_compound_id: UUID
    effect: AllostericEffect
    site_label: str | None
    mechanism: str | None
    source: SourceType | None
    source_id: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedEnzymeStateTransition:
    """Agent 2-facing view of one curated enzyme state transition (Agent 1.x Increment B).

    A faithful reshaping of one ``EnzymeStateTransition`` row.
    ``reaction_id`` is ``None`` unless the source knowledge already
    resolves the transition through the existing reaction-curation
    pipeline -- never fabricated.
    """

    enzyme_state_transition_id: UUID
    from_state_id: UUID
    to_state_id: UUID
    transition_type: EnzymeStateTransitionType
    reaction_id: UUID | None
    source: SourceType | None
    source_id: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedExperimentalContext:
    """Agent 2-facing view of one curated ``ExperimentalContext`` row (Agent 1.x
    Increment "Experimental Context and Quantitative Observation Framework").

    A faithful reshaping of one ``ExperimentalContext`` row -- see that
    model's own docstring for why it is a distinct table from the
    pre-existing ``ExperimentalCondition``.
    """

    experimental_context_id: UUID
    organism_id: UUID | None
    strain: str | None
    genotype: str | None
    medium: str | None
    carbon_source: str | None
    temperature_c: Decimal | None
    ph: Decimal | None
    growth_phase: str | None
    growth_condition: str | None
    classification: ExperimentalContextClassification | None
    source: SourceType | None
    source_id: str | None
    publication_id: UUID | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedPerturbation:
    """Agent 2-facing view of one curated ``Perturbation`` row (Agent 1.x
    Increment "Experimental Context and Quantitative Observation Framework").

    A faithful reshaping of one ``Perturbation`` row. ``perturbation_type``
    is exactly the source's own reported string (see
    ``app.models.perturbation.Perturbation``'s own docstring for why this
    is a deliberately open field, never a closed enum).
    """

    perturbation_id: UUID
    perturbation_type: str
    target: str | None
    magnitude: Decimal | None
    magnitude_unit: str | None
    start_time_value: Decimal | None
    start_time_unit: str | None
    start_time_canonical_s: Decimal | None
    duration_value: Decimal | None
    duration_unit: str | None
    duration_canonical_s: Decimal | None
    description: str | None
    source: SourceType | None
    source_id: str | None
    publication_id: UUID | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedQuantitativeObservationDependency:
    """One input a ``CuratedQuantitativeObservation`` (that is itself ``DERIVED``)
    depends on (Agent 1.x Increment "Experimental Context and Quantitative
    Observation Framework"). See
    ``app.models.quantitative_observation.QuantitativeObservationDependency``'s
    own docstring: this represents dependency/provenance only, never a
    derivation computation.
    """

    input_observation_id: UUID
    role: str | None
    assumption_notes: str | None


@dataclass(frozen=True, slots=True)
class CuratedQuantitativeObservation:
    """Agent 2-facing view of one curated ``QuantitativeObservation`` row (Agent 1.x
    Increment "Experimental Context and Quantitative Observation Framework").

    A faithful reshaping of one ``QuantitativeObservation`` row -- never a
    reinterpretation, and never a decision about model usage, mirroring
    ``CuratedKineticMeasurement``'s own identical architectural boundary.
    ``observation_type`` is exactly the source's own reported string (see
    ``app.models.quantitative_observation.QuantitativeObservation``'s own
    docstring for why this is deliberately open, never a closed enum).
    Every identity link (``protein_id``/``compound_id``/``reaction_id``/
    ``organism_id``) is exactly what the underlying row stores -- ``None``
    when Agent 1 could not deterministically resolve it, never guessed;
    ``unresolved_identity_kind``/``unresolved_identity_text`` preserve the
    source's own free-text identity in that case, rather than silently
    dropping it.
    """

    quantitative_observation_id: UUID

    observation_type: str
    reported_observation_type: str | None
    value: Decimal
    unit: str
    normalized_value: Decimal | None
    normalized_unit: str | None

    uncertainty: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    measurement_method: str | None

    evidence_class: QuantitativeEvidenceClass

    time_reference_basis: TimeReferenceBasis | None
    time_value: Decimal | None
    time_unit: str | None
    time_canonical_s: Decimal | None

    experimental_context_id: UUID | None
    perturbation_id: UUID | None

    biological_replicate_id: str | None
    technical_replicate_id: str | None

    protein_id: UUID | None
    compound_id: UUID | None
    reaction_id: UUID | None
    organism_id: UUID | None
    unresolved_identity_kind: str | None
    unresolved_identity_text: str | None

    source: SourceType | None
    source_id: str | None
    publication_id: UUID | None
    dataset_id: str | None

    notes: str | None

    #: Every input this observation depends on, when it is itself ``DERIVED`` -- computed
    #: from ``QuantitativeObservationDependency`` rows, never a stored column (see
    #: this module's own docstring). Empty for every non-``DERIVED`` observation.
    dependencies: tuple[CuratedQuantitativeObservationDependency, ...] = ()


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
    kinetic_measurement_protein_contexts: tuple[KineticMeasurementProteinContext, ...]
    enzyme_states: tuple[EnzymeState, ...]
    enzyme_modifications: tuple[EnzymeModification, ...]
    allosteric_interactions: tuple[AllostericInteraction, ...]
    enzyme_state_transitions: tuple[EnzymeStateTransition, ...]
    experimental_contexts: tuple[ExperimentalContext, ...]
    perturbations: tuple[Perturbation, ...]
    quantitative_observations: tuple[QuantitativeObservation, ...]
    quantitative_observation_dependencies: tuple[QuantitativeObservationDependency, ...]

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
    enzyme_states: tuple[CuratedEnzymeState, ...]
    enzyme_modifications: tuple[CuratedEnzymeModification, ...]
    allosteric_interactions: tuple[CuratedAllostericInteraction, ...]
    enzyme_state_transitions: tuple[CuratedEnzymeStateTransition, ...]
    experimental_contexts: tuple[CuratedExperimentalContext, ...]
    perturbations: tuple[CuratedPerturbation, ...]
    quantitative_observations: tuple[CuratedQuantitativeObservation, ...]

    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    confidence_summaries: tuple[ClaimConfidenceSummary, ...]

    #: Publication Date Handoff increment. Every publication
    #: ``package.publications`` already resolved (itself already scoped to exactly the
    #: publications this run's own kinetic measurements/experimental contexts/
    #: perturbations/quantitative observations reference -- see
    #: ``app.agent1.service.get_agent1_knowledge_package``), reshaped into the minimal
    #: ``CuratedPublication`` (id + year only). Defaulted to ``()`` so every existing
    #: keyword-based construction of this type continues to construct unchanged.
    publications: tuple[CuratedPublication, ...] = ()


__all__ = [
    "AGENT1_CONTRACT_VERSION",
    "Agent1CuratedKnowledgeView",
    "Agent1KnowledgePackage",
    "ClaimConfidenceSummary",
    "ClaimReviewState",
    "CuratedAllostericInteraction",
    "CuratedEnzymeModification",
    "CuratedEnzymeState",
    "CuratedEnzymeStateTransition",
    "CuratedExperimentalContext",
    "CuratedKineticMeasurement",
    "CuratedPerturbation",
    "CuratedPublication",
    "CuratedQuantitativeObservation",
    "CuratedQuantitativeObservationDependency",
    "ProvenanceSummary",
]
