"""Tests for ``app.persistence.reaction``.

The headline behavior under test is that ``NEW`` is deliberately
unsupported (see ``app.persistence.reaction`` module docstring: no
production-safe ``internal_id`` allocator exists) -- every other status is
exercised the same way as the other seven entity persistence modules.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import SourceType
from app.models.reaction import Reaction
from app.normalization.reaction import ReactionIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.reaction import persist_reaction
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_organism, make_reaction


def _identity(**overrides) -> ReactionIdentity:
    merged = {
        "source": SourceType.KEGG,
        "source_identifier": "src-1",
        "name": "hexokinase reaction",
    } | overrides
    return ReactionIdentity(**merged)


def _result(status: NormalizationStatus, organism_id, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.KEGG,
        "source_identifier": "src-1",
        "entity_type": "reaction",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
        "organism_id": organism_id,
    } | overrides
    return NormalizationResult(**merged)


def test_entity_type_mismatch_raises(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.MATCHED,
        organism.id,
        entity_type="compound",
        matched_entity_id=uuid4(),
    )
    with pytest.raises(EntityTypeMismatchError):
        persist_reaction(_identity(), result, organism_id=organism.id, session=db_session)


def test_matched_reuses_existing_row_and_never_touches_participants(db_session):
    organism = make_organism(db_session)
    existing = make_reaction(db_session, organism_id=organism.id)

    result = _result(NormalizationStatus.MATCHED, organism.id, matched_entity_id=existing.id)
    outcome = persist_reaction(_identity(), result, organism_id=organism.id, session=db_session)

    assert outcome.action is PersistenceAction.REUSED_EXISTING
    assert outcome.entity_id == existing.id
    refreshed = db_session.get(Reaction, existing.id)
    assert refreshed.participants == []


def test_new_is_always_failed_regardless_of_supplied_identity(db_session):
    """The architecturally-unsupported case: no internal_id allocator exists."""
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)

    outcome = persist_reaction(
        _identity(kegg_reaction_id="R00299", name="a fully-specified new reaction"),
        result,
        organism_id=organism.id,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None
    assert "internal_id" in outcome.reason

    rows = db_session.execute(
        select(Reaction).where(Reaction.kegg_reaction_id == "R00299")
    ).scalars().all()
    assert rows == []


def test_ambiguous_requires_review(db_session):
    organism = make_organism(db_session)
    result = _result(
        NormalizationStatus.AMBIGUOUS, organism.id, candidate_entity_ids=(uuid4(), uuid4())
    )
    outcome = persist_reaction(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_conflicted_requires_review(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.CONFLICTED, organism.id, matched_entity_id=uuid4())
    outcome = persist_reaction(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.REQUIRES_REVIEW


def test_unresolved_takes_no_action(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.UNRESOLVED, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_reaction(_identity(), result, organism_id=organism.id, session=db_session)
    assert outcome.action is PersistenceAction.NO_ACTION
