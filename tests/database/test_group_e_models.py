"""Database tests for Group E kinetic_measurement model.

See ``docs/02_database_schema.md`` ("Table: kinetic_measurement").
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models.claim import Claim, Evidence
from app.models.compound import Compound
from app.models.enums import ConfidenceClass, EvidenceType, SourceType
from app.models.enzyme_complex import EnzymeComplex
from app.models.kinetic_measurement import KineticMeasurement
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction

pytestmark = pytest.mark.database


def _make_organism(db_session, suffix: str) -> Organism:
    organism = Organism(scientific_name=f"test-only organism {suffix}")
    db_session.add(organism)
    db_session.flush()
    return organism


def _make_reaction(db_session, suffix: str) -> Reaction:
    reaction = Reaction(internal_id=f"TEST_KM_{suffix}", name=f"test-only reaction {suffix}")
    db_session.add(reaction)
    db_session.flush()
    return reaction


def _make_protein(db_session, organism: Organism, suffix: str) -> Protein:
    protein = Protein(organism_id=organism.id, name=f"Test{suffix}p")
    db_session.add(protein)
    db_session.flush()
    return protein


def _make_complex(db_session, organism: Organism, suffix: str) -> EnzymeComplex:
    complex_ = EnzymeComplex(organism_id=organism.id, name=f"test-only complex {suffix}")
    db_session.add(complex_)
    db_session.flush()
    return complex_


def _make_compound(db_session, suffix: str) -> Compound:
    compound = Compound(canonical_name=f"test-only compound {suffix}")
    db_session.add(compound)
    db_session.flush()
    return compound


def _make_publication(db_session, suffix: str) -> Publication:
    publication = Publication(title=f"test-only publication {suffix}")
    db_session.add(publication)
    db_session.flush()
    return publication


def _make_evidence(db_session, suffix: str) -> Evidence:
    claim = Claim(subject_type="protein", predicate="KM")
    db_session.add(claim)
    db_session.flush()
    evidence = Evidence(
        claim_id=claim.id,
        source_type=SourceType.PUBMED,
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        curator_summary=f"test-only evidence {suffix}",
    )
    db_session.add(evidence)
    db_session.flush()
    return evidence


def _minimal_kwargs() -> dict:
    return {"parameter_type": "Km", "parameter_value": 1, "unit": "mM"}


# --- basic persistence -------------------------------------------------


def test_create_kinetic_measurement(db_session):
    km = KineticMeasurement(**_minimal_kwargs())
    db_session.add(km)
    db_session.flush()

    fetched = db_session.get(KineticMeasurement, km.id)
    assert fetched is not None
    assert fetched.parameter_type == "Km"
    assert fetched.parameter_value == 1
    assert fetched.unit == "mM"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_parameter_type_is_required(db_session):
    db_session.add(KineticMeasurement(parameter_type=None, parameter_value=1, unit="mM"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_parameter_value_is_required(db_session):
    db_session.add(KineticMeasurement(parameter_type="Km", parameter_value=None, unit="mM"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_unit_is_required(db_session):
    db_session.add(KineticMeasurement(parameter_type="Km", parameter_value=1, unit=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_all_context_fields_accept_null(db_session):
    km = KineticMeasurement(**_minimal_kwargs())
    db_session.add(km)
    db_session.flush()

    fetched = db_session.get(KineticMeasurement, km.id)
    for field in (
        "reaction_id",
        "protein_id",
        "complex_id",
        "original_value",
        "original_unit",
        "normalized_value",
        "normalized_unit",
        "substrate_id",
        "organism_id",
        "strain",
        "temperature_c",
        "ph",
        "ionic_strength",
        "ionic_strength_unit",
        "buffer",
        "enzyme_concentration",
        "enzyme_concentration_unit",
        "substrate_concentrations_json",
        "protein_form",
        "purification_state",
        "assay_type",
        "publication_id",
        "evidence_id",
        "confidence_score",
        "confidence_class",
        "model_applicability_score",
        "notes",
    ):
        assert getattr(fetched, field) is None, field


# --- foreign keys --------------------------------------------------------


def test_valid_reaction_id_persists(db_session):
    reaction = _make_reaction(db_session, "fk1")
    km = KineticMeasurement(reaction_id=reaction.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).reaction_id == reaction.id


def test_invalid_reaction_id_fails(db_session):
    db_session.add(KineticMeasurement(reaction_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_protein_id_persists(db_session):
    organism = _make_organism(db_session, "fk-protein")
    protein = _make_protein(db_session, organism, "1")
    km = KineticMeasurement(protein_id=protein.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).protein_id == protein.id


def test_invalid_protein_id_fails(db_session):
    db_session.add(KineticMeasurement(protein_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_complex_id_persists(db_session):
    organism = _make_organism(db_session, "fk-complex")
    complex_ = _make_complex(db_session, organism, "1")
    km = KineticMeasurement(complex_id=complex_.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).complex_id == complex_.id


def test_invalid_complex_id_fails(db_session):
    db_session.add(KineticMeasurement(complex_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_substrate_id_persists(db_session):
    compound = _make_compound(db_session, "fk1")
    km = KineticMeasurement(substrate_id=compound.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).substrate_id == compound.id


def test_invalid_substrate_id_fails(db_session):
    db_session.add(KineticMeasurement(substrate_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_organism_id_persists(db_session):
    organism = _make_organism(db_session, "fk-direct")
    km = KineticMeasurement(organism_id=organism.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).organism_id == organism.id


def test_invalid_organism_id_fails(db_session):
    db_session.add(KineticMeasurement(organism_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_publication_id_persists(db_session):
    publication = _make_publication(db_session, "fk1")
    km = KineticMeasurement(publication_id=publication.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).publication_id == publication.id


def test_invalid_publication_id_fails(db_session):
    db_session.add(KineticMeasurement(publication_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_valid_evidence_id_persists(db_session):
    evidence = _make_evidence(db_session, "fk1")
    km = KineticMeasurement(evidence_id=evidence.id, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).evidence_id == evidence.id


def test_invalid_evidence_id_fails(db_session):
    db_session.add(KineticMeasurement(evidence_id=uuid.uuid4(), **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_referenced_reaction_is_restricted(db_session):
    reaction = _make_reaction(db_session, "restrict")
    db_session.add(KineticMeasurement(reaction_id=reaction.id, **_minimal_kwargs()))
    db_session.flush()
    reaction_id = reaction.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Reaction).where(Reaction.id == reaction_id))


def test_deleting_referenced_protein_is_restricted(db_session):
    organism = _make_organism(db_session, "restrict-protein")
    protein = _make_protein(db_session, organism, "restrict")
    db_session.add(KineticMeasurement(protein_id=protein.id, **_minimal_kwargs()))
    db_session.flush()
    protein_id = protein.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Protein).where(Protein.id == protein_id))


def test_deleting_referenced_enzyme_complex_is_restricted(db_session):
    organism = _make_organism(db_session, "restrict-complex")
    complex_ = _make_complex(db_session, organism, "restrict")
    db_session.add(KineticMeasurement(complex_id=complex_.id, **_minimal_kwargs()))
    db_session.flush()
    complex_id = complex_.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(EnzymeComplex).where(EnzymeComplex.id == complex_id))


def test_deleting_referenced_compound_is_restricted(db_session):
    compound = _make_compound(db_session, "restrict")
    db_session.add(KineticMeasurement(substrate_id=compound.id, **_minimal_kwargs()))
    db_session.flush()
    compound_id = compound.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Compound).where(Compound.id == compound_id))


def test_deleting_referenced_organism_is_restricted(db_session):
    organism = _make_organism(db_session, "restrict-direct")
    db_session.add(KineticMeasurement(organism_id=organism.id, **_minimal_kwargs()))
    db_session.flush()
    organism_id = organism.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Organism).where(Organism.id == organism_id))


def test_deleting_referenced_publication_is_restricted(db_session):
    publication = _make_publication(db_session, "restrict")
    db_session.add(KineticMeasurement(publication_id=publication.id, **_minimal_kwargs()))
    db_session.flush()
    publication_id = publication.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Publication).where(Publication.id == publication_id))


def test_deleting_referenced_evidence_is_restricted(db_session):
    evidence = _make_evidence(db_session, "restrict")
    db_session.add(KineticMeasurement(evidence_id=evidence.id, **_minimal_kwargs()))
    db_session.flush()
    evidence_id = evidence.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Evidence).where(Evidence.id == evidence_id))


# --- parameter_type: not an enum --------------------------------------


def test_arbitrary_parameter_type_strings_can_be_stored(db_session):
    km = KineticMeasurement(
        parameter_type="a completely made-up parameter name",
        parameter_value=1,
        unit="unitless",
    )
    db_session.add(km)
    db_session.flush()  # must not raise: not backed by a PostgreSQL enum

    assert db_session.get(KineticMeasurement, km.id).parameter_type == (
        "a completely made-up parameter name"
    )


def test_two_arbitrary_parameter_types_coexist(db_session):
    """parameter_type is a plain VARCHAR, so any two distinct strings —
    including ones not in the spec's example list — must both persist."""
    first = KineticMeasurement(parameter_type="test-only-param-A", parameter_value=1, unit="mM")
    second = KineticMeasurement(parameter_type="test-only-param-B", parameter_value=2, unit="s^-1")
    db_session.add_all([first, second])
    db_session.flush()  # must not raise

    assert db_session.get(KineticMeasurement, first.id).parameter_type == "test-only-param-A"
    assert db_session.get(KineticMeasurement, second.id).parameter_type == "test-only-param-B"


# --- scientific integrity: no deduplication ---------------------------


def test_identical_measurements_both_persist(db_session):
    """Two rows with the same reaction, protein, parameter_type, value, unit,
    publication, and evidence must both be allowed: no natural-key
    uniqueness/dedup constraint exists on this table."""
    reaction = _make_reaction(db_session, "dedup")
    organism = _make_organism(db_session, "dedup")
    protein = _make_protein(db_session, organism, "dedup")
    publication = _make_publication(db_session, "dedup")
    evidence = _make_evidence(db_session, "dedup")

    shared_kwargs = {
        "reaction_id": reaction.id,
        "protein_id": protein.id,
        "parameter_type": "Km",
        "parameter_value": 4,
        "unit": "mM",
        "publication_id": publication.id,
        "evidence_id": evidence.id,
    }
    db_session.add(KineticMeasurement(**shared_kwargs))
    db_session.add(KineticMeasurement(**shared_kwargs))
    db_session.flush()  # must not raise

    count = (
        db_session.query(KineticMeasurement)
        .filter_by(reaction_id=reaction.id, protein_id=protein.id, parameter_type="Km")
        .count()
    )
    assert count == 2


def test_measurements_differing_only_by_organism_remain_distinct(db_session):
    organism_a = _make_organism(db_session, "strain-a")
    organism_b = _make_organism(db_session, "strain-b")

    km_a = KineticMeasurement(organism_id=organism_a.id, strain="A", **_minimal_kwargs())
    km_b = KineticMeasurement(organism_id=organism_b.id, strain="B", **_minimal_kwargs())
    db_session.add_all([km_a, km_b])
    db_session.flush()  # must not raise

    assert db_session.get(KineticMeasurement, km_a.id).strain == "A"
    assert db_session.get(KineticMeasurement, km_b.id).strain == "B"


def test_measurements_differing_only_by_experimental_context_remain_distinct(db_session):
    """Two otherwise-identical measurements differing only in
    temperature_c/ph (context represented directly on this table) must both
    persist as independent rows."""
    kwargs = _minimal_kwargs()
    km_25c = KineticMeasurement(temperature_c=25, ph=7.0, **kwargs)
    km_37c = KineticMeasurement(temperature_c=37, ph=7.0, **kwargs)
    db_session.add_all([km_25c, km_37c])
    db_session.flush()  # must not raise

    assert db_session.get(KineticMeasurement, km_25c.id).temperature_c == 25
    assert db_session.get(KineticMeasurement, km_37c.id).temperature_c == 37


def test_no_natural_key_unique_constraint_exists(db_session):
    """Direct inspection: no UNIQUE constraint or unique index exists on
    kinetic_measurement other than the primary key."""
    from sqlalchemy import inspect

    inspector = inspect(db_session.get_bind())
    unique_constraints = inspector.get_unique_constraints("kinetic_measurement")
    assert unique_constraints == []

    unique_indexes = [
        ix for ix in inspector.get_indexes("kinetic_measurement") if ix.get("unique")
    ]
    assert unique_indexes == []


# --- values and units ---------------------------------------------------


def test_original_value_and_unit_are_preserved_exactly(db_session):
    km = KineticMeasurement(
        parameter_type="Km",
        parameter_value=1,
        unit="mM",
        original_value=Decimal("0.42"),
        original_unit="mg/mL/min",
    )
    db_session.add(km)
    db_session.flush()

    fetched = db_session.get(KineticMeasurement, km.id)
    assert fetched.original_value == Decimal("0.42")
    assert fetched.original_unit == "mg/mL/min"


def test_normalized_value_coexists_with_original_without_overwriting_it(db_session):
    km = KineticMeasurement(
        parameter_type="Km",
        parameter_value=1,
        unit="mM",
        original_value=Decimal("0.42"),
        original_unit="mg/mL/min",
        normalized_value=Decimal("0.0037"),
        normalized_unit="mM",
    )
    db_session.add(km)
    db_session.flush()

    fetched = db_session.get(KineticMeasurement, km.id)
    assert fetched.original_value == Decimal("0.42")
    assert fetched.original_unit == "mg/mL/min"
    assert fetched.normalized_value == Decimal("0.0037")
    assert fetched.normalized_unit == "mM"


def test_setting_normalized_fields_does_not_overwrite_original_fields(db_session):
    km = KineticMeasurement(
        parameter_type="Km",
        parameter_value=1,
        unit="mM",
        original_value=Decimal("0.42"),
        original_unit="X",
    )
    db_session.add(km)
    db_session.flush()

    km.normalized_value = Decimal("999")
    km.normalized_unit = "mM"
    db_session.flush()

    fetched = db_session.get(KineticMeasurement, km.id)
    assert fetched.original_value == Decimal("0.42")
    assert fetched.original_unit == "X"


# --- confidence -----------------------------------------------------------


def test_confidence_score_accepts_null(db_session):
    km = KineticMeasurement(confidence_score=None, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).confidence_score is None


@pytest.mark.parametrize("value", [0, 100])
def test_confidence_score_accepts_boundary_values(db_session, value):
    km = KineticMeasurement(confidence_score=value, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).confidence_score == value


def test_confidence_score_rejects_below_zero(db_session):
    db_session.add(KineticMeasurement(confidence_score=-1, **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_confidence_score_rejects_above_hundred(db_session):
    db_session.add(KineticMeasurement(confidence_score=101, **_minimal_kwargs()))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("confidence_class", list(ConfidenceClass))
def test_every_confidence_class_persists(db_session, confidence_class):
    km = KineticMeasurement(confidence_class=confidence_class, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).confidence_class == confidence_class


def test_confidence_class_is_not_forced_to_match_confidence_score(db_session):
    km = KineticMeasurement(
        confidence_score=3, confidence_class=ConfidenceClass.VERY_HIGH, **_minimal_kwargs()
    )
    db_session.add(km)
    db_session.flush()  # must not raise despite the mismatch

    fetched = db_session.get(KineticMeasurement, km.id)
    assert fetched.confidence_score == 3
    assert fetched.confidence_class == ConfidenceClass.VERY_HIGH


# --- model_applicability_score --------------------------------------------


def test_model_applicability_score_accepts_value_outside_zero_to_hundred(db_session):
    """No range constraint is declared for this column in the specification."""
    km = KineticMeasurement(model_applicability_score=250, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()  # must not raise

    assert db_session.get(KineticMeasurement, km.id).model_applicability_score == 250


def test_model_applicability_score_accepts_null(db_session):
    km = KineticMeasurement(model_applicability_score=None, **_minimal_kwargs())
    db_session.add(km)
    db_session.flush()
    assert db_session.get(KineticMeasurement, km.id).model_applicability_score is None
