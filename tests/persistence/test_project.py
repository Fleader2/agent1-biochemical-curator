"""Tests for ``app.persistence.project``."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import ProjectType
from app.models.project import ProjectRegulatoryContext
from app.persistence.project import get_project, persist_project
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_organism

pytestmark = pytest.mark.database


def _stable_identifier() -> str:
    return f"PROJ_{uuid.uuid4().hex[:8]}"


def test_creates_new_project(db_session):
    stable_identifier = _stable_identifier()
    result = persist_project(
        stable_identifier=stable_identifier,
        title="FFA biosynthesis in yeast",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )

    assert result.action is PersistenceAction.CREATED
    assert result.created is True
    row = get_project(db_session, result.project_id)
    assert row.stable_identifier == stable_identifier
    assert row.project_type is ProjectType.MICROBIAL_METABOLISM


def test_reuses_existing_project_by_stable_identifier(db_session):
    stable_identifier = _stable_identifier()
    first = persist_project(
        stable_identifier=stable_identifier,
        title="first title",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )

    second = persist_project(
        stable_identifier=stable_identifier,
        title="a different title entirely",
        project_type=ProjectType.REGULATORY_NETWORK,
        session=db_session,
    )

    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.project_id == first.project_id
    # Reuse never overwrites existing content.
    row = get_project(db_session, first.project_id)
    assert row.title == "first title"
    assert row.project_type is ProjectType.MICROBIAL_METABOLISM


def test_microbial_metabolism_project_type(db_session):
    result = persist_project(
        stable_identifier=_stable_identifier(),
        title="test-only",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
    )
    row = get_project(db_session, result.project_id)
    assert row.project_type is ProjectType.MICROBIAL_METABOLISM


def test_regulatory_network_project_type_with_regulatory_context(db_session):
    result = persist_project(
        stable_identifier=_stable_identifier(),
        title="p53 regulatory network",
        project_type=ProjectType.REGULATORY_NETWORK,
        session=db_session,
        regulatory_context={"disease": "colorectal cancer", "tissue": "colon"},
    )

    row = get_project(db_session, result.project_id)
    assert row.project_type is ProjectType.REGULATORY_NETWORK
    context = db_session.query(ProjectRegulatoryContext).filter_by(project_id=row.id).one()
    assert context.disease == "colorectal cancer"
    assert context.tissue == "colon"


def test_reuses_organism_rather_than_duplicating_it(db_session):
    organism = make_organism(db_session, suffix="s-cerevisiae")
    result = persist_project(
        stable_identifier=_stable_identifier(),
        title="test-only",
        project_type=ProjectType.MICROBIAL_METABOLISM,
        session=db_session,
        organism_id=organism.id,
    )

    row = get_project(db_session, result.project_id)
    assert row.organism_id == organism.id
    # persist_project never creates a new Organism row of its own.
    assert db_session.query(type(organism)).filter_by(id=organism.id).count() == 1
