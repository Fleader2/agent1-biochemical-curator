"""SQLAlchemy ORM models.

Group A reference-data tables, Group B gene/protein/enzyme-complex tables,
Group C reaction tables, and Group D claim/evidence tables
(``docs/02_database_schema.md``) are defined here. Submodules are imported so
that ``Base.metadata`` is fully populated as soon as ``app.models`` is
imported, which is what ``migrations/env.py`` relies on for Alembic to
discover table metadata. Remaining tables are added in later phases.
"""

from app.db.base import Base
from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.compartment import Compartment
from app.models.compound import Compound, CompoundSynonym
from app.models.enzyme_complex import EnzymeComplex, EnzymeComplexMember
from app.models.experimental_condition import ExperimentalCondition
from app.models.gene import Gene
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant

__all__ = [
    "Base",
    "Claim",
    "Compartment",
    "Compound",
    "CompoundSynonym",
    "EnzymeComplex",
    "EnzymeComplexMember",
    "Evidence",
    "EvidenceCondition",
    "ExperimentalCondition",
    "Gene",
    "Organism",
    "Protein",
    "Publication",
    "Reaction",
    "ReactionEnzyme",
    "ReactionParticipant",
]
