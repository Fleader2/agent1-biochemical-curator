"""Schema tests for Group H: enzyme regulatory states (Agent 1.x Increment B).

Covers ``EnzymeState``, ``EnzymeModification``, ``AllostericInteraction``,
``EnzymeStateTransition`` (migration ``0014_enzyme_regulatory_states``),
and this migration's extension of ``ReactionEnzyme``/``KineticMeasurement``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
)
from app.models.enzyme_complex import EnzymeComplex
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.models.kinetic_measurement import KineticMeasurement
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme

pytestmark = pytest.mark.database


def _organism(session, suffix="h") -> Organism:
    organism = Organism(scientific_name=f"Test organism {suffix}")
    session.add(organism)
    session.flush()
    return organism


def _protein(session, organism_id, suffix="h") -> Protein:
    protein = Protein(organism_id=organism_id, name=f"Test protein {suffix}")
    session.add(protein)
    session.flush()
    return protein


def _complex(session, organism_id, suffix="h") -> EnzymeComplex:
    complex_row = EnzymeComplex(organism_id=organism_id, name=f"Test complex {suffix}")
    session.add(complex_row)
    session.flush()
    return complex_row


def _reaction(session, organism_id=None, suffix="h") -> Reaction:
    reaction = Reaction(internal_id=f"TEST_H_{suffix}", name=f"Test reaction {suffix}")
    reaction.organism_id = organism_id
    session.add(reaction)
    session.flush()
    return reaction


def _enzyme_state(session, *, protein_id=None, complex_id=None, key="h") -> EnzymeState:
    state = EnzymeState(
        protein_id=protein_id,
        complex_id=complex_id,
        state_type=EnzymeStateType.BASE,
        identity_key=f"test-key-{key}",
    )
    session.add(state)
    session.flush()
    return state


# --- EnzymeState: existence, XOR, state_type ---------------------------------------------


def test_create_enzyme_state_with_protein(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id)
    assert state.state_type == EnzymeStateType.BASE
    assert state.complex_id is None


def test_create_enzyme_state_with_complex(db_session):
    organism = _organism(db_session)
    complex_row = _complex(db_session, organism.id)
    state = EnzymeState(
        complex_id=complex_row.id, state_type=EnzymeStateType.BASE, identity_key="test-key-c1"
    )
    db_session.add(state)
    db_session.flush()
    assert state.protein_id is None


def test_enzyme_state_rejects_both_protein_and_complex(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    complex_row = _complex(db_session, organism.id)
    state = EnzymeState(
        protein_id=protein.id,
        complex_id=complex_row.id,
        state_type=EnzymeStateType.BASE,
        identity_key="test-key-both",
    )
    db_session.add(state)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_state_rejects_neither_protein_nor_complex(db_session):
    state = EnzymeState(state_type=EnzymeStateType.BASE, identity_key="test-key-neither")
    db_session.add(state)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_state_identity_key_is_unique(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    _enzyme_state(db_session, protein_id=protein.id, key="dup")
    dup = EnzymeState(
        protein_id=protein.id, state_type=EnzymeStateType.MODIFIED, identity_key="test-key-dup"
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_state_accepts_every_state_type(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    for i, state_type in enumerate(EnzymeStateType):
        state = EnzymeState(
            protein_id=protein.id, state_type=state_type, identity_key=f"test-key-type-{i}"
        )
        db_session.add(state)
    db_session.flush()


def test_deleting_referenced_protein_is_restricted(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    _enzyme_state(db_session, protein_id=protein.id, key="restrict")
    db_session.delete(protein)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- EnzymeModification -------------------------------------------------------------------


def test_create_enzyme_modification(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="mod1")
    modification = EnzymeModification(
        enzyme_state_id=state.id,
        modification_type=ModificationType.PHOSPHORYLATION,
        residue="Ser",
        residue_position=15,
        identity_key="test-mod-key-1",
    )
    db_session.add(modification)
    db_session.flush()
    assert modification.residue_position == 15


def test_enzyme_modification_accepts_every_modification_type(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="modall")
    for i, modification_type in enumerate(ModificationType):
        db_session.add(
            EnzymeModification(
                enzyme_state_id=state.id,
                modification_type=modification_type,
                identity_key=f"test-mod-key-type-{i}",
            )
        )
    db_session.flush()


def test_enzyme_modification_identity_key_is_unique(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="moddup")
    db_session.add(
        EnzymeModification(
            enzyme_state_id=state.id,
            modification_type=ModificationType.ACETYLATION,
            identity_key="test-mod-dup",
        )
    )
    db_session.flush()
    db_session.add(
        EnzymeModification(
            enzyme_state_id=state.id,
            modification_type=ModificationType.METHYLATION,
            identity_key="test-mod-dup",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_enzyme_state_cascades_to_modifications(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="cascade")
    modification = EnzymeModification(
        enzyme_state_id=state.id,
        modification_type=ModificationType.PHOSPHORYLATION,
        identity_key="test-mod-cascade",
    )
    db_session.add(modification)
    db_session.flush()
    modification_id = modification.id

    db_session.delete(state)
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(EnzymeModification, modification_id) is None


# --- AllostericInteraction -----------------------------------------------------------------


def test_create_allosteric_interaction(db_session):
    from app.models.compound import Compound

    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="allo1")
    compound = Compound(canonical_name="test-ligand")
    db_session.add(compound)
    db_session.flush()

    interaction = AllostericInteraction(
        enzyme_state_id=state.id,
        ligand_compound_id=compound.id,
        effect=AllostericEffect.ACTIVATOR,
        identity_key="test-allo-key-1",
    )
    db_session.add(interaction)
    db_session.flush()
    assert interaction.effect == AllostericEffect.ACTIVATOR


def test_allosteric_interaction_requires_ligand_compound_id(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="alloreq")
    interaction = AllostericInteraction(
        enzyme_state_id=state.id, effect=AllostericEffect.INHIBITOR, identity_key="test-allo-req"
    )
    db_session.add(interaction)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_allosteric_interaction_accepts_every_effect(db_session):
    from app.models.compound import Compound

    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="alloall")
    compound = Compound(canonical_name="test-ligand-all")
    db_session.add(compound)
    db_session.flush()
    for i, effect in enumerate(AllostericEffect):
        db_session.add(
            AllostericInteraction(
                enzyme_state_id=state.id,
                ligand_compound_id=compound.id,
                effect=effect,
                identity_key=f"test-allo-key-effect-{i}",
            )
        )
    db_session.flush()


def test_allosteric_interaction_never_stores_a_numeric_kinetic_value(db_session):
    """Structural guard: the table has no value/unit column at all."""
    columns = {c.name for c in AllostericInteraction.__table__.columns}
    assert "value" not in columns
    assert "unit" not in columns
    assert "parameter_value" not in columns


# --- EnzymeStateTransition -----------------------------------------------------------------


def test_create_enzyme_state_transition(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    base = _enzyme_state(db_session, protein_id=protein.id, key="trans-base")
    modified = _enzyme_state(db_session, protein_id=protein.id, key="trans-mod")
    transition = EnzymeStateTransition(
        from_state_id=base.id,
        to_state_id=modified.id,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
        identity_key="test-trans-key-1",
    )
    db_session.add(transition)
    db_session.flush()
    assert transition.reaction_id is None


def test_enzyme_state_transition_rejects_self_transition(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="trans-self")
    transition = EnzymeStateTransition(
        from_state_id=state.id,
        to_state_id=state.id,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
        identity_key="test-trans-self",
    )
    db_session.add(transition)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_state_transition_accepts_every_transition_type(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    base = _enzyme_state(db_session, protein_id=protein.id, key="trans-all-base")
    for i, transition_type in enumerate(EnzymeStateTransitionType):
        target = _enzyme_state(db_session, protein_id=protein.id, key=f"trans-all-{i}")
        db_session.add(
            EnzymeStateTransition(
                from_state_id=base.id,
                to_state_id=target.id,
                transition_type=transition_type,
                identity_key=f"test-trans-key-type-{i}",
            )
        )
    db_session.flush()


def test_enzyme_state_transition_can_reference_a_reaction(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="trans-rxn")
    base = _enzyme_state(db_session, protein_id=protein.id, key="trans-rxn-base")
    modified = _enzyme_state(db_session, protein_id=protein.id, key="trans-rxn-mod")
    transition = EnzymeStateTransition(
        from_state_id=base.id,
        to_state_id=modified.id,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
        reaction_id=reaction.id,
        identity_key="test-trans-rxn-key",
    )
    db_session.add(transition)
    db_session.flush()
    assert transition.reaction_id == reaction.id


# --- ReactionEnzyme: state-target support ---------------------------------------------------


def test_reaction_enzyme_accepts_enzyme_state_target(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="re-state")
    state = _enzyme_state(db_session, protein_id=protein.id, key="re-state")
    association = ReactionEnzyme(
        reaction_id=reaction.id, enzyme_state_id=state.id, relationship="CATALYZES"
    )
    db_session.add(association)
    db_session.flush()
    assert association.protein_id is None
    assert association.complex_id is None


def test_reaction_enzyme_still_rejects_all_three_targets_at_once(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="re-three")
    state = _enzyme_state(db_session, protein_id=protein.id, key="re-three")
    association = ReactionEnzyme(
        reaction_id=reaction.id,
        protein_id=protein.id,
        enzyme_state_id=state.id,
        relationship="CATALYZES",
    )
    db_session.add(association)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_still_rejects_zero_targets(db_session):
    organism = _organism(db_session)
    reaction = _reaction(db_session, organism.id, suffix="re-zero")
    association = ReactionEnzyme(reaction_id=reaction.id, relationship="CATALYZES")
    db_session.add(association)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_protein_target_still_valid(db_session):
    """Backward compatibility: existing protein-targeted rows remain valid."""
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="re-protein")
    association = ReactionEnzyme(
        reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES"
    )
    db_session.add(association)
    db_session.flush()
    assert association.enzyme_state_id is None


def test_reaction_enzyme_complex_target_still_valid(db_session):
    organism = _organism(db_session)
    complex_row = _complex(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="re-complex")
    association = ReactionEnzyme(
        reaction_id=reaction.id, complex_id=complex_row.id, relationship="CATALYZES"
    )
    db_session.add(association)
    db_session.flush()
    assert association.enzyme_state_id is None


def test_reaction_enzyme_enzyme_state_pair_is_unique(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    reaction = _reaction(db_session, organism.id, suffix="re-dup-state")
    state = _enzyme_state(db_session, protein_id=protein.id, key="re-dup-state")
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, enzyme_state_id=state.id, relationship="CATALYZES")
    )
    db_session.flush()
    db_session.add(
        ReactionEnzyme(
            reaction_id=reaction.id, enzyme_state_id=state.id, relationship="PUTATIVE_CATALYST"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_isozymes_still_preserved_alongside_state_specific_catalysis(db_session):
    """Two proteins catalyzing the same reaction, plus a state-specific association, all coexist."""
    organism = _organism(db_session)
    protein_a = _protein(db_session, organism.id, suffix="iso-a")
    protein_b = _protein(db_session, organism.id, suffix="iso-b")
    reaction = _reaction(db_session, organism.id, suffix="re-iso")
    state = _enzyme_state(db_session, protein_id=protein_a.id, key="re-iso")

    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein_a.id, relationship="CATALYZES")
    )
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein_b.id, relationship="CATALYZES")
    )
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, enzyme_state_id=state.id, relationship="CATALYZES")
    )
    db_session.flush()

    rows = db_session.query(ReactionEnzyme).filter_by(reaction_id=reaction.id).all()
    assert len(rows) == 3


# --- KineticMeasurement: enzyme_state_id ----------------------------------------------------


def test_kinetic_measurement_accepts_enzyme_state_id(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="km1")
    measurement = KineticMeasurement(
        enzyme_state_id=state.id,
        protein_id=protein.id,
        parameter_type="KCAT",
        parameter_value=Decimal("32.0"),
        unit="1/s",
    )
    db_session.add(measurement)
    db_session.flush()
    assert measurement.enzyme_state_id == state.id


def test_kinetic_measurement_enzyme_state_id_is_optional(db_session):
    measurement = KineticMeasurement(
        parameter_type="KCAT", parameter_value=Decimal("4.5"), unit="1/s"
    )
    db_session.add(measurement)
    db_session.flush()
    assert measurement.enzyme_state_id is None


def test_state_specific_and_non_state_measurements_both_persist_independently(db_session):
    """Protein E kcat=4.5 and phosphorylated-E kcat=32 must both persist, never merged."""
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    state = _enzyme_state(db_session, protein_id=protein.id, key="km2")

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

    assert base_measurement.enzyme_state_id is None
    assert state_measurement.enzyme_state_id == state.id
    assert base_measurement.id != state_measurement.id


# --- Indexes ---------------------------------------------------------------------------------


def test_enzyme_state_has_unique_identity_key_index(db_session):
    inspector = inspect(db_session.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("enzyme_state")}
    unique_names = {c["name"] for c in inspector.get_unique_constraints("enzyme_state")}
    assert (
        "uq_enzyme_state_identity_key" in index_names
        or "uq_enzyme_state_identity_key" in unique_names
    )


def test_reaction_enzyme_has_three_partial_unique_indexes(db_session):
    inspector = inspect(db_session.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("reaction_enzyme")}
    assert "uq_reaction_enzyme_reaction_id_protein_id" in index_names
    assert "uq_reaction_enzyme_reaction_id_complex_id" in index_names
    assert "uq_reaction_enzyme_reaction_id_enzyme_state_id" in index_names
