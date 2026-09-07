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
"""

from __future__ import annotations

from app.agent1.service import curated_claims
from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
    CuratedKineticMeasurement,
)
from app.models.kinetic_measurement import KineticMeasurement


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
    kinetic_measurements = tuple(
        _curated_kinetic_measurement(row) for row in package.kinetic_measurements
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
        claims=accepted,
        evidence=accepted_evidence,
        confidence_summaries=accepted_confidence,
    )


def _curated_kinetic_measurement(row: KineticMeasurement) -> CuratedKineticMeasurement:
    """Pure field-for-field reshaping of one ``KineticMeasurement`` row. No I/O, no inference."""
    return CuratedKineticMeasurement(
        kinetic_measurement_id=row.id,
        reaction_id=row.reaction_id,
        protein_id=row.protein_id,
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
    )


__all__ = ["get_agent1_curated_knowledge_view"]
