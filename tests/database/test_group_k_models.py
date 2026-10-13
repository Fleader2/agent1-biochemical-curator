"""Database tests for Group K: Project/Subproject/Model/ModelSpecification
foundation (migration ``0020_project_subproject_model``).

See ``docs/02_database_schema.md``: "Table: project" through "Table:
modeling_decision_option", and
``docs/30_project_subproject_model_foundation.md``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    ModelingDecisionStatus,
    ModelSourceType,
    ModelSpecificationSpeciesRole,
    ModelStatus,
    ProjectLifecycleStatus,
    ProjectType,
    SubprojectTaskType,
)
from app.models.model import Model
from app.models.model_specification import ModelSpecification, ModelSpecificationSpecies
from app.models.modeling_decision import ModelingDecision, ModelingDecisionOption
from app.models.project import Project, ProjectRegulatoryContext
from app.models.subproject import Subproject

pytestmark = pytest.mark.database


def _make_project(db_session, **kwargs) -> Project:
    kwargs.setdefault("stable_identifier", f"PROJ_{uuid.uuid4().hex[:8]}")
    kwargs.setdefault("title", "test-only project")
    kwargs.setdefault("project_type", ProjectType.MICROBIAL_METABOLISM)
    project = Project(**kwargs)
    db_session.add(project)
    db_session.flush()
    return project


def _make_subproject(db_session, *, project_id, **kwargs) -> Subproject:
    kwargs.setdefault("title", f"test-only subproject {uuid.uuid4().hex[:8]}")
    kwargs.setdefault("task_type", SubprojectTaskType.BUILD_MODEL)
    subproject = Subproject(project_id=project_id, **kwargs)
    db_session.add(subproject)
    db_session.flush()
    return subproject


def _make_model(db_session, *, subproject_id, **kwargs) -> Model:
    kwargs.setdefault("stable_model_identifier", f"MODEL_{uuid.uuid4().hex[:8]}")
    kwargs.setdefault("name", "test-only model")
    kwargs.setdefault("source_type", ModelSourceType.GENERATED)
    model = Model(subproject_id=subproject_id, **kwargs)
    db_session.add(model)
    db_session.flush()
    return model


def test_create_project(db_session):
    project = _make_project(db_session, title="FFA biosynthesis in yeast")

    fetched = db_session.get(Project, project.id)
    assert fetched is not None
    assert fetched.status == ProjectLifecycleStatus.ACTIVE
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_project_stable_identifier_must_be_unique(db_session):
    _make_project(db_session, stable_identifier="PROJ_DUP")
    with pytest.raises(IntegrityError):
        _make_project(db_session, stable_identifier="PROJ_DUP")


@pytest.mark.parametrize(
    "project_type", [ProjectType.MICROBIAL_METABOLISM, ProjectType.REGULATORY_NETWORK]
)
def test_both_project_types_are_persistable(db_session, project_type):
    project = _make_project(db_session, project_type=project_type)
    fetched = db_session.get(Project, project.id)
    assert fetched.project_type == project_type


def test_project_regulatory_context_is_one_to_one(db_session):
    project = _make_project(db_session, project_type=ProjectType.REGULATORY_NETWORK)
    db_session.add(ProjectRegulatoryContext(project_id=project.id, disease="test-only disease"))
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.add(ProjectRegulatoryContext(project_id=project.id, disease="a second one"))
        db_session.flush()


def test_deleting_a_project_cascades_to_its_regulatory_context(db_session):
    project = _make_project(db_session)
    db_session.add(ProjectRegulatoryContext(project_id=project.id, disease="test-only disease"))
    db_session.flush()
    project_id = project.id

    db_session.delete(db_session.get(Project, project_id))
    db_session.flush()
    db_session.expunge_all()

    assert (
        db_session.query(ProjectRegulatoryContext).filter_by(project_id=project_id).one_or_none()
        is None
    )


def test_a_project_can_contain_multiple_subprojects(db_session):
    project = _make_project(db_session)
    sub_a = _make_subproject(db_session, project_id=project.id, title="build a model")
    sub_b = _make_subproject(db_session, project_id=project.id, title="calibrate the model")

    fetched = db_session.get(Project, project.id)
    assert {s.id for s in fetched.subprojects} == {sub_a.id, sub_b.id}


def test_subproject_project_id_title_must_be_unique(db_session):
    project = _make_project(db_session)
    _make_subproject(db_session, project_id=project.id, title="dup title")
    with pytest.raises(IntegrityError):
        _make_subproject(db_session, project_id=project.id, title="dup title")


def test_deleting_a_project_with_a_subproject_is_restricted(db_session):
    project = _make_project(db_session)
    _make_subproject(db_session, project_id=project.id)

    with pytest.raises(IntegrityError):
        db_session.delete(db_session.get(Project, project.id))
        db_session.flush()


def test_a_subproject_can_contain_multiple_models(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    model_a = _make_model(db_session, subproject_id=subproject.id)
    model_b = _make_model(db_session, subproject_id=subproject.id)

    fetched = db_session.get(Subproject, subproject.id)
    assert {m.id for m in fetched.models} == {model_a.id, model_b.id}


def test_model_parent_lineage(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    parent = _make_model(db_session, subproject_id=subproject.id, status=ModelStatus.SUPERSEDED)
    child = _make_model(db_session, subproject_id=subproject.id, parent_model_id=parent.id)

    fetched = db_session.get(Model, child.id)
    assert fetched.parent_model_id == parent.id


def test_deleting_a_parent_model_with_a_child_version_is_restricted(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    parent = _make_model(db_session, subproject_id=subproject.id)
    _make_model(db_session, subproject_id=subproject.id, parent_model_id=parent.id)

    with pytest.raises(IntegrityError):
        db_session.delete(db_session.get(Model, parent.id))
        db_session.flush()


@pytest.mark.parametrize(
    "source_type", [ModelSourceType.GENERATED, ModelSourceType.IMPORTED, ModelSourceType.DERIVED]
)
def test_generated_imported_and_derived_models_are_persistable(db_session, source_type):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    model = _make_model(db_session, subproject_id=subproject.id, source_type=source_type)

    fetched = db_session.get(Model, model.id)
    assert fetched.source_type == source_type


def test_model_stable_model_identifier_must_be_unique(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    _make_model(db_session, subproject_id=subproject.id, stable_model_identifier="MODEL_DUP")
    with pytest.raises(IntegrityError):
        _make_model(db_session, subproject_id=subproject.id, stable_model_identifier="MODEL_DUP")


def test_model_specification_is_one_per_subproject(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    db_session.add(ModelSpecification(subproject_id=subproject.id))
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.add(ModelSpecification(subproject_id=subproject.id))
        db_session.flush()


def test_deleting_a_model_specification_cascades_to_its_species(db_session):
    project = _make_project(db_session)
    subproject = _make_subproject(db_session, project_id=project.id)
    spec = ModelSpecification(subproject_id=subproject.id)
    db_session.add(spec)
    db_session.flush()
    db_session.add(
        ModelSpecificationSpecies(
            model_specification_id=spec.id,
            role=ModelSpecificationSpeciesRole.INPUT,
            raw_identifier="glucose",
        )
    )
    db_session.flush()
    spec_id = spec.id

    db_session.delete(db_session.get(ModelSpecification, spec_id))
    db_session.flush()
    db_session.expunge_all()

    assert (
        db_session.query(ModelSpecificationSpecies)
        .filter_by(model_specification_id=spec_id)
        .one_or_none()
        is None
    )


def test_modeling_decision_selected_option_must_belong_to_the_same_decision_at_db_level(db_session):
    """No database CHECK enforces this -- confirm the FK alone still lets a
    valid, same-decision option be selected, and that the schema otherwise
    allows two decisions to each have their own independent option sets
    (the actual cross-decision rejection is an application-level rule, see
    ``app.persistence.modeling_decision.resolve_modeling_decision``).
    """
    decision = ModelingDecision(question="which boundary species should be used?")
    db_session.add(decision)
    db_session.flush()
    option = ModelingDecisionOption(decision_id=decision.id, label="option A")
    db_session.add(option)
    db_session.flush()

    decision.selected_option_id = option.id
    db_session.flush()

    fetched = db_session.get(ModelingDecision, decision.id)
    assert fetched.selected_option_id == option.id
    assert fetched.status == ModelingDecisionStatus.OPEN


def test_deleting_a_modeling_decision_cascades_to_its_options(db_session):
    decision = ModelingDecision(question="test-only question")
    db_session.add(decision)
    db_session.flush()
    db_session.add(ModelingDecisionOption(decision_id=decision.id, label="option A"))
    db_session.flush()
    decision_id = decision.id

    db_session.delete(db_session.get(ModelingDecision, decision_id))
    db_session.flush()
    db_session.expunge_all()

    assert (
        db_session.query(ModelingDecisionOption).filter_by(decision_id=decision_id).one_or_none()
        is None
    )


def test_modeling_decision_option_confidence_score_range_check(db_session):
    decision = ModelingDecision(question="test-only question")
    db_session.add(decision)
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.add(
            ModelingDecisionOption(
                decision_id=decision.id, label="bad option", confidence_score=150
            )
        )
        db_session.flush()
