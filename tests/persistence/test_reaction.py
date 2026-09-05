"""Tests for ``app.persistence.reaction``.

As of Increment 11 (``migrations/versions/0009_persistence_hardening.py``),
``NEW`` Reaction persistence is supported via
``app.persistence.reaction_id_allocator.allocate_reaction_internal_id`` --
see that module and ``app.persistence.reaction``'s own docstrings for the
mechanism. Every other status is exercised the same way as the other seven
entity persistence modules.
"""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import ReactionParticipantRole, SourceType
from app.models.reaction import Reaction, ReactionParticipant
from app.models.source_cross_reference import SourceCrossReference
from app.normalization.reaction import ReactionIdentity, ReactionParticipantIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import ExternalRecordProvenance
from app.persistence.reaction import persist_reaction
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_compartment, make_compound, make_organism, make_reaction


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


def test_new_creates_a_row_with_allocated_internal_id(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)

    outcome = persist_reaction(
        _identity(kegg_reaction_id="R00299", name="a fully-specified new reaction"),
        result,
        organism_id=organism.id,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.CREATED
    assert outcome.entity_id is not None
    row = db_session.get(Reaction, outcome.entity_id)
    assert row.organism_id == organism.id
    assert row.kegg_reaction_id == "R00299"
    assert re.fullmatch(r"FFA_R\d{4,}", row.internal_id)


def test_new_without_name_fails_conservatively(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_reaction(
        ReactionIdentity(
            source=SourceType.KEGG, source_identifier="src-1", kegg_reaction_id="R00300"
        ),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None


def test_stale_new_detected_via_kegg_reaction_id(db_session):
    organism = make_organism(db_session)
    db_session.add(
        Reaction(internal_id="FFA_TESTDUP0001", name="existing", kegg_reaction_id="R00301")
    )
    db_session.flush()

    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_reaction(
        _identity(kegg_reaction_id="R00301", name="duplicate attempt"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.FAILED
    assert "stale" in outcome.reason.lower()


def test_repeated_new_creations_get_distinct_internal_ids(db_session):
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)

    first = persist_reaction(
        _identity(kegg_reaction_id="R00401", name="first reaction"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    second = persist_reaction(
        _identity(kegg_reaction_id="R00402", name="second reaction"),
        result,
        organism_id=organism.id,
        session=db_session,
    )

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.CREATED
    first_row = db_session.get(Reaction, first.entity_id)
    second_row = db_session.get(Reaction, second.entity_id)
    assert first_row.internal_id != second_row.internal_id


def test_new_persists_participants_literally(db_session):
    organism = make_organism(db_session)
    compound_a = make_compound(db_session, suffix="reactant")
    compound_b = make_compound(db_session, suffix="product")
    compartment = make_compartment(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)

    participants = (
        ReactionParticipantIdentity(
            compound_id=compound_a.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=Decimal("2.5"),
            compartment_id=compartment.id,
        ),
        ReactionParticipantIdentity(
            compound_id=compound_b.id,
            role=ReactionParticipantRole.PRODUCT,
            stoichiometry=Decimal("1"),
            compartment_id=None,
        ),
    )
    outcome = persist_reaction(
        _identity(
            kegg_reaction_id="R00500", name="participant reaction", participants=participants
        ),
        result,
        organism_id=organism.id,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.CREATED
    rows = db_session.execute(
        select(ReactionParticipant).where(ReactionParticipant.reaction_id == outcome.entity_id)
    ).scalars().all()
    assert len(rows) == 2
    by_compound = {row.compound_id: row for row in rows}
    reactant_row = by_compound[compound_a.id]
    assert reactant_row.role == ReactionParticipantRole.REACTANT
    assert reactant_row.stoichiometry == Decimal("2.5")
    assert reactant_row.compartment_id == compartment.id
    product_row = by_compound[compound_b.id]
    assert product_row.role == ReactionParticipantRole.PRODUCT
    assert product_row.stoichiometry == Decimal("1")
    assert product_row.compartment_id is None


def test_new_without_participants_still_succeeds(db_session):
    """Participants remain not required for NEW -- open question J
    (docs/07_normalization_design.md) is not resolved by this increment."""
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    outcome = persist_reaction(
        _identity(kegg_reaction_id="R00600", name="no participants"),
        result,
        organism_id=organism.id,
        session=db_session,
    )
    assert outcome.action is PersistenceAction.CREATED


def test_new_forced_participant_failure_rolls_back_reaction_as_a_unit(db_session):
    """An invalid participant compound_id must roll back the whole reaction
    creation as one unit, and must not poison the caller's own transaction --
    only a SAVEPOINT is rolled back, never the outer transaction this package
    never commits or rolls back itself."""
    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    bogus_compound_id = uuid4()

    outcome = persist_reaction(
        _identity(
            kegg_reaction_id="R00700",
            name="doomed reaction",
            participants=(
                ReactionParticipantIdentity(
                    compound_id=bogus_compound_id,
                    role=ReactionParticipantRole.REACTANT,
                    stoichiometry=Decimal("1"),
                ),
            ),
        ),
        result,
        organism_id=organism.id,
        session=db_session,
    )

    assert outcome.action is PersistenceAction.FAILED
    assert outcome.entity_id is None

    rows = db_session.execute(
        select(Reaction).where(Reaction.kegg_reaction_id == "R00700")
    ).scalars().all()
    assert rows == []

    # The caller's own transaction must still be usable afterward.
    another_organism = make_organism(db_session, suffix="post-rollback")
    assert another_organism.id is not None


def test_new_attaches_source_cross_reference_reused_idempotently_on_later_match(db_session):
    organism = make_organism(db_session)
    new_result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    identity = _identity(kegg_reaction_id="R00800", name="cross-ref reaction")

    created = persist_reaction(identity, new_result, organism_id=organism.id, session=db_session)
    assert created.action is PersistenceAction.CREATED
    assert created.source_cross_reference_id is not None

    matched_result = _result(
        NormalizationStatus.MATCHED, organism.id, matched_entity_id=created.entity_id
    )
    matched = persist_reaction(
        identity, matched_result, organism_id=organism.id, session=db_session
    )
    assert matched.source_cross_reference_id == created.source_cross_reference_id

    rows = db_session.execute(
        select(SourceCrossReference).where(SourceCrossReference.entity_id == created.entity_id)
    ).scalars().all()
    assert len(rows) == 1


def test_new_appends_external_record_provenance(db_session):
    from datetime import UTC, datetime

    organism = make_organism(db_session)
    result = _result(NormalizationStatus.NEW, organism.id, match_method=MatchMethod.NONE)
    provenance = ExternalRecordProvenance(
        retrieval_date=datetime(2024, 1, 1, tzinfo=UTC),
        raw_response_hash="deadbeef",
        external_id="R00900",
    )
    outcome = persist_reaction(
        _identity(kegg_reaction_id="R00900", name="provenance reaction"),
        result,
        organism_id=organism.id,
        session=db_session,
        provenance=provenance,
    )
    assert outcome.action is PersistenceAction.CREATED
    assert outcome.external_record_id is not None


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
