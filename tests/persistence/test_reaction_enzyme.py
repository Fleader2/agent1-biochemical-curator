"""Tests for ``app.persistence.reaction_enzyme``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.reaction import ReactionEnzyme
from app.models.source_cross_reference import SourceCrossReference
from app.normalization.reaction_enzyme import ReactionEnzymeIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.reaction_enzyme import persist_reaction_enzyme
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import (
    make_organism,
    make_protein,
    make_reaction,
    make_reaction_enzyme,
)


def _identity(
    *, reaction_id, protein_id=None, complex_id=None, **overrides
) -> ReactionEnzymeIdentity:
    merged = {
        "source": SourceType.OTHER,
        "source_identifier": "src-1",
        "reaction_id": reaction_id,
        "protein_id": protein_id,
        "complex_id": complex_id,
        "relationship": "CATALYZES",
    } | overrides
    return ReactionEnzymeIdentity(**merged)


def _result(status: NormalizationStatus, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.OTHER,
        "source_identifier": "src-1",
        "entity_type": "reaction_enzyme",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)
    result = _result(
        NormalizationStatus.NEW, entity_type="reaction", match_method=MatchMethod.NONE
    )
    with pytest.raises(EntityTypeMismatchError):
        persist_reaction_enzyme(
            _identity(reaction_id=reaction.id, protein_id=protein.id), result, session=db_session
        )


def test_matched_reuses_existing_association_and_attaches_no_cross_reference(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)
    existing = make_reaction_enzyme(db_session, reaction_id=reaction.id, protein_id=protein.id)

    result = _result(NormalizationStatus.MATCHED, matched_entity_id=existing.id)
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=reaction.id, protein_id=protein.id, relationship="PUTATIVE_CATALYST"),
        result,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.REUSED_EXISTING
    assert outcome.entity_id == existing.id
    assert outcome.source_cross_reference_id is None
    refreshed = db_session.get(ReactionEnzyme, existing.id)
    assert refreshed.relationship == "CATALYZES"  # never overwritten

    rows = db_session.execute(
        select(SourceCrossReference).where(SourceCrossReference.entity_id == existing.id)
    ).scalars().all()
    assert rows == []


def test_new_creates_a_protein_association(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=reaction.id, protein_id=protein.id), result, session=db_session
    )
    assert outcome.action is PersistenceAction.CREATED
    row = db_session.get(ReactionEnzyme, outcome.entity_id)
    assert row.reaction_id == reaction.id
    assert row.protein_id == protein.id
    assert row.complex_id is None
    assert row.relationship == "CATALYZES"
    assert outcome.source_cross_reference_id is None


def test_new_without_relationship_fails_conservatively(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=reaction.id, protein_id=protein.id, relationship=None),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_for_reaction_protein_pair(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    protein = make_protein(db_session, organism_id=organism.id)
    make_reaction_enzyme(db_session, reaction_id=reaction.id, protein_id=protein.id)

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=reaction.id, protein_id=protein.id),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()

    rows = db_session.execute(
        select(ReactionEnzyme).where(
            ReactionEnzyme.reaction_id == reaction.id, ReactionEnzyme.protein_id == protein.id
        )
    ).scalars().all()
    assert len(rows) == 1


def test_ambiguous_requires_review(db_session):
    result = _result(NormalizationStatus.AMBIGUOUS, candidate_entity_ids=(uuid4(), uuid4()))
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=uuid4(), protein_id=uuid4()), result, session=db_session
    )
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    result = _result(NormalizationStatus.UNRESOLVED, match_method=MatchMethod.NONE)
    outcome = persist_reaction_enzyme(
        _identity(reaction_id=uuid4(), protein_id=uuid4(), relationship=None),
        result,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.NO_ACTION
