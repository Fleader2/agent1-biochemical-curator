# Cursor Project Rules
## Agent 1: Biochemical Evidence Curator

This repository implements Agent 1, a biochemical evidence-curation system for constructing scientifically traceable inputs to downstream mechanistic metabolic models.

The authoritative specifications are located in the `docs/` directory.

Before making any code change, read and follow:

- `docs/01_overview.md`
- `docs/02_database_schema.md`
- `docs/03_agent_behavior.md`
- `docs/04_api_spec.md`
- `docs/05_testing.md`
- `docs/06_export_format.md`

If implementation convenience conflicts with these specifications, follow the specifications.

If two specification documents appear to conflict, stop the implementation of the conflicting behavior, identify the conflict clearly, and preserve the more scientifically conservative behavior until the specification is corrected.

---

# Primary Development Principle

This is scientific software.

Correctness, provenance, reproducibility, uncertainty preservation, and auditability are more important than minimizing code or completing features quickly.

Never make the software appear more complete by inventing scientific information.

Prefer:

```text
UNKNOWN
```

over unsupported conclusions.

Prefer:

```text
null
```

over invented defaults.

Prefer:

```text
CONFLICTED
```

over artificial consensus.

Prefer:

```text
NEEDS_REVIEW
```

over unsupported automatic acceptance.

---

# Scientific Integrity Rules

These rules are mandatory.

Never invent:

- PMIDs
- PMCIDs
- DOIs
- database identifiers
- publications
- genes
- proteins
- enzymes
- enzyme complexes
- metabolites
- chemical formulas
- reaction stoichiometry
- compartments
- kinetic parameters
- regulatory relationships
- experimental conditions
- organism or strain assignments

The LLM is never a scientific evidence source.

LLM output may propose:

- search queries,
- extracted candidate claims,
- classifications,
- hypotheses,
- critic findings,
- normalization suggestions.

LLM output must not by itself establish a supported biological claim.

Every supported scientific claim must be grounded in external evidence.

---

# Evidence Rules

Always preserve provenance.

Every supported claim must be traceable to one or more evidence records.

Distinguish explicitly among:

- direct biochemical evidence
- direct in-vivo evidence
- genetic evidence
- curated database annotation
- computational prediction
- homology inference
- review interpretation
- author hypothesis
- modeling assumption
- LLM hypothesis

Do not collapse these categories.

Do not treat a review article as equivalent to a primary experiment.

Do not treat a curated database annotation as direct experimental evidence.

Do not treat absence of search results as evidence that a biological phenomenon does not exist.

---

# Uncertainty Rules

Unknown information must remain unknown.

Do not silently infer:

- reaction reversibility
- cellular compartment
- gene-protein relationships
- enzyme assignments
- transport capability
- kinetic values
- missing cofactors
- missing reaction participants

Use `null`, `UNKNOWN`, or an explicit knowledge-gap record as defined in the specifications.

---

# Conflict Rules

Preserve conflicting evidence.

Do not delete or overwrite one claim because another source disagrees.

Before declaring two claims contradictory, compare context including:

- organism
- strain
- temperature
- pH
- growth medium
- carbon source
- oxygen status
- growth phase
- assay type
- enzyme construct
- purification state

Context-dependent differences may explain apparent contradictions.

---

# Kinetic Data Rules

Every kinetic measurement must be stored independently.

Never automatically average measurements across:

- publications
- strains
- organisms
- temperatures
- pH values
- enzyme constructs
- purification states
- assay systems

Always preserve:

- original value
- original unit
- normalized value
- normalized unit
- organism
- strain
- temperature
- pH
- assay method
- enzyme construct
- purification state
- publication
- evidence record

Never overwrite the original value during normalization.

Do not convert specific activity to `kcat` unless the required molecular information is actually available.

Measurement confidence and model applicability must remain separate concepts.

---

# Modeling Assumption Rules

Modeling assumptions are not biological facts.

Store assumptions separately from evidence-backed claims.

An assumption may be proposed automatically.

An assumption must not be represented as experimental evidence.

An unapproved assumption must not silently become active in a production export.

---

# Human Review Rules

The system must preserve these curation states:

```text
PROPOSED
MACHINE_REVIEWED
NEEDS_REVIEW
HUMAN_ACCEPTED
REJECTED
```

Automated processes may set:

```text
PROPOSED
MACHINE_REVIEWED
NEEDS_REVIEW
```

Automated processes must never set:

```text
HUMAN_ACCEPTED
```

Only an authorized human action may set `HUMAN_ACCEPTED`.

Do not create shortcuts that bypass this rule.

---

# Agent 1 Scope

Agent 1 performs:

- scientific source discovery
- database retrieval
- literature retrieval
- entity normalization
- evidence extraction
- reaction curation
- kinetics curation
- regulation curation
- confidence scoring
- scientific criticism
- deterministic validation
- conflict detection
- knowledge-gap identification
- structured export

Agent 1 does not perform:

- Antimony model generation
- SBML model generation
- ODE simulation
- parameter fitting
- flux balance analysis
- automatic kinetic parameter estimation
- pathway redesign

Do not add these responsibilities unless the specifications are explicitly changed.

---

# Initial Biological Scope

Version 0.1 focuses on:

```text
Organism:
Saccharomyces cerevisiae

Biological scope:
free fatty acid metabolism
```

The architecture must remain organism-agnostic and pathway-agnostic.

Do not hard-code yeast-specific logic into generic scientific infrastructure unless explicitly justified.

Yeast-specific search helpers may be isolated in organism-specific modules.

---

# Technology Requirements

Use:

```text
Python 3.12+
FastAPI
PostgreSQL
SQLAlchemy 2.x
Pydantic
Alembic
HTTPX
pytest
ruff
```

Use modern SQLAlchemy declarative syntax:

```python
Mapped[]
mapped_column()
relationship()
```

Do not introduce legacy SQLAlchemy APIs.

---

# Python Coding Rules

Use type hints throughout production code.

All public functions, classes, methods, and modules should have concise docstrings where useful.

Prefer small, focused functions.

Prefer explicit data transformations over clever abstractions.

Avoid large monolithic service classes.

Avoid hidden global mutable state.

Avoid unnecessary metaprogramming.

Avoid premature optimization.

Use dependency injection where it improves testability.

Prefer composition over deep inheritance.

---

# Repository Organization

Follow the project structure described in the specifications.

Keep responsibilities separated among modules such as:

```text
api
agents
connectors
models
schemas
scoring
normalization
validation
exports
config
```

Long LLM prompts must live in version-controlled files under:

```text
prompts/
```

Do not hard-code large prompts inside Python source files.

---

# Database Rules

PostgreSQL is the canonical database.

Do not redesign the relational model into a document store or graph database for Version 0.1.

External identifiers must not be primary keys.

Use UUIDs for internal primary keys.

Use conservative deletion behavior.

Prefer `ON DELETE RESTRICT` for scientific records.

Do not silently delete:

- claims
- evidence
- publications
- kinetic measurements
- review history
- provenance

Use Alembic for every schema change.

Never change SQLAlchemy database models without creating or updating the corresponding migration.

---

# Schema Rules

Follow `docs/02_database_schema.md`.

Do not omit tables merely because they appear redundant.

In particular, preserve separate representations for:

- claim
- evidence
- publication
- kinetic measurement
- regulatory interaction
- modeling assumption
- knowledge gap
- external record
- review event

Do not merge these concepts for convenience.

---

# API Rules

Follow `docs/04_api_spec.md`.

Use the API prefix:

```text
/api/v1
```

Do not silently alter endpoint semantics.

Use standard HTTP status codes.

Use Pydantic request and response models.

Do not trust client-supplied authorization roles in production-oriented code.

Human-only actions must be enforceable through authorization architecture.

External-source failure must be distinguishable from an empty scientific search result.

---

# Connector Rules

Every external scientific source must have an isolated connector.

A connector should expose behavior equivalent to:

```text
search()
fetch()
normalize()
cache()
rate_limit()
```

Do not mix PubMed, KEGG, BRENDA, SGD, or other source-specific parsing directly into curation logic.

Every connector must:

- respect source-specific rate limits
- support retry for transient failures
- use exponential backoff
- cache successful responses
- preserve retrieval metadata
- distinguish failure from zero results

Never place API credentials in source code.

---

# External Data Rules

Preserve raw external retrieval provenance where permitted.

Record:

- source
- external identifier
- retrieval timestamp
- raw response hash
- request metadata

Do not silently overwrite previous retrieved versions.

External source updates should be auditable.

---

# LLM Integration Rules

Use an internal provider abstraction.

Do not tightly couple scientific logic to one LLM provider.

Use structured outputs validated by Pydantic.

If LLM structured output fails validation:

- do not silently accept malformed output,
- retry only according to explicit policy,
- preserve the error,
- fail conservatively.

Use low-randomness settings for scientific extraction and classification.

Do not use creative prompting for evidence extraction.

---

# Deterministic Validation Rules

Use deterministic code whenever possible for:

- mass balance
- charge balance
- unit conversion
- identifier validation
- enum validation
- foreign-key validation
- duplicate detection
- confidence calculation
- export validation

Do not ask the LLM to perform deterministic checks when normal software can perform them reliably.

The governing rule is:

```text
Use AI for interpretation.
Use software for verification.
```

---

# Confidence Scoring Rules

Confidence calculations must follow the specifications.

Do not let the LLM directly assign final confidence scores.

Confidence scoring must be deterministic.

Confidence represents curation strength, not probability.

Do not simply sum multiple evidence records.

Account for evidence independence where possible.

A review citing a primary paper must not count as independent replication of that paper.

---

# Export Rules

Follow `docs/06_export_format.md`.

The canonical Agent 1 export is JSON.

Production exports must default to:

```text
HUMAN_ACCEPTED
```

records only.

Production exports must not include as accepted biology:

- REJECTED records
- LLM hypotheses
- unsupported assumptions
- dangling references
- supported claims without evidence

Unknown values must remain `null`.

Do not substitute defaults to make an export easier for Agent 2 to consume.

Agent 2 should receive uncertainty explicitly.

---

# JSON Rules

Use separate numeric values and units.

Correct:

```json
{
  "normalized_value": 0.42,
  "normalized_unit": "mM"
}
```

Incorrect:

```json
{
  "value": "0.42 mM"
}
```

Do not arbitrarily round scientific values during export.

---

# Stable Identifier Rules

Internal UUID relationships must remain stable.

Reaction IDs such as:

```text
FFA_R0001
```

must remain stable once assigned.

Do not renumber existing reactions during refactoring.

Do not match biological records solely by display names.

---

# Testing Rules

Follow `docs/05_testing.md`.

Every new production feature must include tests.

Every bug fix must include a regression test.

Tests must include both positive and negative cases.

External APIs must be mocked by default.

Live tests must be marked:

```python
@pytest.mark.live
```

and excluded from normal test execution.

Use PostgreSQL for integration tests where practical.

Do not rely solely on SQLite.

---

# Scientific Integrity Tests

The following class of tests is mandatory and build-blocking.

The system must prove that it cannot silently:

- invent PMIDs
- invent DOIs
- invent kinetic values
- invent reactions
- convert LLM output into evidence
- average unrelated kinetic measurements
- merge strain-specific measurements
- erase conflicting claims
- infer negative evidence from search failure
- promote machine review to human acceptance
- export rejected records as valid biology

Any scientific-integrity test failure must be treated as a release-blocking defect.

---

# Test Coverage

Target at least:

```text
90%
```

line coverage for core application code.

Target at least:

```text
95%
```

where practical for:

- confidence scoring
- unit conversion
- scientific validation
- review-state transitions
- export filtering

Do not optimize for coverage percentage at the expense of meaningful tests.

---

# Code Quality

Before considering a task complete, run:

```text
ruff
pytest
```

and relevant migration tests.

Do not leave known failing tests.

Do not silence lint errors without a clear reason.

Do not disable scientific-integrity tests.

---

# Migration Rules

An empty PostgreSQL database must always be migratable to the current schema head.

Every database change requires:

- SQLAlchemy model update
- Alembic migration
- migration test
- relevant database tests

Do not manually modify a production database schema outside migrations.

---

# Logging Rules

Log scientifically relevant workflow events, including:

- search queries
- source retrievals
- extraction events
- claim creation
- evidence linking
- duplicate detection
- conflict detection
- confidence calculation
- critic findings
- validation findings
- review transitions
- exports
- external-source failures

Never log secrets.

Keep scientific audit logging distinguishable from normal debugging logs.

---

# Security Rules

Never commit:

- API keys
- passwords
- tokens
- database credentials
- BRENDA credentials
- NCBI API keys
- LLM credentials

Use environment variables.

Maintain:

```text
.env.example
```

with variable names and safe placeholders only.

Do not include secrets in:

- API responses
- logs
- exception messages
- test fixtures
- documentation

---

# Change Discipline

Before changing existing behavior:

1. identify the governing specification,
2. determine whether the requested change is compatible,
3. update tests,
4. update migrations if needed,
5. preserve scientific provenance.

Do not make broad refactors while implementing an unrelated feature unless necessary.

Prefer small, reviewable commits.

---

# Implementation Workflow

When asked to implement a feature:

1. read the relevant specification documents,
2. inspect existing code,
3. identify the smallest coherent change,
4. implement the feature,
5. add or update tests,
6. run relevant tests,
7. run `ruff`,
8. report what changed,
9. report any unresolved specification issue.

Do not claim completion if required tests are failing.

---

# Phase Discipline

When instructed to implement one phase, implement only that phase unless a prerequisite is required.

Do not jump ahead and build unrelated future components.

For example, when implementing the database layer:

Do not also implement Antimony generation.

When implementing PubMed retrieval:

Do not also redesign the confidence system.

Keep phases isolated and reviewable.

---

# Placeholder Rules

Do not add realistic-looking placeholder biology.

Avoid code such as:

```python
DEFAULT_KM = 1.0
DEFAULT_KCAT = 1.0
```

unless the value is explicitly part of a non-scientific test fixture.

Do not hard-code invented example PMIDs or biological facts in production code.

Synthetic test data must be unmistakably test-only.

---

# Comments and TODO Rules

TODO comments must explain the unresolved technical or scientific issue.

Bad:

```text
TODO: fix later
```

Good:

```text
TODO: Implement Rhea identifier validation before enabling automatic
reaction normalization from Rhea records.
```

Do not use TODOs to bypass mandatory scientific-integrity requirements.

---

# Error Handling

Fail conservatively.

When encountering:

- malformed source data
- ambiguous identifiers
- unsupported units
- external API failure
- incomplete provenance
- LLM parsing failure
- conflicting entity identities

do not guess.

Return or record an explicit error, unresolved state, or review requirement.

---

# Performance

Correctness comes before optimization.

Avoid obvious N+1 database behavior.

Use pagination for large collections.

Do not eagerly load entire publication or evidence collections unless needed.

Do not optimize by dropping scientific metadata.

---

# Documentation

Keep code aligned with the six authoritative specification documents.

If implementation introduces an important behavior not described in the specifications, document it.

Do not silently create new scientific semantics in code.

---

# Definition of Completion for Any Task

A task is complete only when:

- implementation matches the relevant specification,
- type hints are present,
- required tests exist,
- tests pass,
- `ruff` passes,
- migrations are included if needed,
- scientific provenance is preserved,
- uncertainty is preserved,
- no human-review boundary is bypassed,
- no unsupported biological fact has been introduced.

---

# Final Rule

When uncertain how to implement a scientific behavior, choose the implementation that preserves the most information and makes the least unsupported biological assumption.

The project must always favor:

```text
traceability over convenience

evidence over plausibility

uncertainty over invention

explicit assumptions over hidden defaults

human approval over automated overconfidence
```
