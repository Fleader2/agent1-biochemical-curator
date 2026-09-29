"""Tests for the Publication Date Handoff increment: ``Agent1CuratedKnowledgeView
.publications`` and its reshaping in ``app.agent1.export``.

Pure, in-memory tests (no database session, no flush) -- mirrors
``tests/pathway_curation/test_readiness.py``'s own established convention for
exercising read-only reshaping code against directly-constructed ORM rows.
"""

from __future__ import annotations

from uuid import uuid4

from app.agent1.export import _curated_publication, get_agent1_curated_knowledge_view
from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1KnowledgePackage,
    CuratedPublication,
    ProvenanceSummary,
)
from app.models.publication import Publication


def _provenance_summary() -> ProvenanceSummary:
    return ProvenanceSummary(
        total_claims=0,
        claims_with_evidence=0,
        claims_without_evidence=0,
        total_evidence=0,
        evidence_with_publication=0,
        evidence_with_quoted_support=0,
        publications_referenced=0,
    )


def _publication(**overrides) -> Publication:
    defaults = {
        "id": uuid4(),
        "title": "fake publication",
        "year": 2001,
    }
    defaults.update(overrides)
    return Publication(**defaults)


def _package(**overrides) -> Agent1KnowledgePackage:
    defaults = {
        "contract_version": AGENT1_CONTRACT_VERSION,
        "organism_id": None,
        "organisms": (),
        "genes": (),
        "proteins": (),
        "compounds": (),
        "compartments": (),
        "reactions": (),
        "reaction_participants": (),
        "reaction_enzyme_associations": (),
        "regulatory_interactions": (),
        "publications": (),
        "kinetic_measurements": (),
        "kinetic_measurement_protein_contexts": (),
        "enzyme_states": (),
        "enzyme_modifications": (),
        "allosteric_interactions": (),
        "enzyme_state_transitions": (),
        "experimental_contexts": (),
        "perturbations": (),
        "quantitative_observations": (),
        "quantitative_observation_dependencies": (),
        "claims": (),
        "evidence": (),
        "confidence_summaries": (),
        "review_states": (),
        "knowledge_gaps": (),
        "experiment_recommendations": (),
        "experiment_executions": (),
        "experiment_results": (),
        "provenance_summary": _provenance_summary(),
        "limitations": (),
    }
    defaults.update(overrides)
    return Agent1KnowledgePackage(**defaults)


def test_reshape_preserves_id_and_year():
    pub = _publication(year=1995)
    curated = _curated_publication(pub)
    assert curated == CuratedPublication(id=pub.id, year=1995)


def test_reshape_preserves_missing_year_as_none():
    pub = _publication(year=None)
    curated = _curated_publication(pub)
    assert curated.year is None
    assert curated.id == pub.id


def test_curated_view_carries_every_package_publication_unfiltered():
    pub_a = _publication(year=2010)
    pub_b = _publication(year=None)
    package = _package(publications=(pub_a, pub_b))

    view = get_agent1_curated_knowledge_view(package)

    assert set(view.publications) == {
        CuratedPublication(id=pub_a.id, year=2010),
        CuratedPublication(id=pub_b.id, year=None),
    }


def test_curated_view_publications_default_empty():
    view = get_agent1_curated_knowledge_view(_package())
    assert view.publications == ()


def test_contract_version_bumped_for_this_increment():
    assert AGENT1_CONTRACT_VERSION == "1.5"
