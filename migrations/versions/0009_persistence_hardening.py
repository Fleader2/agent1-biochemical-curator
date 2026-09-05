"""Persistence schema hardening and Reaction internal_id allocation.

Resolves the concrete persistence/concurrency gaps recorded in
``docs/07_normalization_design.md`` (Open Questions A, E, H, N, Q) and
``docs/08_normalization_persistence.md`` after Increment 10's persistence
layer landed. See ``docs/08_normalization_persistence.md``'s "Increment 11"
section for the finalized rationale behind each change below; this
docstring summarizes the mechanics.

**1. ``reaction_internal_id_seq`` (new PostgreSQL sequence).** Backs
``app.persistence.reaction.allocate_reaction_internal_id``, the new
production-safe, concurrency-safe allocator for ``Reaction.internal_id``
(format ``FFA_R####``, e.g. ``FFA_R0001``). A bare ``CREATE SEQUENCE`` is
used rather than an application-level ``MAX + 1`` query or counter:
``nextval()`` is atomic and safe under arbitrarily many concurrent
PostgreSQL sessions by construction, with no row locking and no
serialization of concurrent inserts. Immediately after creation, the
sequence is advanced past the highest existing ``FFA_R####``-formatted
``internal_id`` already present (a one-time ``setval()``, executed once
under this migration, not a per-insert allocator -- this is the standard,
safe way to adopt a sequence onto a table that may already contain
manually-assigned values; it does not reintroduce the forbidden
"``MAX + 1`` at insert time" pattern). On an empty table this is a no-op:
the sequence starts at 1, producing ``FFA_R0001`` first, exactly matching
``docs/02_database_schema.md``'s documented example numbering.

**2. ``ck_reaction_enzyme_exactly_one_target`` (new CHECK constraint).**
Promotes ``app.normalization.reaction_enzyme``'s already-finalized identity
rule ("exactly one of ``protein_id``/``complex_id``, never both, never
neither") from an application-layer invariant to a database-enforced one.
This constraint will fail loudly (not silently) if any pre-existing row in
the target database violates it -- no automatic cleanup or deduplication is
performed, since no such row is created by any seed migration and none is
expected in a project-controlled development/test database. If a real
deployment's existing data violates this constraint, this migration is
expected to fail at that `ALTER TABLE` statement, and that data must be
resolved before upgrading, not silently discarded here.

**3. Two new partial unique indexes on ``reaction_enzyme``**:
``uq_reaction_enzyme_reaction_id_protein_id`` (``WHERE protein_id IS NOT
NULL``) and ``uq_reaction_enzyme_reaction_id_complex_id`` (``WHERE
complex_id IS NOT NULL``). Enforces that a given
``(reaction_id, protein_id)``/``(reaction_id, complex_id)`` pair is
recorded at most once, regardless of ``relationship`` --
``app.normalization.reaction_enzyme`` already treats ``relationship`` as
inert metadata for identity purposes, so the pair alone is the identity key
(Open Question Q, now resolved in favor of uniqueness). Same loud-failure,
no-auto-cleanup posture as the CHECK constraint above.

**4. New non-unique indexes**, added purely to support existing
persistence freshness-recheck query paths and existing normalization
lookup methods that previously had no index backing them at all:
``organism.kegg_code``, ``organism.biocyc_id``, ``compound.pubchem_cid``,
``compound.metacyc_id``, ``compartment.ontology_id``,
``compartment(organism_id, name)``, ``compartment(organism_id,
abbreviation)``, ``reaction.metacyc_reaction_id``. **None of these is a
uniqueness constraint.** Compound's five external identifiers, Compartment
entirely, and Reaction's three external identifiers remain deliberately
non-unique in this migration -- each is a live, expected axis of
``AMBIGUOUS``/multi-row ambiguity in its normalizer today (Open Questions
E, H, N), and this migration does not resolve those open questions by
schema fiat.

**Deliberately not changed by this migration**: ``protein.uniprot_id``
remains non-unique (Open Question A is not resolved here -- see
``docs/08_normalization_persistence.md``'s Increment 11 section for why: an
existing database test, ``test_protein_uniprot_id_is_not_unique``, encodes
non-uniqueness as a deliberate, tested design decision, and no proof exists
that one UniProt accession must map to exactly one ``Protein`` row
globally). ``Gene``/``Publication`` already have DB uniqueness matching
their normalizers' identity rules and are not touched.

Revision ID: 0009_persistence_hardening
Revises: 0008_external_records_reviews
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.naming import conv

revision: str = "0009_persistence_hardening"
down_revision: str | None = "0008_external_records_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEQUENCE_NAME = "reaction_internal_id_seq"


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Reaction.internal_id allocation ---------------------------------
    op.execute(f"CREATE SEQUENCE {_SEQUENCE_NAME}")
    # A one-time adoption of the sequence onto any pre-existing FFA_R####
    # rows -- see module docstring. setval() rejects 0 as an explicit value
    # (Postgres sequences are 1-based), so an empty/no-match table is
    # handled via the is_called=false form instead of COALESCE-ing to 0.
    bind.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
                max_suffix INT;
            BEGIN
                SELECT MAX((substring(internal_id from '^FFA_R([0-9]+)$'))::int)
                INTO max_suffix
                FROM reaction
                WHERE internal_id ~ '^FFA_R[0-9]+$';

                IF max_suffix IS NULL THEN
                    PERFORM setval('{_SEQUENCE_NAME}', 1, false);
                ELSE
                    PERFORM setval('{_SEQUENCE_NAME}', max_suffix, true);
                END IF;
            END $$;
            """
        )
    )

    # --- 2 & 3. ReactionEnzyme XOR + pairwise uniqueness --------------------
    # conv() marks the name as already final: migrations/env.py sets
    # target_metadata = Base.metadata, so op.create_check_constraint applies
    # app/db/base.py's "ck" naming convention (a %(constraint_name)s
    # template) to a plain string name, doubling it into
    # ck_reaction_enzyme_ck_reaction_enzyme_exactly_one_target unless conv()
    # marks it already-final -- the exact same pitfall already documented on
    # reaction_participant's stoichiometry CheckConstraint (migration 0004).
    op.create_check_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"),
        "reaction_enzyme",
        "(protein_id IS NOT NULL AND complex_id IS NULL) "
        "OR (protein_id IS NULL AND complex_id IS NOT NULL)",
    )
    op.create_index(
        "uq_reaction_enzyme_reaction_id_protein_id",
        "reaction_enzyme",
        ["reaction_id", "protein_id"],
        unique=True,
        postgresql_where=sa.text("protein_id IS NOT NULL"),
    )
    op.create_index(
        "uq_reaction_enzyme_reaction_id_complex_id",
        "reaction_enzyme",
        ["reaction_id", "complex_id"],
        unique=True,
        postgresql_where=sa.text("complex_id IS NOT NULL"),
    )

    # --- 4. Non-unique index hardening ---------------------------------------
    op.create_index("ix_organism_kegg_code", "organism", ["kegg_code"])
    op.create_index("ix_organism_biocyc_id", "organism", ["biocyc_id"])
    op.create_index("ix_compound_pubchem_cid", "compound", ["pubchem_cid"])
    op.create_index("ix_compound_metacyc_id", "compound", ["metacyc_id"])
    op.create_index("ix_compartment_ontology_id", "compartment", ["ontology_id"])
    op.create_index("ix_compartment_organism_id_name", "compartment", ["organism_id", "name"])
    op.create_index(
        "ix_compartment_organism_id_abbreviation", "compartment", ["organism_id", "abbreviation"]
    )
    op.create_index("ix_reaction_metacyc_reaction_id", "reaction", ["metacyc_reaction_id"])


def downgrade() -> None:
    op.drop_index("ix_reaction_metacyc_reaction_id", table_name="reaction")
    op.drop_index("ix_compartment_organism_id_abbreviation", table_name="compartment")
    op.drop_index("ix_compartment_organism_id_name", table_name="compartment")
    op.drop_index("ix_compartment_ontology_id", table_name="compartment")
    op.drop_index("ix_compound_metacyc_id", table_name="compound")
    op.drop_index("ix_compound_pubchem_cid", table_name="compound")
    op.drop_index("ix_organism_biocyc_id", table_name="organism")
    op.drop_index("ix_organism_kegg_code", table_name="organism")

    op.drop_index("uq_reaction_enzyme_reaction_id_complex_id", table_name="reaction_enzyme")
    op.drop_index("uq_reaction_enzyme_reaction_id_protein_id", table_name="reaction_enzyme")
    op.drop_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"), "reaction_enzyme", type_="check"
    )

    op.execute(f"DROP SEQUENCE {_SEQUENCE_NAME}")
