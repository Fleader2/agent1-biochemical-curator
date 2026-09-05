"""Database tests for Group A reference-data models.

See ``docs/02_database_schema.md``: "Table: organism", "Table: compound",
"Table: compound_synonym", "Table: compartment", "Table: publication",
"Table: experimental_condition".
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from app.models.compartment import Compartment
from app.models.compound import Compound, CompoundSynonym
from app.models.experimental_condition import ExperimentalCondition
from app.models.organism import Organism
from app.models.publication import Publication

pytestmark = pytest.mark.database

# Verbatim from docs/02_database_schema.md, "Initial Seed Data".
_STANDARD_COMPARTMENTS = {
    "cytosol",
    "mitochondrial matrix",
    "mitochondrial intermembrane space",
    "mitochondrial inner membrane",
    "mitochondrial outer membrane",
    "peroxisome",
    "endoplasmic reticulum",
    "Golgi",
    "lipid droplet",
    "nucleus",
    "vacuole",
    "plasma membrane",
    "extracellular",
}


# --- creation and round-trip persistence ------------------------------------


def test_create_organism(db_session):
    organism = Organism(scientific_name="Saccharomyces cerevisiae", strain="test-only-S288C")
    db_session.add(organism)
    db_session.flush()

    fetched = db_session.get(Organism, organism.id)
    assert fetched is not None
    assert fetched.scientific_name == "Saccharomyces cerevisiae"
    assert fetched.strain == "test-only-S288C"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_create_compartment(db_session):
    compartment = Compartment(name="test-only-compartment")
    db_session.add(compartment)
    db_session.flush()

    fetched = db_session.get(Compartment, compartment.id)
    assert fetched is not None
    assert fetched.name == "test-only-compartment"
    assert fetched.organism_id is None


def test_create_compound(db_session):
    compound = Compound(canonical_name="test-only compound")
    db_session.add(compound)
    db_session.flush()

    fetched = db_session.get(Compound, compound.id)
    assert fetched is not None
    assert fetched.canonical_name == "test-only compound"
    assert fetched.is_generic is False


def test_create_compound_synonym(db_session):
    compound = Compound(canonical_name="test-only compound")
    db_session.add(compound)
    db_session.flush()

    synonym = CompoundSynonym(compound_id=compound.id, synonym="test-only synonym")
    db_session.add(synonym)
    db_session.flush()

    fetched = db_session.get(CompoundSynonym, synonym.id)
    assert fetched is not None
    assert fetched.synonym == "test-only synonym"
    assert fetched.compound_id == compound.id


def test_create_publication(db_session):
    publication = Publication(title="A test-only publication")
    db_session.add(publication)
    db_session.flush()

    fetched = db_session.get(Publication, publication.id)
    assert fetched is not None
    assert fetched.title == "A test-only publication"


def test_create_experimental_condition(db_session):
    condition = ExperimentalCondition(medium="test-only-YPD")
    db_session.add(condition)
    db_session.flush()

    fetched = db_session.get(ExperimentalCondition, condition.id)
    assert fetched is not None
    assert fetched.medium == "test-only-YPD"


# --- organism uniqueness -----------------------------------------------------


def test_organism_scientific_name_and_strain_must_be_unique_when_strain_present(db_session):
    db_session.add(Organism(scientific_name="test-only species A", strain="A"))
    db_session.flush()

    db_session.add(Organism(scientific_name="test-only species A", strain="A"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_organism_same_species_different_strain_is_allowed(db_session):
    db_session.add(Organism(scientific_name="test-only species B", strain="A"))
    db_session.add(Organism(scientific_name="test-only species B", strain="B"))
    db_session.flush()  # must not raise


def test_organism_multiple_null_strain_rows_are_allowed(db_session):
    """docs/02_database_schema.md requires uniqueness only "when strain is
    present"; rows with a NULL strain are not deduplicated by the schema."""
    db_session.add(Organism(scientific_name="test-only species, no strain"))
    db_session.add(Organism(scientific_name="test-only species, no strain"))
    db_session.flush()  # must not raise


def test_organism_kegg_code_and_biocyc_id_are_not_unique(db_session):
    """Multiple strain-specific Organism rows for one species may legitimately
    share one species-level kegg_code/biocyc_id (same rationale as
    ncbi_taxonomy_id, already permitted) -- migration 0009_persistence_hardening
    indexes both columns but does not make either unique."""
    db_session.add(
        Organism(scientific_name="test-only species C", strain="A", kegg_code="tco")
    )
    db_session.add(
        Organism(scientific_name="test-only species C", strain="B", kegg_code="tco")
    )
    db_session.add(
        Organism(scientific_name="test-only species D", strain="A", biocyc_id="TCO-D")
    )
    db_session.add(
        Organism(scientific_name="test-only species D", strain="B", biocyc_id="TCO-D")
    )
    db_session.flush()  # must not raise


def test_organism_index_hardening_present(db_session):
    """Migration 0009_persistence_hardening adds indexes on kegg_code and
    biocyc_id, matching ncbi_taxonomy_id's pre-existing index -- neither is
    unique."""
    index_names = {ix["name"] for ix in inspect(db_session.get_bind()).get_indexes("organism")}
    assert {"ix_organism_kegg_code", "ix_organism_biocyc_id"} <= index_names


# --- publication uniqueness --------------------------------------------------


def test_publication_pmid_uniqueness(db_session):
    db_session.add(Publication(title="test-only first", pmid="test-only-pmid-1"))
    db_session.flush()

    db_session.add(Publication(title="test-only second", pmid="test-only-pmid-1"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_publication_multiple_null_pmid_rows_are_allowed(db_session):
    db_session.add(Publication(title="test-only first, no pmid"))
    db_session.add(Publication(title="test-only second, no pmid"))
    db_session.flush()  # must not raise


# --- compound external identifiers: no invented uniqueness -----------------


def test_compound_external_identifiers_are_not_unique(db_session):
    """docs/02_database_schema.md does not require chebi_id uniqueness, unlike
    the identifier fields on gene or publication. Two compounds may share one."""
    db_session.add(Compound(canonical_name="test-only compound A", chebi_id="CHEBI:test-only-1"))
    db_session.add(Compound(canonical_name="test-only compound B", chebi_id="CHEBI:test-only-1"))
    db_session.flush()  # must not raise


def test_compound_pubchem_cid_and_metacyc_id_are_not_unique(db_session):
    """Migration 0009_persistence_hardening indexes these two columns (to
    support app.persistence.compound's freshness-recheck lookups) but
    deliberately does not make them unique -- same policy as chebi_id above."""
    db_session.add(Compound(canonical_name="test-only compound C", pubchem_cid="test-only-cid-1"))
    db_session.add(Compound(canonical_name="test-only compound D", pubchem_cid="test-only-cid-1"))
    db_session.add(
        Compound(canonical_name="test-only compound E", metacyc_id="test-only-metacyc-1")
    )
    db_session.add(
        Compound(canonical_name="test-only compound F", metacyc_id="test-only-metacyc-1")
    )
    db_session.flush()  # must not raise


def test_compound_index_hardening_present(db_session):
    """Migration 0009_persistence_hardening adds indexes on pubchem_cid and
    metacyc_id, matching chebi_id/kegg_compound_id/inchikey's pre-existing
    indexes -- none of the five is unique."""
    index_names = {ix["name"] for ix in inspect(db_session.get_bind()).get_indexes("compound")}
    assert {"ix_compound_pubchem_cid", "ix_compound_metacyc_id"} <= index_names


# --- compound_synonym uniqueness and cascade --------------------------------


def test_compound_synonym_uniqueness(db_session):
    compound = Compound(canonical_name="test-only compound")
    db_session.add(compound)
    db_session.flush()

    db_session.add(CompoundSynonym(compound_id=compound.id, synonym="test-only-dup"))
    db_session.flush()

    db_session.add(CompoundSynonym(compound_id=compound.id, synonym="test-only-dup"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_compound_synonym_cascade_deletes_with_compound(db_session):
    """Exercises the database-level ON DELETE CASCADE directly via a Core
    DELETE statement, bypassing the ORM relationship's own client-side
    cascade so the database constraint itself is what is being tested."""
    compound = Compound(canonical_name="test-only compound")
    db_session.add(compound)
    db_session.flush()

    synonym = CompoundSynonym(compound_id=compound.id, synonym="test-only synonym")
    db_session.add(synonym)
    db_session.flush()
    compound_id, synonym_id = compound.id, synonym.id
    db_session.expunge_all()

    db_session.execute(delete(Compound).where(Compound.id == compound_id))
    db_session.flush()

    assert db_session.get(CompoundSynonym, synonym_id) is None


# --- compartment seed data ---------------------------------------------------


def test_compartment_seed_data_exists(db_session):
    names = set(db_session.execute(select(Compartment.name)).scalars().all())
    assert names >= _STANDARD_COMPARTMENTS


def test_seeded_compartments_have_null_organism_id(db_session):
    seeded = (
        db_session.execute(
            select(Compartment).where(Compartment.name.in_(_STANDARD_COMPARTMENTS))
        )
        .scalars()
        .all()
    )
    assert len(seeded) == len(_STANDARD_COMPARTMENTS)
    assert all(c.organism_id is None for c in seeded)


# --- compartment index hardening (migration 0009) ----------------------------


def test_compartment_ontology_id_and_name_and_abbreviation_are_not_unique(db_session):
    """Reference rows (organism_id IS NULL) and organism-specific rows may
    legitimately coexist and even share an ontology_id/name/abbreviation
    (docs/07_normalization_design.md Open Questions F/G/H) -- migration
    0009_persistence_hardening adds lookup-path indexes only, never
    uniqueness, for any of the three."""
    organism = Organism(scientific_name="test-only compartment-index organism")
    db_session.add(organism)
    db_session.flush()

    db_session.add(Compartment(name="test-only cytosol A", ontology_id="GO:test-only-0001"))
    db_session.add(
        Compartment(
            organism_id=organism.id, name="test-only cytosol A", ontology_id="GO:test-only-0001"
        )
    )
    db_session.flush()  # must not raise


def test_compartment_index_hardening_present(db_session):
    """Migration 0009_persistence_hardening adds an index on ontology_id and
    two composite indexes on (organism_id, name)/(organism_id, abbreviation)
    -- Compartment previously had no index of any kind beyond its primary
    key."""
    index_names = {ix["name"] for ix in inspect(db_session.get_bind()).get_indexes("compartment")}
    assert {
        "ix_compartment_ontology_id",
        "ix_compartment_organism_id_name",
        "ix_compartment_organism_id_abbreviation",
    } <= index_names


# --- NOT NULL / nullable field behavior -------------------------------------


def test_organism_scientific_name_is_required(db_session):
    db_session.add(Organism(scientific_name=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_organism_common_name_accepts_null(db_session):
    db_session.add(Organism(scientific_name="test-only species", common_name=None))
    db_session.flush()  # must not raise


def test_compound_canonical_name_is_required(db_session):
    db_session.add(Compound(canonical_name=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_compound_formula_accepts_null(db_session):
    db_session.add(Compound(canonical_name="test-only compound", formula=None))
    db_session.flush()  # must not raise


def test_compound_synonym_synonym_is_required(db_session):
    compound = Compound(canonical_name="test-only compound")
    db_session.add(compound)
    db_session.flush()

    db_session.add(CompoundSynonym(compound_id=compound.id, synonym=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_compartment_name_is_required(db_session):
    db_session.add(Compartment(name=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_publication_title_is_required(db_session):
    db_session.add(Publication(title=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_publication_journal_accepts_null(db_session):
    db_session.add(Publication(title="test-only", journal=None))
    db_session.flush()  # must not raise


def test_experimental_condition_only_id_is_required(db_session):
    condition = ExperimentalCondition()
    db_session.add(condition)
    db_session.flush()  # must not raise
    assert condition.id is not None
