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
"""

from __future__ import annotations

from app.agent1.service import curated_claims
from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
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

    return Agent1CuratedKnowledgeView(
        contract_version=AGENT1_CONTRACT_VERSION,
        organism_id=package.organism_id,
        compartments=package.compartments,
        compounds=package.compounds,
        reactions=package.reactions,
        reaction_participants=package.reaction_participants,
        reaction_enzyme_associations=package.reaction_enzyme_associations,
        regulatory_interactions=package.regulatory_interactions,
        claims=accepted,
        evidence=accepted_evidence,
        confidence_summaries=accepted_confidence,
    )


__all__ = ["get_agent1_curated_knowledge_view"]
