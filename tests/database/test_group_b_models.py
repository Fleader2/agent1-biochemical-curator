"""Database tests for Group B gene/protein/enzyme-complex models.

See ``docs/02_database_schema.md``: "Table: gene", "Table: protein",
"Table: enzyme_complex", "Table: enzyme_complex_member".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models.enzyme_complex import EnzymeComplex, EnzymeComplexMember
from app.models.gene import Gene
from app.models.organism import Organism
from app.models.protein import Protein

pytestmark = pytest.mark.database


def _make_organism(db_session, suffix: str) -> Organism:
    organism = Organism(scientific_name=f"test-only organism {suffix}")
    db_session.add(organism)
    db_session.flush()
    return organism


# --- creation and round-trip persistence ------------------------------------


def test_create_gene(db_session):
    organism = _make_organism(db_session, "gene-create")
    gene = Gene(organism_id=organism.id, symbol="TEST1", systematic_name="Y0000001")
    db_session.add(gene)
    db_session.flush()

    fetched = db_session.get(Gene, gene.id)
    assert fetched is not None
    assert fetched.organism_id == organism.id
    assert fetched.symbol == "TEST1"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_create_protein(db_session):
    organism = _make_organism(db_session, "protein-create")
    protein = Protein(organism_id=organism.id, name="Test1p")
    db_session.add(protein)
    db_session.flush()

    fetched = db_session.get(Protein, protein.id)
    assert fetched is not None
    assert fetched.organism_id == organism.id
    assert fetched.name == "Test1p"
    assert fetched.gene_id is None


def test_create_enzyme_complex(db_session):
    organism = _make_organism(db_session, "complex-create")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    db_session.add(complex_)
    db_session.flush()

    fetched = db_session.get(EnzymeComplex, complex_.id)
    assert fetched is not None
    assert fetched.organism_id == organism.id
    assert fetched.name == "test-only complex"


def test_create_enzyme_complex_member(db_session):
    organism = _make_organism(db_session, "member-create")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    protein = Protein(organism_id=organism.id, name="Test1p")
    db_session.add_all([complex_, protein])
    db_session.flush()

    member = EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id, stoichiometry=2)
    db_session.add(member)
    db_session.flush()

    fetched = db_session.get(EnzymeComplexMember, member.id)
    assert fetched is not None
    assert fetched.complex_id == complex_.id
    assert fetched.protein_id == protein.id
    assert fetched.required is True


# --- foreign key requirements -------------------------------------------


def test_gene_requires_a_valid_organism(db_session):
    db_session.add(Gene(organism_id=uuid.uuid4(), symbol="BOGUS"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_protein_requires_a_valid_organism(db_session):
    db_session.add(Protein(organism_id=uuid.uuid4(), name="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_protein_gene_id_may_be_null(db_session):
    """docs/02_database_schema.md: protein.gene_id has no NOT NULL — a protein
    is not assumed to always correspond to exactly one active enzyme's gene."""
    organism = _make_organism(db_session, "protein-null-gene")
    protein = Protein(organism_id=organism.id, name="Test2p", gene_id=None)
    db_session.add(protein)
    db_session.flush()  # must not raise

    assert db_session.get(Protein, protein.id).gene_id is None


def test_protein_invalid_gene_id_is_rejected(db_session):
    organism = _make_organism(db_session, "protein-bad-gene")
    db_session.add(Protein(organism_id=organism.id, name="bogus", gene_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_complex_requires_a_valid_organism(db_session):
    db_session.add(EnzymeComplex(organism_id=uuid.uuid4(), name="bogus complex"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_complex_member_requires_valid_complex_and_protein(db_session):
    organism = _make_organism(db_session, "member-bad-fk")
    protein = Protein(organism_id=organism.id, name="Test3p")
    db_session.add(protein)
    db_session.flush()

    db_session.add(EnzymeComplexMember(complex_id=uuid.uuid4(), protein_id=protein.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- gene external-identifier uniqueness ------------------------------------


@pytest.mark.parametrize("field", ["sgd_id", "ncbi_gene_id", "uniprot_id", "kegg_gene_id"])
def test_gene_external_identifier_is_unique_when_present(db_session, field):
    organism = _make_organism(db_session, f"gene-uniq-{field}")
    value = f"test-only-{field}-1"

    db_session.add(Gene(organism_id=organism.id, symbol="A", **{field: value}))
    db_session.flush()

    db_session.add(Gene(organism_id=organism.id, symbol="B", **{field: value}))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("field", ["sgd_id", "ncbi_gene_id", "uniprot_id", "kegg_gene_id"])
def test_gene_external_identifier_allows_multiple_nulls(db_session, field):
    organism = _make_organism(db_session, f"gene-null-{field}")

    db_session.add(Gene(organism_id=organism.id, symbol="A"))
    db_session.add(Gene(organism_id=organism.id, symbol="B"))
    db_session.flush()  # must not raise: field left unset (NULL) on both


def test_gene_identifier_uniqueness_is_not_scoped_by_organism(db_session):
    """Approved decision: gene identifier uniqueness applies globally, not
    per organism_id."""
    organism_a = _make_organism(db_session, "gene-global-a")
    organism_b = _make_organism(db_session, "gene-global-b")

    db_session.add(Gene(organism_id=organism_a.id, symbol="A", sgd_id="test-only-global-sgd"))
    db_session.flush()

    db_session.add(Gene(organism_id=organism_b.id, symbol="B", sgd_id="test-only-global-sgd"))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- protein.uniprot_id must not be accidentally unique ---------------------


def test_protein_uniprot_id_is_not_unique(db_session):
    organism = _make_organism(db_session, "protein-uniprot")
    db_session.add(Protein(organism_id=organism.id, name="A", uniprot_id="test-only-P12345"))
    db_session.add(Protein(organism_id=organism.id, name="B", uniprot_id="test-only-P12345"))
    db_session.flush()  # must not raise: only gene.uniprot_id is unique


# --- enzyme_complex_member uniqueness and delete behavior -------------------


def test_enzyme_complex_member_uniqueness(db_session):
    organism = _make_organism(db_session, "member-uniq")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    protein = Protein(organism_id=organism.id, name="Test4p")
    db_session.add_all([complex_, protein])
    db_session.flush()

    db_session.add(EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id))
    db_session.flush()

    db_session.add(EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_enzyme_complex_cascades_to_members(db_session):
    """Exercises the database-level ON DELETE CASCADE directly via a Core
    DELETE statement, bypassing the ORM relationship's own client-side
    cascade so the database constraint itself is what is being tested."""
    organism = _make_organism(db_session, "member-cascade")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    protein = Protein(organism_id=organism.id, name="Test5p")
    db_session.add_all([complex_, protein])
    db_session.flush()

    member = EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id)
    db_session.add(member)
    db_session.flush()
    complex_id, member_id = complex_.id, member.id
    db_session.expunge_all()

    db_session.execute(delete(EnzymeComplex).where(EnzymeComplex.id == complex_id))
    db_session.flush()

    assert db_session.get(EnzymeComplexMember, member_id) is None


def test_deleting_referenced_protein_is_restricted(db_session):
    organism = _make_organism(db_session, "member-restrict")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    protein = Protein(organism_id=organism.id, name="Test6p")
    db_session.add_all([complex_, protein])
    db_session.flush()

    db_session.add(EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id))
    db_session.flush()
    protein_id = protein.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Protein).where(Protein.id == protein_id))


# --- required / nullable fields ---------------------------------------------


def test_protein_name_is_required(db_session):
    organism = _make_organism(db_session, "protein-name-required")
    db_session.add(Protein(organism_id=organism.id, name=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_protein_notes_accepts_null(db_session):
    organism = _make_organism(db_session, "protein-notes-null")
    protein = Protein(organism_id=organism.id, name="Test7p", notes=None)
    db_session.add(protein)
    db_session.flush()  # must not raise


def test_enzyme_complex_name_is_required(db_session):
    organism = _make_organism(db_session, "complex-name-required")
    db_session.add(EnzymeComplex(organism_id=organism.id, name=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enzyme_complex_member_stoichiometry_accepts_null(db_session):
    organism = _make_organism(db_session, "member-stoich-null")
    complex_ = EnzymeComplex(organism_id=organism.id, name="test-only complex")
    protein = Protein(organism_id=organism.id, name="Test8p")
    db_session.add_all([complex_, protein])
    db_session.flush()

    member = EnzymeComplexMember(complex_id=complex_.id, protein_id=protein.id, stoichiometry=None)
    db_session.add(member)
    db_session.flush()  # must not raise

    assert db_session.get(EnzymeComplexMember, member.id).stoichiometry is None


def test_gene_organism_id_is_required(db_session):
    db_session.add(Gene(organism_id=None, symbol="test-only"))
    with pytest.raises(IntegrityError):
        db_session.flush()
