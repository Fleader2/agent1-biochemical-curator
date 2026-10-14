# Project/Subproject/Model/ModelSpecification Foundation (RETRACTED)

**This increment was removed from Agent 1 by the "Agent 1 cleanup increment."** A read-only,
six-repository architecture audit (see `docs/31_agent1_cleanup.md`) determined that
`Project`/`Subproject`/`Model`/`ModelSpecification`/`ModelingDecision` are persistent
user/workflow state, not biochemical curation -- Agent 1's actual chartered scope
(`docs/23_agent1_v1_scope_and_completion.md` §3). That functionality now belongs to the
separate `mechanistic-modeling-workbench` repository (as `Project`/`Subproject`/
`ModelBuildRequest`/`ModelingDecision`/`Model`, with `ModelSpecification` renamed to
`ModelBuildRequest` to avoid colliding with Agent 2's own, unrelated, identically-named
in-memory type). Migration `0020_project_subproject_model` remains in Agent 1's migration
history (a later migration drops the tables it created; the migration file itself is not
deleted), but every ORM model, enum, persistence module, and test this document describes has
been removed from the current codebase. The design rationale below is preserved as a
historical record only -- it no longer describes any current Agent 1 responsibility, table, or
code path.

---

Added in the Project/Subproject/Model/ModelSpecification foundation increment. See
`docs/02_database_schema.md` ("Table: project" through "Table: modeling_decision_option") for the
full schema, and `docs/07_normalization_design.md` ("Finalized Normalization Policy:
Project/Subproject/Model/ModelSpecification/ModelingDecision") for the identity/persistence
design.

## Purpose

This increment creates the persistent organizational structure that will support model
generation, model import, Antimony execution, experimental data, calibration, and later
modularization. It adds schema and persistence only -- no Antimony parsing/generation, no
modularization, no parameter calibration, no simulation, no experimental-data requirement
inference, and no automatic model extension. Those are separate, later increments.

## The hierarchy

```text
Project                     what biological system is being studied
   |
   +-- ProjectRegulatoryContext   (disease/tissue/cell_type, REGULATORY_NETWORK projects only)
   |
   +-- Subproject               what modeling task is being performed
           |
           +-- ModelSpecification   what the user wants built, and what constraints apply
           |       |
           |       +-- eight child tables (species/reaction/gene_protein/compartment/
           |       |   regulatory_interaction/enzyme_state/pathway) -- normalized
           |       |   constraint lists, each reusing an existing biological entity FK
           |       |
           |       +-- ModelingAssumption (existing table, reused polymorphically for
           |           free-form user assumptions/constraints)
           |
           +-- ModelingDecision      a competing-alternatives decision requiring
           |       |                 reconciliation, scoped to this subproject (or to its
           |       +-- ModelingDecisionOption   project, or to one of its models, or more
           |                                     than one of these at once)
           |
           +-- Model                 a concrete model artifact/version
                 |
                 +-- Model (parent_model_id: self-referential version lineage)
                 |
                 +-- (future) ModelNetworkRepresentation -- the executable reaction/
                     species/parameter model, attached here by a later increment
```

**Architectural boundaries, preserved exactly as specified:**

* **Project** = what biological problem is studied. Never carries model calibration data.
* **Subproject** = what modeling task is performed. A project may have many subprojects; nothing
  in this schema assumes one project has only one model-building effort.
* **ModelSpecification** = what the user wants built, and what constraints apply. Distinct from
  the model artifact itself.
* **Model** = a concrete model artifact/version. A subproject may contain multiple models; a
  later calibration or modification never overwrites a prior model -- it creates a new row,
  linked via `parent_model_id`, so model lineage is always reproducible.

**Modules are explicitly NOT part of the initial model specification.** `ModelSpecification` has
no notion of a "module" anywhere in its schema. Modular structure is a later, separate derivation
from an executable whole-network model (the future `ModelNetworkRepresentation`), and that
derivation may legitimately produce multiple alternative decompositions of the identical whole
network for different purposes -- there is no single canonical module boundary a specification
could meaningfully pin down in advance. Designing modularity into `ModelSpecification` now would
force a premature, single decomposition decision this increment has no basis for making.

## Reuse over duplication

Audited before writing any new schema (task's own instruction, section 7). Existing entities
reused directly, with no new table invented in their place:

| Need | Existing entity reused | Why not a new table |
|---|---|---|
| Species/organism context | `Organism` | Already the canonical organism record; `Project.organism_id` FKs here directly. |
| Biological context for a modeling task | `ExperimentalContext` (not `ExperimentalCondition`) | `ExperimentalContext` is the newer, general-purpose record with `organism_id`/`strain`/`genotype`; `ExperimentalCondition` is narrowly scoped to the Claim/Evidence pipeline and has no such fields. The two are deliberately distinct, pre-existing tables -- see `app/models/experimental_context.py`'s own docstring. |
| User assumptions/constraints on a specification | `ModelingAssumption` (polymorphic `subject_type="model_specification"`) | Already exactly "an assumption required for downstream model construction," with its own `confidence`/`human_approved` fields. Structurally different from the new `ModelingDecision` (one declarative assumption vs. a multi-option decision-resolution workflow), so cross-documented rather than merged. |
| PTMs/protein states | `EnzymeState` (via `model_specification_enzyme_state`) | Already models exactly this (Agent 1.x Increment B). |
| Regulatory interactions | `RegulatoryInteraction` (via `model_specification_regulatory_interaction`) | Already models exactly this. |
| Reactions/genes/proteins/compounds/compartments | `Reaction`/`Gene`/`Protein`/`Compound`/`Compartment` | Already the canonical biological entities throughout this schema. |
| Confidence on a decision option | `ConfidenceClass` + the `claim.confidence_score`-style `CHECK (0..100)` pattern | Reuses the existing enum and the existing range-check convention rather than inventing a new confidence representation. |
| Pathway references | Plain `external_pathway_id`/`name` strings (`model_specification_pathway`) | Agent 1 has no `Pathway` entity at all (the same gap `NetworkPathway` already discloses in the KEGG NETWORK increment); inventing one now would be out of scope. |
| Model-id allocation | A dedicated PostgreSQL sequence (`model_stable_identifier_seq`), mirroring `reaction_internal_id_seq` | Same proven-safe, concurrency-correct pattern already used for `Reaction.internal_id`; see `app/persistence/model_id_allocator.py`. |

No existing table was altered, redesigned, or renamed. `docs/02_database_schema.md`'s existing
sections for `Organism`/`ExperimentalContext`/`ModelingAssumption`/`EnzymeState`/
`RegulatoryInteraction`/`Reaction`/`Gene`/`Protein`/`Compound`/`Compartment` are untouched by
this increment.

## What the eight `model_specification_*` child tables cover

The task's own specification lists these as separate bullets; this schema folds each cluster
into one table with a `role` column, following `ReactionParticipant.role`'s own established
single-table-plus-role convention, rather than one table per bullet:

```text
model_specification_species             INPUT / OUTPUT / BOUNDARY / SEED
  -> covers: model input/start species, model output/end species, boundary species, seed
     metabolites

model_specification_reaction            SEED / REQUIRED / EXCLUDED
  -> covers: seed reactions, reactions that must be included, reactions that must be excluded

model_specification_gene_protein        SEED / IMPORTANT
  -> covers: seed genes, seed proteins/enzymes, genes/proteins known to be important
  -> localization_compartment_id: subcellular localization constraint on this one reference
     (distinct from "compartments to include" below)

model_specification_compartment         (no role column)
  -> covers: compartments to include

model_specification_regulatory_interaction   (no role column)
  -> covers: regulatory interactions to include

model_specification_enzyme_state        (no role column)
  -> covers: PTMs/protein states to include

model_specification_pathway             INCLUDE / EXCLUDE
  -> covers: pathway scope / pathway inclusion and exclusion
```

Every child table carries a nullable `raw_identifier` alongside its resolved entity FK(s): a
user-provided identifier that cannot yet be resolved is retained for later normalization, never
discarded (same precedent as `NetworkNode.raw_token`).

## ModelingDecision / ModelingDecisionOption

`ModelingDecision` represents one decision requiring reconciliation between competing
alternatives, scoped by any combination of `project_id`/`subproject_id`/`model_id` (all nullable
-- a decision may be project-wide, subproject-specific, model-specific, or more than one at
once). `ModelingDecisionOption` is one candidate option, carrying `evidence_summary`,
`confidence_score`/`confidence_class` (reusing `ConfidenceClass`), `consequence_summary`, and a
`recommended` flag.

`ModelingDecision.selected_option_id` is a genuine forward reference: it must point at a row in
`ModelingDecisionOption`, a table that does not exist yet when `modeling_decision` itself is
created. The migration resolves this with a two-phase approach -- `modeling_decision` is created
first with `selected_option_id` as a plain nullable column with no foreign key, then
`modeling_decision_option` is created with its own (ordinary) foreign key back to
`modeling_decision.id`, and only then is the `selected_option_id -> modeling_decision_option.id`
foreign key added via a separate `ALTER TABLE ... ADD CONSTRAINT`
(`op.create_foreign_key`). This gives real referential-integrity enforcement -- unlike the
"polymorphic reference with no foreign key, deferred to the validation layer" pattern used
elsewhere in this schema for `claim.subject_id`/`modeling_assumption.subject_id` -- at a small,
one-time migration-ordering cost.

This increment persists decisions and options and lets a decision be marked `RESOLVED` with a
selected option (`app.persistence.modeling_decision.resolve_modeling_decision`), which validates
that the selected option actually belongs to the decision being resolved. **No UI for resolving
competing alternatives is implemented by this increment** -- that is explicitly deferred, per the
task's own instructions.

## What is deliberately not here yet

* No executable reaction/species/parameter model representation. `Model.executable_format` is a
  plain, open `VARCHAR` (currently always `"ANTIMONY"` in practice, but never a closed enum --
  the same "never restrict the database so tightly that future values cannot be added" policy
  already applied to `kinetic_measurement.parameter_type`). The schema is designed so a future
  `ModelNetworkRepresentation` table can attach to `Model` without altering `Model`'s own shape.
* No Antimony parsing or generation.
* No model modularization.
* No parameter calibration or simulation.
* No automatic experimental-data requirement inference.
* No automatic model extension (though `SubprojectTaskType.EXTEND_MODEL` is reserved now, per
  the task's own instruction, for when that increment lands).

## Architectural questions / conflicts discovered during this increment

* `ExperimentalCondition` vs. `ExperimentalContext` are two pre-existing, deliberately distinct
  tables with similar-sounding names. `Subproject.experimental_context_id` intentionally FKs to
  `ExperimentalContext` (the newer, more general record); this is documented here and in
  `docs/02_database_schema.md`'s own `subproject` section specifically so a future reader does
  not "fix" this into the wrong table.
* `ModelingAssumption` (existing, single declarative assumption) and the new `ModelingDecision`/
  `ModelingDecisionOption` (multi-option decision-resolution workflow) are related but
  structurally different concepts that could be superficially confused; both are referenced from
  `ModelSpecification`'s own context, and are cross-documented here to keep the distinction clear
  for future increments (e.g. a later increment formalizing automatic decision generation from
  detected ambiguities should not try to unify the two tables).
* No other naming conflicts or would-be duplications were found during the audit (see the reuse
  table above); no existing entity needed redesigning for this increment.
