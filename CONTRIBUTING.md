# Contributing to Agent 1

Thank you for contributing to **Agent 1: Biochemical Evidence Curator**.

Agent 1 is scientific infrastructure. Its purpose is to curate biochemical knowledge with traceable provenance so that downstream modeling agents can construct mechanistic models without losing scientific rigor.

Correctness and reproducibility are more important than implementation speed.

---

# Before You Begin

Before implementing any significant change, read the relevant project specification(s):

- `docs/01_overview.md`
- `docs/02_database_schema.md`
- `docs/03_agent_behavior.md`
- `docs/04_api_spec.md`
- `docs/05_testing.md`
- `docs/06_export_format.md`

These documents are the authoritative project specifications.

If implementation and specification disagree, the specification takes precedence.

If two specifications appear to conflict, do not guess. Open an issue or document the conflict before implementing a behavioral change.

---

# Guiding Principles

Every contribution should preserve:

- scientific provenance
- uncertainty
- experimental context
- reproducibility
- deterministic validation
- auditability

When there is uncertainty, prefer conservative behavior.

Examples:

- Preserve `null` instead of inventing a value.
- Preserve conflicting evidence instead of selecting a winner.
- Create a knowledge gap instead of making an unsupported assumption.
- Require human review rather than silently accepting uncertain biology.

---

# Development Workflow

For most contributions:

1. Identify the relevant specification document(s).
2. Create a focused branch or working change.
3. Implement the smallest coherent change.
4. Add or update tests.
5. Run formatting and linting.
6. Run the relevant test suite.
7. Update documentation if behavior changed.

Avoid combining unrelated refactoring with feature work unless the refactoring is necessary.

---

# Repository Organization

Major project areas include:

```text
app/
    api/
    agents/
    config/
    connectors/
    exports/
    models/
    normalization/
    prompts/
    schemas/
    scoring/
    services/
    validation/

docs/
migrations/
prompts/
schemas/
tests/
```

Keep responsibilities separated.

Examples:

- Connectors retrieve external information.
- Normalization standardizes biological entities.
- Scoring computes deterministic confidence.
- Validation performs deterministic scientific checks.
- Export converts curated knowledge into the Agent 2 contract.

Avoid placing scientific business logic inside API route handlers.

---

# Coding Standards

Use:

- Python 3.12+
- Type hints
- SQLAlchemy 2.x
- Pydantic
- FastAPI

Prefer:

- small functions
- explicit logic
- dependency injection where appropriate
- composition over inheritance

Avoid:

- hidden global state
- unnecessary metaprogramming
- large monolithic classes
- premature optimization

---

# Database Changes

Database changes require:

- SQLAlchemy model updates
- Alembic migration
- migration tests (where appropriate)

Do not manually modify production database schemas outside Alembic.

Never change a database model without considering migration implications.

---

# Scientific Data Rules

Never invent:

- publications
- PMIDs
- DOIs
- reactions
- metabolites
- kinetic parameters
- experimental conditions

The LLM is not a scientific evidence source.

Every supported biological claim must be linked to external evidence.

Unknown information should remain unknown.

---

# Testing Expectations

Every production change should include appropriate tests.

Depending on the change, this may include:

- unit tests
- database tests
- API tests
- connector tests
- workflow tests
- scientific-integrity tests

Every bug fix should include a regression test.

---

# Running Checks

Before submitting changes, run:

```bash
ruff check .
```

Run the test suite:

```bash
pytest
```

Run scientific-integrity tests:

```bash
pytest -m scientific_integrity
```

If your changes affect database schema:

```bash
alembic upgrade head
```

and verify that migrations apply successfully to a clean database.

---

# Documentation

If implementation changes project behavior:

- update the relevant specification document,
- update API documentation if needed,
- update tests,
- update export schema if necessary.

Code should not silently diverge from documentation.

---

# Commit Guidelines

Prefer small, focused commits.

Good examples:

- Add PubMed connector retry logic
- Preserve strain metadata in kinetic measurements
- Add confidence scoring regression tests
- Validate reaction participant stoichiometry

Avoid combining unrelated features in a single commit.

---

# Pull Requests

A pull request should clearly explain:

- what changed,
- why it changed,
- which specification(s) it implements,
- whether database migrations are included,
- whether export formats changed,
- what tests were added or updated.

If a change affects scientific interpretation, explain the rationale.

---

# Scientific Integrity Checklist

Before merging, confirm:

- No provenance has been lost.
- No unsupported biological claim has been introduced.
- Experimental context is preserved.
- Conflicting evidence remains visible.
- Unknown values remain unknown.
- Human-review boundaries are preserved.
- New behavior is covered by tests.

---

# Reporting Issues

When reporting a bug, include:

- expected behavior,
- observed behavior,
- reproduction steps,
- relevant log output,
- relevant scientific context (if applicable).

If the issue concerns biological interpretation, include the relevant publication or database reference whenever possible.

---

# AI-Assisted Development

AI coding assistants are welcome for implementation tasks, but generated code must satisfy the same standards as human-written code.

In particular:

- verify generated code,
- preserve scientific provenance,
- preserve uncertainty,
- add tests,
- do not accept unsupported biological assumptions simply because they were suggested by an AI assistant.

The six specification documents remain the authoritative source of project behavior.

---

# Thank You

Agent 1 is intended to become reliable scientific infrastructure for mechanistic biological modeling.

Every contribution that improves correctness, reproducibility, provenance, testing, or scientific integrity helps move the project toward that goal.
