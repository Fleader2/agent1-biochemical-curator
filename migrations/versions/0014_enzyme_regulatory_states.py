"""Enzyme regulatory states, allostery, modification (Agent 1.x Increment B).

Creates four new tables (``enzyme_state``, ``enzyme_modification``,
``allosteric_interaction``, ``enzyme_state_transition``) and four new
native enum types (``enzyme_state_type``, ``modification_type``,
``allosteric_effect``, ``enzyme_state_transition_type``). Extends
``reaction_enzyme`` with a third, optional catalytic target
(``enzyme_state_id``) and widens its exactly-one-target ``CHECK`` from two
options to three. Extends ``kinetic_measurement`` with an optional
``enzyme_state_id`` so a measurement can be attributed to one specific
regulatory state rather than only to the parent protein/complex.

See ``docs/25_enzyme_regulatory_states_contract.md`` for the full
contract. Protein/EnzymeComplex identity is unchanged by this migration --
no existing row of any table is altered, and every pre-existing
``reaction_enzyme``/``kinetic_measurement`` row (which could only ever
reference a protein or complex) remains valid unchanged.

**``ck_reaction_enzyme_exactly_one_target`` is redefined, not weakened.**
Its old 2-way body (exactly one of ``protein_id``/``complex_id``) is
replaced with a 3-way body (exactly one of ``protein_id``/``complex_id``/
``enzyme_state_id``) under the identical constraint name -- the 2-way rule
remains a strict special case (a row naming ``enzyme_state_id`` simply
could not exist before), so no previously-valid row can violate the new
constraint.

Revision ID: 0014_enzyme_regulatory_states
Revises: 0013_kinetic_measurement_sources
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
)

revision: str = "0014_enzyme_regulatory_states"
down_revision: str | None = "0013_kinetic_measurement_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_enzyme_state_type_enum = postgresql.ENUM(
    *[member.value for member in EnzymeStateType], name="enzyme_state_type"
)
_modification_type_enum = postgresql.ENUM(
    *[member.value for member in ModificationType], name="modification_type"
)
_allosteric_effect_enum = postgresql.ENUM(
    *[member.value for member in AllostericEffect], name="allosteric_effect"
)
_enzyme_state_transition_type_enum = postgresql.ENUM(
    *[member.value for member in EnzymeStateTransitionType],
    name="enzyme_state_transition_type",
)

_OLD_REACTION_ENZYME_EXACTLY_ONE_TARGET = (
    "(protein_id IS NOT NULL AND complex_id IS NULL) "
    "OR (protein_id IS NULL AND complex_id IS NOT NULL)"
)
_NEW_REACTION_ENZYME_EXACTLY_ONE_TARGET = (
    "(CASE WHEN protein_id IS NOT NULL THEN 1 ELSE 0 END) "
    "+ (CASE WHEN complex_id IS NOT NULL THEN 1 ELSE 0 END) "
    "+ (CASE WHEN enzyme_state_id IS NOT NULL THEN 1 ELSE 0 END) = 1"
)


def upgrade() -> None:
    bind = op.get_bind()
    _enzyme_state_type_enum.create(bind, checkfirst=True)
    _modification_type_enum.create(bind, checkfirst=True)
    _allosteric_effect_enum.create(bind, checkfirst=True)
    _enzyme_state_transition_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "enzyme_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("complex_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "state_type",
            postgresql.ENUM(
                *[member.value for member in EnzymeStateType],
                name="enzyme_state_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("state_label", sa.String(), nullable=True),
        sa.Column("compartment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_state", sa.Boolean(), nullable=True),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="source_type", create_type=False),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enzyme_state"),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_enzyme_state_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complex_id"],
            ["enzyme_complex.id"],
            name="fk_enzyme_state_complex_id_enzyme_complex",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compartment_id"],
            ["compartment.id"],
            name="fk_enzyme_state_compartment_id_compartment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_enzyme_state_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_enzyme_state_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_enzyme_state_exactly_one_target"),
        "enzyme_state",
        "(protein_id IS NOT NULL AND complex_id IS NULL) "
        "OR (protein_id IS NULL AND complex_id IS NOT NULL)",
    )
    op.create_index("uq_enzyme_state_identity_key", "enzyme_state", ["identity_key"], unique=True)
    op.create_index(
        "uq_enzyme_state_source_source_id",
        "enzyme_state",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )

    op.create_table(
        "enzyme_modification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enzyme_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "modification_type",
            postgresql.ENUM(
                *[member.value for member in ModificationType],
                name="modification_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("residue", sa.String(), nullable=True),
        sa.Column("residue_position", sa.Integer(), nullable=True),
        sa.Column("site_label", sa.String(), nullable=True),
        sa.Column("modifying_compound_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stoichiometry", sa.Numeric(), nullable=True),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="source_type", create_type=False),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enzyme_modification"),
        sa.ForeignKeyConstraint(
            ["enzyme_state_id"],
            ["enzyme_state.id"],
            name="fk_enzyme_modification_enzyme_state_id_enzyme_state",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["modifying_compound_id"],
            ["compound.id"],
            name="fk_enzyme_modification_modifying_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_enzyme_modification_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_enzyme_modification_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_enzyme_modification_identity_key", "enzyme_modification", ["identity_key"], unique=True
    )
    op.create_index(
        "uq_enzyme_modification_source_source_id",
        "enzyme_modification",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )
    op.create_index(
        "ix_enzyme_modification_enzyme_state_id", "enzyme_modification", ["enzyme_state_id"]
    )

    op.create_table(
        "allosteric_interaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enzyme_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ligand_compound_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "effect",
            postgresql.ENUM(
                *[member.value for member in AllostericEffect],
                name="allosteric_effect",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("site_label", sa.String(), nullable=True),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="source_type", create_type=False),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_allosteric_interaction"),
        sa.ForeignKeyConstraint(
            ["enzyme_state_id"],
            ["enzyme_state.id"],
            name="fk_allosteric_interaction_enzyme_state_id_enzyme_state",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ligand_compound_id"],
            ["compound.id"],
            name="fk_allosteric_interaction_ligand_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_allosteric_interaction_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_allosteric_interaction_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_allosteric_interaction_identity_key",
        "allosteric_interaction",
        ["identity_key"],
        unique=True,
    )
    op.create_index(
        "uq_allosteric_interaction_source_source_id",
        "allosteric_interaction",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )
    op.create_index(
        "ix_allosteric_interaction_enzyme_state_id", "allosteric_interaction", ["enzyme_state_id"]
    )

    op.create_table(
        "enzyme_state_transition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "transition_type",
            postgresql.ENUM(
                *[member.value for member in EnzymeStateTransitionType],
                name="enzyme_state_transition_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="source_type", create_type=False),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enzyme_state_transition"),
        sa.ForeignKeyConstraint(
            ["from_state_id"],
            ["enzyme_state.id"],
            name="fk_enzyme_state_transition_from_state_id_enzyme_state",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_state_id"],
            ["enzyme_state.id"],
            name="fk_enzyme_state_transition_to_state_id_enzyme_state",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_enzyme_state_transition_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name="fk_enzyme_state_transition_publication_id_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_enzyme_state_transition_evidence_id_evidence",
            ondelete="RESTRICT",
        ),
    )
    op.create_check_constraint(
        conv("ck_enzyme_state_transition_distinct_states"),
        "enzyme_state_transition",
        "from_state_id <> to_state_id",
    )
    op.create_index(
        "uq_enzyme_state_transition_identity_key",
        "enzyme_state_transition",
        ["identity_key"],
        unique=True,
    )
    op.create_index(
        "uq_enzyme_state_transition_source_source_id",
        "enzyme_state_transition",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL AND source_id IS NOT NULL"),
    )

    # --- reaction_enzyme: add enzyme_state_id, widen exactly-one-target CHECK ---
    op.add_column(
        "reaction_enzyme",
        sa.Column("enzyme_state_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_reaction_enzyme_enzyme_state_id_enzyme_state",
        "reaction_enzyme",
        "enzyme_state",
        ["enzyme_state_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"), "reaction_enzyme", type_="check"
    )
    op.create_check_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"),
        "reaction_enzyme",
        _NEW_REACTION_ENZYME_EXACTLY_ONE_TARGET,
    )
    op.create_index(
        "uq_reaction_enzyme_reaction_id_enzyme_state_id",
        "reaction_enzyme",
        ["reaction_id", "enzyme_state_id"],
        unique=True,
        postgresql_where=sa.text("enzyme_state_id IS NOT NULL"),
    )

    # --- kinetic_measurement: add enzyme_state_id ---
    op.add_column(
        "kinetic_measurement",
        sa.Column("enzyme_state_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_kinetic_measurement_enzyme_state_id_enzyme_state",
        "kinetic_measurement",
        "enzyme_state",
        ["enzyme_state_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_kinetic_measurement_enzyme_state_id", "kinetic_measurement", ["enzyme_state_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_kinetic_measurement_enzyme_state_id", table_name="kinetic_measurement")
    op.drop_constraint(
        "fk_kinetic_measurement_enzyme_state_id_enzyme_state",
        "kinetic_measurement",
        type_="foreignkey",
    )
    op.drop_column("kinetic_measurement", "enzyme_state_id")

    op.drop_index("uq_reaction_enzyme_reaction_id_enzyme_state_id", table_name="reaction_enzyme")
    op.drop_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"), "reaction_enzyme", type_="check"
    )
    op.create_check_constraint(
        conv("ck_reaction_enzyme_exactly_one_target"),
        "reaction_enzyme",
        _OLD_REACTION_ENZYME_EXACTLY_ONE_TARGET,
    )
    op.drop_constraint(
        "fk_reaction_enzyme_enzyme_state_id_enzyme_state", "reaction_enzyme", type_="foreignkey"
    )
    op.drop_column("reaction_enzyme", "enzyme_state_id")

    op.drop_index(
        "uq_enzyme_state_transition_source_source_id", table_name="enzyme_state_transition"
    )
    op.drop_index("uq_enzyme_state_transition_identity_key", table_name="enzyme_state_transition")
    op.drop_table("enzyme_state_transition")

    op.drop_index("ix_allosteric_interaction_enzyme_state_id", table_name="allosteric_interaction")
    op.drop_index("uq_allosteric_interaction_source_source_id", table_name="allosteric_interaction")
    op.drop_index("uq_allosteric_interaction_identity_key", table_name="allosteric_interaction")
    op.drop_table("allosteric_interaction")

    op.drop_index("ix_enzyme_modification_enzyme_state_id", table_name="enzyme_modification")
    op.drop_index("uq_enzyme_modification_source_source_id", table_name="enzyme_modification")
    op.drop_index("uq_enzyme_modification_identity_key", table_name="enzyme_modification")
    op.drop_table("enzyme_modification")

    op.drop_index("uq_enzyme_state_source_source_id", table_name="enzyme_state")
    op.drop_index("uq_enzyme_state_identity_key", table_name="enzyme_state")
    op.drop_table("enzyme_state")

    _enzyme_state_transition_type_enum.drop(op.get_bind(), checkfirst=True)
    _allosteric_effect_enum.drop(op.get_bind(), checkfirst=True)
    _modification_type_enum.drop(op.get_bind(), checkfirst=True)
    _enzyme_state_type_enum.drop(op.get_bind(), checkfirst=True)
