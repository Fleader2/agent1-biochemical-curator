"""Tests for ``app.persistence.model_specification``."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import (
    ModelSpecificationGeneProteinRole,
    ModelSpecificationPathwayRole,
    ModelSpecificationReactionRole,
    ModelSpecificationSpeciesRole,
    ProjectType,
    SubprojectTaskType,
)
from app.persistence.model_specification import (
    add_model_specification_compartment,
    add_model_specification_enzyme_state,
    add_model_specification_gene_protein,
    add_model_specification_pathway,
    add_model_specification_reaction,
    add_model_specification_regulatory_interaction,
    add_model_specification_species,
    get_model_specification,
    persist_model_specification,
)
from app.persistence.project import persist_project
from app.persistence.subproject import persist_subproject
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import (
    make_compartment,
    make_compound,
    make_enzyme_state,
    make_gene,
    make_organism,
    make_protein,
    make_reaction,
    make_regulatory_interaction,
)

pytestmark = pytest.mark.database


def _make_subproject_id(db_session) -> uuid.UUID:
    project = persist_project(
        stable_identifier=f"PROJ_{uuid.uuid4().hex[:8]}",
        title="test-only project",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )
    subproject = persist_subproject(
        project_id=project.project_id,
        title=f"test-only subproject {uuid.uuid4().hex[:8]}",
        task_type=SubprojectTaskType.BUILD_MODEL,
        session=db_session,
    )
    return subproject.subproject_id


def test_creates_new_specification(db_session):
    subproject_id = _make_subproject_id(db_session)
    result = persist_model_specification(subproject_id=subproject_id, session=db_session)

    assert result.action is PersistenceAction.CREATED
    row = get_model_specification(db_session, result.model_specification_id)
    assert row.subproject_id == subproject_id


def test_reuses_existing_specification_for_the_same_subproject(db_session):
    subproject_id = _make_subproject_id(db_session)
    first = persist_model_specification(subproject_id=subproject_id, session=db_session, notes="v1")
    second = persist_model_specification(
        subproject_id=subproject_id, session=db_session, notes="v2"
    )

    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.model_specification_id == first.model_specification_id
    row = get_model_specification(db_session, first.model_specification_id)
    assert row.notes == "v1"


def test_seed_metabolites_and_boundary_species(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    compound = make_compound(db_session)
    compartment = make_compartment(db_session)

    seed = add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.SEED,
        session=db_session,
        compound_id=compound.id,
    )
    boundary = add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.BOUNDARY,
        session=db_session,
        compartment_id=compartment.id,
        raw_identifier="extracellular glucose",
    )

    assert seed.action is PersistenceAction.CREATED
    assert boundary.action is PersistenceAction.CREATED
    assert seed.child_id != boundary.child_id


def test_input_and_output_species(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id

    input_result = add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.INPUT,
        session=db_session,
        raw_identifier="glucose",
    )
    output_result = add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.OUTPUT,
        session=db_session,
        raw_identifier="palmitate",
    )

    assert input_result.action is PersistenceAction.CREATED
    assert output_result.action is PersistenceAction.CREATED


def test_required_and_excluded_reactions(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    reaction = make_reaction(db_session)

    required = add_model_specification_reaction(
        model_specification_id=spec_id,
        role=ModelSpecificationReactionRole.REQUIRED,
        session=db_session,
        reaction_id=reaction.id,
    )
    excluded = add_model_specification_reaction(
        model_specification_id=spec_id,
        role=ModelSpecificationReactionRole.EXCLUDED,
        session=db_session,
        raw_identifier="R_unresolved_reaction",
    )

    assert required.action is PersistenceAction.CREATED
    assert excluded.action is PersistenceAction.CREATED


def test_seed_genes_and_proteins(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    organism = make_organism(db_session)
    gene = make_gene(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)

    gene_result = add_model_specification_gene_protein(
        model_specification_id=spec_id,
        role=ModelSpecificationGeneProteinRole.SEED,
        session=db_session,
        gene_id=gene.id,
    )
    protein_result = add_model_specification_gene_protein(
        model_specification_id=spec_id,
        role=ModelSpecificationGeneProteinRole.IMPORTANT,
        session=db_session,
        protein_id=protein.id,
    )

    assert gene_result.action is PersistenceAction.CREATED
    assert protein_result.action is PersistenceAction.CREATED


def test_gene_protein_rejects_both_gene_and_protein_set(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    organism = make_organism(db_session)
    gene = make_gene(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)

    with pytest.raises(ValueError):
        add_model_specification_gene_protein(
            model_specification_id=spec_id,
            role=ModelSpecificationGeneProteinRole.SEED,
            session=db_session,
            gene_id=gene.id,
            protein_id=protein.id,
        )


def test_boundary_species_and_compartments_to_include(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    compartment = make_compartment(db_session)

    result = add_model_specification_compartment(
        model_specification_id=spec_id, session=db_session, compartment_id=compartment.id
    )
    assert result.action is PersistenceAction.CREATED


def test_regulatory_interactions_to_include(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    interaction = make_regulatory_interaction(db_session)

    result = add_model_specification_regulatory_interaction(
        model_specification_id=spec_id,
        session=db_session,
        regulatory_interaction_id=interaction.id,
    )
    assert result.action is PersistenceAction.CREATED


def test_enzyme_states_to_include(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id)

    result = add_model_specification_enzyme_state(
        model_specification_id=spec_id, session=db_session, enzyme_state_id=state.id
    )
    assert result.action is PersistenceAction.CREATED


def test_pathway_scope_inclusion_and_exclusion(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id

    include = add_model_specification_pathway(
        model_specification_id=spec_id,
        role=ModelSpecificationPathwayRole.INCLUDE,
        session=db_session,
        external_pathway_id="map00061",
        name="Fatty acid biosynthesis",
    )
    exclude = add_model_specification_pathway(
        model_specification_id=spec_id,
        role=ModelSpecificationPathwayRole.EXCLUDE,
        session=db_session,
        external_pathway_id="map00062",
        name="Fatty acid elongation",
    )

    assert include.action is PersistenceAction.CREATED
    assert exclude.action is PersistenceAction.CREATED


def test_unresolved_specification_identifiers_are_retained(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id

    result = add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.SEED,
        session=db_session,
        raw_identifier="some-unresolved-user-provided-name",
    )

    assert result.action is PersistenceAction.CREATED
    from app.models.model_specification import ModelSpecificationSpecies

    row = db_session.get(ModelSpecificationSpecies, result.child_id)
    assert row.compound_id is None
    assert row.raw_identifier == "some-unresolved-user-provided-name"


def test_idempotent_child_persistence(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    reaction = make_reaction(db_session)

    first = add_model_specification_reaction(
        model_specification_id=spec_id,
        role=ModelSpecificationReactionRole.REQUIRED,
        session=db_session,
        reaction_id=reaction.id,
    )
    second = add_model_specification_reaction(
        model_specification_id=spec_id,
        role=ModelSpecificationReactionRole.REQUIRED,
        session=db_session,
        reaction_id=reaction.id,
    )

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.child_id == second.child_id


def test_no_accidental_duplication_of_existing_biological_entities(db_session):
    subproject_id = _make_subproject_id(db_session)
    spec_id = persist_model_specification(
        subproject_id=subproject_id, session=db_session
    ).model_specification_id
    compound = make_compound(db_session)

    add_model_specification_species(
        model_specification_id=spec_id,
        role=ModelSpecificationSpeciesRole.SEED,
        session=db_session,
        compound_id=compound.id,
    )

    assert db_session.query(type(compound)).filter_by(id=compound.id).count() == 1
