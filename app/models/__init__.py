"""SQLAlchemy ORM models.

Group A reference-data tables (``docs/02_database_schema.md``) are defined
here. Submodules are imported so that ``Base.metadata`` is fully populated as
soon as ``app.models`` is imported, which is what ``migrations/env.py`` relies
on for Alembic to discover table metadata. Remaining tables are added in later
phases.
"""

from app.db.base import Base
from app.models.compartment import Compartment
from app.models.compound import Compound, CompoundSynonym
from app.models.experimental_condition import ExperimentalCondition
from app.models.organism import Organism
from app.models.publication import Publication

__all__ = [
    "Base",
    "Compartment",
    "Compound",
    "CompoundSynonym",
    "ExperimentalCondition",
    "Organism",
    "Publication",
]
