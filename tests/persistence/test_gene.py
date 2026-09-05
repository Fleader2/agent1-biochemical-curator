"""Tests for ``app.persistence.gene``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.gene import Gene
from app.normalization.gene import GeneIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.gene import persist_gene
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_organism


def _identity(**overrides) -> GeneIdentity:
    merged = {
        "source": SourceType.SGD,
        "source_identifier": "src-1",
        "symbol": "ABC1",
    } | overrides
    return GeneIdentity(**merged)


def _result(status: NormalizationStatus, organism_id, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.SGD,
        "source_identifier": "src-1",
        "entity_type": "gene",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "organism_id": organism_id,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.NEW, organism.id, entity_type="protein", match_method=MatchMethod.NONE
    )
    with pytest.raises(EntityTypeMismatchError):
        persist_gene(_identity(), result, organism_id=organism.id, session=db_session)


def test_matched_reuses_existing_row_without_overwriting(db_session):
    organism = make_organism(db_session)
    existing = Gene(organism_id=organism.id, symbol="ORIG1")
    db_session.add(existing)
    db_session.flush()

    result = _result(NormalizationStatus.MATCHED, organism.id, matched_entity_id=existing.id)
    outcome = persist_gene(
        _identity(symbol="DIFFERENT1"), result, organism_id=organism.id, session=db_session
    )

    assert outcome.action is PersistenceAction.REUSED_EXISTING
    refreshed = db_session.get(Gene, existing.id)
    assert refreshed.symbol == "ORIG1"


def test_new_creates_a_row_scoped_to_organism(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_gene(
        _identity(symbol="NEW1", aliases=("N1", "N2")),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Gene, outcome.entity_id)
    assert row.organism_id == organism.id
    assert row.symbol == "NEW1"
    assert row.aliases_json == ["N1", "N2"]


def test_new_with_only_kegg_gene_id_fails_creation_completeness(db_session):
    """``kegg_gene_id`` is a Level 1 identity signal but is not part of the

    schema's own creation-completeness rule (symbol/systematic_name/
    ncbi_gene_id/sgd_id) -- ``GeneIdentity`` accepts it as sufficient
    identity to normalize against, but ``persist_gene`` must still refuse to
    create a row from it alone.
    """
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_gene(
        GeneIdentity(
            source=SourceType.SGD, source_identifier="src-1", kegg_gene_id="sce:YAL001C"
        ),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_sgd_id_globally(db_session):
    other_organism = make_organism(db_session, suffix="other")
    db_session.add(Gene(organism_id=other_organism.id, sgd_id="S000000099"))
    db_session.flush()

    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_gene(
        _identity(symbol=None, sgd_id="S000000099"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(select(Gene).where(Gene.sgd_id == "S000000099")).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.AMBIGUOUS, organism.id, candidate_entity_ids=(uuid4(), uuid4())
    )
    outcome = persist_gene(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.UNRESOLVED, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_gene(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
