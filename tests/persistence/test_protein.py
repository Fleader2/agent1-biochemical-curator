"""Tests for ``app.persistence.protein``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.protein import Protein
from app.normalization.protein import ProteinIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.protein import persist_protein
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_gene, make_organism


def _identity(**overrides) -> ProteinIdentity:
    merged = {
        "source": SourceType.UNIPROT,
        "source_identifier": "src-1",
        "name": "Test1p",
    } | overrides
    return ProteinIdentity(**merged)


def _result(status: NormalizationStatus, organism_id, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.UNIPROT,
        "source_identifier": "src-1",
        "entity_type": "protein",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "organism_id": organism_id,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.NEW, organism.id, entity_type="gene", match_method=MatchMethod.NONE
    )
    with pytest.raises(EntityTypeMismatchError):
        persist_protein(_identity(), result, organism_id=organism.id, session=db_session)


def test_matched_reuses_existing_row_without_overwriting(db_session):
    organism = make_organism(db_session)
    existing = Protein(organism_id=organism.id, name="Orig1p")
    db_session.add(existing)
    db_session.flush()

    result = _result(NormalizationStatus.MATCHED, organism.id, matched_entity_id=existing.id)
    outcome = persist_protein(
        _identity(name="Different1p"), result, organism_id=organism.id, session=db_session
    )
    assert outcome.action is PersistenceAction.REUSED_EXISTING
    refreshed = db_session.get(Protein, existing.id)
    assert refreshed.name == "Orig1p"


def test_new_creates_a_row_with_supplied_gene_id_passthrough(db_session):
    organism = make_organism(db_session)
    gene = make_gene(db_session, organism_id=organism.id)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_protein(
        _identity(name="New1p", uniprot_id="P12345", gene_id=gene.id),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Protein, outcome.entity_id)
    assert row.name == "New1p"
    assert row.gene_id == gene.id
    assert row.uniprot_id == "P12345"


def test_new_without_name_fails_conservatively(db_session):
    """``uniprot_id`` alone is sufficient identity for ``ProteinIdentity`` construction,

    but ``Protein.name`` is ``NOT NULL`` -- persistence must refuse to guess one.
    """
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_protein(
        ProteinIdentity(source=SourceType.UNIPROT, source_identifier="src-1", uniprot_id="P99999"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_uniprot_id(db_session):
    organism = make_organism(db_session)
    db_session.add(Protein(organism_id=organism.id, name="Existing1p", uniprot_id="P55555"))
    db_session.flush()

    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_protein(
        _identity(name="Duplicate1p", uniprot_id="P55555"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(select(Protein).where(Protein.uniprot_id == "P55555")).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.AMBIGUOUS, organism.id, candidate_entity_ids=(uuid4(), uuid4())
    )
    outcome = persist_protein(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.UNRESOLVED, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_protein(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
