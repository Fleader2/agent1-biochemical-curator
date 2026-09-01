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

The planned workflow is:

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

## Phase 1

Project infrastructure

- configuration
- logging
- database
- migrations
- API skeleton

## Phase 2

Scientific connectors

- PubMed
- KEGG
- BRENDA
- SGD
- BioCyc

## Phase 3

Entity normalization

## Phase 4

Evidence extraction

## Phase 5

Claim generation

## Phase 6

Confidence scoring

## Phase 7

Scientific validation

## Phase 8

Knowledge-gap generation

## Phase 9

Export generation

## Phase 10

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

**Status:** Early development

The software is under active design and implementation.

The primary objective of Version 0.1 is to establish a robust scientific curation framework that can safely support downstream mechanistic model construction.

Future versions will expand organism coverage, biological scope, and downstream integrations while preserving the project's core principles of provenance, reproducibility, and scientific rigor.
