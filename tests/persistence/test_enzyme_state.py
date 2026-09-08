"""Tests for ``app.persistence.enzyme_state`` (Agent 1.x Increment B).

Covers state-specific kinetics preservation (Step 44), persistence
behavior (Step 45), and the module's own transaction-handling guarantees.
"""

from __future__ import annotations

import inspect
import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
    SourceType,
)
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.models.kinetic_measurement import KineticMeasurement
from app.normalization.enzyme_state import (
    AllostericInteractionIdentity,
    AllostericLigandIdentity,
    EnzymeModificationIdentity,
    EnzymeStateIdentity,
    EnzymeStateParentType,
    EnzymeStateTransitionIdentity,
    ModificationIdentity,
)
from app.persistence import enzyme_state as enzyme_state_module
from app.persistence.enzyme_state import (
    get_enzyme_state,
    list_enzyme_states_for_complex,
    list_enzyme_states_for_protein,
    persist_allosteric_interaction,
    persist_enzyme_modification,
    persist_enzyme_state,
    persist_enzyme_state_transition,
)
from app.persistence.types import PersistenceAction
from tests.persistence.conftest import make_compound, make_enzyme_state, make_organism, make_protein


def _state_identity(*, protein_id, **overrides) -> EnzymeStateIdentity:
    merged = {
        "source": SourceType.OTHER,
        "source_identifier": f"src-{uuid4()}",
        "parent_type": EnzymeStateParentType.PROTEIN,
        "parent_id": protein_id,
        "state_type": EnzymeStateType.BASE,
    } | overrides
    return EnzymeStateIdentity(**merged)


# --- persist_enzyme_state: create / reuse -----------------------------------------------------


def test_create_new_enzyme_state(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    identity = _state_identity(protein_id=protein.id)

    result = persist_enzyme_state(identity, session=db_session)

    assert result.action is PersistenceAction.CREATED
    assert result.created is True
    row = get_enzyme_state(db_session, result.entity_id)
    assert row.protein_id == protein.id
    assert row.state_type is EnzymeStateType.BASE


def test_replay_reuses_existing_state_deterministically(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    identity = _state_identity(protein_id=protein.id, source_identifier="fixed-src")

    first = persist_enzyme_state(identity, session=db_session)
    second = persist_enzyme_state(identity, session=db_session)

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.entity_id == first.entity_id

    rows = (
        db_session.execute(select(EnzymeState).where(EnzymeState.protein_id == protein.id))
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_multiple_states_for_one_protein(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    base = persist_enzyme_state(_state_identity(protein_id=protein.id), session=db_session)
    modified = persist_enzyme_state(
        _state_identity(
            protein_id=protein.id,
            state_type=EnzymeStateType.MODIFIED,
            modifications=(
                ModificationIdentity(modification_type=ModificationType.PHOSPHORYLATION),
            ),
        ),
        session=db_session,
    )
    assert base.entity_id != modified.entity_id
    states = list_enzyme_states_for_protein(db_session, protein.id)
    assert len(states) == 2


def test_multiple_states_for_one_complex(db_session):
    from app.models.enzyme_complex import EnzymeComplex

    organism = make_organism(db_session)
    complex_row = EnzymeComplex(organism_id=organism.id, name="test complex")
    db_session.add(complex_row)
    db_session.flush()

    base = persist_enzyme_state(
        EnzymeStateIdentity(
            source=SourceType.OTHER,
            source_identifier="c1",
            parent_type=EnzymeStateParentType.COMPLEX,
            parent_id=complex_row.id,
            state_type=EnzymeStateType.BASE,
        ),
        session=db_session,
    )
    modified = persist_enzyme_state(
        EnzymeStateIdentity(
            source=SourceType.OTHER,
            source_identifier="c2",
            parent_type=EnzymeStateParentType.COMPLEX,
            parent_id=complex_row.id,
            state_type=EnzymeStateType.MODIFIED,
            modifications=(ModificationIdentity(modification_type=ModificationType.ACETYLATION),),
        ),
        session=db_session,
    )
    assert base.entity_id != modified.entity_id
    states = list_enzyme_states_for_complex(db_session, complex_row.id)
    assert len(states) == 2


def test_no_silent_overwrite_on_replay(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    identity = _state_identity(
        protein_id=protein.id, notes="first notes", source_identifier="fixed"
    )
    first = persist_enzyme_state(identity, session=db_session)

    replay = _state_identity(protein_id=protein.id, notes="second notes", source_identifier="fixed")
    persist_enzyme_state(replay, session=db_session)

    row = get_enzyme_state(db_session, first.entity_id)
    assert row.notes == "first notes"


def test_rejects_non_identity_type(db_session):
    with pytest.raises(TypeError):
        persist_enzyme_state("not-an-identity", session=db_session)


# --- persist_enzyme_modification / persist_allosteric_interaction ----------------------------


def test_modification_attaches_to_existing_state(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id)

    result = persist_enzyme_modification(
        EnzymeModificationIdentity(
            source=SourceType.OTHER,
            source_identifier="m1",
            enzyme_state_id=state.id,
            modification_type=ModificationType.PHOSPHORYLATION,
            residue="Ser",
            residue_position=15,
        ),
        session=db_session,
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(EnzymeModification, result.entity_id)
    assert row.enzyme_state_id == state.id


def test_modification_replay_reuses(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id)
    identity = EnzymeModificationIdentity(
        source=SourceType.OTHER,
        source_identifier="m1",
        enzyme_state_id=state.id,
        modification_type=ModificationType.PHOSPHORYLATION,
        residue_position=15,
    )
    first = persist_enzyme_modification(identity, session=db_session)
    second = persist_enzyme_modification(identity, session=db_session)
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.entity_id == first.entity_id


def test_allosteric_interaction_attaches_to_existing_state(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id)
    compound = make_compound(db_session)

    result = persist_allosteric_interaction(
        AllostericInteractionIdentity(
            source=SourceType.OTHER,
            source_identifier="a1",
            enzyme_state_id=state.id,
            ligand_compound_id=compound.id,
            effect=AllostericEffect.ACTIVATOR,
        ),
        session=db_session,
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(AllostericInteraction, result.entity_id)
    assert row.ligand_compound_id == compound.id


def test_allosteric_interaction_replay_reuses(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id)
    compound = make_compound(db_session)
    identity = AllostericInteractionIdentity(
        source=SourceType.OTHER,
        source_identifier="a1",
        enzyme_state_id=state.id,
        ligand_compound_id=compound.id,
        effect=AllostericEffect.INHIBITOR,
    )
    first = persist_allosteric_interaction(identity, session=db_session)
    second = persist_allosteric_interaction(identity, session=db_session)
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.entity_id == first.entity_id


# --- persist_enzyme_state_transition -----------------------------------------------------------


def test_transition_persists_between_two_states(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    base = make_enzyme_state(db_session, protein_id=protein.id, suffix="t-base")
    modified = make_enzyme_state(db_session, protein_id=protein.id, suffix="t-mod")

    result = persist_enzyme_state_transition(
        EnzymeStateTransitionIdentity(
            source=SourceType.OTHER,
            source_identifier="tr1",
            from_state_id=base.id,
            to_state_id=modified.id,
            transition_type=EnzymeStateTransitionType.MODIFICATION,
        ),
        session=db_session,
    )
    assert result.action is PersistenceAction.CREATED
    row = db_session.get(EnzymeStateTransition, result.entity_id)
    assert row.from_state_id == base.id
    assert row.to_state_id == modified.id


def test_transition_replay_reuses(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    base = make_enzyme_state(db_session, protein_id=protein.id, suffix="t-base2")
    modified = make_enzyme_state(db_session, protein_id=protein.id, suffix="t-mod2")
    identity = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="tr1",
        from_state_id=base.id,
        to_state_id=modified.id,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
    )
    first = persist_enzyme_state_transition(identity, session=db_session)
    second = persist_enzyme_state_transition(identity, session=db_session)
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.entity_id == first.entity_id


# --- Step 44: state-specific kinetics (via KineticMeasurement.enzyme_state_id) ------------------


def test_unmodified_protein_measurement_remains_valid(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    measurement = KineticMeasurement(
        protein_id=protein.id, parameter_type="KCAT", parameter_value=Decimal("4.5"), unit="1/s"
    )
    db_session.add(measurement)
    db_session.flush()
    assert measurement.enzyme_state_id is None


def test_modified_state_measurement_distinct_from_base(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id, suffix="km-mod")

    base_measurement = KineticMeasurement(
        protein_id=protein.id, parameter_type="KCAT", parameter_value=Decimal("4.5"), unit="1/s"
    )
    state_measurement = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state.id,
        parameter_type="KCAT",
        parameter_value=Decimal("32.0"),
        unit="1/s",
    )
    db_session.add(base_measurement)
    db_session.add(state_measurement)
    db_session.flush()

    assert base_measurement.parameter_value != state_measurement.parameter_value
    assert base_measurement.id != state_measurement.id


def test_allosteric_state_measurement(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    compound = make_compound(db_session)
    allosteric_result = persist_enzyme_state(
        EnzymeStateIdentity(
            source=SourceType.OTHER,
            source_identifier="allo-km",
            parent_type=EnzymeStateParentType.PROTEIN,
            parent_id=protein.id,
            state_type=EnzymeStateType.ALLOSTERICALLY_BOUND,
            allosteric_ligands=(
                AllostericLigandIdentity(
                    ligand_compound_id=compound.id, effect=AllostericEffect.ACTIVATOR
                ),
            ),
        ),
        session=db_session,
    )
    measurement = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=allosteric_result.entity_id,
        parameter_type="KM",
        parameter_value=Decimal("0.4"),
        unit="mM",
    )
    db_session.add(measurement)
    db_session.flush()
    assert measurement.enzyme_state_id == allosteric_result.entity_id


def test_same_parameter_type_different_states_stays_distinct(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state_a = make_enzyme_state(db_session, protein_id=protein.id, suffix="km-a")
    state_b = make_enzyme_state(db_session, protein_id=protein.id, suffix="km-b")

    m_a = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state_a.id,
        parameter_type="KM",
        parameter_value=Decimal("1.0"),
        unit="mM",
    )
    m_b = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state_b.id,
        parameter_type="KM",
        parameter_value=Decimal("1.0"),
        unit="mM",
    )
    db_session.add(m_a)
    db_session.add(m_b)
    db_session.flush()
    assert m_a.id != m_b.id
    assert m_a.enzyme_state_id != m_b.enzyme_state_id


def test_same_state_multiple_experimental_conditions_stays_distinct(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id, suffix="km-conditions")

    low_ph = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state.id,
        parameter_type="KM",
        parameter_value=Decimal("1.0"),
        unit="mM",
        ph=Decimal("6.0"),
    )
    high_ph = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state.id,
        parameter_type="KM",
        parameter_value=Decimal("1.5"),
        unit="mM",
        ph=Decimal("8.0"),
    )
    db_session.add(low_ph)
    db_session.add(high_ph)
    db_session.flush()
    assert low_ph.id != high_ph.id


def test_no_cross_state_inheritance_reading_one_state_never_returns_another(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state_a = make_enzyme_state(db_session, protein_id=protein.id, suffix="inherit-a")
    state_b = make_enzyme_state(db_session, protein_id=protein.id, suffix="inherit-b")
    db_session.add(
        KineticMeasurement(
            protein_id=protein.id,
            enzyme_state_id=state_a.id,
            parameter_type="KCAT",
            parameter_value=Decimal("10"),
            unit="1/s",
        )
    )
    db_session.flush()

    measurements_for_b = (
        db_session.execute(
            select(KineticMeasurement).where(KineticMeasurement.enzyme_state_id == state_b.id)
        )
        .scalars()
        .all()
    )
    assert measurements_for_b == []


def test_value_and_unit_precision_unchanged_for_state_specific_measurement(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id, suffix="precision")
    measurement = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state.id,
        parameter_type="KM",
        parameter_value=Decimal("0.123456789"),
        unit="mM",
    )
    db_session.add(measurement)
    db_session.flush()
    db_session.refresh(measurement)
    assert measurement.parameter_value == Decimal("0.123456789")


def test_source_lineage_unchanged_for_state_specific_measurement(db_session):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    state = make_enzyme_state(db_session, protein_id=protein.id, suffix="lineage")
    measurement = KineticMeasurement(
        protein_id=protein.id,
        enzyme_state_id=state.id,
        parameter_type="KCAT",
        parameter_value=Decimal("5"),
        unit="1/s",
        source=SourceType.BRENDA,
        source_id="brenda-test-1",
    )
    db_session.add(measurement)
    db_session.flush()
    assert measurement.source == SourceType.BRENDA
    assert measurement.source_id == "brenda-test-1"


# --- Concurrency -----------------------------------------------------------------------------


def test_concurrent_same_state_creation_yields_one_row(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        session = Session(bind=connection)
        organism = make_organism(session)
        protein = make_protein(session, organism_id=organism.id)
        session.commit()
        organism_id, protein_id = organism.id, protein.id

    identity = _state_identity(protein_id=protein_id, source_identifier="concurrent-src")

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                ready.wait(timeout=5)
                outcomes[label] = persist_enzyme_state(identity, session=session)
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
        assert errors == {}, f"persist_enzyme_state leaked an unhandled exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.REUSED_EXISTING) == 1

        with migrated_engine.connect() as verify_connection:
            rows = verify_connection.execute(
                select(EnzymeState).where(EnzymeState.protein_id == protein_id)
            ).all()
            assert len(rows) == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                delete(EnzymeState).where(EnzymeState.protein_id == protein_id)
            )
            cleanup_connection.execute(
                delete(KineticMeasurement).where(KineticMeasurement.protein_id == protein_id)
            )
            from app.models.organism import Organism
            from app.models.protein import Protein

            cleanup_connection.execute(delete(Protein).where(Protein.id == protein_id))
            cleanup_connection.execute(delete(Organism).where(Organism.id == organism_id))
            cleanup_connection.commit()


# --- Transactions ------------------------------------------------------------------------------


def test_module_never_commits_or_rolls_back():
    source = inspect.getsource(enzyme_state_module)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_module_uses_savepoint():
    source = inspect.getsource(enzyme_state_module)
    assert "begin_nested" in source


def _force_flush_to_raise_integrity_error(session, monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise IntegrityError("forced", {}, Exception("forced failure"))

    monkeypatch.setattr(session, "flush", _raise)


def test_forced_integrity_error_yields_conservative_result(db_session, monkeypatch):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    identity = _state_identity(protein_id=protein.id)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    result = persist_enzyme_state(identity, session=db_session)
    assert result.action is PersistenceAction.FAILED
    assert result.entity_id is None


def test_no_partial_row_on_failure(db_session, monkeypatch):
    organism = make_organism(db_session)
    protein = make_protein(db_session, organism_id=organism.id)
    identity = _state_identity(protein_id=protein.id)
    _force_flush_to_raise_integrity_error(db_session, monkeypatch)

    persist_enzyme_state(identity, session=db_session)

    monkeypatch.undo()
    rows = (
        db_session.execute(select(EnzymeState).where(EnzymeState.protein_id == protein.id))
        .scalars()
        .all()
    )
    assert rows == []
