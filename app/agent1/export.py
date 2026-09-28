"""The Agent 2 handoff view (Increment 27, scope freeze).

``get_agent1_curated_knowledge_view`` builds ``Agent1CuratedKnowledgeView``
-- the deliberately narrow, model-relevant, **curated-only** subset of an
``Agent1KnowledgePackage`` intended for Agent 2 (the Antimony Builder).
Data only: reactions, participants, compounds, compartments, enzyme
associations, regulation, provenance, and confidence. No reaction-graph
optimization, no kinetic-law selection, no parameter selection, no
Antimony syntax, and no validation occur anywhere in this module -- those
are Agent 2/3 responsibilities (``docs/23_agent1_v1_scope_and_completion.md``
§§23, 29).

This module never queries the database itself -- it is a pure, read-only
reshaping of an already-assembled ``Agent1KnowledgePackage``
(``app.agent1.service.get_agent1_knowledge_package``), so it never
duplicates that function's own query logic.

**Kinetic measurements** (Agent 1.x Increment A). Every
``package.kinetic_measurements`` row is reshaped into a
``CuratedKineticMeasurement`` and passed through unfiltered -- see
``app.agent1.types``'s module docstring for why no ``HUMAN_ACCEPTED``-style
gate applies (no ``Claim``/``CurationState`` exists on this table). This
reshaping is purely mechanical (one field copied to another of the same
name) and asserts no kinetic-law selection, parameter mapping, or model
usage decision -- those remain Agent 2's responsibility.

**Protein context** (Agent 1.x Increment C.6). ``CuratedKineticMeasurement
.protein_ids`` -- the authoritative field for "which protein(s) is this
measurement applicable to," per that dataclass's own field comment -- is
computed here, not copied from a single column: the union of
``row.protein_id`` (a legacy convenience field only, kept solely for
pre-C.6 callers) and every ``kinetic_measurement_protein_context`` row
scoped to it, so a measurement two independent proteins legitimately,
separately discovered (SABIO-RK's own EC-scoped search; confirmed live,
Real Integration Pilot 1 Run 7, yeast's real FAS1/FAS2) reaches Agent 2's
handoff with both contexts intact -- never just whichever protein happened
to persist the underlying source record first.

**Enzyme regulatory states** (Agent 1.x Increment B). Every
``package.enzyme_states``/``.enzyme_modifications``/
``.allosteric_interactions``/``.enzyme_state_transitions`` row is reshaped
into its ``Curated*`` counterpart and passed through unfiltered, for the
identical reason kinetic measurements are (no ``Claim``/``CurationState``
column on any of the four tables). Each reshaping is purely mechanical.

**Experimental context and quantitative observations** (Agent 1.x
Increment "Experimental Context and Quantitative Observation Framework").
Every ``package.experimental_contexts``/``.perturbations``/
``.quantitative_observations`` row is reshaped into its ``Curated*``
counterpart and passed through unfiltered, for the identical reason
kinetic measurements are (no ``Claim``/``CurationState`` column on any of
the four new tables). ``CuratedQuantitativeObservation.dependencies`` is
computed here from ``package.quantitative_observation_dependencies``,
exactly like ``CuratedKineticMeasurement.protein_ids`` is computed from
``package.kinetic_measurement_protein_contexts`` -- never copied from a
single column, and never itself a derivation (see that dataclass's own
docstring).
"""

from __future__ import annotations

from app.agent1.service import curated_claims
from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
    CuratedAllostericInteraction,
    CuratedEnzymeModification,
    CuratedEnzymeState,
    CuratedEnzymeStateTransition,
    CuratedExperimentalContext,
    CuratedKineticMeasurement,
    CuratedPerturbation,
    CuratedQuantitativeObservation,
    CuratedQuantitativeObservationDependency,
)
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.models.experimental_context import ExperimentalContext
from app.models.kinetic_measurement import KineticMeasurement
from app.models.perturbation import Perturbation
from app.models.quantitative_observation import (
    QuantitativeObservation,
    QuantitativeObservationDependency,
)


def get_agent1_curated_knowledge_view(
    package: Agent1KnowledgePackage,
) -> Agent1CuratedKnowledgeView:
    """Narrow ``package`` to the model-relevant, curated-only subset Agent 2 needs.

    ``claims``/``evidence`` here are restricted to ``HUMAN_ACCEPTED``
    claims only (Increment 27 instructions, Step 10's eligibility policy,
    applied via ``app.agent1.service.curated_claims`` -- never
    reimplemented here). Reactions, participants, compounds, compartments,
    enzyme associations, and regulation are passed through unfiltered from
    the package: these are structural/schema records, not claims, and
    carry no curation state of their own to filter by (see
    ``docs/23_agent1_v1_scope_and_completion.md`` §11 for the disclosed
    regulation limitation this applies to as well).
    """
    accepted = curated_claims(package)
    accepted_ids = {claim.id for claim in accepted}
    accepted_evidence = tuple(
        record for record in package.evidence if record.claim_id in accepted_ids
    )
    accepted_confidence = tuple(
        summary for summary in package.confidence_summaries if summary.claim_id in accepted_ids
    )
    protein_ids_by_measurement: dict = {}
    for context in package.kinetic_measurement_protein_contexts:
        protein_ids_by_measurement.setdefault(context.kinetic_measurement_id, set()).add(
            context.protein_id
        )
    kinetic_measurements = tuple(
        _curated_kinetic_measurement(
            row, protein_ids_by_measurement.get(row.id, frozenset())
        )
        for row in package.kinetic_measurements
    )
    enzyme_states = tuple(_curated_enzyme_state(row) for row in package.enzyme_states)
    enzyme_modifications = tuple(
        _curated_enzyme_modification(row) for row in package.enzyme_modifications
    )
    allosteric_interactions = tuple(
        _curated_allosteric_interaction(row) for row in package.allosteric_interactions
    )
    enzyme_state_transitions = tuple(
        _curated_enzyme_state_transition(row) for row in package.enzyme_state_transitions
    )
    experimental_contexts = tuple(
        _curated_experimental_context(row) for row in package.experimental_contexts
    )
    perturbations = tuple(_curated_perturbation(row) for row in package.perturbations)
    dependencies_by_observation: dict = {}
    for dependency in package.quantitative_observation_dependencies:
        dependencies_by_observation.setdefault(dependency.derived_observation_id, []).append(
            dependency
        )
    quantitative_observations = tuple(
        _curated_quantitative_observation(
            row, dependencies_by_observation.get(row.id, ())
        )
        for row in package.quantitative_observations
    )

    return Agent1CuratedKnowledgeView(
        contract_version=AGENT1_CONTRACT_VERSION,
        organism_id=package.organism_id,
        compartments=package.compartments,
        compounds=package.compounds,
        reactions=package.reactions,
        reaction_participants=package.reaction_participants,
        reaction_enzyme_associations=package.reaction_enzyme_associations,
        regulatory_interactions=package.regulatory_interactions,
        kinetic_measurements=kinetic_measurements,
        enzyme_states=enzyme_states,
        enzyme_modifications=enzyme_modifications,
        allosteric_interactions=allosteric_interactions,
        enzyme_state_transitions=enzyme_state_transitions,
        experimental_contexts=experimental_contexts,
        perturbations=perturbations,
        quantitative_observations=quantitative_observations,
        claims=accepted,
        evidence=accepted_evidence,
        confidence_summaries=accepted_confidence,
    )


def _curated_kinetic_measurement(
    row: KineticMeasurement, additional_protein_ids
) -> CuratedKineticMeasurement:
    """Pure field-for-field reshaping of one ``KineticMeasurement`` row. No I/O, no inference.

    ``additional_protein_ids`` (Agent 1.x Increment C.6) is the set of
    protein ids ``kinetic_measurement_protein_context`` records for this row
    -- ``protein_ids`` is always their union with ``row.protein_id`` itself
    (never just one or the other), so a caller never has to separately check
    both fields to get the complete, order-independent set of applicable
    proteins.
    """
    protein_ids = set(additional_protein_ids)
    if row.protein_id is not None:
        protein_ids.add(row.protein_id)
    return CuratedKineticMeasurement(
        kinetic_measurement_id=row.id,
        reaction_id=row.reaction_id,
        protein_id=row.protein_id,
        protein_ids=tuple(sorted(protein_ids, key=str)),
        complex_id=row.complex_id,
        substrate_id=row.substrate_id,
        organism_id=row.organism_id,
        publication_id=row.publication_id,
        parameter_type=row.parameter_type,
        reported_parameter_type=row.reported_parameter_type,
        value=row.parameter_value,
        unit=row.unit,
        normalized_value=row.normalized_value,
        normalized_unit=row.normalized_unit,
        strain=row.strain,
        temperature_c=row.temperature_c,
        ph=row.ph,
        reported_rate_law=row.reported_rate_law,
        source=row.source,
        source_id=row.source_id,
        confidence_score=row.confidence_score,
        confidence_class=row.confidence_class,
        notes=row.notes,
        enzyme_state_id=row.enzyme_state_id,
    )


def _curated_enzyme_state(row: EnzymeState) -> CuratedEnzymeState:
    """Pure field-for-field reshaping of one ``EnzymeState`` row. No I/O, no inference."""
    return CuratedEnzymeState(
        enzyme_state_id=row.id,
        protein_id=row.protein_id,
        complex_id=row.complex_id,
        state_type=row.state_type,
        state_label=row.state_label,
        compartment_id=row.compartment_id,
        active_state=row.active_state,
        source=row.source,
        source_id=row.source_id,
        notes=row.notes,
    )


def _curated_enzyme_modification(row: EnzymeModification) -> CuratedEnzymeModification:
    """Pure field-for-field reshaping of one ``EnzymeModification`` row. No I/O, no inference."""
    return CuratedEnzymeModification(
        enzyme_modification_id=row.id,
        enzyme_state_id=row.enzyme_state_id,
        modification_type=row.modification_type,
        residue=row.residue,
        residue_position=row.residue_position,
        site_label=row.site_label,
        modifying_compound_id=row.modifying_compound_id,
        stoichiometry=row.stoichiometry,
        source=row.source,
        source_id=row.source_id,
        notes=row.notes,
    )


def _curated_allosteric_interaction(row: AllostericInteraction) -> CuratedAllostericInteraction:
    """Pure field-for-field reshaping of one ``AllostericInteraction`` row. No I/O, no inference."""
    return CuratedAllostericInteraction(
        allosteric_interaction_id=row.id,
        enzyme_state_id=row.enzyme_state_id,
        ligand_compound_id=row.ligand_compound_id,
        effect=row.effect,
        site_label=row.site_label,
        mechanism=row.mechanism,
        source=row.source,
        source_id=row.source_id,
        notes=row.notes,
    )


def _curated_enzyme_state_transition(
    row: EnzymeStateTransition,
) -> CuratedEnzymeStateTransition:
    """Pure field-for-field reshaping of one ``EnzymeStateTransition`` row. No I/O, no inference."""
    return CuratedEnzymeStateTransition(
        enzyme_state_transition_id=row.id,
        from_state_id=row.from_state_id,
        to_state_id=row.to_state_id,
        transition_type=row.transition_type,
        reaction_id=row.reaction_id,
        source=row.source,
        source_id=row.source_id,
        notes=row.notes,
    )


def _curated_experimental_context(row: ExperimentalContext) -> CuratedExperimentalContext:
    """Pure field-for-field reshaping of one ``ExperimentalContext`` row. No I/O, no inference."""
    return CuratedExperimentalContext(
        experimental_context_id=row.id,
        organism_id=row.organism_id,
        strain=row.strain,
        genotype=row.genotype,
        medium=row.medium,
        carbon_source=row.carbon_source,
        temperature_c=row.temperature_c,
        ph=row.ph,
        growth_phase=row.growth_phase,
        growth_condition=row.growth_condition,
        classification=row.classification,
        source=row.source,
        source_id=row.source_id,
        publication_id=row.publication_id,
        notes=row.notes,
    )


def _curated_perturbation(row: Perturbation) -> CuratedPerturbation:
    """Pure field-for-field reshaping of one ``Perturbation`` row. No I/O, no inference."""
    return CuratedPerturbation(
        perturbation_id=row.id,
        perturbation_type=row.perturbation_type,
        target=row.target,
        magnitude=row.magnitude,
        magnitude_unit=row.magnitude_unit,
        start_time_value=row.start_time_value,
        start_time_unit=row.start_time_unit,
        start_time_canonical_s=row.start_time_canonical_s,
        duration_value=row.duration_value,
        duration_unit=row.duration_unit,
        duration_canonical_s=row.duration_canonical_s,
        description=row.description,
        source=row.source,
        source_id=row.source_id,
        publication_id=row.publication_id,
        notes=row.notes,
    )


def _curated_quantitative_observation(
    row: QuantitativeObservation, dependency_rows: tuple[QuantitativeObservationDependency, ...]
) -> CuratedQuantitativeObservation:
    """Pure field-for-field reshaping of one ``QuantitativeObservation`` row, plus its
    dependency rows (when it is itself ``DERIVED``) reshaped into
    ``CuratedQuantitativeObservationDependency``. No I/O, no inference, no derivation."""
    dependencies = tuple(
        CuratedQuantitativeObservationDependency(
            input_observation_id=dependency.input_observation_id,
            role=dependency.role,
            assumption_notes=dependency.assumption_notes,
        )
        for dependency in dependency_rows
    )
    return CuratedQuantitativeObservation(
        quantitative_observation_id=row.id,
        observation_type=row.observation_type,
        reported_observation_type=row.reported_observation_type,
        value=row.value,
        unit=row.unit,
        normalized_value=row.normalized_value,
        normalized_unit=row.normalized_unit,
        uncertainty=row.uncertainty,
        lower_bound=row.lower_bound,
        upper_bound=row.upper_bound,
        measurement_method=row.measurement_method,
        evidence_class=row.evidence_class,
        time_reference_basis=row.time_reference_basis,
        time_value=row.time_value,
        time_unit=row.time_unit,
        time_canonical_s=row.time_canonical_s,
        experimental_context_id=row.experimental_context_id,
        perturbation_id=row.perturbation_id,
        biological_replicate_id=row.biological_replicate_id,
        technical_replicate_id=row.technical_replicate_id,
        protein_id=row.protein_id,
        compound_id=row.compound_id,
        reaction_id=row.reaction_id,
        organism_id=row.organism_id,
        unresolved_identity_kind=row.unresolved_identity_kind,
        unresolved_identity_text=row.unresolved_identity_text,
        source=row.source,
        source_id=row.source_id,
        publication_id=row.publication_id,
        dataset_id=row.dataset_id,
        notes=row.notes,
        dependencies=dependencies,
    )


__all__ = ["get_agent1_curated_knowledge_view"]
