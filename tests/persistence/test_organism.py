"""Tests for ``app.persistence.organism``."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.organism import Organism
from app.models.source_cross_reference import SourceCrossReference
from app.normalization.organism import OrganismIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.organism import persist_organism
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_organism


def _identity(**overrides) -> OrganismIdentity:
    merged = {
        "source": SourceType.NCBI,
        "source_identifier": "src-1",
        "scientific_name": "Test org",
    } | overrides
    return OrganismIdentity(**merged)


def _result(status: NormalizationStatus, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.NCBI,
        "source_identifier": "src-1",
        "entity_type": "organism",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    result = _result(NormalizationStatus.NEW, entity_type="gene", match_method=MatchMethod.NONE)
    with pytest.raises(EntityTypeMismatchError):
        persist_organism(_identity(), result, session=db_session)


def test_matched_reuses_existing_row_and_attaches_cross_reference(db_session):
    existing = make_organism(db_session, suffix="matched")
    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)

    outcome = persist_organism(
        _identity(scientific_name="a completely different name"), result, session=db_session
    )

    assert outcome.action is PersistenceAction.REUSED_EXISTING
    assert outcome.entity_id == existing.id
    assert outcome.source_cross_reference_id is not None

    refreshed = db_session.get(Organism, existing.id)
    assert refreshed.scientific_name == "Test organism matched"  # never overwritten


def test_matched_cross_reference_attachment_is_idempotent(db_session):
    existing = make_organism(db_session, suffix="idempotent")
    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)

    first = persist_organism(_identity(), result, session=db_session)
    second = persist_organism(_identity(), result, session=db_session)
    assert first.source_cross_reference_id == second.source_cross_reference_id

    rows = db_session.execute(
        select(SourceCrossReference).where(SourceCrossReference.entity_id == existing.id)
    ).scalars().all()
    assert len(rows) == 1


def test_new_creates_a_row(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_organism(
        _identity(scientific_name="Brand new organism", ncbi_taxonomy_id=123456),
        result,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.CREATED
    assert outcome.entity_id is not None
    row = db_session.get(Organism, outcome.entity_id)
    assert row.scientific_name == "Brand new organism"
    assert row.ncbi_taxonomy_id == 123456


def test_new_without_scientific_name_fails_conservatively(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_organism(
        _identity(scientific_name=None, ncbi_taxonomy_id=999), result, session=db_session
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_is_detected_and_rejected(db_session):
    """A row matching the identifier appears after normalization ran but before persistence."""
    make_organism(db_session, suffix="stale")
    Organism_row = Organism(scientific_name="Someone else", ncbi_taxonomy_id=42)
    db_session.add(Organism_row)
    db_session.flush()

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_organism(
        _identity(scientific_name="Brand new", ncbi_taxonomy_id=42), result, session=db_session
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    count = db_session.execute(
        select(Organism).where(Organism.ncbi_taxonomy_id == 42)
    ).scalars().all()
    assert len(count) == 1


def test_ambiguous_requires_review(db_session):
    result = _result(
        NormalizationStatus.AMBIGUOUS,
        candidate_entity_ids=(uuid4(), uuid4()),
        matched_entity_id=None,
    )
    outcome = persist_organism(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW
    assert outcome.review_required is True
    assert outcome.entity_id is None


def test_conflicted_requires_review(db_session):
    result = _result(NormalizationStatus.CONFLICTED, matched_entity_id=uuid4())
    outcome = persist_organism(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    result = _result(NormalizationStatus.UNRESOLVED, match_method=MatchMethod.NONE)
    outcome = persist_organism(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
    assert outcome.entity_id is None


def test_persist_organism_never_commits_the_session(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    db_session.commit = Mock(side_effect=AssertionError("persist_organism must not commit"))
    persist_organism(_identity(scientific_name="No commit here"), result, session=db_session)
