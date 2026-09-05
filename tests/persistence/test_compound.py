"""Tests for ``app.persistence.compound``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.compound import Compound, CompoundSynonym
from app.models.enums import SourceType
from app.normalization.compound import CompoundIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.compound import persist_compound
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.types import PersistenceAction


def _identity(**overrides) -> CompoundIdentity:
    merged = {
        "source": SourceType.CHEBI,
        "source_identifier": "src-1",
        "canonical_name": "D-glucose",
    } | overrides
    return CompoundIdentity(**merged)


def _result(status: NormalizationStatus, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.CHEBI,
        "source_identifier": "src-1",
        "entity_type": "compound",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    result = _result(NormalizationStatus.NEW, entity_type="protein", match_method=MatchMethod.NONE)
    with pytest.raises(EntityTypeMismatchError):
        persist_compound(_identity(), result, session=db_session)


def test_matched_reuses_existing_row_without_overwriting_chemistry(db_session):
    existing = Compound(canonical_name="Original name", formula="C6H12O6", charge=0)
    db_session.add(existing)
    db_session.flush()

    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)
    outcome = persist_compound(
        _identity(canonical_name="Different name", formula="C6H11O6", charge=-1),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.REUSED_EXISTING
    refreshed = db_session.get(Compound, existing.id)
    assert refreshed.canonical_name == "Original name"
    assert refreshed.formula == "C6H12O6"
    assert refreshed.charge == 0


def test_new_creates_a_row_with_literal_chemistry_and_synonyms(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_compound(
        _identity(
            canonical_name="D-glucose",
            formula="C6H12O6",
            charge=0,
            chebi_id="CHEBI:4167",
            synonyms=("dextrose", "grape sugar"),
        ),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(Compound, outcome.entity_id)
    assert row.formula == "C6H12O6"
    assert row.charge == 0
    assert row.is_generic is False  # never forced True/None when identity leaves it unspecified

    synonyms = db_session.execute(
        select(CompoundSynonym).where(CompoundSynonym.compound_id == row.id)
    ).scalars().all()
    assert {s.synonym for s in synonyms} == {"dextrose", "grape sugar"}


def test_new_does_not_write_synonyms_on_matched_reuse(db_session):
    existing = Compound(canonical_name="Existing compound")
    db_session.add(existing)
    db_session.flush()

    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)
    persist_compound(
        _identity(canonical_name="Existing compound", synonyms=("should not be added",)),
        result,
        session=db_session,
    )
    synonyms = db_session.execute(
        select(CompoundSynonym).where(CompoundSynonym.compound_id == existing.id)
    ).scalars().all()
    assert synonyms == []


def test_new_without_canonical_name_fails_conservatively(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_compound(
        _identity(canonical_name=None, chebi_id="CHEBI:9999"), result, session=db_session
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_inchikey(db_session):
    db_session.add(Compound(canonical_name="Existing", inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N"))
    db_session.flush()

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_compound(
        _identity(canonical_name="Dup attempt", inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N"),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(
        select(Compound).where(Compound.inchikey == "WQZGKKKJIJFFOK-GASJEMHNSA-N")
    ).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    result = _result(NormalizationStatus.AMBIGUOUS, candidate_entity_ids=(uuid4(), uuid4()))
    outcome = persist_compound(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    result = _result(NormalizationStatus.UNRESOLVED, match_method=MatchMethod.NONE)
    outcome = persist_compound(_identity(), result, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
