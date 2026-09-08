"""Tests for enzyme regulatory state identity (Agent 1.x Increment B).

Pure unit tests: no database, no HTTP. Covers state identity policy
(Steps 39, 21-23), modification handling (Step 40), allostery (Step 41),
and state transitions (Step 42).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
    SourceType,
)
from app.normalization.enzyme_state import (
    AllostericInteractionIdentity,
    AllostericLigandIdentity,
    EnzymeModificationIdentity,
    EnzymeStateIdentity,
    EnzymeStateParentType,
    EnzymeStateTransitionIdentity,
    ModificationIdentity,
    compute_allosteric_interaction_identity_key,
    compute_enzyme_modification_identity_key,
    compute_enzyme_state_identity_key,
    compute_enzyme_state_transition_identity_key,
)

pytestmark = pytest.mark.unit

PROTEIN_A = uuid4()
PROTEIN_B = uuid4()
COMPLEX_A = uuid4()
COMPOUND_A = uuid4()
COMPOUND_B = uuid4()


def _state(**overrides) -> EnzymeStateIdentity:
    merged = {
        "source": SourceType.OTHER,
        "source_identifier": "src-1",
        "parent_type": EnzymeStateParentType.PROTEIN,
        "parent_id": PROTEIN_A,
        "state_type": EnzymeStateType.BASE,
    } | overrides
    return EnzymeStateIdentity(**merged)


# --- Step 39: state identity ---------------------------------------------------------------


def test_base_state_identity_is_deterministic():
    a = _state()
    b = _state()
    assert compute_enzyme_state_identity_key(a) == compute_enzyme_state_identity_key(b)


def test_phosphorylated_state_distinct_from_base():
    base = _state()
    phospho = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(
            ModificationIdentity(
                modification_type=ModificationType.PHOSPHORYLATION,
                residue="Ser",
                residue_position=15,
            ),
        ),
    )
    assert compute_enzyme_state_identity_key(base) != compute_enzyme_state_identity_key(phospho)


def test_two_phosphorylation_sites_remain_distinct():
    site_15 = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(
            ModificationIdentity(
                modification_type=ModificationType.PHOSPHORYLATION,
                residue="Ser",
                residue_position=15,
            ),
        ),
    )
    site_42 = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(
            ModificationIdentity(
                modification_type=ModificationType.PHOSPHORYLATION,
                residue="Ser",
                residue_position=42,
            ),
        ),
    )
    assert compute_enzyme_state_identity_key(site_15) != compute_enzyme_state_identity_key(site_42)


def test_two_different_modifications_remain_distinct():
    phospho = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(ModificationIdentity(modification_type=ModificationType.PHOSPHORYLATION),),
    )
    acetyl = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(ModificationIdentity(modification_type=ModificationType.ACETYLATION),),
    )
    assert compute_enzyme_state_identity_key(phospho) != compute_enzyme_state_identity_key(acetyl)


def test_modification_ordering_does_not_change_state_identity():
    m1 = ModificationIdentity(
        modification_type=ModificationType.PHOSPHORYLATION, residue="Ser", residue_position=15
    )
    m2 = ModificationIdentity(
        modification_type=ModificationType.ACETYLATION, residue="Lys", residue_position=20
    )
    forward = _state(state_type=EnzymeStateType.MODIFIED, modifications=(m1, m2))
    backward = _state(state_type=EnzymeStateType.MODIFIED, modifications=(m2, m1))
    assert compute_enzyme_state_identity_key(forward) == compute_enzyme_state_identity_key(backward)


def test_ligand_bound_state_distinct_from_base():
    base = _state()
    bound = _state(
        state_type=EnzymeStateType.ALLOSTERICALLY_BOUND,
        allosteric_ligands=(
            AllostericLigandIdentity(
                ligand_compound_id=COMPOUND_A, effect=AllostericEffect.ACTIVATOR
            ),
        ),
    )
    assert compute_enzyme_state_identity_key(base) != compute_enzyme_state_identity_key(bound)


def test_two_different_allosteric_ligands_remain_distinct():
    bound_a = _state(
        state_type=EnzymeStateType.ALLOSTERICALLY_BOUND,
        allosteric_ligands=(
            AllostericLigandIdentity(
                ligand_compound_id=COMPOUND_A, effect=AllostericEffect.ACTIVATOR
            ),
        ),
    )
    bound_b = _state(
        state_type=EnzymeStateType.ALLOSTERICALLY_BOUND,
        allosteric_ligands=(
            AllostericLigandIdentity(
                ligand_compound_id=COMPOUND_B, effect=AllostericEffect.ACTIVATOR
            ),
        ),
    )
    assert compute_enzyme_state_identity_key(bound_a) != compute_enzyme_state_identity_key(bound_b)


def test_protein_state_distinct_from_complex_state():
    protein_state = _state(parent_type=EnzymeStateParentType.PROTEIN, parent_id=PROTEIN_A)
    complex_state = _state(parent_type=EnzymeStateParentType.COMPLEX, parent_id=COMPLEX_A)
    assert compute_enzyme_state_identity_key(protein_state) != compute_enzyme_state_identity_key(
        complex_state
    )


def test_different_parent_proteins_remain_distinct():
    a = _state(parent_id=PROTEIN_A)
    b = _state(parent_id=PROTEIN_B)
    assert compute_enzyme_state_identity_key(a) != compute_enzyme_state_identity_key(b)


def test_state_label_and_notes_never_affect_identity():
    """Metadata-only fields must not change identity (mirrors knowledge_gap's own policy)."""
    a = _state(state_label="phospho form", notes="curated from figure 3")
    b = _state(state_label="completely different label", notes="different notes entirely")
    assert compute_enzyme_state_identity_key(a) == compute_enzyme_state_identity_key(b)


def test_compartment_distinguishes_state_identity():
    compartment_a = uuid4()
    compartment_b = uuid4()
    a = _state(compartment_id=compartment_a)
    b = _state(compartment_id=compartment_b)
    assert compute_enzyme_state_identity_key(a) != compute_enzyme_state_identity_key(b)


# --- EnzymeStateIdentity construction validation --------------------------------------------


def test_identity_requires_parent_id():
    with pytest.raises(ValueError, match="requires parent_id"):
        _state(parent_id=None)


def test_identity_rejects_non_enum_parent_type():
    with pytest.raises(TypeError):
        _state(parent_type="PROTEIN")  # type: ignore[arg-type]


def test_identity_rejects_non_enum_state_type():
    with pytest.raises(TypeError):
        _state(state_type="BASE")  # type: ignore[arg-type]


def test_identity_never_resolves_entity_identity_itself():
    """No field here accepts a name/EC-number/free-text lookup key -- only UUIDs."""
    import inspect

    params = inspect.signature(EnzymeStateIdentity).parameters
    for forbidden in ("protein_name", "ec_number", "gene_symbol", "organism_name"):
        assert forbidden not in params


# --- Step 40: modification -------------------------------------------------------------------


@pytest.mark.parametrize(
    "modification_type",
    [
        ModificationType.PHOSPHORYLATION,
        ModificationType.ACETYLATION,
        ModificationType.CYSTEINYLATION,
        ModificationType.OTHER,
    ],
)
def test_modification_type_accepted(modification_type):
    modification = ModificationIdentity(modification_type=modification_type)
    assert modification.modification_type is modification_type


def test_modification_preserves_site_and_residue():
    modification = ModificationIdentity(
        modification_type=ModificationType.PHOSPHORYLATION,
        residue="Thr",
        residue_position=308,
        site_label="activation loop",
    )
    assert modification.residue == "Thr"
    assert modification.residue_position == 308
    assert modification.site_label == "activation loop"


def test_multiple_modifications_per_state_all_participate_in_identity():
    doubly_modified = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(
            ModificationIdentity(
                modification_type=ModificationType.PHOSPHORYLATION, residue_position=15
            ),
            ModificationIdentity(
                modification_type=ModificationType.ACETYLATION, residue_position=20
            ),
        ),
    )
    singly_modified = _state(
        state_type=EnzymeStateType.MODIFIED,
        modifications=(
            ModificationIdentity(
                modification_type=ModificationType.PHOSPHORYLATION, residue_position=15
            ),
        ),
    )
    assert compute_enzyme_state_identity_key(doubly_modified) != compute_enzyme_state_identity_key(
        singly_modified
    )


def test_duplicate_modification_prevention_via_identical_identity_key():
    """Two EnzymeModificationIdentity values with identical content yield the identical key
    -- app.persistence.enzyme_state relies on this for duplicate prevention."""
    a = EnzymeModificationIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        enzyme_state_id=PROTEIN_A,
        modification_type=ModificationType.PHOSPHORYLATION,
        residue="Ser",
        residue_position=15,
    )
    b = EnzymeModificationIdentity(
        source=SourceType.OTHER,
        source_identifier="s2",
        enzyme_state_id=PROTEIN_A,
        modification_type=ModificationType.PHOSPHORYLATION,
        residue="Ser",
        residue_position=15,
    )
    assert compute_enzyme_modification_identity_key(a) == compute_enzyme_modification_identity_key(
        b
    )


def test_modification_stoichiometry_participates_in_identity():
    single = ModificationIdentity(
        modification_type=ModificationType.PHOSPHORYLATION,
        residue_position=15,
        stoichiometry=Decimal("1"),
    )
    double = ModificationIdentity(
        modification_type=ModificationType.PHOSPHORYLATION,
        residue_position=15,
        stoichiometry=Decimal("2"),
    )
    state_single = _state(state_type=EnzymeStateType.MODIFIED, modifications=(single,))
    state_double = _state(state_type=EnzymeStateType.MODIFIED, modifications=(double,))
    assert compute_enzyme_state_identity_key(state_single) != compute_enzyme_state_identity_key(
        state_double
    )


def test_no_site_inference_residue_position_stays_none_when_not_supplied():
    modification = ModificationIdentity(modification_type=ModificationType.PHOSPHORYLATION)
    assert modification.residue is None
    assert modification.residue_position is None


def test_modification_rejects_non_decimal_stoichiometry():
    with pytest.raises(TypeError):
        ModificationIdentity(modification_type=ModificationType.PHOSPHORYLATION, stoichiometry=1.5)  # type: ignore[arg-type]


# --- Step 41: allostery -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "effect",
    [
        AllostericEffect.ACTIVATOR,
        AllostericEffect.INHIBITOR,
        AllostericEffect.MODULATOR,
        AllostericEffect.UNKNOWN,
    ],
)
def test_allosteric_effect_accepted(effect):
    ligand = AllostericLigandIdentity(ligand_compound_id=COMPOUND_A, effect=effect)
    assert ligand.effect is effect


def test_allosteric_ligand_requires_compound_id():
    with pytest.raises(ValueError, match="requires ligand_compound_id"):
        AllostericLigandIdentity(ligand_compound_id=None, effect=AllostericEffect.ACTIVATOR)  # type: ignore[arg-type]


def test_allosteric_interaction_identity_requires_compound_id():
    with pytest.raises(ValueError, match="requires ligand_compound_id"):
        AllostericInteractionIdentity(
            source=SourceType.OTHER,
            source_identifier="s1",
            enzyme_state_id=PROTEIN_A,
            ligand_compound_id=None,  # type: ignore[arg-type]
            effect=AllostericEffect.ACTIVATOR,
        )


def test_binding_fact_and_kinetic_effect_remain_separate():
    """AllostericInteraction/AllostericLigandIdentity carry no numeric kinetic field at all."""
    import inspect

    for cls in (AllostericLigandIdentity, AllostericInteractionIdentity):
        params = inspect.signature(cls).parameters
        for forbidden in ("value", "km", "kcat", "parameter_value"):
            assert forbidden not in params


def test_no_binary_on_off_inference_effect_is_always_explicit_enum():
    """There is no boolean 'is_activator' field -- only the explicit AllostericEffect value."""
    import inspect

    params = inspect.signature(AllostericLigandIdentity).parameters
    assert "is_activator" not in params
    assert "active" not in params


def test_allosteric_interaction_identity_key_ignores_mechanism_text():
    a = AllostericInteractionIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        enzyme_state_id=PROTEIN_A,
        ligand_compound_id=COMPOUND_A,
        effect=AllostericEffect.ACTIVATOR,
        mechanism="binds at the regulatory site",
    )
    b = AllostericInteractionIdentity(
        source=SourceType.OTHER,
        source_identifier="s2",
        enzyme_state_id=PROTEIN_A,
        ligand_compound_id=COMPOUND_A,
        effect=AllostericEffect.ACTIVATOR,
        mechanism=None,
    )
    assert compute_allosteric_interaction_identity_key(
        a
    ) == compute_allosteric_interaction_identity_key(b)


# --- Step 42: state transitions -----------------------------------------------------------------


@pytest.mark.parametrize(
    "transition_type",
    [
        EnzymeStateTransitionType.MODIFICATION,
        EnzymeStateTransitionType.DEMODIFICATION,
        EnzymeStateTransitionType.LIGAND_BINDING,
        EnzymeStateTransitionType.LIGAND_RELEASE,
    ],
)
def test_transition_type_accepted(transition_type):
    transition = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        from_state_id=PROTEIN_A,
        to_state_id=PROTEIN_B,
        transition_type=transition_type,
    )
    assert transition.transition_type is transition_type


def test_transition_optional_reaction_link():
    reaction_id = uuid4()
    transition = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        from_state_id=PROTEIN_A,
        to_state_id=PROTEIN_B,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
        reaction_id=reaction_id,
    )
    assert transition.reaction_id == reaction_id


def test_transition_reaction_link_defaults_to_none_never_invented():
    transition = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        from_state_id=PROTEIN_A,
        to_state_id=PROTEIN_B,
        transition_type=EnzymeStateTransitionType.LIGAND_BINDING,
    )
    assert transition.reaction_id is None


def test_transition_rejects_identical_from_and_to_state():
    with pytest.raises(ValueError, match="must differ"):
        EnzymeStateTransitionIdentity(
            source=SourceType.OTHER,
            source_identifier="s1",
            from_state_id=PROTEIN_A,
            to_state_id=PROTEIN_A,
            transition_type=EnzymeStateTransitionType.MODIFICATION,
        )


def test_transition_identity_is_deterministic():
    a = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="s1",
        from_state_id=PROTEIN_A,
        to_state_id=PROTEIN_B,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
    )
    b = EnzymeStateTransitionIdentity(
        source=SourceType.OTHER,
        source_identifier="s2",
        from_state_id=PROTEIN_A,
        to_state_id=PROTEIN_B,
        transition_type=EnzymeStateTransitionType.MODIFICATION,
    )
    assert compute_enzyme_state_transition_identity_key(
        a
    ) == compute_enzyme_state_transition_identity_key(b)


def test_transition_never_invents_a_biochemical_reaction():
    """No field lets a transition construct a new Reaction -- only reference an existing id."""
    import inspect

    params = inspect.signature(EnzymeStateTransitionIdentity).parameters
    for forbidden in ("reaction_name", "participants", "stoichiometry"):
        assert forbidden not in params
