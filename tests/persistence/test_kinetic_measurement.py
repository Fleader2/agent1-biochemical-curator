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
from app.models.kinetic_measurement import KineticMeasurement, KineticMeasurementProteinContext
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.reaction import Reaction
from app.normalization.kinetic_measurement import KineticMeasurementIdentity, KineticParameterType
from app.persistence import kinetic_measurement as kinetic_measurement_module
from app.persistence.kinetic_measurement import (
    attach_kinetic_measurement_protein_context,
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


def _protein(session: Session, *, name: str = "test protein") -> Protein:
    organism = Organism(scientific_name=f"Test organism {uuid4()}")
    session.add(organism)
    session.flush()
    row = Protein(organism_id=organism.id, name=name)
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


# ---------------------------------------------------------------------------------------------
# Increment C.6 -- Context-preserving kinetic measurement persistence
# ---------------------------------------------------------------------------------------------
#
# Real Integration Pilot 1 Run 7: two distinct proteins (yeast's real FAS1/FAS2)
# sharing one EC number each independently, legitimately discovered the identical
# 7 real SABIO-RK records. Before this increment, the second protein's own
# successful discovery left no trace at all once persist_kinetic_measurement's
# existing (source, source_id) uniqueness check found the first protein's row
# already there. These tests exercise kinetic_measurement_protein_context, the
# join table that fixes this without redesigning (source, source_id) identity
# itself and without ever choosing a "winning" protein.


def _protein_context_rows(session, kinetic_measurement_id):
    return (
        session.execute(
            select(KineticMeasurementProteinContext).where(
                KineticMeasurementProteinContext.kinetic_measurement_id == kinetic_measurement_id
            )
        )
        .scalars()
        .all()
    )


# --- Test A: same source record, two proteins ---------------------------------------------


def test_c6_same_source_record_two_proteins_both_contexts_survive(db_session):
    protein_a = _protein(db_session, name="FAS2-like")
    protein_b = _protein(db_session, name="FAS1-like")
    shared_source_id = "sabiork:18229:Vmax"

    first = persist_kinetic_measurement(
        _identity(source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_a.id),
        session=db_session,
    )
    second = persist_kinetic_measurement(
        _identity(source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_b.id),
        session=db_session,
    )

    # (source, source_id) identity is unchanged: still exactly one row.
    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.kinetic_measurement_id == first.kinetic_measurement_id
    rows = db_session.execute(
        select(KineticMeasurement).where(KineticMeasurement.source_id == shared_source_id)
    ).scalars().all()
    assert len(rows) == 1

    # Legacy .protein_id: whichever protein persisted first (protein_a) -- kept
    # exactly as before, for backward compatibility, never a fabricated re-attribution.
    row = get_kinetic_measurement(db_session, first.kinetic_measurement_id)
    assert row.protein_id == protein_a.id

    # Both protein contexts survive via the new join table -- neither protein
    # "wins" at this level, and protein_b's own successful discovery is not lost.
    contexts = _protein_context_rows(db_session, first.kinetic_measurement_id)
    assert {c.protein_id for c in contexts} == {protein_a.id, protein_b.id}
    assert len(contexts) == 2


# --- Test B: reversed processing order -----------------------------------------------------


def test_c6_reversed_processing_order_yields_identical_context_set(db_session):
    protein_a = _protein(db_session, name="A")
    protein_b = _protein(db_session, name="B")
    shared_source_id = "sabiork:reversed:Km"

    # protein_b processed first this time (order reversed from the test above).
    first = persist_kinetic_measurement(
        _identity(source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_b.id),
        session=db_session,
    )
    persist_kinetic_measurement(
        _identity(source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_a.id),
        session=db_session,
    )

    contexts = _protein_context_rows(db_session, first.kinetic_measurement_id)
    assert {c.protein_id for c in contexts} == {protein_a.id, protein_b.id}
    # The set of surviving contexts is identical regardless of which protein
    # happened to be processed first -- only the legacy .protein_id column
    # (an explicitly-preserved, order-dependent, backward-compatible field)
    # differs between this test and the one above.
    row = get_kinetic_measurement(db_session, first.kinetic_measurement_id)
    assert row.protein_id == protein_b.id


# --- Test C: one protein, one source record (ordinary case unaffected) ----------------------


def test_c6_single_protein_context_unaffected(db_session):
    protein = _protein(db_session)
    result = persist_kinetic_measurement(
        _identity(protein_id=protein.id), session=db_session
    )
    row = get_kinetic_measurement(db_session, result.kinetic_measurement_id)
    assert row.protein_id == protein.id

    contexts = _protein_context_rows(db_session, result.kinetic_measurement_id)
    assert len(contexts) == 1
    assert contexts[0].protein_id == protein.id


def test_c6_no_protein_context_created_when_identity_has_no_protein(db_session):
    """A measurement with no resolved protein at all creates no join-table row --
    never a fabricated NULL-protein context."""
    result = persist_kinetic_measurement(_identity(), session=db_session)
    contexts = _protein_context_rows(db_session, result.kinetic_measurement_id)
    assert contexts == []


# --- Test J: idempotent repeat ---------------------------------------------------------------


def test_c6_idempotent_repeat_creates_no_duplicate_contexts(db_session):
    protein_a = _protein(db_session, name="A")
    protein_b = _protein(db_session, name="B")
    shared_source_id = "sabiork:idempotent:Km"

    for _ in range(2):  # simulate the identical curation request running twice
        persist_kinetic_measurement(
            _identity(
                source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_a.id
            ),
            session=db_session,
        )
        persist_kinetic_measurement(
            _identity(
                source=SourceType.SABIORK, source_id=shared_source_id, protein_id=protein_b.id
            ),
            session=db_session,
        )

    rows = db_session.execute(
        select(KineticMeasurement).where(KineticMeasurement.source_id == shared_source_id)
    ).scalars().all()
    assert len(rows) == 1  # no duplicate KineticMeasurement row

    contexts = _protein_context_rows(db_session, rows[0].id)
    assert len(contexts) == 2  # no duplicate context rows despite 4 total persist calls
    assert {c.protein_id for c in contexts} == {protein_a.id, protein_b.id}


def test_c6_attach_kinetic_measurement_protein_context_is_idempotent(db_session):
    """Direct unit test of the new helper itself: attaching the identical
    (measurement, protein) pair twice returns the same row id, never a duplicate."""
    protein = _protein(db_session)
    result = persist_kinetic_measurement(_identity(), session=db_session)

    first_id = attach_kinetic_measurement_protein_context(
        db_session, kinetic_measurement_id=result.kinetic_measurement_id, protein_id=protein.id
    )
    second_id = attach_kinetic_measurement_protein_context(
        db_session, kinetic_measurement_id=result.kinetic_measurement_id, protein_id=protein.id
    )
    assert first_id == second_id

    contexts = _protein_context_rows(db_session, result.kinetic_measurement_id)
    assert len(contexts) == 1


def test_c6_derivative_lineage_merge_also_attaches_protein_context(db_session):
    """A derivative-lineage merge (Increment A Step 25) is also a 'reuse' outcome --
    its own identity.protein_id, if set, must also be attached, not silently dropped."""
    protein_a = _protein(db_session, name="original-protein")
    protein_b = _protein(db_session, name="derivative-protein")

    original = _identity(
        source=SourceType.SABIORK, source_id="sabiork:derivative-ctx:Km", protein_id=protein_a.id
    )
    original_result = persist_kinetic_measurement(original, session=db_session)

    derivative = _identity(
        source=SourceType.OED,
        source_id="oed:derivative-ctx-digest",
        protein_id=protein_b.id,
        original_source=SourceType.SABIORK,
        original_source_identifier="sabiork:derivative-ctx:Km",
    )
    derivative_result = persist_kinetic_measurement(derivative, session=db_session)
    assert derivative_result.kinetic_measurement_id == original_result.kinetic_measurement_id

    contexts = _protein_context_rows(db_session, original_result.kinetic_measurement_id)
    assert {c.protein_id for c in contexts} == {protein_a.id, protein_b.id}
