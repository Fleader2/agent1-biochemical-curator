"""Tests for ``app.persistence.publication``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.publication import Publication
from app.normalization.publication import PublicationIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.publication import persist_publication
from app.persistence.types import PersistenceAction


def _identity(**overrides) -> PublicationIdentity:
    merged = {
        "source": SourceType.PUBMED,
        "source_identifier": "src-1",
        "pmid": "1000",
    } | overrides
    return PublicationIdentity(**merged)


def _result(status: NormalizationStatus, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.PUBMED,
        "source_identifier": "src-1",
        "entity_type": "publication",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    result = _result(NormalizationStatus.NEW, entity_type="gene", match_method=MatchMethod.NONE)
    with pytest.raises(EntityTypeMismatchError):
        persist_publication(_identity(), result, session=db_session)


def test_matched_reuses_existing_row_without_overwriting(db_session):
    existing = Publication(pmid="1000", title="Original title")
    db_session.add(existing)
    db_session.flush()

    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)
    outcome = persist_publication(_identity(title="A different title"), result, session=db_session)

    assert outcome.action is PersistenceAction.REUSED_EXISTING
    assert outcome.entity_id == existing.id
    refreshed = db_session.get(Publication, existing.id)
    assert refreshed.title == "Original title"


def test_new_creates_a_row(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_publication(
        _identity(pmid="2000", title="A new paper"), result, session=db_session
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Publication, outcome.entity_id)
    assert row.pmid == "2000"
    assert row.title == "A new paper"


def test_new_without_title_fails_conservatively(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_publication(_identity(pmid="3000", title=None), result, session=db_session)
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_doi(db_session):
    db_session.add(Publication(doi="10.1/example", title="Existing"))
    db_session.flush()

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_publication(
        _identity(pmid=None, doi="10.1/example", title="Duplicate attempt"),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(
        select(Publication).where(Publication.doi == "10.1/example")
    ).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    result = _result(NormalizationStatus.AMBIGUOUS, candidate_entity_ids=(uuid4(), uuid4()))
    outcome = persist_publication(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_conflicted_requires_review(db_session):
    result = _result(NormalizationStatus.CONFLICTED, matched_entity_id=uuid4())
    outcome = persist_publication(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    result = _result(NormalizationStatus.UNRESOLVED, match_method=MatchMethod.NONE)
    outcome = persist_publication(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
