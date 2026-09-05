"""Database tests for Group C reaction models.

See ``docs/02_database_schema.md``: "Table: reaction", "Table:
reaction_participant", "Table: reaction_enzyme".
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import CurationState, ReactionParticipantRole
from app.models.enzyme_complex import EnzymeComplex
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant

pytestmark = pytest.mark.database

_internal_id_counter = itertools.count()


def _internal_id() -> str:
    return f"TEST_R{next(_internal_id_counter):05d}"


def _make_reaction(db_session, **kwargs) -> Reaction:
    kwargs.setdefault("internal_id", _internal_id())
    kwargs.setdefault("name", "test-only reaction")
    reaction = Reaction(**kwargs)
    db_session.add(reaction)
    db_session.flush()
    return reaction


def _make_compound(db_session, suffix: str = "") -> Compound:
    compound = Compound(canonical_name=f"test-only compound {suffix}")
    db_session.add(compound)
    db_session.flush()
    return compound


def _make_organism(db_session, suffix: str) -> Organism:
    organism = Organism(scientific_name=f"test-only organism {suffix}")
    db_session.add(organism)
    db_session.flush()
    return organism


# --- reaction ----------------------------------------------------------


def test_create_reaction(db_session):
    reaction = _make_reaction(db_session)
    fetched = db_session.get(Reaction, reaction.id)
    assert fetched is not None
    assert fetched.name == "test-only reaction"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_reaction_internal_id_is_required(db_session):
    db_session.add(Reaction(internal_id=None, name="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_internal_id_is_unique(db_session):
    _make_reaction(db_session, internal_id="TEST_DUPLICATE")
    db_session.add(Reaction(internal_id="TEST_DUPLICATE", name="dup"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_organism_id_may_be_null(db_session):
    reaction = _make_reaction(db_session, organism_id=None)
    assert db_session.get(Reaction, reaction.id).organism_id is None


def test_reaction_invalid_organism_id_is_rejected(db_session):
    db_session.add(Reaction(internal_id=_internal_id(), name="bogus", organism_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_curation_state_defaults_to_proposed(db_session):
    reaction = _make_reaction(db_session)
    assert db_session.get(Reaction, reaction.id).curation_state == CurationState.PROPOSED


def test_reaction_invalid_curation_state_is_rejected_by_enum(db_session):
    reaction = _make_reaction(db_session)
    with pytest.raises(DataError):
        db_session.execute(
            text("UPDATE reaction SET curation_state = 'NOT_A_REAL_STATE' WHERE id = :id"),
            {"id": reaction.id},
        )


def test_reaction_status_is_independent_of_curation_state(db_session):
    """docs/02_database_schema.md defines status and curation_state as two
    separate columns with no stated relationship between them."""
    reaction = _make_reaction(db_session, status="draft", curation_state=CurationState.REJECTED)
    fetched = db_session.get(Reaction, reaction.id)
    assert fetched.status == "draft"
    assert fetched.curation_state == CurationState.REJECTED


def test_reaction_required_indexes_exist(db_session):
    inspector = inspect(db_session.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("reaction")}
    assert {
        "ix_reaction_kegg_reaction_id",
        "ix_reaction_rhea_id",
        "ix_reaction_ec_number",
        "ix_reaction_metacyc_reaction_id",
    } <= index_names
    unique_names = {uq["name"] for uq in inspector.get_unique_constraints("reaction")}
    assert "uq_reaction_internal_id" in unique_names


def test_reaction_external_ids_remain_non_unique(db_session):
    """kegg_reaction_id/metacyc_reaction_id/rhea_id are indexed (all three as
    of migration 0009_persistence_hardening) but deliberately not unique --
    app.normalization.reaction treats a duplicate row sharing one of these
    identifiers as a live, expected AMBIGUOUS outcome, not a defensive edge
    case, and this schema hardening increment does not change that policy."""
    _make_reaction(db_session, kegg_reaction_id="R-test-only-0001")
    _make_reaction(db_session, kegg_reaction_id="R-test-only-0001")
    _make_reaction(db_session, metacyc_reaction_id="test-only-metacyc-rxn")
    _make_reaction(db_session, metacyc_reaction_id="test-only-metacyc-rxn")
    _make_reaction(db_session, rhea_id="test-only-rhea-1")
    _make_reaction(db_session, rhea_id="test-only-rhea-1")
    db_session.flush()  # must not raise


def test_reaction_enzyme_constraints_present(db_session):
    """Migration 0009_persistence_hardening adds
    ck_reaction_enzyme_exactly_one_target and two partial unique indexes on
    reaction_enzyme."""
    inspector = inspect(db_session.get_bind())
    check_names = {ck["name"] for ck in inspector.get_check_constraints("reaction_enzyme")}
    assert "ck_reaction_enzyme_exactly_one_target" in check_names

    index_names = {ix["name"] for ix in inspector.get_indexes("reaction_enzyme")}
    assert {
        "uq_reaction_enzyme_reaction_id_protein_id",
        "uq_reaction_enzyme_reaction_id_complex_id",
    } <= index_names


# --- reaction_participant -----------------------------------------------


@pytest.mark.parametrize("role", list(ReactionParticipantRole))
def test_reaction_participant_valid_roles_persist(db_session, role):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, role.value)
    participant = ReactionParticipant(
        reaction_id=reaction.id, compound_id=compound.id, role=role, stoichiometry=1
    )
    db_session.add(participant)
    db_session.flush()

    assert db_session.get(ReactionParticipant, participant.id).role == role


def test_reaction_participant_invalid_role_is_rejected_by_enum(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "bad-role")
    with pytest.raises(DataError):
        db_session.execute(
            text(
                "INSERT INTO reaction_participant "
                "(id, reaction_id, compound_id, role, stoichiometry) "
                "VALUES (:id, :reaction_id, :compound_id, 'NOT_A_REAL_ROLE', 1)"
            ),
            {"id": uuid.uuid4(), "reaction_id": reaction.id, "compound_id": compound.id},
        )


def test_reaction_participant_positive_stoichiometry_succeeds(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "positive")
    participant = ReactionParticipant(
        reaction_id=reaction.id,
        compound_id=compound.id,
        role=ReactionParticipantRole.REACTANT,
        stoichiometry=2,
    )
    db_session.add(participant)
    db_session.flush()  # must not raise


def test_reaction_participant_zero_stoichiometry_fails(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "zero")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_participant_negative_stoichiometry_fails(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "negative")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.PRODUCT,
            stoichiometry=-1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_participant_invalid_reaction_id_fails(db_session):
    compound = _make_compound(db_session, "bad-reaction")
    db_session.add(
        ReactionParticipant(
            reaction_id=uuid.uuid4(),
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_participant_invalid_compound_id_fails(db_session):
    reaction = _make_reaction(db_session)
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=uuid.uuid4(),
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_participant_invalid_compartment_id_fails(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "bad-compartment")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            compartment_id=uuid.uuid4(),
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_participant_compartment_id_may_be_null(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "null-compartment")
    participant = ReactionParticipant(
        reaction_id=reaction.id,
        compound_id=compound.id,
        compartment_id=None,
        role=ReactionParticipantRole.REACTANT,
        stoichiometry=1,
    )
    db_session.add(participant)
    db_session.flush()  # must not raise

    assert db_session.get(ReactionParticipant, participant.id).compartment_id is None


def test_deleting_referenced_reaction_is_restricted_via_participant(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "restrict-reaction")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    db_session.flush()
    reaction_id = reaction.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Reaction).where(Reaction.id == reaction_id))


def test_deleting_referenced_compound_is_restricted(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "restrict-compound")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    db_session.flush()
    compound_id = compound.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Compound).where(Compound.id == compound_id))


def test_deleting_referenced_compartment_is_restricted(db_session):
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "restrict-compartment")
    compartment = Compartment(name="test-only compartment")
    db_session.add(compartment)
    db_session.flush()
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            compartment_id=compartment.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    db_session.flush()
    compartment_id = compartment.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Compartment).where(Compartment.id == compartment_id))


def test_reaction_participant_no_dedup_constraint_blocks_distinct_rows(db_session):
    """Two participant rows with the same reaction/compound/role/stoichiometry
    must both be allowed: the specification defines no uniqueness constraint
    on this table, and inventing one would risk silently merging scientifically
    distinct records."""
    reaction = _make_reaction(db_session)
    compound = _make_compound(db_session, "no-dedup")
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    db_session.add(
        ReactionParticipant(
            reaction_id=reaction.id,
            compound_id=compound.id,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=1,
        )
    )
    db_session.flush()  # must not raise


# --- reaction_enzyme ------------------------------------------------------


def test_reaction_enzyme_round_trip_with_protein(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "enzyme-protein")
    protein = Protein(organism_id=organism.id, name="Test1p")
    db_session.add(protein)
    db_session.flush()

    re = ReactionEnzyme(reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES")
    db_session.add(re)
    db_session.flush()

    fetched = db_session.get(ReactionEnzyme, re.id)
    assert fetched is not None
    assert fetched.protein_id == protein.id
    assert fetched.complex_id is None
    assert fetched.relationship == "CATALYZES"


def test_reaction_enzyme_round_trip_with_complex(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "enzyme-complex")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add(complex_)
    db_session.flush()

    re = ReactionEnzyme(reaction_id=reaction.id, complex_id=complex_.id, relationship="CATALYZES")
    db_session.add(re)
    db_session.flush()

    fetched = db_session.get(ReactionEnzyme, re.id)
    assert fetched is not None
    assert fetched.complex_id == complex_.id
    assert fetched.protein_id is None


def test_reaction_enzyme_protein_id_may_be_null(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "protein-null")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add(complex_)
    db_session.flush()

    re = ReactionEnzyme(
        reaction_id=reaction.id, protein_id=None, complex_id=complex_.id, relationship="CATALYZES"
    )
    db_session.add(re)
    db_session.flush()  # must not raise


def test_reaction_enzyme_complex_id_may_be_null(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "complex-null")
    protein = Protein(organism_id=organism.id, name="Test2p")
    db_session.add(protein)
    db_session.flush()

    re = ReactionEnzyme(
        reaction_id=reaction.id, protein_id=protein.id, complex_id=None, relationship="CATALYZES"
    )
    db_session.add(re)
    db_session.flush()  # must not raise


def test_reaction_enzyme_neither_target_is_rejected(db_session):
    """As of migration 0009_persistence_hardening,
    ck_reaction_enzyme_exactly_one_target enforces at the database level what
    app.normalization.reaction_enzyme already finalized as identity policy:
    exactly one of protein_id/complex_id, never neither. Prior to that
    migration this was permitted (soft "should normally" language, no CHECK)."""
    reaction = _make_reaction(db_session)
    re = ReactionEnzyme(
        reaction_id=reaction.id, protein_id=None, complex_id=None, relationship="PUTATIVE_CATALYST"
    )
    db_session.add(re)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_both_targets_is_rejected(db_session):
    """Same ck_reaction_enzyme_exactly_one_target constraint, "never both" side."""
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "both-non-null")
    protein = Protein(organism_id=organism.id, name="Test3p")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add_all([protein, complex_])
    db_session.flush()

    re = ReactionEnzyme(
        reaction_id=reaction.id,
        protein_id=protein.id,
        complex_id=complex_.id,
        relationship="ISOENZYME",
    )
    db_session.add(re)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_duplicate_reaction_protein_pair_is_rejected(db_session):
    """uq_reaction_enzyme_reaction_id_protein_id (migration 0009) enforces
    that a (reaction_id, protein_id) pair is recorded at most once, regardless
    of relationship -- app.normalization.reaction_enzyme treats relationship
    as inert metadata for identity purposes, so the pair alone is identity."""
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "dup-protein-pair")
    protein = Protein(organism_id=organism.id, name="Test5p")
    db_session.add(protein)
    db_session.flush()

    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES")
    )
    db_session.flush()

    db_session.add(
        ReactionEnzyme(
            reaction_id=reaction.id, protein_id=protein.id, relationship="PUTATIVE_CATALYST"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_duplicate_reaction_complex_pair_is_rejected(db_session):
    """uq_reaction_enzyme_reaction_id_complex_id (migration 0009), the
    complex_id counterpart of the protein_id uniqueness above."""
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "dup-complex-pair")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add(complex_)
    db_session.flush()

    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, complex_id=complex_.id, relationship="CATALYZES")
    )
    db_session.flush()

    db_session.add(
        ReactionEnzyme(
            reaction_id=reaction.id, complex_id=complex_.id, relationship="ISOENZYME"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_same_protein_different_reaction_is_allowed(db_session):
    """The new pairwise uniqueness is scoped per-reaction: one protein
    catalyzing two different reactions is two independent, legitimate rows."""
    organism = _make_organism(db_session, "multi-reaction-protein")
    protein = Protein(organism_id=organism.id, name="Test6p")
    db_session.add(protein)
    db_session.flush()
    reaction_a = _make_reaction(db_session)
    reaction_b = _make_reaction(db_session)

    db_session.add(
        ReactionEnzyme(reaction_id=reaction_a.id, protein_id=protein.id, relationship="CATALYZES")
    )
    db_session.add(
        ReactionEnzyme(reaction_id=reaction_b.id, protein_id=protein.id, relationship="CATALYZES")
    )
    db_session.flush()  # must not raise


def test_reaction_enzyme_two_proteins_same_reaction_is_allowed(db_session):
    """Isoenzymes: two different proteins catalyzing the same reaction remain
    two independent, legitimate rows -- the new uniqueness is per-pair, not
    per-reaction."""
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "isoenzymes")
    protein_a = Protein(organism_id=organism.id, name="Test7Ap")
    protein_b = Protein(organism_id=organism.id, name="Test7Bp")
    db_session.add_all([protein_a, protein_b])
    db_session.flush()

    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein_a.id, relationship="CATALYZES")
    )
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein_b.id, relationship="CATALYZES")
    )
    db_session.flush()  # must not raise


def test_reaction_enzyme_invalid_reaction_id_fails(db_session):
    organism = _make_organism(db_session, "invalid-reaction-id")
    protein = Protein(organism_id=organism.id, name="Test8p")
    db_session.add(protein)
    db_session.flush()
    db_session.add(
        ReactionEnzyme(reaction_id=uuid.uuid4(), protein_id=protein.id, relationship="CATALYZES")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_invalid_protein_id_fails(db_session):
    reaction = _make_reaction(db_session)
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=uuid.uuid4(), relationship="CATALYZES")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reaction_enzyme_invalid_complex_id_fails(db_session):
    reaction = _make_reaction(db_session)
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, complex_id=uuid.uuid4(), relationship="CATALYZES")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_referenced_reaction_is_restricted_via_enzyme(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "restrict-reaction-via-enzyme")
    protein = Protein(organism_id=organism.id, name="Test9p")
    db_session.add(protein)
    db_session.flush()
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES")
    )
    db_session.flush()
    reaction_id = reaction.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Reaction).where(Reaction.id == reaction_id))


def test_deleting_referenced_protein_is_restricted_via_reaction_enzyme(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "restrict-protein")
    protein = Protein(organism_id=organism.id, name="Test4p")
    db_session.add(protein)
    db_session.flush()
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES")
    )
    db_session.flush()
    protein_id = protein.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Protein).where(Protein.id == protein_id))


def test_deleting_referenced_enzyme_complex_is_restricted_via_reaction_enzyme(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "restrict-complex")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add(complex_)
    db_session.flush()
    db_session.add(
        ReactionEnzyme(reaction_id=reaction.id, complex_id=complex_.id, relationship="CATALYZES")
    )
    db_session.flush()
    complex_id = complex_.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(EnzymeComplex).where(EnzymeComplex.id == complex_id))


def test_reaction_enzyme_confidence_summary_accepts_any_numeric_value(db_session):
    """confidence_summary carries no range CHECK (only its declared NUMERIC
    type constrains it) — docs/02_database_schema.md gives no explicit range
    for this column, unlike claim/kinetic_measurement confidence_score."""
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "confidence-any-value")
    protein = Protein(organism_id=organism.id, name="Test10p")
    db_session.add(protein)
    db_session.flush()
    re = ReactionEnzyme(
        reaction_id=reaction.id,
        protein_id=protein.id,
        relationship="CATALYZES",
        confidence_summary=150,
    )
    db_session.add(re)
    db_session.flush()  # must not raise despite being outside 0-100

    assert db_session.get(ReactionEnzyme, re.id).confidence_summary == 150


def test_reaction_enzyme_confidence_summary_accepts_null(db_session):
    reaction = _make_reaction(db_session)
    organism = _make_organism(db_session, "confidence-null")
    protein = Protein(organism_id=organism.id, name="Test11p")
    db_session.add(protein)
    db_session.flush()
    re = ReactionEnzyme(reaction_id=reaction.id, protein_id=protein.id, relationship="CATALYZES")
    db_session.add(re)
    db_session.flush()

    assert db_session.get(ReactionEnzyme, re.id).confidence_summary is None
