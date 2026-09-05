"""Shared row factories for ``tests/persistence``.

Mirrors the plain-function factory pattern already used by
``tests/database/test_group_c_models.py`` -- these are not fixtures, just
helpers that create and flush a minimal valid row directly against
``db_session``, for use as "an existing row already in the database" setup
in persistence tests. None of them goes through ``app.persistence`` itself.
"""

from __future__ import annotations

import itertools
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.gene import Gene
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme

_internal_id_counter = itertools.count()


def make_internal_id() -> str:
    """A unique, test-only ``Reaction.internal_id`` value."""
    return f"TEST_PERSIST_R{next(_internal_id_counter):05d}"


def make_organism(session: Session, *, suffix: str | None = None) -> Organism:
    organism = Organism(scientific_name=f"Test organism {suffix or uuid4().hex[:8]}")
    session.add(organism)
    session.flush()
    return organism


def make_gene(session: Session, *, organism_id, suffix: str | None = None) -> Gene:
    gene = Gene(organism_id=organism_id, symbol=f"g{suffix or uuid4().hex[:8]}")
    session.add(gene)
    session.flush()
    return gene


def make_protein(session: Session, *, organism_id, suffix: str | None = None) -> Protein:
    protein = Protein(organism_id=organism_id, name=f"Test protein {suffix or uuid4().hex[:8]}")
    session.add(protein)
    session.flush()
    return protein


def make_compound(session: Session, *, suffix: str | None = None) -> Compound:
    compound = Compound(canonical_name=f"test-only compound {suffix or uuid4().hex[:8]}")
    session.add(compound)
    session.flush()
    return compound


def make_compartment(
    session: Session, *, organism_id=None, suffix: str | None = None
) -> Compartment:
    compartment = Compartment(
        organism_id=organism_id, name=f"test-only compartment {suffix or uuid4().hex[:8]}"
    )
    session.add(compartment)
    session.flush()
    return compartment


def make_reaction(session: Session, *, organism_id=None, suffix: str | None = None) -> Reaction:
    reaction = Reaction(
        internal_id=make_internal_id(),
        name=f"test-only reaction {suffix or uuid4().hex[:8]}",
        organism_id=organism_id,
    )
    session.add(reaction)
    session.flush()
    return reaction


def make_reaction_enzyme(
    session: Session, *, reaction_id, protein_id=None, complex_id=None, relationship="CATALYZES"
) -> ReactionEnzyme:
    row = ReactionEnzyme(
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        relationship=relationship,
    )
    session.add(row)
    session.flush()
    return row
