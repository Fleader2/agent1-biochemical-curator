# Agent 1 Cleanup Increment

## 1. Purpose

This document records the removal of the `Project`/`Subproject`/`Model`/
`ModelSpecification`/`ModelingDecision` foundation (migration
`0020_project_subproject_model`, added by the increment documented in
`docs/30_project_subproject_model_foundation.md`) from Agent 1. It
restores Agent 1 to the scope frozen by
`docs/23_agent1_v1_scope_and_completion.md` §3:

> Agent 1 owns biological knowledge acquisition, normalization,
> curation, evidence/provenance, confidence, knowledge gaps, and related
> biochemical context — not user project/workflow state.

## 2. Why this was removed

A read-only, six-repository architecture audit (covering Agent 1 and the
then-new `mechanistic-modeling-workbench` repository, Agents 2–5, and the
`five-agent-integration-harness`) found that `Project`, `Subproject`,
`Model`, `ModelSpecification`, and `ModelingDecision` are persistent
user/project/workflow state, not biochemical curation:

- No Agent 1 public contract (`app.agent1.export`,
  `Agent1KnowledgePackage`, `Agent1CuratedKnowledgeView`) ever exposed
  these tables. `AGENT1_CONTRACT_VERSION` was never bumped for this
  increment, confirming they were never load-bearing for any downstream
  consumer.
- Agents 2–5 and the integration harness are stateless — none of them
  read or wrote these tables.
- Agent 2 has its own, unrelated, in-memory type also named
  `ModelSpecification` (the complete assembled executable model — the
  opposite meaning from Agent 1's "what the user wants built"), a
  genuine naming collision that this functionality's continued presence
  in Agent 1 risked compounding.
- This is exactly the kind of persistent, user-facing project/workflow
  state that belongs to `mechanistic-modeling-workbench`, not to a
  biochemical-curation repository. That repository already implements
  the equivalent concepts as `Project`/`Subproject`/`ModelBuildRequest`/
  `ModelingDecision`/`Model`, with `ModelSpecification` deliberately
  renamed to `ModelBuildRequest` there to avoid colliding with Agent 2's
  own type of the same name.

## 3. What was removed

### 3.1 ORM models (`app/models/`)

- `project.py` — `Project`, `ProjectRegulatoryContext`
- `subproject.py` — `Subproject`
- `model.py` — `Model`
- `model_specification.py` — `ModelSpecification` and its 7 child tables
- `modeling_decision.py` — `ModelingDecision`, `ModelingDecisionOption`

### 3.2 Persistence modules (`app/persistence/`)

`project.py`, `project_types.py`, `subproject.py`, `subproject_types.py`,
`model.py`, `model_types.py`, `model_specification.py`,
`model_specification_types.py`, `modeling_decision.py`,
`modeling_decision_types.py`, `model_id_allocator.py` (the
`model_stable_identifier_seq` allocator).

### 3.3 Enums (`app/models/enums.py`)

`ProjectType`, `ProjectLifecycleStatus`, `SubprojectTaskType`,
`ModelSourceType`, `ModelStatus`, `ModelSpecificationSpeciesRole`,
`ModelSpecificationReactionRole`, `ModelSpecificationGeneProteinRole`,
`ModelSpecificationPathwayRole`, `ModelingDecisionStatus`,
`ModelingDecisionResolutionSource` — 11 enums.

### 3.4 Database objects (dropped by migration `0021_agent1_cleanup`)

Tables, in dependency order: `modeling_decision_option`,
`modeling_decision`, the 8 `model_specification_*` child tables,
`model_specification`, `model`, `subproject`, `project_regulatory_context`,
`project`. Sequence: `model_stable_identifier_seq`. Enum types: the 11
listed in §3.3 (Postgres `ENUM` types, not just the Python classes).

### 3.5 Tests

- `tests/database/test_group_k_models.py` (21 tests)
- `tests/persistence/test_project.py`, `test_subproject.py`,
  `test_model.py`, `test_model_specification.py`,
  `test_modeling_decision.py` (42 tests)
- `tests/persistence/conftest.py`'s `make_experimental_context`/
  `make_regulatory_interaction` factories, which existed only to support
  the deleted `ModelSpecification`/`ModelingDecision` tests.
- `tests/unit/test_model_enums.py` reverted to its pre-`0020` 20-enum
  expectation set.

### 3.6 Documentation

- `docs/02_database_schema.md` — removed the 14 `# Table:` sections and
  11 `## EnumName` sections these entities added, plus the corresponding
  entries in `# Relationships`, `# Required Indexes`, and
  `# Delete Behavior`.
- `docs/07_normalization_design.md` — removed the "Finalized
  Normalization Policy: Project/Subproject/Model/ModelSpecification/
  ModelingDecision" section.
- `docs/30_project_subproject_model_foundation.md` — **not deleted**;
  marked `(RETRACTED)` with a banner pointing here, per this project's
  convention of preserving historical design records rather than
  erasing them (`docs/23`'s own stated policy for superseded increment
  contracts).

## 4. What was explicitly preserved

Nothing about Agent 1's actual biochemical-curation scope changed. All
of the following remain intact, with ORM classes importable from
`app.models` and all associated tests passing:

`Organism`, `Gene`, `Protein`, `Compound`, `Compartment`, `Reaction`/
`ReactionEnzyme`, `RegulatoryInteraction`, `EnzymeState` (and
`EnzymeModification`/`AllostericInteraction`/`EnzymeStateTransition`),
`ExperimentalContext` (and `Perturbation`/`QuantitativeObservation`/
`QuantitativeObservationDependency`), `ModelingAssumption`, `Claim` and
its evidence/provenance chain, `KnowledgeGap`,
`ExperimentRecommendationRecord`/`ExperimentRecommendationEvent`, and
KEGG NETWORK support (`Network`, `NetworkNode`, `NetworkEdge`,
`NetworkPerturbation`, `NetworkPathway`, `NetworkDisease`,
`NetworkPublication`). No Agent 1 contract, normalization, or
persistence behavior unrelated to `0020` was touched.

## 5. Migration

Migration `0020_project_subproject_model` is **not deleted** — it
remains in the migration history as the historical record of what was
built. A new forward migration, `0021_agent1_cleanup`
(`down_revision = "0020_project_subproject_model"`), drops everything
`0020` created. Its `upgrade()` is an exact copy of `0020`'s own
`downgrade()`; its `downgrade()` is an exact copy of `0020`'s own
`upgrade()`, fully recreating the schema if reversed — consistent with
`docs/02_database_schema.md`'s migration-reversibility policy
("Migrations must be reversible whenever practical").

Before writing `0021`, the migration graph, every later migration, and
every current Agent 1 contract were checked for any dependency on an
`0020`-owned object; none was found (§2, §4).

**Migration-chain fix (discovered during this increment):** Alembic
imports every historical migration file in `migrations/versions/` to
build its revision graph on every invocation, not just the files being
newly applied. `0020`'s own top-level
`from app.models.enums import (...)` of the 11 enums removed in §3.3
therefore broke `alembic current`/`upgrade`/`downgrade` entirely with an
`ImportError`, once those enum classes no longer existed in
`app/models/enums.py`. `0020` was amended in place (same revision id, no
SQL behavior change) to use hard-coded enum-value tuples instead of
importing from `app.models.enums`; `0021` uses the same pattern. This is
a general rule for this codebase: a migration file must never import
model/enum classes that a later migration might delete, since the file
must remain importable forever.

## 6. Verification performed

- `alembic upgrade head` from `0020` to `0021` succeeds; `alembic
  downgrade` back to `0020` fully recreates the dropped schema;
  re-upgrading to `0021` again succeeds; a full `downgrade base` →
  `upgrade head` replay of the entire migration chain succeeds (the same
  mechanism the test suite's per-session database reset relies on).
- `alembic-utils`/SQLAlchemy `compare_metadata` shows no new schema
  drift beyond the pre-existing, unrelated baseline drift already
  present before this increment.
- Direct SQL confirms zero leftover `0020`-owned tables, enum types, or
  the sequence after `0021` is applied.
- Full test suite, plus the targeted categories (`tests/database/`,
  `tests/normalization/`, `tests/persistence/`, `tests/agent1/`,
  `tests/unit/`) all pass.
- `ruff check` / `ruff format --check` pass on every file touched or
  created by this increment.

## 7. Ownership boundary going forward

Persistent, user-facing project/workflow state — projects, subprojects,
model build requests, modeling decisions, run history — is owned by
`mechanistic-modeling-workbench`, a separate repository. Agent 1 does
not implement, and must not reintroduce, any of it. See the README's
"Multi-Agent Architecture" section for the one-line pointer.
