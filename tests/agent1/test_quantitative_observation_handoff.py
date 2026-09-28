"""Handoff tests for experimental context and quantitative observations (Agent 1.x
Increment "Experimental Context and Quantitative Observation Framework").

Builds a small, realistic fixture directly against the ORM (mirroring
``tests/agent1/test_outputs.py``'s own convention) and verifies
``get_agent1_knowledge_package``/``get_agent1_curated_knowledge_view`` expose it
deterministically and without loss -- never repeating
``tests/database/test_group_i_models.py``'s or
``tests/normalization/test_quantitative_observation.py``'s own unit tests.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.agent1 import (
    AGENT1_CONTRACT_VERSION,
    get_agent1_curated_knowledge_view,
    get_agent1_knowledge_package,
)
from app.models.enums import (
    ExperimentalContextClassification,
    QuantitativeEvidenceClass,
    SourceType,
)
from app.models.experimental_context import ExperimentalContext
from app.models.organism import Organism
from app.models.perturbation import Perturbation
from app.models.protein import Protein
from app.models.quantitative_observation import (
    QuantitativeObservation,
    QuantitativeObservationDependency,
)


def _fixture(session):
    organism = Organism(scientific_name=f"Test organism {uuid4().hex[:8]}")
    session.add(organism)
    session.flush()

    protein = Protein(organism_id=organism.id, name=f"Test protein {uuid4().hex[:8]}")
    session.add(protein)
    session.flush()

    ctx = ExperimentalContext(
        organism_id=organism.id,
        strain="BY4741",
        medium="YPD",
        classification=ExperimentalContextClassification.REFERENCE,
        source=SourceType.SGD,
        source_id="sgd-ref-1",
    )
    session.add(ctx)
    session.flush()

    pert = Perturbation(perturbation_type="TEMPERATURE_PH", target="temperature")
    session.add(pert)
    session.flush()

    abundance = QuantitativeObservation(
        observation_type="PROTEIN_ABUNDANCE",
        value=Decimal("5000"),
        unit="molecules/cell",
        normalized_value=Decimal("5000"),
        normalized_unit="molecules_per_cell",
        evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
        experimental_context_id=ctx.id,
        protein_id=protein.id,
        organism_id=organism.id,
        source=SourceType.SGD,
        source_id=f"sgd-abundance-{uuid4()}",
    )
    session.add(abundance)
    session.flush()

    derived = QuantitativeObservation(
        observation_type="PROTEIN_CONCENTRATION",
        value=Decimal("8.3"),
        unit="nM",
        evidence_class=QuantitativeEvidenceClass.DERIVED,
        experimental_context_id=ctx.id,
        protein_id=protein.id,
        organism_id=organism.id,
    )
    session.add(derived)
    session.flush()

    session.add(
        QuantitativeObservationDependency(
            derived_observation_id=derived.id,
            input_observation_id=abundance.id,
            role="protein_abundance_input",
            assumption_notes="assumed reference cell volume of 0.1 pL",
        )
    )
    session.flush()

    return organism, protein, ctx, pert, abundance, derived


def test_contract_version_bumped_for_this_increment():
    assert AGENT1_CONTRACT_VERSION == "1.4"


def test_package_exposes_new_records(db_session):
    organism, _protein, ctx, pert, abundance, derived = _fixture(db_session)

    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)

    assert {c.id for c in package.experimental_contexts} == {ctx.id}
    assert {o.id for o in package.quantitative_observations} == {abundance.id, derived.id}
    assert len(package.quantitative_observation_dependencies) == 1
    # The perturbation exists but is never referenced by any scoped observation in this
    # fixture -- it must not be silently pulled in.
    assert pert.id not in {p.id for p in package.perturbations}


def test_curated_view_reshapes_deterministically_and_without_loss(db_session):
    organism, protein, ctx, _pert, abundance, derived = _fixture(db_session)

    package = get_agent1_knowledge_package(db_session, organism_id=organism.id)
    view = get_agent1_curated_knowledge_view(package)

    assert view.contract_version == "1.4"
    assert len(view.experimental_contexts) == 1
    curated_ctx = view.experimental_contexts[0]
    assert curated_ctx.experimental_context_id == ctx.id
    assert curated_ctx.strain == "BY4741"
    assert curated_ctx.classification is ExperimentalContextClassification.REFERENCE

    curated_by_id = {o.quantitative_observation_id: o for o in view.quantitative_observations}
    curated_abundance = curated_by_id[abundance.id]
    assert curated_abundance.observation_type == "PROTEIN_ABUNDANCE"
    assert curated_abundance.evidence_class is QuantitativeEvidenceClass.REFERENCE_BASELINE
    assert curated_abundance.protein_id == protein.id
    assert curated_abundance.normalized_value == Decimal("5000")
    assert curated_abundance.dependencies == ()

    curated_derived = curated_by_id[derived.id]
    assert curated_derived.evidence_class is QuantitativeEvidenceClass.DERIVED
    assert len(curated_derived.dependencies) == 1
    dependency = curated_derived.dependencies[0]
    assert dependency.input_observation_id == abundance.id
    assert dependency.role == "protein_abundance_input"
    assert "0.1 pL" in dependency.assumption_notes


def test_handoff_round_trip_is_deterministic(db_session):
    """Calling the handoff twice over the identical, already-persisted state must produce
    field-for-field identical results -- no randomness, no ordering dependence."""
    organism, *_ = _fixture(db_session)

    first_view = get_agent1_curated_knowledge_view(
        get_agent1_knowledge_package(db_session, organism_id=organism.id)
    )
    second_view = get_agent1_curated_knowledge_view(
        get_agent1_knowledge_package(db_session, organism_id=organism.id)
    )

    def _key(o):
        return str(o.quantitative_observation_id)

    assert sorted(first_view.quantitative_observations, key=_key) == sorted(
        second_view.quantitative_observations, key=_key
    )
    assert sorted(
        first_view.experimental_contexts, key=lambda c: str(c.experimental_context_id)
    ) == sorted(second_view.experimental_contexts, key=lambda c: str(c.experimental_context_id))


def test_whole_database_export_includes_unreferenced_perturbation(db_session):
    """organism_id=None sees everything, including a perturbation no scoped observation
    references -- mirrors every other table's own identical "None = everything" policy."""
    _organism, _protein, _ctx, pert, _abundance, _derived = _fixture(db_session)

    package = get_agent1_knowledge_package(db_session, organism_id=None)
    assert pert.id in {p.id for p in package.perturbations}
