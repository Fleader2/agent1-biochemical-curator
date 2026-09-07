"""Output-completeness tests for ``app.agent1`` (Increment 27).

Builds one realistic, moderately rich fixture (an organism with a gene,
protein, compound, compartment, reaction, reaction participant,
reaction-enzyme association, regulatory interaction, publication, claim,
and evidence) directly against the ORM -- the same "construct and flush a
minimal valid row" convention ``tests/persistence/conftest.py`` already
established -- and verifies ``get_agent1_knowledge_package`` surfaces every
category. Does not repeat any subsystem's own unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.agent1 import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
    get_agent1_curated_knowledge_view,
    get_agent1_knowledge_package,
)
from app.models.claim import Claim, Evidence
from app.models.enums import (
    ClaimStatus,
    CurationState,
    EvidenceType,
    ReactionParticipantRole,
    RegulatoryEffect,
    SourceType,
)
from app.models.kinetic_measurement import KineticMeasurement
from app.models.publication import Publication
from app.models.reaction import ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.review.types import ReviewDecision
from app.review.workflow import human_review_claim
from tests.persistence.conftest import (
    make_compartment,
    make_compound,
    make_gene,
    make_organism,
    make_protein,
    make_reaction,
    make_reaction_enzyme,
)

pytestmark = pytest.mark.database


def _rich_fixture(session):
    organism = make_organism(session)
    gene = make_gene(session, organism_id=organism.id)
    protein = make_protein(session, organism_id=organism.id)
    compartment = make_compartment(session, organism_id=organism.id)
    compound = make_compound(session)
    reaction = make_reaction(session, organism_id=organism.id)
    participant = ReactionParticipant(
        reaction_id=reaction.id,
        compound_id=compound.id,
        compartment_id=compartment.id,
        role=ReactionParticipantRole.REACTANT,
        stoichiometry=Decimal("1"),
    )
    session.add(participant)
    session.flush()
    reaction_enzyme = make_reaction_enzyme(session, reaction_id=reaction.id, protein_id=protein.id)

    regulatory_interaction = RegulatoryInteraction(
        regulator_type="protein",
        regulator_id=protein.id,
        target_type="reaction",
        target_id=reaction.id,
        effect=RegulatoryEffect.INHIBITION,
        organism_id=organism.id,
    )
    session.add(regulatory_interaction)

    publication = Publication(title="A test-only publication")
    session.add(publication)
    session.flush()

    claim = Claim(
        subject_type="protein",
        subject_id=protein.id,
        predicate="catalyzes",
        object_type="reaction",
        object_id=reaction.id,
        organism_id=organism.id,
        status=ClaimStatus.UNKNOWN,
    )
    session.add(claim)
    session.flush()

    evidence = Evidence(
        claim_id=claim.id,
        publication_id=publication.id,
        source_type=SourceType.PUBMED,
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        quoted_support="The enzyme catalyzed the reaction in vitro.",
        curator_summary="Test-only curator summary.",
    )
    session.add(evidence)
    session.flush()

    kinetic_measurement = KineticMeasurement(
        reaction_id=reaction.id,
        organism_id=organism.id,
        parameter_type="KM",
        parameter_value=Decimal("0.5"),
        unit="mM",
        source=SourceType.BRENDA,
        source_id=f"brenda:{uuid4()}",
    )
    session.add(kinetic_measurement)
    session.flush()

    return {
        "organism": organism,
        "gene": gene,
        "protein": protein,
        "compartment": compartment,
        "compound": compound,
        "reaction": reaction,
        "participant": participant,
        "reaction_enzyme": reaction_enzyme,
        "regulatory_interaction": regulatory_interaction,
        "publication": publication,
        "claim": claim,
        "evidence": evidence,
        "kinetic_measurement": kinetic_measurement,
    }


def test_package_exposes_every_curated_entity_category(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)

    assert isinstance(package, Agent1KnowledgePackage)
    assert package.contract_version == AGENT1_CONTRACT_VERSION
    assert [o.id for o in package.organisms] == [fixture["organism"].id]
    assert [g.id for g in package.genes] == [fixture["gene"].id]
    assert [p.id for p in package.proteins] == [fixture["protein"].id]
    assert fixture["compound"].id in {c.id for c in package.compounds}
    assert fixture["compartment"].id in {c.id for c in package.compartments}
    assert [r.id for r in package.reactions] == [fixture["reaction"].id]


def test_package_exposes_reaction_participant(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert fixture["participant"].id in {p.id for p in package.reaction_participants}


def test_package_exposes_reaction_enzyme_association(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert fixture["reaction_enzyme"].id in {
        re.id for re in package.reaction_enzyme_associations
    }


def test_package_exposes_regulatory_interaction(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert fixture["regulatory_interaction"].id in {
        r.id for r in package.regulatory_interactions
    }


def test_package_exposes_kinetic_measurement(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert fixture["kinetic_measurement"].id in {k.id for k in package.kinetic_measurements}


def test_package_exposes_publication(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert fixture["publication"].id in {p.id for p in package.publications}


def test_package_exposes_claim_and_evidence(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    assert [c.id for c in package.claims] == [fixture["claim"].id]
    assert [e.id for e in package.evidence] == [fixture["evidence"].id]


def test_package_exposes_confidence_summary(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    summary = next(s for s in package.confidence_summaries if s.claim_id == fixture["claim"].id)
    assert summary.status == ClaimStatus.UNKNOWN
    assert summary.confidence_score is None
    assert summary.confidence_class is None


def test_package_exposes_review_state(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    review_state = next(
        r for r in package.review_states if r.claim_id == fixture["claim"].id
    )
    assert review_state.curation_state.value == "PROPOSED"
    assert review_state.history == ()


def test_package_does_not_require_every_category_to_exist(db_session):
    """A scope with only an organism (no reactions/claims/gaps/experiments)
    still returns a valid package -- categories are independently optional."""
    organism = make_organism(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    assert package.reactions == ()
    assert package.claims == ()
    assert package.knowledge_gaps == ()
    assert package.experiment_recommendations == ()
    assert package.experiment_executions == ()
    assert package.experiment_results == ()


def test_provenance_summary_reflects_fixture(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    summary = package.provenance_summary
    assert summary.total_claims == 1
    assert summary.claims_with_evidence == 1
    assert summary.claims_without_evidence == 0
    assert summary.total_evidence == 1
    assert summary.evidence_with_publication == 1
    assert summary.evidence_with_quoted_support == 1
    assert summary.publications_referenced == 1


def test_limitations_always_present(db_session):
    package = get_agent1_knowledge_package(db_session, organism_id=uuid4())
    assert len(package.limitations) > 0
    assert any("egulation" in item for item in package.limitations)
    assert any("ofactor" in item for item in package.limitations)


def test_curated_knowledge_view_is_narrower_than_package(db_session):
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    view = get_agent1_curated_knowledge_view(package)
    assert isinstance(view, Agent1CuratedKnowledgeView)
    assert view.contract_version == AGENT1_CONTRACT_VERSION
    # Claim is still PROPOSED (never reviewed) -- not curated yet.
    assert view.claims == ()
    # Structural/schema records still pass through.
    assert [r.id for r in view.reactions] == [fixture["reaction"].id]


def test_curated_knowledge_view_exposes_kinetic_measurement_unfiltered(db_session):
    """No Claim/CurationState gate exists for KineticMeasurement -- it always passes through."""
    fixture = _rich_fixture(db_session)
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    view = get_agent1_curated_knowledge_view(package)

    curated = next(
        k for k in view.kinetic_measurements
        if k.kinetic_measurement_id == fixture["kinetic_measurement"].id
    )
    assert curated.parameter_type == "KM"
    assert curated.value == Decimal("0.5")
    assert curated.unit == "mM"
    assert curated.source == SourceType.BRENDA
    assert curated.reaction_id == fixture["reaction"].id
    # No unit-conversion framework exists yet -- always None this increment.
    assert curated.normalized_value is None
    assert curated.normalized_unit is None


def test_curated_knowledge_view_includes_accepted_claim(db_session):
    fixture = _rich_fixture(db_session)
    human_review_claim(
        ReviewDecision(
            claim_id=fixture["claim"].id,
            decision=CurationState.HUMAN_ACCEPTED,
            reviewer="curator@example.com",
            reason="looks solid",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        claim=fixture["claim"],
        session=db_session,
    )
    package = get_agent1_knowledge_package(db_session, organism_id=fixture["organism"].id)
    view = get_agent1_curated_knowledge_view(package)
    assert [c.id for c in view.claims] == [fixture["claim"].id]
    assert [e.id for e in view.evidence] == [fixture["evidence"].id]
