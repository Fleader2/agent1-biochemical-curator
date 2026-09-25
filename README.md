# Agent 1: Biochemical Evidence Curator

## Overview

Agent 1 is the first component of a multi-agent system for constructing scientifically traceable mechanistic models of microbial metabolism.

Its purpose is **not** to build mathematical models directly.

Instead, Agent 1 collects, normalizes, evaluates, and curates biochemical knowledge from scientific literature and biological databases, producing a structured, evidence-backed representation of biology that can be safely consumed by downstream modeling agents.

Version 0.1 focuses on:

- **Organism:** *Saccharomyces cerevisiae*
- **Biological scope:** free fatty acid metabolism

The architecture is intentionally organism-independent and pathway-independent so that additional organisms and pathways can be incorporated later.

---

# Project Goals

Agent 1 is designed to:

- discover relevant scientific literature
- retrieve biological database records
- normalize biological entities
- curate biochemical reactions
- curate kinetic measurements
- curate regulatory interactions
- preserve scientific provenance
- preserve experimental context
- preserve uncertainty
- detect conflicting evidence
- identify knowledge gaps
- assign deterministic confidence scores
- produce structured exports for downstream modeling

Agent 1 intentionally does **not**:

- generate Antimony models
- generate SBML models
- perform ODE simulation
- estimate kinetic parameters automatically
- optimize metabolic networks
- perform flux balance analysis

Those responsibilities belong to downstream agents.

---

# Multi-Agent Architecture

Agent 1 is one component of a five-agent pipeline. Each agent has a fixed,
non-overlapping responsibility:

- **Agent 1 — Literature Curator** (this repository): curates biochemical
  knowledge — entities, reactions, enzymes, regulation, provenance,
  confidence, knowledge gaps, and experiment recommendations/execution —
  into a structured, evidence-backed, reviewable knowledge base.
- **Agent 2 — Antimony Builder**: turns Agent 1's curated knowledge into
  syntactically valid Antimony (reactions, parameters, compartments,
  events, rules).
- **Agent 3 — Validator**: checks a built model for mass balance, missing
  species, duplicate reactions, disconnected subnetworks, unit
  consistency, and conservation laws.
- **Agent 4 — Simulator**: runs Tellurium/COPASI simulations, parameter
  scans, sensitivity analyses, and steady-state calculations.
- **Agent 5 — Model Critic**: asks whether a model is thermodynamically
  sound, whether cofactors/regulation are missing, and whether its
  assumptions are experimentally supported.

Agent 1 v1 is complete and frozen at the boundary above — it does not
build, validate, simulate, or critique mathematical models. See
`docs/23_agent1_v1_scope_and_completion.md` for the full scope-freeze
contract.

---

# Scientific Philosophy

This project follows five guiding principles.

1. Evidence is more important than plausibility.

2. Unknown information should remain unknown.

3. Scientific provenance must never be discarded.

4. Experimental context must always be preserved.

5. Human review is required before biological knowledge becomes accepted.

The software is intentionally conservative.

It prefers:

- explicit uncertainty,
- explicit assumptions,
- explicit conflicts,

rather than unsupported biological conclusions.

---

# Project Architecture

The planned workflow is (see `docs/23_agent1_v1_scope_and_completion.md`
§6 for the actual, currently implemented pipeline, which supersedes this
original planning diagram):

```text
Scientific Sources
        │
        ▼
External Connectors
        │
        ▼
Normalization
        │
        ▼
Evidence Extraction
        │
        ▼
Claim Generation
        │
        ▼
Confidence Scoring
        │
        ▼
Scientific Validation
        │
        ▼
Critic Review
        │
        ▼
Human Review
        │
        ▼
Structured Export
        │
        ▼
Agent 2 (Antimony Model Builder)
```

---

# Repository Structure

```text
agent1/
│
├── app/
│   ├── api/
│   ├── agents/
│   ├── config/
│   ├── connectors/
│   ├── exports/
│   ├── models/
│   ├── normalization/
│   ├── prompts/
│   ├── schemas/
│   ├── scoring/
│   ├── services/
│   └── validation/
│
├── docs/
│
├── migrations/
│
├── prompts/
│
├── schemas/
│
├── tests/
│
└── .cursor/
```

---

# Specifications

The project is driven by six primary specification documents.

| Document | Purpose |
|----------|---------|
| `docs/01_overview.md` | System overview |
| `docs/02_database_schema.md` | PostgreSQL database design |
| `docs/03_agent_behavior.md` | Scientific behavior and workflow |
| `docs/04_api_spec.md` | REST API |
| `docs/05_testing.md` | Testing and scientific integrity |
| `docs/06_export_format.md` | Export contract for Agent 2 |

These documents are the authoritative project specifications.

---

# Development Principles

All implementation should preserve:

- scientific provenance
- uncertainty
- experimental context
- conflicting evidence
- deterministic validation
- reproducibility

The software must never silently:

- invent biological evidence
- invent citations
- invent kinetic parameters
- average incompatible measurements
- erase conflicting evidence
- bypass human review

---

# Technology Stack

- Python 3.12+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Pydantic
- HTTPX
- pytest
- Ruff

---

# Installation

Create a virtual environment.

```bash
python -m venv .venv
```

Activate it.

Linux/macOS

```bash
source .venv/bin/activate
```

Windows

```powershell
.venv\Scripts\activate
```

Install project dependencies. Dependencies are declared in `pyproject.toml`.

```bash
pip install -e ".[dev]"
```

Copy the example environment file.

```bash
cp .env.example .env
```

Configure:

- PostgreSQL (`DATABASE_URL`, and `TEST_DATABASE_URL` for the test suite)
- LLM provider
- PubMed credentials
- BRENDA credentials

Run database migrations.

```bash
alembic upgrade head
```

Start the API.

```bash
uvicorn app.main:app --reload
```

---

# Running Tests

Run Ruff.

```bash
ruff check .
```

Run the test suite.

```bash
pytest
```

Run only scientific-integrity tests.

```bash
pytest -m scientific_integrity
```

Run live connector tests.

```bash
pytest -m live
```

---

# Current Development Roadmap

`IMPLEMENTATION_PLAN.md` defines the detailed engineering build order (project
skeleton, database schema, individual connectors, API, testing, deployment,
and so on). The roadmap below instead describes the higher-level scientific
capability progression; each stage here may span one or more phases of the
detailed engineering plan.

**Phases 1-11 below are complete — Agent 1 v1 is done.** See
`docs/23_agent1_v1_scope_and_completion.md` for the authoritative
completion contract, the Agent 1.x backlog (additional connectors,
regulation curation, richer cofactor semantics), and the Agent 2 boundary
this roadmap's final phase hands off to.

## Phase 1

Project infrastructure

- configuration
- logging
- database
- migrations
- API skeleton

## Phase 2

Database schema

## Phase 3

Scientific connectors

- PubMed
- KEGG
- BRENDA
- SGD
- UniProt
- MetaCyc
- BioCyc

Implemented: KEGG, PubMed, SGD, BRENDA, UniProt.

MetaCyc/BioCyc are deferred, not abandoned — deferred to Agent 1.x, not a
blocker for Agent 1 v1 completion. Current BioCyc access requires a paid
subscription for both YeastCyc (*Saccharomyces cerevisiae*) and MetaCyc,
which this project does not currently have. Implementing and validating a
BioCyc/MetaCyc connector responsibly requires licensed access to verify
its actual request/response behavior; this is a licensing/access
constraint, not a technical limitation. Revisit once appropriate access is
obtained.

## Phase 4

Entity normalization

## Phase 5

Evidence extraction

## Phase 6

Claim generation

## Phase 7

Confidence scoring

## Phase 8

Scientific validation

## Phase 9

Knowledge-gap generation

## Phase 10

Export generation

## Phase 11

Integration with Agent 2

---

# Contributing

Before implementing new functionality:

1. Read the relevant specification document.
2. Implement the smallest coherent change.
3. Add or update tests.
4. Run Ruff.
5. Run the relevant test suite.
6. Preserve scientific provenance and uncertainty.

---

# License

Add the appropriate project license before public distribution.

---

# Project Status

**Status:** Agent 1 v1 complete. See `docs/23_agent1_v1_scope_and_completion.md` for the authoritative completion contract.

Agent 1 v1 establishes a robust, evidence-backed scientific curation framework that can safely support downstream mechanistic model construction — it does not itself build, validate, simulate, or critique those models (Agents 2-5).

"Complete" describes v1's scope, not a stopping point for the project: Agent 1.x may still add connectors (MetaCyc, BioCyc), a fuller regulation curation pipeline, and richer cofactor semantics, none of which were required for v1. Future work will expand organism coverage, biological scope, and downstream integrations while preserving the project's core principles of provenance, reproducibility, and scientific rigor.

**Agent 1.x Increment A** added SABIO-RK and Open Enzyme Database kinetic-data connectors and extended Agent 1's output contract with curated kinetic measurements (`AGENT1_CONTRACT_VERSION` "1.0" → "1.1"). See `docs/24_kinetic_data_curation_and_handoff.md` for the full contract; the v1 baseline above remains unchanged.

**Agent 1.x Increment B** added structured enzyme regulatory states (allostery, covalent/post-translational modification, and state-specific kinetic measurements) via `EnzymeState`/`EnzymeModification`/`AllostericInteraction`/`EnzymeStateTransition`, bumping `AGENT1_CONTRACT_VERSION` to "1.2". See `docs/25_enzyme_regulatory_states_contract.md`.

**Agent 1.x Increment C** added `app.pathway_curation`, an autonomous pathway curation planner/executor: a high-level request (an organism plus a biological process) is planned deterministically and executed within an explicit, auditable connector/iteration budget, reusing Agent 1's existing connectors, normalization, persistence, and knowledge-gap infrastructure end to end — never inventing a biological fact, and never generating Antimony/SBML/simulation output. Execution covers pathway/reaction discovery, deterministic KEGG reaction-equation parsing and reaction-participant (compound + role + stoichiometry) resolution, compartment handling via an explicit, conservative caller-asserted scope assumption (never a silent cytosol default), conservative reaction-enzyme catalyst association, and literature/kinetic-measurement enrichment (SABIO-RK and Open Enzyme Database) — producing an `Agent1CuratedKnowledgeView` plus a read-only `Agent2ReadinessAssessment` that says whether that export is structurally usable by Agent 2 without manual repair. It introduces no new `Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView` field and does not change `AGENT1_CONTRACT_VERSION`; its own policy version is tracked separately (`PATHWAY_CURATION_POLICY_VERSION`). See `docs/26_autonomous_pathway_curation_planner.md` for the full contract, including its disclosed limitations (no LLM-based evidence extraction, no enzyme-complex discovery, no regulation/enzyme-state discovery — both explicitly disclosed via a `*_REQUESTED_NOT_SUPPORTED` frontier item rather than silently attempted).

**Agent 1.x Increment C.1 (Live Pathway Discovery Repair)** fixed structural defects a real integration pilot against live KEGG/SGD/UniProt/PubMed/SABIO-RK exposed in Increment C: pathway reaction-membership discovery now uses KEGG's actual pathway↔reaction `link` operation (never a `REACTION` field that KEGG's live pathway records do not reliably carry) and discloses a valid pathway with zero linked reactions rather than finishing silently; a request may supply a structured KEGG pathway id (e.g. `sce00061`), which takes deterministic precedence over free-text pathway search; catalyst discovery is no longer gated on caller-supplied `seed_entity_texts` — it is also attempted autonomously from a resolved reaction's own EC number, still never fabricating a `ReactionEnzyme` association from EC equality alone (a real isozyme pair sharing one EC number is a case this was verified against); and `Agent2ReadinessAssessment.is_ready` can no longer be `True` for an export with zero structurally modelable reactions. An optional strain field was also added to the request. Publications discovered by this planner still are not represented in the Agent 1 → Agent 2 handoff (that remains Claims/Evidence-driven, per Increment 27) — a disclosed, deliberately deferred limitation, not solved by C.1. **A final C.1 completion** resolved a live-investigated gap in that same repair: `sce00061`-style organism-specific pathway ids link to zero reactions on KEGG (only the generic, organism-agnostic reference pathway, e.g. `map00061`, does), and naively treating the generic pathway's full reaction set as organism-specific would misattribute reactions the organism does not actually carry genes for. Reaction discovery now also tries that pathway id's own KGML pathway-diagram document first (KEGG's own curated, organism-scoped reaction membership, confirmed live across three independent organisms), falling back to the original link-based behavior only when no such document exists — with no organism-specific logic anywhere in the mechanism. See `docs/26_autonomous_pathway_curation_planner.md` §46 for the full live investigation and rejected alternatives, and `artifacts/pilots/yeast_fatty_acid_001/13_pilot_report.md` for the pilot that motivated the original repair.

**Agent 1.x Increment C.2 (Organism-Specific Catalyst Resolution)** improved autonomous catalyst discovery by exploiting the same organism-specific KGML diagrams C.1 uses for reaction membership: a `type="gene"` diagram entry directly, explicitly associates one or more organism-specific KEGG gene ids with a specific reaction (confirmed live to exist for every reaction in `sce00061`) — this evidence is now tried first, resolved through the existing Gene/Protein normalization and persistence paths with no new resolution architecture, and takes precedence over the broad EC-number search that previously had to guess among 5–18 organism-scoped UniProt candidates per enzyme family. Multiple explicitly-named genes at one entry (e.g. ACC1/HFA1) are preserved as independent catalyst candidates, never collapsed and never expanded into EC-driven ambiguity; a `type="group"` entry (KGML's separate mechanism for an explicit multi-node complex) is disclosed as unresolved catalyst context rather than guessing whether its members are independent isozymes or obligate complex subunits — this package still never constructs an `EnzymeComplex`. A real, independent defect this same investigation found — reactions with more than one EC number were being sent to UniProt as a single invalid combined query, reliably returning zero candidates — is also fixed: each EC number is now queried independently and the results merged deterministically. EC-based discovery remains as a conservative fallback for reactions with no direct gene evidence, and EC equality alone still never by itself establishes a `ReactionEnzyme`. See `docs/26_autonomous_pathway_curation_planner.md` §47 for the full contract, including its own non-goals (enzyme complexes, the reaction-identity/polymer-notation gaps §46 already disclosed, and F5).

**Agent 1.x Increment C.3 (Gene-Anchored Protein Identity Resolution)** fixed a real integration pilot's finding that C.2's own gene discovery worked perfectly (13/13 KEGG genes resolved) while protein resolution failed completely (0/13) — every gene-symbol UniProt query returned multiple candidates from cross-species leakage, unrelated same-organism genes matched by an unscoped text search, and multiple real, distinct-strain accessions for the correct gene. The fix treats database-record multiplicity as distinct from biological-identity ambiguity: once a Gene is resolved, it anchors protein resolution — UniProt's own exact numeric taxonomy filter (already implemented, never previously used here) and its own `gene_names` field classify each candidate as confirming the same gene product, positively identifying a different one, or providing insufficient evidence, before any candidate is treated as competing. Multiple confirmed records for one gene never create multiple `Protein` rows: a unique reviewed/canonical accession (when one exists) becomes the primary identifier, and every other confirmed accession is preserved as an additional cross-reference. Confirmed live: all 13 real pilot genes now resolve to exactly one confirmed, canonical protein. See `docs/26_autonomous_pathway_curation_planner.md` §48 for the full contract, including a genuine, disclosed architectural boundary (Protein creation still requires a UniProt accession; a gene confirmed via multiple equally-weighted, non-canonical records alone cannot yet create one) that this increment did not work around.
