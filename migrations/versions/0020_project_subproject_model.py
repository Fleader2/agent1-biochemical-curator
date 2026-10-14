"""Project/Subproject/Model/ModelSpecification foundation increment.

Creates fourteen new tables (``project``, ``project_regulatory_context``,
``subproject``, ``model``, ``model_specification`` and its eight child
tables ``model_specification_species``, ``model_specification_reaction``,
``model_specification_gene_protein``, ``model_specification_compartment``,
``model_specification_regulatory_interaction``,
``model_specification_enzyme_state``, ``model_specification_pathway``, and
``modeling_decision``/``modeling_decision_option``), eleven new native enum
types, and one new PostgreSQL sequence (``model_stable_identifier_seq``,
backing ``app.persistence.model_id_allocator``). No existing table or
column is altered.

See ``docs/02_database_schema.md`` ("Table: project" through "Table:
modeling_decision_option") and
``docs/30_project_subproject_model_foundation.md`` for the full design
rationale. This migration adds schema only -- no Antimony/model-network
representation, no modularization, no calibration logic.

``modeling_decision.selected_option_id`` is a genuine forward reference to
``modeling_decision_option``, a table that does not exist yet when
``modeling_decision`` is created. It is created here as a plain nullable
column with no inline foreign key, and the foreign key constraint is added
via a separate ``op.create_foreign_key`` call once
``modeling_decision_option`` exists (see ``upgrade()``'s own comment at
that call).

**Amended by the Agent 1 cleanup increment** (migration
``0021_agent1_cleanup``, which drops everything this migration creates):
the ``ModelingDecisionResolutionSource``/``ModelingDecisionStatus``/
``ModelSourceType``/``ModelSpecificationGeneProteinRole``/
``ModelSpecificationPathwayRole``/``ModelSpecificationReactionRole``/
``ModelSpecificationSpeciesRole``/``ModelStatus``/``ProjectLifecycleStatus``/
``ProjectType``/``SubprojectTaskType`` Python enum classes this migration
originally imported from ``app.models.enums`` were removed from that
module by the cleanup increment (see ``docs/31_agent1_cleanup.md``).
Alembic must still be able to import *every* historical migration file to
build its revision graph, including this one, long after those classes
stopped existing in the live codebase -- the enum values below are
therefore now hard-coded tuples instead of an import from application
code, with no change whatsoever to the SQL this migration's own
``upgrade()``/``downgrade()`` issue.

Revision ID: 0020_project_subproject_model
Revises: 0019_kegg_network
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

# Hard-coded rather than imported from app.models.enums -- see the module
# docstring's "Amended by the Agent 1 cleanup increment" note.
_PROJECT_TYPE_VALUES = ("MICROBIAL_METABOLISM", "REGULATORY_NETWORK")
_PROJECT_LIFECYCLE_STATUS_VALUES = ("ACTIVE", "COMPLETED", "ON_HOLD", "ARCHIVED")
_SUBPROJECT_TASK_TYPE_VALUES = (
    "BUILD_MODEL",
    "IMPORT_MODEL",
    "TEST_MODEL",
    "CALIBRATE_MODEL",
    "EXTEND_MODEL",
)
_MODEL_SOURCE_TYPE_VALUES = ("GENERATED", "IMPORTED", "DERIVED")
_MODEL_STATUS_VALUES = ("DRAFT", "ACTIVE", "SUPERSEDED", "ARCHIVED")
_MODEL_SPECIFICATION_SPECIES_ROLE_VALUES = ("INPUT", "OUTPUT", "BOUNDARY", "SEED")
_MODEL_SPECIFICATION_REACTION_ROLE_VALUES = ("SEED", "REQUIRED", "EXCLUDED")
_MODEL_SPECIFICATION_GENE_PROTEIN_ROLE_VALUES = ("SEED", "IMPORTANT")
_MODEL_SPECIFICATION_PATHWAY_ROLE_VALUES = ("INCLUDE", "EXCLUDE")
_MODELING_DECISION_STATUS_VALUES = ("OPEN", "RESOLVED", "DEFERRED")
_MODELING_DECISION_RESOLUTION_SOURCE_VALUES = ("USER", "AGENT", "RULE")

revision: str = "0020_project_subproject_model"
down_revision: str | None = "0019_kegg_network"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEQUENCE_NAME = "model_stable_identifier_seq"

_project_type_enum = postgresql.ENUM(*_PROJECT_TYPE_VALUES, name="project_type")
_project_lifecycle_status_enum = postgresql.ENUM(
    *_PROJECT_LIFECYCLE_STATUS_VALUES, name="project_lifecycle_status"
)
_subproject_task_type_enum = postgresql.ENUM(
    *_SUBPROJECT_TASK_TYPE_VALUES, name="subproject_task_type"
)
_model_source_type_enum = postgresql.ENUM(*_MODEL_SOURCE_TYPE_VALUES, name="model_source_type")
_model_status_enum = postgresql.ENUM(*_MODEL_STATUS_VALUES, name="model_status")
_model_specification_species_role_enum = postgresql.ENUM(
    *_MODEL_SPECIFICATION_SPECIES_ROLE_VALUES, name="model_specification_species_role"
)
_model_specification_reaction_role_enum = postgresql.ENUM(
    *_MODEL_SPECIFICATION_REACTION_ROLE_VALUES, name="model_specification_reaction_role"
)
_model_specification_gene_protein_role_enum = postgresql.ENUM(
    *_MODEL_SPECIFICATION_GENE_PROTEIN_ROLE_VALUES,
    name="model_specification_gene_protein_role",
)
_model_specification_pathway_role_enum = postgresql.ENUM(
    *_MODEL_SPECIFICATION_PATHWAY_ROLE_VALUES, name="model_specification_pathway_role"
)
_modeling_decision_status_enum = postgresql.ENUM(
    *_MODELING_DECISION_STATUS_VALUES, name="modeling_decision_status"
)
_modeling_decision_resolution_source_enum = postgresql.ENUM(
    *_MODELING_DECISION_RESOLUTION_SOURCE_VALUES,
    name="modeling_decision_resolution_source",
)


def upgrade() -> None:
    bind = op.get_bind()
    _project_type_enum.create(bind, checkfirst=True)
    _project_lifecycle_status_enum.create(bind, checkfirst=True)
    _subproject_task_type_enum.create(bind, checkfirst=True)
    _model_source_type_enum.create(bind, checkfirst=True)
    _model_status_enum.create(bind, checkfirst=True)
    _model_specification_species_role_enum.create(bind, checkfirst=True)
    _model_specification_reaction_role_enum.create(bind, checkfirst=True)
    _model_specification_gene_protein_role_enum.create(bind, checkfirst=True)
    _model_specification_pathway_role_enum.create(bind, checkfirst=True)
    _modeling_decision_status_enum.create(bind, checkfirst=True)
    _modeling_decision_resolution_source_enum.create(bind, checkfirst=True)

    op.execute(f"CREATE SEQUENCE {_SEQUENCE_NAME}")

    op.create_table(
        "project",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_identifier", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "project_type",
            postgresql.ENUM(*_PROJECT_TYPE_VALUES, name="project_type", create_type=False),
            nullable=False,
        ),
        sa.Column("organism_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strain_genotype_notes", sa.Text(), nullable=True),
        sa.Column("biological_process", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *_PROJECT_LIFECYCLE_STATUS_VALUES,
                name="project_lifecycle_status",
                create_type=False,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project"),
        sa.ForeignKeyConstraint(
            ["organism_id"],
            ["organism.id"],
            name="fk_project_organism_id_organism",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("stable_identifier", name="uq_project_stable_identifier"),
    )
    op.create_index("ix_project_organism_id", "project", ["organism_id"])

    op.create_table(
        "project_regulatory_context",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disease", sa.String(), nullable=True),
        sa.Column("tissue", sa.String(), nullable=True),
        sa.Column("cell_type", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_regulatory_context"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_project_regulatory_context_project_id_project",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_project_regulatory_context_project_id",
        "project_regulatory_context",
        ["project_id"],
        unique=True,
    )

    op.create_table(
        "subproject",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column(
            "task_type",
            postgresql.ENUM(
                *_SUBPROJECT_TASK_TYPE_VALUES,
                name="subproject_task_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("experimental_context_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *_PROJECT_LIFECYCLE_STATUS_VALUES,
                name="project_lifecycle_status",
                create_type=False,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subproject"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_subproject_project_id_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experimental_context_id"],
            ["experimental_context.id"],
            name="fk_subproject_experimental_context_id_experimental_context",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("project_id", "title", name="uq_subproject_project_id_title"),
    )
    op.create_index("ix_subproject_project_id", "subproject", ["project_id"])
    op.create_index(
        "ix_subproject_experimental_context_id", "subproject", ["experimental_context_id"]
    )

    op.create_table(
        "model",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subproject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_model_identifier", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                *_MODEL_SOURCE_TYPE_VALUES, name="model_source_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*_MODEL_STATUS_VALUES, name="model_status", create_type=False),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("parent_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_label", sa.String(), nullable=True),
        sa.Column("executable_format", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model"),
        sa.ForeignKeyConstraint(
            ["subproject_id"],
            ["subproject.id"],
            name="fk_model_subproject_id_subproject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_model_id"],
            ["model.id"],
            name="fk_model_parent_model_id_model",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("stable_model_identifier", name="uq_model_stable_model_identifier"),
    )
    op.create_index("ix_model_subproject_id", "model", ["subproject_id"])
    op.create_index("ix_model_parent_model_id", "model", ["parent_model_id"])

    op.create_table(
        "model_specification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subproject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification"),
        sa.ForeignKeyConstraint(
            ["subproject_id"],
            ["subproject.id"],
            name="fk_model_specification_subproject_id_subproject",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_subproject_id",
        "model_specification",
        ["subproject_id"],
        unique=True,
    )

    op.create_table(
        "model_specification_species",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                *_MODEL_SPECIFICATION_SPECIES_ROLE_VALUES,
                name="model_specification_species_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("compound_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compartment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_species"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_species_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["compound_id"],
            ["compound.id"],
            name="fk_model_specification_species_compound_id_compound",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compartment_id"],
            ["compartment.id"],
            name="fk_model_specification_species_compartment_id_compartment",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_species_model_specification_id",
        "model_specification_species",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_model_specification_species_compound_id", "model_specification_species", ["compound_id"]
    )
    op.create_index(
        "ix_model_specification_species_compartment_id",
        "model_specification_species",
        ["compartment_id"],
    )

    op.create_table(
        "model_specification_reaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                *_MODEL_SPECIFICATION_REACTION_ROLE_VALUES,
                name="model_specification_reaction_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("reaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_reaction"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_reaction_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reaction_id"],
            ["reaction.id"],
            name="fk_model_specification_reaction_reaction_id_reaction",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_reaction_model_specification_id",
        "model_specification_reaction",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_model_specification_reaction_reaction_id",
        "model_specification_reaction",
        ["reaction_id"],
    )

    op.create_table(
        "model_specification_gene_protein",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                *_MODEL_SPECIFICATION_GENE_PROTEIN_ROLE_VALUES,
                name="model_specification_gene_protein_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("gene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protein_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("localization_compartment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_gene_protein"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_gene_protein_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gene_id"],
            ["gene.id"],
            name="fk_model_specification_gene_protein_gene_id_gene",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["protein_id"],
            ["protein.id"],
            name="fk_model_specification_gene_protein_protein_id_protein",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["localization_compartment_id"],
            ["compartment.id"],
            name="fk_ms_gene_protein_localization_compartment_id_compartment",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_gene_protein_model_specification_id",
        "model_specification_gene_protein",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_model_specification_gene_protein_gene_id",
        "model_specification_gene_protein",
        ["gene_id"],
    )
    op.create_index(
        "ix_model_specification_gene_protein_protein_id",
        "model_specification_gene_protein",
        ["protein_id"],
    )
    op.create_index(
        "ix_model_specification_gene_protein_localization_compartment_id",
        "model_specification_gene_protein",
        ["localization_compartment_id"],
    )

    op.create_table(
        "model_specification_compartment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compartment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_compartment"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_compartment_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["compartment_id"],
            ["compartment.id"],
            name="fk_model_specification_compartment_compartment_id_compartment",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_compartment_model_specification_id",
        "model_specification_compartment",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_model_specification_compartment_compartment_id",
        "model_specification_compartment",
        ["compartment_id"],
    )

    op.create_table(
        "model_specification_regulatory_interaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("regulatory_interaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_regulatory_interaction"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_reg_interaction_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["regulatory_interaction_id"],
            ["regulatory_interaction.id"],
            name="fk_ms_reg_interaction_regulatory_interaction_id_reg_interaction",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_ms_reg_interaction_model_specification_id",
        "model_specification_regulatory_interaction",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_ms_reg_interaction_regulatory_interaction_id",
        "model_specification_regulatory_interaction",
        ["regulatory_interaction_id"],
    )

    op.create_table(
        "model_specification_enzyme_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enzyme_state_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_identifier", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_enzyme_state"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_enzyme_state_model_specification_id_ms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["enzyme_state_id"],
            ["enzyme_state.id"],
            name="fk_ms_enzyme_state_enzyme_state_id_enzyme_state",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_specification_enzyme_state_model_specification_id",
        "model_specification_enzyme_state",
        ["model_specification_id"],
    )
    op.create_index(
        "ix_model_specification_enzyme_state_enzyme_state_id",
        "model_specification_enzyme_state",
        ["enzyme_state_id"],
    )

    op.create_table(
        "model_specification_pathway",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_specification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                *_MODEL_SPECIFICATION_PATHWAY_ROLE_VALUES,
                name="model_specification_pathway_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("external_pathway_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_specification_pathway"),
        sa.ForeignKeyConstraint(
            ["model_specification_id"],
            ["model_specification.id"],
            name="fk_ms_pathway_model_specification_id_ms",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_model_specification_pathway_model_specification_id",
        "model_specification_pathway",
        ["model_specification_id"],
    )

    # modeling_decision.selected_option_id is created here as a plain nullable
    # column with no foreign key -- modeling_decision_option does not exist
    # yet. The constraint is added below, after that table is created (see
    # module docstring).
    op.create_table(
        "modeling_decision",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subproject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("decision_type", sa.String(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *_MODELING_DECISION_STATUS_VALUES,
                name="modeling_decision_status",
                create_type=False,
            ),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("selected_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "resolution_source",
            postgresql.ENUM(
                *_MODELING_DECISION_RESOLUTION_SOURCE_VALUES,
                name="modeling_decision_resolution_source",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_modeling_decision"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_modeling_decision_project_id_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subproject_id"],
            ["subproject.id"],
            name="fk_modeling_decision_subproject_id_subproject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["model.id"],
            name="fk_modeling_decision_model_id_model",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_modeling_decision_project_id", "modeling_decision", ["project_id"])
    op.create_index("ix_modeling_decision_subproject_id", "modeling_decision", ["subproject_id"])
    op.create_index("ix_modeling_decision_model_id", "modeling_decision", ["model_id"])
    op.create_index(
        "ix_modeling_decision_selected_option_id", "modeling_decision", ["selected_option_id"]
    )

    op.create_table(
        "modeling_decision_option",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(), nullable=True),
        sa.Column(
            "confidence_class",
            postgresql.ENUM(name="confidence_class", create_type=False),
            nullable=True,
        ),
        sa.Column("consequence_summary", sa.Text(), nullable=True),
        sa.Column("recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_modeling_decision_option"),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["modeling_decision.id"],
            name="fk_modeling_decision_option_decision_id_modeling_decision",
            ondelete="CASCADE",
        ),
        # conv() marks the name as already final -- see 0006_kinetic_measurement.py's
        # identical note for why this is required to avoid the "ck" naming
        # convention mangling an already-explicit name.
        sa.CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name=conv("ck_modeling_decision_option_confidence_score_range"),
        ),
    )
    op.create_index(
        "ix_modeling_decision_option_decision_id", "modeling_decision_option", ["decision_id"]
    )

    # The deferred forward-reference FK -- see module docstring.
    op.create_foreign_key(
        "fk_modeling_decision_selected_option_id_mdec_opt",
        "modeling_decision",
        "modeling_decision_option",
        ["selected_option_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_modeling_decision_selected_option_id_mdec_opt",
        "modeling_decision",
        type_="foreignkey",
    )

    op.drop_index("ix_modeling_decision_option_decision_id", table_name="modeling_decision_option")
    op.drop_table("modeling_decision_option")

    op.drop_index("ix_modeling_decision_selected_option_id", table_name="modeling_decision")
    op.drop_index("ix_modeling_decision_model_id", table_name="modeling_decision")
    op.drop_index("ix_modeling_decision_subproject_id", table_name="modeling_decision")
    op.drop_index("ix_modeling_decision_project_id", table_name="modeling_decision")
    op.drop_table("modeling_decision")

    op.drop_index(
        "ix_model_specification_pathway_model_specification_id",
        table_name="model_specification_pathway",
    )
    op.drop_table("model_specification_pathway")

    op.drop_index(
        "ix_model_specification_enzyme_state_enzyme_state_id",
        table_name="model_specification_enzyme_state",
    )
    op.drop_index(
        "ix_model_specification_enzyme_state_model_specification_id",
        table_name="model_specification_enzyme_state",
    )
    op.drop_table("model_specification_enzyme_state")

    op.drop_index(
        "ix_ms_reg_interaction_regulatory_interaction_id",
        table_name="model_specification_regulatory_interaction",
    )
    op.drop_index(
        "ix_ms_reg_interaction_model_specification_id",
        table_name="model_specification_regulatory_interaction",
    )
    op.drop_table("model_specification_regulatory_interaction")

    op.drop_index(
        "ix_model_specification_compartment_compartment_id",
        table_name="model_specification_compartment",
    )
    op.drop_index(
        "ix_model_specification_compartment_model_specification_id",
        table_name="model_specification_compartment",
    )
    op.drop_table("model_specification_compartment")

    op.drop_index(
        "ix_model_specification_gene_protein_localization_compartment_id",
        table_name="model_specification_gene_protein",
    )
    op.drop_index(
        "ix_model_specification_gene_protein_protein_id",
        table_name="model_specification_gene_protein",
    )
    op.drop_index(
        "ix_model_specification_gene_protein_gene_id",
        table_name="model_specification_gene_protein",
    )
    op.drop_index(
        "ix_model_specification_gene_protein_model_specification_id",
        table_name="model_specification_gene_protein",
    )
    op.drop_table("model_specification_gene_protein")

    op.drop_index(
        "ix_model_specification_reaction_reaction_id", table_name="model_specification_reaction"
    )
    op.drop_index(
        "ix_model_specification_reaction_model_specification_id",
        table_name="model_specification_reaction",
    )
    op.drop_table("model_specification_reaction")

    op.drop_index(
        "ix_model_specification_species_compartment_id", table_name="model_specification_species"
    )
    op.drop_index(
        "ix_model_specification_species_compound_id", table_name="model_specification_species"
    )
    op.drop_index(
        "ix_model_specification_species_model_specification_id",
        table_name="model_specification_species",
    )
    op.drop_table("model_specification_species")

    op.drop_index("ix_model_specification_subproject_id", table_name="model_specification")
    op.drop_table("model_specification")

    op.drop_index("ix_model_parent_model_id", table_name="model")
    op.drop_index("ix_model_subproject_id", table_name="model")
    op.drop_table("model")

    op.drop_index("ix_subproject_experimental_context_id", table_name="subproject")
    op.drop_index("ix_subproject_project_id", table_name="subproject")
    op.drop_table("subproject")

    op.drop_index(
        "ix_project_regulatory_context_project_id", table_name="project_regulatory_context"
    )
    op.drop_table("project_regulatory_context")

    op.drop_index("ix_project_organism_id", table_name="project")
    op.drop_table("project")

    op.execute(f"DROP SEQUENCE {_SEQUENCE_NAME}")

    bind = op.get_bind()
    _modeling_decision_resolution_source_enum.drop(bind, checkfirst=True)
    _modeling_decision_status_enum.drop(bind, checkfirst=True)
    _model_specification_pathway_role_enum.drop(bind, checkfirst=True)
    _model_specification_gene_protein_role_enum.drop(bind, checkfirst=True)
    _model_specification_reaction_role_enum.drop(bind, checkfirst=True)
    _model_specification_species_role_enum.drop(bind, checkfirst=True)
    _model_status_enum.drop(bind, checkfirst=True)
    _model_source_type_enum.drop(bind, checkfirst=True)
    _subproject_task_type_enum.drop(bind, checkfirst=True)
    _project_lifecycle_status_enum.drop(bind, checkfirst=True)
    _project_type_enum.drop(bind, checkfirst=True)
