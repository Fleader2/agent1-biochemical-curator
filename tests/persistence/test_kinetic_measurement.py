"""Tests for ``app.persistence.kinetic_measurement``."""

from __future__ import annotations

import inspect
import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.kinetic_measurement import KineticMeasurement
from app.models.reaction import Reaction
from app.normalization.kinetic_measurement import KineticMeasurementIdentity, KineticParameterType
from app.persistence import kinetic_measurement as kinetic_measurement_module
from app.persistence.kinetic_measurement import (
    get_kinetic_measurement,
    list_kinetic_measurements_by_reaction,
    persist_kinetic_measurement,
)
from app.persistence.kinetic_measurement_types import KineticMeasurementPersistenceResult
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _identity(**overrides: object) -> KineticMeasurementIdentity:
    base = {
        "source": SourceType.BRENDA,
        "source_id": f"brenda:{uuid4()}",
        "parameter_type": KineticParameterType.KM,
        "value": Decimal("0.33"),
        "unit": "mM",
    }
    base.update(overrides)
    return KineticMeasurementIdentity(**base)


# --- create ------------------------------------------------------------------------


def test_create_new_measurement(db_session):
    identity = _identity()
    result = persist_kinetic_measurement(identity, session=db_session)

    assert isinstance(result, KineticMeasurementPersistenceResult)
    assert result.action is PersistenceAction.CREATED
    assert result.created is True
    assert result.kinetic_measurement_id is not None

    row = get_kinetic_measurement(db_session, result.kinetic_measurement_id)
    assert row is not None
    assert row.source == SourceType.BRENDA
    assert row.source_id == identity.source_id
    assert row.parameter_value == Decimal("0.33")
    assert row.unit == "mM"
    assert row.parameter_type == "KM"


def test_create_sets_original_value_equal_to_reported_value(db_session):
    """No unit conversion this increment -- original and working values start identical."""
    identity = _identity(value=Decimal("1.5"), unit="1/s")
    result = persist_kinetic_measurement(identity, session=db_session)
    row = get_kinetic_measurement(db_session, result.kinetic_measurement_id)
    assert row.original_value == Decimal("1.5")
    assert row.original_unit == "1/s"
    assert row.normalized_value is None
    assert row.normalized_unit is None


def test_create_attaches_source_cross_reference(db_session):
    from app.models.source_cross_reference import SourceCrossReference

    identity = _identity()
    result = persist_kinetic_measurement(identity, session=db_session)

    xref = db_session.execute(
        select(SourceCrossReference).where(
            SourceCrossReference.id == result.source_cross_reference_id
        )
    ).scalar_one()
    assert xref.entity_type == "kinetic_measurement"
    assert xref.entity_id == result.kinetic_measurement_id
    assert xref.source == SourceType.BRENDA
    assert xref.external_id == identity.source_id


def test_range_maximum_preserved_in_notes_never_averaged(db_session):
    identity = _identity(value=Decimal("0.3"), value_maximum=Decimal("0.5"), unit="mM")
    result = persist_kinetic_measurement(identity, session=db_session)
    row = get_kinetic_measurement(db_session, result.kinetic_measurement_id)
    assert row.parameter_value == Decimal("0.3")  # never averaged with 0.5
    assert "0.5" in (row.notes or "")


def _reaction(session: Session) -> Reaction:
    row = Reaction(internal_id=f"R{uuid4()}", name="test reaction")
    session.add(row)
    session.flush()
    return row


def test_passes_through_resolved_entity_ids(db_session):
    reaction = _reaction(db_session)
    identity = _identity(reaction_id=reaction.id)
    result = persist_kinetic_measurement(identity, session=db_session)
    row = get_kinetic_measurement(db_session, result.kinetic_measurement_id)
    assert row.reaction_id == reaction.id


# --- idempotent replay ---------------------------------------------------------------


def test_exact_replay_reuses_existing_row(db_session):
    identity = _identity()
    first = persist_kinetic_measurement(identity, session=db_session)
    second = persist_kinetic_measurement(identity, session=db_session)

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.kinetic_measurement_id == first.kinetic_measurement_id

    rows = db_session.execute(
        select(KineticMeasurement).where(KineticMeasurement.source_id == identity.source_id)
    ).scalars().all()
    assert len(rows) == 1


def test_independent_measurements_with_identical_value_both_persist(db_session):
    """Never numeric-equality-based deduplication -- distinct source_id, both persist."""
    identity_a = _identity(source_id="brenda:a", value=Decimal("1.0"))
    identity_b = _identity(source_id="brenda:b", value=Decimal("1.0"))

    result_a = persist_kinetic_measurement(identity_a, session=db_session)
    result_b = persist_kinetic_measurement(identity_b, session=db_session)

    assert result_a.action is PersistenceAction.CREATED
    assert result_b.action is PersistenceAction.CREATED
    assert result_a.kinetic_measurement_id != result_b.kinetic_measurement_id


def test_reuse_never_overwrites_existing_row(db_session):
    identity = _identity(value=Decimal("1.0"), notes="first")
    first = persist_kinetic_measurement(identity, session=db_session)

    replay_with_different_notes = _identity(
        source=identity.source, source_id=identity.source_id, value=Decimal("1.0"), notes="second"
    )
    persist_kinetic_measurement(replay_with_different_notes, session=db_session)

    row = get_kinetic_measurement(db_session, first.kinetic_measurement_id)
    assert row.notes == "first"  # never rewritten by the "replay"


# --- derivative lineage merge (Increment A Step 25) -----------------------------------


def test_derivative_lineage_merges_into_existing_row(db_session):
    original = _identity(source=SourceType.SABIORK, source_id="sabiork:1:Km", value=Decimal("0.4"))
    original_result = persist_kinetic_measurement(original, session=db_session)

    derivative = _identity(
        source=SourceType.OED,
        source_id="oed:digest-xyz",
        value=Decimal("0.4"),
        original_source=SourceType.SABIORK,
        original_source_identifier="sabiork:1:Km",
    )
    derivative_result = persist_kinetic_measurement(derivative, session=db_session)

    assert derivative_result.action is PersistenceAction.REUSED_EXISTING
    assert derivative_result.kinetic_measurement_id == original_result.kinetic_measurement_id

    rows = db_session.execute(select(KineticMeasurement)).scalars().all()
    assert len(rows) == 1  # no duplicate row created for the derivative


def test_derivative_lineage_attaches_cross_reference_for_new_source(db_session):
    from app.models.source_cross_reference import SourceCrossReference

    original = _identity(source=SourceType.SABIORK, source_id="sabiork:2:Km")
    original_result = persist_kinetic_measurement(original, session=db_session)

    derivative = _identity(
        source=SourceType.OED,
        source_id="oed:digest-abc",
        original_source=SourceType.SABIORK,
        original_source_identifier="sabiork:2:Km",
    )
    persist_kinetic_measurement(derivative, session=db_session)

    xrefs = db_session.execute(
        select(SourceCrossReference).where(
            SourceCrossReference.entity_id == original_result.kinetic_measurement_id
        )
    ).scalars().all()
    sources = {xref.source for xref in xrefs}
    assert SourceType.OED in sources


def test_unresolved_claimed_origin_falls_through_to_creation(db_session):
    """A derivative claim against an origin that was never ingested creates its own row."""
    derivative = _identity(
        source=SourceType.OED,
        source_id="oed:digest-orphan",
        original_source=SourceType.SABIORK,
        original_source_identifier="sabiork:does-not-exist",
    )
    result = persist_kinetic_measurement(derivative, session=db_session)
    assert result.action is PersistenceAction.CREATED


def test_derivative_never_adjusts_confidence(db_session):
    original = _identity(source=SourceType.SABIORK, source_id="sabiork:3:Km")
    original_result = persist_kinetic_measurement(original, session=db_session)
    row_before = get_kinetic_measurement(db_session, original_result.kinetic_measurement_id)
    confidence_before = row_before.confidence_score

    derivative = _identity(
        source=SourceType.OED,
        source_id="oed:digest-conf",
        original_source=SourceType.SABIORK,
        original_source_identifier="sabiork:3:Km",
    )
    persist_kinetic_measurement(derivative, session=db_session)

    row_after = get_kinetic_measurement(db_session, original_result.kinetic_measurement_id)
    assert row_after.confidence_score == confidence_before


# --- read APIs -----------------------------------------------------------------------


def test_get_kinetic_measurement_returns_none_for_missing_id(db_session):
    assert get_kinetic_measurement(db_session, uuid4()) is None


def test_list_kinetic_measurements_by_reaction(db_session):
    reaction = _reaction(db_session)
    other_reaction = _reaction(db_session)
    persist_kinetic_measurement(_identity(reaction_id=reaction.id), session=db_session)
    persist_kinetic_measurement(_identity(reaction_id=reaction.id), session=db_session)
    persist_kinetic_measurement(_identity(reaction_id=other_reaction.id), session=db_session)

    rows = list_kinetic_measurements_by_reaction(db_session, reaction.id)
    assert len(rows) == 2
    assert all(row.reaction_id == reaction.id for row in rows)


# --- concurrency -----------------------------------------------------------------------


def test_concurrent_same_source_record_yields_one_row(migrated_engine: Engine) -> None:
    source_id = f"brenda:{uuid4()}"
    identity = _identity(source_id=source_id)

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                ready.wait(timeout=5)
                outcomes[label] = persist_kinetic_measurement(identity, session=session)
                session.commit()
            except BaseException as exc:
                errors[label] = exc
            finally:
                session.close()

    threads = [threading.Thread(target=attempt, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == {}, f"persist_kinetic_measurement leaked an unhandled exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            rows = verify_connection.execute(
                select(KineticMeasurement).where(KineticMeasurement.source_id == source_id)
            ).all()
            assert len(rows) == 1
    finally:
        from app.models.source_cross_reference import SourceCrossReference

        with migrated_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                delete(SourceCrossReference).where(
                    SourceCrossReference.entity_type == "kinetic_measurement",
                    SourceCrossReference.external_id == source_id,
                )
            )
            cleanup_connection.execute(
                delete(KineticMeasurement).where(KineticMeasurement.source_id == source_id)
            )
            cleanup_connection.commit()


# --- transactions ------------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(kinetic_measurement_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_uses_savepoint():
    source = inspect.getsource(kinetic_measurement_module)
    assert "begin_nested" in source


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    identity = _identity()
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = persist_kinetic_measurement(identity, session=db_session)
    assert result.action is PersistenceAction.FAILED
    assert result.kinetic_measurement_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    identity = _identity()
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    persist_kinetic_measurement(identity, session=db_session)

    monkeypatch.undo()
    rows = db_session.execute(
        select(KineticMeasurement).where(KineticMeasurement.source_id == identity.source_id)
    ).scalars().all()
    assert rows == []


def test_rejects_non_identity_type(db_session):
    with pytest.raises(TypeError):
        persist_kinetic_measurement("not-an-identity", session=db_session)
