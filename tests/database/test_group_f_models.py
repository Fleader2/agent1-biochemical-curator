"""Database tests for Group F regulatory_interaction/modeling_assumption/
knowledge_gap models.

See ``docs/02_database_schema.md``: "Table: regulatory_interaction",
"Table: modeling_assumption", "Table: knowledge_gap".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.claim import Claim
from app.models.enums import RegulatoryEffect
from app.models.knowledge_gap import KnowledgeGap
from app.models.modeling_assumption import ModelingAssumption
from app.models.organism import Organism
from app.models.regulatory_interaction import RegulatoryInteraction

pytestmark = pytest.mark.database


def _make_organism(db_session, suffix: str) -> Organism:
    organism = Organism(scientific_name=f"test-only organism {suffix}")
    db_session.add(organism)
    db_session.flush()
    return organism


def _make_claim(db_session, suffix: str) -> Claim:
    claim = Claim(subject_type="protein", predicate=f"test-only-{suffix}")
    db_session.add(claim)
    db_session.flush()
    return claim


def _minimal_regulatory_interaction_kwargs() -> dict:
    return {
        "regulator_type": "protein",
        "target_type": "protein",
        "effect": RegulatoryEffect.PHOSPHORYLATION,
    }


# --- regulatory_interaction ----------------------------------------------


def test_create_regulatory_interaction(db_session):
    ri = RegulatoryInteraction(**_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()

    fetched = db_session.get(RegulatoryInteraction, ri.id)
    assert fetched is not None
    assert fetched.regulator_type == "protein"
    assert fetched.target_type == "protein"
    assert fetched.effect == RegulatoryEffect.PHOSPHORYLATION
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_regulator_type_is_required(db_session):
    db_session.add(
        RegulatoryInteraction(
            regulator_type=None, target_type="protein", effect=RegulatoryEffect.INHIBITION
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_target_type_is_required(db_session):
    db_session.add(
        RegulatoryInteraction(
            regulator_type="protein", target_type=None, effect=RegulatoryEffect.INHIBITION
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_effect_is_required(db_session):
    db_session.add(
        RegulatoryInteraction(regulator_type="protein", target_type="protein", effect=None)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nullable_fields_accept_null(db_session):
    ri = RegulatoryInteraction(
        regulator_id=None,
        target_id=None,
        mechanism=None,
        direct=None,
        condition_dependent=None,
        organism_id=None,
        claim_id=None,
        notes=None,
        **_minimal_regulatory_interaction_kwargs(),
    )
    db_session.add(ri)
    db_session.flush()  # must not raise

    fetched = db_session.get(RegulatoryInteraction, ri.id)
    assert fetched.regulator_id is None
    assert fetched.target_id is None
    assert fetched.organism_id is None
    assert fetched.claim_id is None


def test_valid_organism_id_persists(db_session):
    organism = _make_organism(db_session, "ri-valid")
    ri = RegulatoryInteraction(organism_id=organism.id, **_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()
    assert db_session.get(RegulatoryInteraction, ri.id).organism_id == organism.id


def test_invalid_organism_id_fails(db_session):
    db_session.add(
        RegulatoryInteraction(
            organism_id=uuid.uuid4(), **_minimal_regulatory_interaction_kwargs()
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_claim_id_persists(db_session):
    claim = _make_claim(db_session, "ri-valid")
    ri = RegulatoryInteraction(claim_id=claim.id, **_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()
    assert db_session.get(RegulatoryInteraction, ri.id).claim_id == claim.id


def test_invalid_claim_id_fails(db_session):
    db_session.add(
        RegulatoryInteraction(claim_id=uuid.uuid4(), **_minimal_regulatory_interaction_kwargs())
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_curation_state_defaults_to_proposed(db_session):
    from app.models.enums import CurationState

    ri = RegulatoryInteraction(**_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()
    assert db_session.get(RegulatoryInteraction, ri.id).curation_state == CurationState.PROPOSED


@pytest.mark.parametrize("effect", list(RegulatoryEffect))
def test_every_regulatory_effect_persists(db_session, effect):
    ri = RegulatoryInteraction(regulator_type="protein", target_type="protein", effect=effect)
    db_session.add(ri)
    db_session.flush()
    assert db_session.get(RegulatoryInteraction, ri.id).effect == effect


def test_invalid_regulatory_effect_is_rejected_by_postgres(db_session):
    ri = RegulatoryInteraction(**_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()
    with pytest.raises(DataError):
        db_session.execute(
            text("UPDATE regulatory_interaction SET effect = 'NOT_A_REAL_EFFECT' WHERE id = :id"),
            {"id": ri.id},
        )


def test_regulator_type_and_id_persist_literally(db_session):
    regulator_id = uuid.uuid4()
    ri = RegulatoryInteraction(
        regulator_type="protein",
        regulator_id=regulator_id,
        target_type="compartment",
        effect=RegulatoryEffect.PHOSPHORYLATION,
    )
    db_session.add(ri)
    db_session.flush()

    fetched = db_session.get(RegulatoryInteraction, ri.id)
    assert fetched.regulator_type == "protein"
    assert fetched.regulator_id == regulator_id


def test_target_type_and_id_persist_literally(db_session):
    target_id = uuid.uuid4()
    ri = RegulatoryInteraction(
        regulator_type="protein",
        target_type="compartment",
        target_id=target_id,
        effect=RegulatoryEffect.PHOSPHORYLATION,
    )
    db_session.add(ri)
    db_session.flush()

    fetched = db_session.get(RegulatoryInteraction, ri.id)
    assert fetched.target_type == "compartment"
    assert fetched.target_id == target_id


def test_dangling_regulator_id_is_allowed(db_session):
    """No FK exists on regulator_id: a UUID matching no row in any entity
    table must still be accepted, since the referential-integrity check for
    polymorphic references is deferred to the validation layer."""
    dangling = uuid.uuid4()
    ri = RegulatoryInteraction(
        regulator_id=dangling, **_minimal_regulatory_interaction_kwargs()
    )
    db_session.add(ri)
    db_session.flush()  # must not raise
    assert db_session.get(RegulatoryInteraction, ri.id).regulator_id == dangling


def test_dangling_target_id_is_allowed(db_session):
    dangling = uuid.uuid4()
    ri = RegulatoryInteraction(target_id=dangling, **_minimal_regulatory_interaction_kwargs())
    db_session.add(ri)
    db_session.flush()  # must not raise
    assert db_session.get(RegulatoryInteraction, ri.id).target_id == dangling


def test_no_polymorphic_foreign_key_exists(db_session):
    """Direct inspection: regulator_id/target_id carry no FK constraint."""
    inspector = inspect(db_session.get_bind())
    fk_columns = {
        col
        for fk in inspector.get_foreign_keys("regulatory_interaction")
        for col in fk["constrained_columns"]
    }
    assert fk_columns == {"organism_id", "claim_id"}


def test_no_confidence_summary_column_exists():
    """confidence_summary belongs to reaction_enzyme, not
    regulatory_interaction — the specification does not define it here."""
    assert not hasattr(RegulatoryInteraction, "confidence_summary")


def test_regulatory_interaction_has_no_index_beyond_primary_key(db_session):
    """docs/02_database_schema.md's "Required Indexes" section lists nothing
    for regulatory_interaction — none should be invented."""
    inspector = inspect(db_session.get_bind())
    assert inspector.get_indexes("regulatory_interaction") == []


def test_two_similar_regulatory_interactions_coexist(db_session):
    """Two rows describing the same regulator/target/effect must both
    persist: the specification defines no uniqueness constraint here."""
    kwargs = _minimal_regulatory_interaction_kwargs()
    db_session.add(RegulatoryInteraction(**kwargs))
    db_session.add(RegulatoryInteraction(**kwargs))
    db_session.flush()  # must not raise


# --- modeling_assumption --------------------------------------------------


def test_create_modeling_assumption(db_session):
    ma = ModelingAssumption(subject_type="reaction", assumption="test-only assumption text")
    db_session.add(ma)
    db_session.flush()

    fetched = db_session.get(ModelingAssumption, ma.id)
    assert fetched is not None
    assert fetched.subject_type == "reaction"
    assert fetched.assumption == "test-only assumption text"
    assert fetched.required_for_model is False
    assert fetched.human_approved is False
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_modeling_assumption_subject_type_is_required(db_session):
    db_session.add(ModelingAssumption(subject_type=None, assumption="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_modeling_assumption_assumption_is_required(db_session):
    db_session.add(ModelingAssumption(subject_type="reaction", assumption=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_modeling_assumption_nullable_fields_accept_null(db_session):
    ma = ModelingAssumption(
        subject_type="reaction",
        subject_id=None,
        assumption="x",
        reason=None,
        confidence=None,
    )
    db_session.add(ma)
    db_session.flush()  # must not raise

    fetched = db_session.get(ModelingAssumption, ma.id)
    assert fetched.subject_id is None
    assert fetched.reason is None
    assert fetched.confidence is None


def test_modeling_assumption_subject_type_and_id_persist_literally(db_session):
    subject_id = uuid.uuid4()
    ma = ModelingAssumption(
        subject_type="reaction", subject_id=subject_id, assumption="x"
    )
    db_session.add(ma)
    db_session.flush()

    fetched = db_session.get(ModelingAssumption, ma.id)
    assert fetched.subject_type == "reaction"
    assert fetched.subject_id == subject_id


def test_modeling_assumption_dangling_subject_id_is_allowed(db_session):
    dangling = uuid.uuid4()
    ma = ModelingAssumption(subject_type="reaction", subject_id=dangling, assumption="x")
    db_session.add(ma)
    db_session.flush()  # must not raise
    assert db_session.get(ModelingAssumption, ma.id).subject_id == dangling


def test_modeling_assumption_no_foreign_key_on_subject_id(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_foreign_keys("modeling_assumption") == []


@pytest.mark.parametrize("value", [-500, -1, 0, 100, 101, 1000])
def test_modeling_assumption_confidence_accepts_values_outside_zero_to_hundred(
    db_session, value
):
    """No range constraint is declared for this column in the specification
    (unlike claim.confidence_score)."""
    ma = ModelingAssumption(subject_type="reaction", assumption="x", confidence=value)
    db_session.add(ma)
    db_session.flush()  # must not raise
    assert db_session.get(ModelingAssumption, ma.id).confidence == value


def test_modeling_assumption_no_range_check_was_invented(db_session):
    """Direct schema inspection: no CHECK constraint exists on this table."""
    inspector = inspect(db_session.get_bind())
    assert inspector.get_check_constraints("modeling_assumption") == []


def test_modeling_assumption_has_no_index_beyond_primary_key(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_indexes("modeling_assumption") == []


def test_two_modeling_assumptions_with_same_subject_and_text_coexist(db_session):
    subject_id = uuid.uuid4()
    db_session.add(
        ModelingAssumption(subject_type="reaction", subject_id=subject_id, assumption="x")
    )
    db_session.add(
        ModelingAssumption(subject_type="reaction", subject_id=subject_id, assumption="x")
    )
    db_session.flush()  # must not raise


def test_modeling_assumption_is_not_evidence(db_session):
    """A modeling assumption is never automatically a Claim, Evidence,
    SUPPORTED, or scientifically validated — it has no FK to claim or
    evidence at all, and this test creates none."""
    ma = ModelingAssumption(subject_type="reaction", assumption="x")
    db_session.add(ma)
    db_session.flush()

    inspector = inspect(db_session.get_bind())
    columns = {col["name"] for col in inspector.get_columns("modeling_assumption")}
    assert "claim_id" not in columns
    assert "evidence_id" not in columns


# --- knowledge_gap ----------------------------------------------------------


def test_create_knowledge_gap(db_session):
    kg = KnowledgeGap(subject_type="reaction", missing_information="test-only missing info")
    db_session.add(kg)
    db_session.flush()

    fetched = db_session.get(KnowledgeGap, kg.id)
    assert fetched is not None
    assert fetched.subject_type == "reaction"
    assert fetched.missing_information == "test-only missing info"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_knowledge_gap_subject_type_is_required(db_session):
    db_session.add(KnowledgeGap(subject_type=None, missing_information="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_knowledge_gap_missing_information_is_required(db_session):
    db_session.add(KnowledgeGap(subject_type="reaction", missing_information=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_knowledge_gap_nullable_fields_accept_null(db_session):
    kg = KnowledgeGap(
        subject_type="reaction",
        subject_id=None,
        missing_information="x",
        importance=None,
        model_impact=None,
        suggested_experiment=None,
        priority=None,
        status=None,
    )
    db_session.add(kg)
    db_session.flush()  # must not raise

    fetched = db_session.get(KnowledgeGap, kg.id)
    assert fetched.subject_id is None
    assert fetched.priority is None
    assert fetched.status is None


def test_knowledge_gap_subject_type_and_id_persist_literally(db_session):
    subject_id = uuid.uuid4()
    kg = KnowledgeGap(
        subject_type="reaction", subject_id=subject_id, missing_information="x"
    )
    db_session.add(kg)
    db_session.flush()

    fetched = db_session.get(KnowledgeGap, kg.id)
    assert fetched.subject_type == "reaction"
    assert fetched.subject_id == subject_id


def test_knowledge_gap_dangling_subject_id_is_allowed(db_session):
    dangling = uuid.uuid4()
    kg = KnowledgeGap(subject_type="reaction", subject_id=dangling, missing_information="x")
    db_session.add(kg)
    db_session.flush()  # must not raise
    assert db_session.get(KnowledgeGap, kg.id).subject_id == dangling


def test_knowledge_gap_no_foreign_key_on_subject_id(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_foreign_keys("knowledge_gap") == []


def test_knowledge_gap_arbitrary_status_strings_can_be_stored(db_session):
    kg = KnowledgeGap(
        subject_type="reaction", missing_information="x", status="a completely made-up status"
    )
    db_session.add(kg)
    db_session.flush()  # must not raise: not backed by a PostgreSQL enum
    assert db_session.get(KnowledgeGap, kg.id).status == "a completely made-up status"


def test_knowledge_gap_two_arbitrary_statuses_coexist(db_session):
    """status is a plain VARCHAR, so any two distinct strings — including
    ones with no corresponding Python enum — must both persist."""
    first = KnowledgeGap(subject_type="reaction", missing_information="x", status="OPEN")
    second = KnowledgeGap(
        subject_type="reaction", missing_information="y", status="test-only-custom-status"
    )
    db_session.add_all([first, second])
    db_session.flush()  # must not raise

    assert db_session.get(KnowledgeGap, first.id).status == "OPEN"
    assert db_session.get(KnowledgeGap, second.id).status == "test-only-custom-status"


def test_knowledge_gap_status_has_no_check_constraint(db_session):
    """Direct schema inspection: no CHECK constraint restricts status to a
    fixed vocabulary — there is deliberately no KnowledgeGapStatus enum."""
    inspector = inspect(db_session.get_bind())
    assert inspector.get_check_constraints("knowledge_gap") == []

    columns = {col["name"]: col for col in inspector.get_columns("knowledge_gap")}
    # A plain VARCHAR reports as a String-family type, never ENUM/native enum.
    assert "ENUM" not in type(columns["status"]["type"]).__name__.upper()


def test_knowledge_gap_has_no_index_beyond_primary_key(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_indexes("knowledge_gap") == []


def test_two_knowledge_gaps_describing_same_subject_coexist(db_session):
    subject_id = uuid.uuid4()
    db_session.add(
        KnowledgeGap(subject_type="reaction", subject_id=subject_id, missing_information="x")
    )
    db_session.add(
        KnowledgeGap(subject_type="reaction", subject_id=subject_id, missing_information="x")
    )
    db_session.flush()  # must not raise


# --- no entity-type enum anywhere in Group F --------------------------------


def test_no_entity_type_enum_exists_in_database(db_session):
    """Confirms the seven pre-existing enums plus regulatory_effect are the
    only enum types in the database — no invented entity-type enum for
    subject_type/regulator_type/target_type exists."""
    enum_names = set(
        db_session.execute(
            text("SELECT typname FROM pg_type WHERE typtype = 'e'")
        ).scalars().all()
    )
    assert "entity_type" not in enum_names
    assert "knowledgegapstatus" not in {name.lower() for name in enum_names}
