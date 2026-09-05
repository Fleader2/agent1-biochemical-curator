"""Tests for ``app.persistence.compartment``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.compartment import Compartment
from app.models.enums import SourceType
from app.normalization.compartment import CompartmentIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.compartment import persist_compartment
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_compartment, make_organism


def _identity(**overrides) -> CompartmentIdentity:
    merged = {
        "source": SourceType.OTHER,
        "source_identifier": "src-1",
        "name": "cytoplasm",
    } | overrides
    return CompartmentIdentity(**merged)


def _result(status: NormalizationStatus, organism_id=None, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.OTHER,
        "source_identifier": "src-1",
        "entity_type": "compartment",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "organism_id": organism_id,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    result = _result(NormalizationStatus.NEW, entity_type="compound", match_method=MatchMethod.NONE)
    with pytest.raises(EntityTypeMismatchError):
        persist_compartment(_identity(), result, organism_id=None, session=db_session)


def test_matched_reuses_existing_row_without_overwriting(db_session):
    existing = make_compartment(db_session, suffix="orig")
    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)
    outcome = persist_compartment(
        _identity(name="a different name"), result, organism_id=None, session=db_session
    )
    assert outcome.action is PersistenceAction.REUSED_EXISTING
    refreshed = db_session.get(Compartment, existing.id)
    assert refreshed.name == "test-only compartment orig"


def test_new_creates_a_reference_row_with_null_organism(db_session):
    """``organism_id=None`` is a legitimate reference-scope compartment, not an error."""
    result = _result(NormalizationStatus.NEW, organism_id=None, match_method=MatchMethod.NONE)
    outcome = persist_compartment(
        _identity(name="cytosol", ontology_id="GO:0005829"),
        result,
        organism_id=None,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Compartment, outcome.entity_id)
    assert row.organism_id is None
    assert row.name == "cytosol"


def test_new_creates_an_organism_scoped_row(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.NEW, organism_id=organism.id, match_method=MatchMethod.NONE
    )
    outcome = persist_compartment(
        _identity(name="mitochondrial matrix"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Compartment, outcome.entity_id)
    assert row.organism_id == organism.id


def test_new_without_name_fails_conservatively(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_compartment(
        _identity(name=None, ontology_id="GO:0005737"), result, organism_id=None, session=db_session
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_ontology_id(db_session):
    db_session.add(Compartment(name="Existing", ontology_id="GO:0005737"))
    db_session.flush()

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_compartment(
        _identity(name="Duplicate attempt", ontology_id="GO:0005737"),
        result,
        organism_id=None,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(
        select(Compartment).where(Compartment.ontology_id == "GO:0005737")
    ).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    result = _result(NormalizationStatus.AMBIGUOUS, candidate_entity_ids=(uuid4(), uuid4()))
    outcome = persist_compartment(_identity(), result, organism_id=None, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    result = _result(NormalizationStatus.UNRESOLVED, match_method=MatchMethod.NONE)
    outcome = persist_compartment(_identity(), result, organism_id=None, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
