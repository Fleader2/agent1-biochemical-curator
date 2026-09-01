# Agent 1: Biochemical Evidence Curator
## API Specification

**Document:** `docs/04_api_spec.md`

**Version:** 0.1

**Status:** Implementation Specification

---

# Purpose

This document defines the REST API for Agent 1.

The API provides controlled access to:

- curation tasks,
- organisms,
- genes,
- proteins,
- compounds,
- reactions,
- claims,
- evidence,
- kinetic measurements,
- regulatory interactions,
- knowledge gaps,
- review actions,
- exports.

The API must expose the scientific curation system without allowing client applications to bypass provenance, validation, or review-state requirements.

FastAPI shall be used.

Pydantic shall be used for request and response validation.

The initial API version shall be:

```text id="cfx7ad"
/api/v1
```

All routes described below should be prefixed with:

```text id="e0z635"
/api/v1
```

---

# General API Principles

The API must follow these rules.

1. Requests and responses shall use JSON unless otherwise specified.

2. UUIDs shall be used for internal entity identifiers.

3. External database identifiers must not be treated as internal primary keys.

4. Invalid scientific state transitions must be rejected.

5. Unsupported claims must not be silently promoted.

6. Human acceptance requires an explicit review endpoint.

7. Destructive operations must be minimized.

8. Scientific provenance must never be deleted through ordinary API operations.

9. API responses must expose curation state where scientifically relevant.

10. API endpoints must distinguish between retrieval, curation, validation, review, and export.

---

# Base Response Format

Normal successful responses should return the requested resource directly.

Example:

```json id="rvwbnj"
{
  "id": "uuid",
  "scientific_name": "Saccharomyces cerevisiae",
  "strain": "S288C"
}
```

Do not wrap every response in unnecessary objects such as:

```json id="rxn067"
{
  "success": true,
  "data": {}
}
```

unless required for a specific operation.

---

# Error Format

Errors should follow a consistent structure.

Example:

```json id="f6ymtx"
{
  "detail": {
    "code": "INVALID_CURATION_TRANSITION",
    "message": "Only a human reviewer may set HUMAN_ACCEPTED.",
    "context": {
      "entity_type": "reaction",
      "entity_id": "uuid"
    }
  }
}
```

---

# HTTP Status Codes

Use standard HTTP status codes.

```text id="kn5hs4"
200 OK
201 Created
202 Accepted
204 No Content

400 Bad Request
404 Not Found
409 Conflict
422 Validation Error

500 Internal Server Error
502 External Source Failure
503 Service Unavailable
```

Use `409 Conflict` for scientific or workflow conflicts such as:

- duplicate external identifier,
- invalid review transition,
- conflicting immutable record state.

---

# Pagination

List endpoints must support pagination.

Query parameters:

```text id="4rko5d"
limit
offset
```

Defaults:

```text id="15vfqt"
limit = 50
offset = 0
```

Maximum:

```text id="o55fma"
limit = 500
```

Paginated responses should use:

```json id="3jtnxr"
{
  "items": [],
  "limit": 50,
  "offset": 0,
  "total": 0
}
```

---

# Filtering

List endpoints should support filtering where useful.

Common filters include:

```text id="gg8dmv"
organism_id
strain
curation_state
confidence_class
gene_id
protein_id
reaction_id
publication_id
source_type
status
```

Exact supported filters may vary by endpoint.

---

# Sorting

List endpoints may support:

```text id="ynsaz4"
sort_by
sort_order
```

Allowed values for `sort_order`:

```text id="kj6a9p"
asc
desc
```

Only explicitly allowed database fields may be used for sorting.

Never pass user-provided field names directly into SQL.

---

# Health Endpoint

## GET `/health`

Returns basic application health.

Response:

```json id="mbs0a6"
{
  "status": "ok",
  "database": "ok",
  "version": "0.1.0"
}
```

This endpoint must not trigger external API calls.

---

# System Information

## GET `/system/info`

Returns application metadata relevant to reproducibility.

Response:

```json id="u8vgzz"
{
  "application": "Agent 1 Biochemical Evidence Curator",
  "version": "0.1.0",
  "api_version": "v1",
  "prompt_version": "0.1",
  "llm_provider": "configured-provider",
  "llm_model": "configured-model"
}
```

Do not expose secrets or API keys.

---

# Curation Task Endpoints

A curation task is the primary unit of work submitted to Agent 1.

---

## POST `/curation-tasks`

Creates a new curation task.

Request:

```json id="21dfqi"
{
  "organism_id": "uuid",
  "title": "Free fatty acid metabolism",
  "biological_question": "Curate the reactions contributing to free fatty acid production and consumption.",
  "scope": {
    "include_kinetics": true,
    "include_regulation": true,
    "include_localization": true,
    "include_conflict_search": true
  }
}
```

Response:

```json id="6tnumb"
{
  "id": "uuid",
  "status": "CREATED",
  "title": "Free fatty acid metabolism",
  "created_at": "2026-08-31T00:00:00Z"
}
```

HTTP status:

```text id="77ph8t"
201 Created
```

---

## GET `/curation-tasks/{task_id}`

Returns task state and summary.

Response:

```json id="re94jd"
{
  "id": "uuid",
  "title": "Free fatty acid metabolism",
  "status": "RUNNING",
  "organism_id": "uuid",
  "created_at": "timestamp",
  "started_at": "timestamp",
  "completed_at": null,
  "summary": {
    "candidate_reactions": 42,
    "claims_created": 186,
    "sources_retrieved": 73,
    "conflicts_found": 5,
    "knowledge_gaps_created": 18
  }
}
```

---

## GET `/curation-tasks`

Lists tasks.

Filters:

```text id="29j6nm"
status
organism_id
```

---

## POST `/curation-tasks/{task_id}/run`

Starts or resumes execution of a curation task.

Response:

```json id="wcz7pz"
{
  "id": "uuid",
  "status": "RUNNING"
}
```

HTTP status:

```text id="tzfpt6"
202 Accepted
```

The endpoint must not run long curation workflows synchronously inside the HTTP request.

The implementation may initially use a simple internal task runner, but the API must be designed so that background workers can be added later.

---

## POST `/curation-tasks/{task_id}/cancel`

Requests cancellation.

Response:

```json id="83tr2d"
{
  "id": "uuid",
  "status": "CANCEL_REQUESTED"
}
```

Cancellation must not delete already retrieved evidence.

---

# Organism Endpoints

## POST `/organisms`

Creates an organism or strain record.

Request:

```json id="myjr2v"
{
  "scientific_name": "Saccharomyces cerevisiae",
  "common_name": "budding yeast",
  "ncbi_taxonomy_id": 4932,
  "strain": "S288C",
  "kegg_code": "sce"
}
```

---

## GET `/organisms/{organism_id}`

Returns one organism.

---

## GET `/organisms`

Supported filters:

```text id="f262xu"
scientific_name
ncbi_taxonomy_id
strain
```

---

## PATCH `/organisms/{organism_id}`

Updates editable metadata.

Do not allow external identifier changes without validation.

---

# Gene Endpoints

## POST `/genes`

Creates a gene record.

Request:

```json id="sc56a0"
{
  "organism_id": "uuid",
  "symbol": "ACC1",
  "systematic_name": "YNR016C",
  "name": "acetyl-CoA carboxylase"
}
```

---

## GET `/genes/{gene_id}`

Returns gene details.

---

## GET `/genes`

Supported filters:

```text id="fqf2mo"
organism_id
symbol
systematic_name
sgd_id
ncbi_gene_id
```

---

## PATCH `/genes/{gene_id}`

Updates gene metadata.

Changes must not silently merge gene identities.

---

# Protein Endpoints

## POST `/proteins`

Creates a protein.

Request:

```json id="f4hyiz"
{
  "organism_id": "uuid",
  "gene_id": "uuid",
  "name": "Acetyl-CoA carboxylase",
  "ec_number": "6.4.1.2"
}
```

---

## GET `/proteins/{protein_id}`

---

## GET `/proteins`

Filters:

```text id="3uwpz8"
organism_id
gene_id
uniprot_id
ec_number
```

---

# Enzyme Complex Endpoints

## POST `/enzyme-complexes`

Creates a complex.

Request:

```json id="8p0rvr"
{
  "organism_id": "uuid",
  "name": "Fatty acid synthase",
  "description": "Cytosolic fatty acid synthase complex"
}
```

---

## POST `/enzyme-complexes/{complex_id}/members`

Adds a protein member.

Request:

```json id="fns849"
{
  "protein_id": "uuid",
  "stoichiometry": 1,
  "required": true
}
```

---

## GET `/enzyme-complexes/{complex_id}`

Returns complex and member information.

---

# Compound Endpoints

## POST `/compounds`

Creates a compound.

Request:

```json id="4fuhgd"
{
  "canonical_name": "acetyl-CoA",
  "formula": "C23H38N7O17P3S",
  "charge": -4,
  "chebi_id": "CHEBI:15351"
}
```

Chemical identifiers should be validated where possible.

---

## GET `/compounds/{compound_id}`

---

## GET `/compounds`

Supported filters:

```text id="q2badg"
canonical_name
chebi_id
kegg_compound_id
inchikey
```

---

## POST `/compounds/{compound_id}/synonyms`

Request:

```json id="ztmj50"
{
  "synonym": "AcCoA",
  "source": "literature"
}
```

---

# Compartment Endpoints

## GET `/compartments`

Filters:

```text id="d3us7z"
organism_id
name
```

---

## POST `/compartments`

Creates a compartment when needed.

Standard seed compartments should normally already exist.

---

# Reaction Endpoints

## POST `/reactions`

Creates a candidate reaction.

Request:

```json id="w5cnuk"
{
  "internal_id": "FFA_R0001",
  "name": "Acetyl-CoA carboxylase reaction",
  "organism_id": "uuid",
  "reversible": false,
  "curation_state": "PROPOSED"
}
```

---

## GET `/reactions/{reaction_id}`

Returns a reaction including:

```text id="2if16e"
core metadata
participants
enzyme associations
claim summaries
confidence summaries
curation state
knowledge-gap count
```

Example response:

```json id="ixs6s4"
{
  "id": "uuid",
  "internal_id": "FFA_R0001",
  "name": "Acetyl-CoA carboxylase reaction",
  "organism_id": "uuid",
  "reversible": false,
  "balanced_mass": true,
  "balanced_charge": true,
  "curation_state": "MACHINE_REVIEWED",
  "participants": [],
  "enzymes": [],
  "confidence": {
    "reaction_identity": 96,
    "enzyme_assignment": 97,
    "compartment": 91,
    "reversibility": 84,
    "kinetics": 62
  }
}
```

---

## GET `/reactions`

Supported filters:

```text id="71j414"
organism_id
internal_id
ec_number
curation_state
kegg_reaction_id
rhea_id
```

---

## POST `/reactions/{reaction_id}/participants`

Adds a reaction participant.

Request:

```json id="ojgjhn"
{
  "compound_id": "uuid",
  "compartment_id": "uuid",
  "role": "REACTANT",
  "stoichiometry": 1
}
```

Stoichiometry must be greater than zero.

---

## POST `/reactions/{reaction_id}/enzymes`

Associates a protein or complex.

Request example:

```json id="25mv9h"
{
  "protein_id": "uuid",
  "complex_id": null,
  "relationship": "CATALYZES"
}
```

Exactly one of:

```text id="i6znag"
protein_id
complex_id
```

should normally be provided.

---

## POST `/reactions/{reaction_id}/validate`

Runs deterministic validation.

Validation should include:

```text id="9fpr2l"
schema consistency
duplicate detection
mass balance
charge balance
identifier consistency
```

Response:

```json id="tl829v"
{
  "reaction_id": "uuid",
  "valid": false,
  "checks": [
    {
      "name": "mass_balance",
      "status": "PASS"
    },
    {
      "name": "charge_balance",
      "status": "FAIL",
      "message": "Net charge differs between reactants and products."
    }
  ]
}
```

This endpoint must not silently modify the reaction.

---

## POST `/reactions/{reaction_id}/critic-review`

Runs the scientific critic.

Response:

```json id="p2klv8"
{
  "reaction_id": "uuid",
  "issues": [
    {
      "severity": "HIGH",
      "problem": "Reversibility is not supported by the cited evidence.",
      "recommended_action": "Set reversibility to unknown pending review."
    }
  ]
}
```

The critic must not automatically repair the reaction.

---

## POST `/reactions/{reaction_id}/completion-review`

Runs the completion evaluator.

Response:

```json id="dbke4a"
{
  "classification": "MODEL_READY_WITH_ASSUMPTIONS",
  "assumptions": [],
  "knowledge_gaps": [],
  "checked_categories": {
    "stoichiometry": true,
    "enzyme_assignment": true,
    "compartment": true,
    "reversibility": true,
    "kinetics": true,
    "regulation": true
  }
}
```

---

# Claim Endpoints

## POST `/claims`

Creates a proposed claim.

Request:

```json id="58a4qa"
{
  "subject_type": "protein",
  "subject_id": "uuid",
  "predicate": "LOCALIZED_IN",
  "object_type": "compartment",
  "object_id": "uuid",
  "organism_id": "uuid",
  "claim_category": "LOCALIZATION"
}
```

New claims must default to:

```text id="11h1mz"
status = UNKNOWN
curation_state-equivalent behavior = PROPOSED
```

A claim must not become supported merely because it was submitted through the API.

---

## GET `/claims/{claim_id}`

Returns:

```text id="iswofm"
claim
supporting evidence
confidence
status
conflicts
review history
```

---

## GET `/claims`

Filters:

```text id="11s31g"
subject_type
subject_id
predicate
organism_id
status
confidence_class
```

---

## POST `/claims/{claim_id}/recalculate-confidence`

Runs deterministic confidence calculation.

Response:

```json id="e33hg7"
{
  "claim_id": "uuid",
  "confidence_score": 87,
  "confidence_class": "HIGH"
}
```

The LLM must not directly set this value.

---

# Evidence Endpoints

## POST `/claims/{claim_id}/evidence`

Adds evidence to a claim.

Request:

```json id="d5jjs6"
{
  "publication_id": "uuid",
  "source_type": "PUBMED",
  "source_id": "12345678",
  "evidence_type": "DIRECT_BIOCHEMICAL",
  "organism": "Saccharomyces cerevisiae",
  "strain": "S288C",
  "experimental_system": "purified enzyme",
  "directness": "DIRECT",
  "curator_summary": "The study measured catalytic activity of purified Acc1."
}
```

---

## GET `/evidence/{evidence_id}`

---

## GET `/evidence`

Supported filters:

```text id="420kxi"
claim_id
publication_id
source_type
evidence_type
organism
strain
```

---

# Publication Endpoints

## GET `/publications/{publication_id}`

Returns stored publication metadata.

---

## GET `/publications`

Filters:

```text id="638a6r"
pmid
pmcid
doi
year
```

---

## POST `/publications/import/pubmed`

Imports metadata using PubMed.

Request:

```json id="d7bj4c"
{
  "pmid": "12345678"
}
```

Response:

```json id="n2tnp9"
{
  "publication_id": "uuid",
  "pmid": "12345678",
  "retrieved": true
}
```

The endpoint must retrieve real data from the configured connector.

It must never create placeholder publication metadata when retrieval fails.

---

# Search Endpoints

Search endpoints provide controlled access to external scientific sources.

---

## POST `/search/pubmed`

Request:

```json id="1f7zc2"
{
  "query": "ACC1 Saccharomyces cerevisiae kinetics",
  "limit": 20
}
```

Response:

```json id="1j686r"
{
  "query": "ACC1 Saccharomyces cerevisiae kinetics",
  "results": [
    {
      "pmid": "12345678",
      "title": "Example title",
      "year": 2001
    }
  ]
}
```

The endpoint must respect configured NCBI rate limits.

---

## POST `/search/kegg`

Request:

```json id="ws1298"
{
  "query_type": "gene",
  "query": "ACC1",
  "organism_code": "sce"
}
```

---

## POST `/search/brenda`

Request:

```json id="5sr1ft"
{
  "ec_number": "6.4.1.2",
  "organism": "Saccharomyces cerevisiae",
  "parameter_type": "Km"
}
```

The connector must enforce BRENDA authentication and rate limits.

---

## POST `/search/sgd`

Request:

```json id="pnimjq"
{
  "query": "ACC1"
}
```

---

# Kinetic Measurement Endpoints

## POST `/kinetic-measurements`

Creates a kinetic measurement record.

Request:

```json id="f7l1do"
{
  "reaction_id": "uuid",
  "protein_id": "uuid",
  "parameter_type": "Km",
  "parameter_value": 0.42,
  "unit": "mM",
  "original_value": 420,
  "original_unit": "uM",
  "substrate_id": "uuid",
  "organism_id": "uuid",
  "strain": "S288C",
  "temperature_c": 30,
  "ph": 7.0,
  "publication_id": "uuid",
  "evidence_id": "uuid"
}
```

The API must not merge an incoming measurement with an existing measurement merely because the numeric value is similar.

---

## GET `/kinetic-measurements/{measurement_id}`

---

## GET `/kinetic-measurements`

Filters:

```text id="26whwy"
reaction_id
protein_id
parameter_type
organism_id
strain
publication_id
confidence_class
```

---

## POST `/kinetic-measurements/{measurement_id}/normalize`

Performs deterministic unit normalization.

Response:

```json id="jiuf8o"
{
  "original_value": 420,
  "original_unit": "uM",
  "normalized_value": 0.42,
  "normalized_unit": "mM"
}
```

The original value must remain unchanged.

---

# Regulatory Interaction Endpoints

## POST `/regulatory-interactions`

Request:

```json id="j01i8v"
{
  "regulator_type": "protein",
  "regulator_id": "uuid",
  "target_type": "protein",
  "target_id": "uuid",
  "effect": "PHOSPHORYLATION",
  "mechanism": "Direct phosphorylation",
  "direct": true,
  "organism_id": "uuid",
  "claim_id": "uuid"
}
```

---

## GET `/regulatory-interactions/{interaction_id}`

---

## GET `/regulatory-interactions`

Filters:

```text id="ihn5qn"
regulator_id
target_id
effect
organism_id
curation_state
```

---

# Knowledge Gap Endpoints

## POST `/knowledge-gaps`

Creates a knowledge gap.

Request:

```json id="ev92wv"
{
  "subject_type": "reaction",
  "subject_id": "uuid",
  "missing_information": "No organism-specific kcat was found.",
  "importance": "HIGH",
  "model_impact": "Limits prediction of maximal reaction rate.",
  "suggested_experiment": "Measure purified-enzyme turnover under physiological conditions.",
  "priority": 2
}
```

---

## GET `/knowledge-gaps/{gap_id}`

---

## GET `/knowledge-gaps`

Filters:

```text id="9rsyym"
subject_type
subject_id
priority
status
```

---

## PATCH `/knowledge-gaps/{gap_id}`

Permitted changes include:

```text id="nl473n"
priority
status
notes
```

The original scientific description should not be silently rewritten.

---

# Modeling Assumption Endpoints

## POST `/modeling-assumptions`

Request:

```json id="36yt28"
{
  "subject_type": "reaction",
  "subject_id": "uuid",
  "assumption": "Treat the reaction as irreversible under glucose-rich conditions.",
  "reason": "Required for initial kinetic model construction.",
  "required_for_model": true,
  "human_approved": false
}
```

---

## GET `/modeling-assumptions`

Filters:

```text id="ymc8q9"
subject_type
subject_id
human_approved
required_for_model
```

---

## POST `/modeling-assumptions/{assumption_id}/approve`

This endpoint represents an explicit human action.

Request:

```json id="vybe9q"
{
  "reviewer_id": "identifier",
  "comment": "Approved for initial model construction."
}
```

Response:

```json id="mji90r"
{
  "id": "uuid",
  "human_approved": true
}
```

---

# Review Endpoints

Review endpoints control scientific state transitions.

---

## POST `/review/{entity_type}/{entity_id}`

Request:

```json id="f3v6bd"
{
  "reviewer_type": "HUMAN",
  "reviewer_id": "user-identifier",
  "new_state": "HUMAN_ACCEPTED",
  "comment": "Reaction stoichiometry and enzyme assignment verified."
}
```

Permitted `entity_type` examples:

```text id="wvggml"
reaction
claim
regulatory_interaction
kinetic_measurement
```

The application must validate allowed state transitions.

---

# Allowed Curation-State Transitions

Normal transitions:

```text id="m0fx5g"
PROPOSED
    ->
MACHINE_REVIEWED
```

```text id="z5vyqz"
PROPOSED
    ->
NEEDS_REVIEW
```

```text id="43p0g5"
MACHINE_REVIEWED
    ->
NEEDS_REVIEW
```

Human-only transitions:

```text id="zbq8ve"
MACHINE_REVIEWED
    ->
HUMAN_ACCEPTED
```

```text id="cr5e4h"
NEEDS_REVIEW
    ->
HUMAN_ACCEPTED
```

```text id="rw4a3f"
PROPOSED
    ->
REJECTED
```

```text id="i1o5fv"
MACHINE_REVIEWED
    ->
REJECTED
```

```text id="z4amxk"
NEEDS_REVIEW
    ->
REJECTED
```

Only a human reviewer may set:

```text id="53lknk"
HUMAN_ACCEPTED
```

---

# Review History Endpoint

## GET `/review/{entity_type}/{entity_id}/history`

Returns ordered review events.

Example:

```json id="q305g0"
{
  "items": [
    {
      "previous_state": "PROPOSED",
      "new_state": "MACHINE_REVIEWED",
      "reviewer_type": "AI_CRITIC",
      "created_at": "timestamp"
    },
    {
      "previous_state": "MACHINE_REVIEWED",
      "new_state": "HUMAN_ACCEPTED",
      "reviewer_type": "HUMAN",
      "reviewer_id": "user-identifier",
      "created_at": "timestamp"
    }
  ]
}
```

---

# Conflict Endpoints

The API should provide an explicit way to inspect conflicting claims.

## GET `/claims/{claim_id}/conflicts`

Response:

```json id="c9g4kn"
{
  "claim_id": "uuid",
  "conflicts": [
    {
      "claim_id": "uuid",
      "predicate": "LOCALIZED_IN",
      "object": "mitochondrion",
      "status": "SUPPORTED",
      "confidence_score": 71
    }
  ]
}
```

---

# External Record Endpoints

## GET `/external-records/{record_id}`

Returns metadata for a retrieved external record.

Raw responses may be omitted from normal API responses.

---

## GET `/external-records`

Filters:

```text id="4i6yaj"
source
external_id
retrieval_date
```

---

# Export Endpoints

Exports are the boundary between Agent 1 and downstream agents.

---

## POST `/exports`

Request:

```json id="nupota"
{
  "organism_id": "uuid",
  "format": "JSON",
  "curation_states": [
    "HUMAN_ACCEPTED"
  ],
  "include": [
    "reactions",
    "compounds",
    "genes",
    "proteins",
    "enzyme_complexes",
    "compartments",
    "kinetics",
    "regulation",
    "evidence",
    "assumptions",
    "knowledge_gaps"
  ]
}
```

Default:

```text id="rktqta"
curation_states = HUMAN_ACCEPTED only
```

The API must reject accidental export of rejected records as valid biology.

---

## GET `/exports/{export_id}`

Returns export metadata.

Example:

```json id="ut9twg"
{
  "id": "uuid",
  "status": "COMPLETED",
  "format": "JSON",
  "created_at": "timestamp",
  "record_counts": {
    "reactions": 28,
    "compounds": 63,
    "kinetic_measurements": 112
  }
}
```

---

# Agent 2 Export Structure

The JSON export should conceptually contain:

```json id="ow9s1d"
{
  "metadata": {
    "organism": {},
    "generated_at": "timestamp",
    "agent1_version": "0.1.0",
    "curation_states": [
      "HUMAN_ACCEPTED"
    ]
  },
  "reactions": [],
  "compounds": [],
  "genes": [],
  "proteins": [],
  "enzyme_complexes": [],
  "compartments": [],
  "kinetics": [],
  "regulation": [],
  "evidence": [],
  "assumptions": [],
  "knowledge_gaps": []
}
```

This export must contain stable internal IDs so Agent 2 can preserve relationships among records.

---

# Reaction Export Requirements

Each exported reaction should include at minimum:

```json id="5al9jw"
{
  "reaction_id": "FFA_R0001",
  "name": "Example reaction",
  "reversible": false,
  "curation_state": "HUMAN_ACCEPTED",
  "participants": [
    {
      "compound_id": "uuid",
      "role": "REACTANT",
      "stoichiometry": 1,
      "compartment_id": "uuid"
    }
  ],
  "enzymes": [],
  "confidence": {
    "reaction_identity": 96,
    "stoichiometry": 94,
    "enzyme_assignment": 97,
    "compartment": 91,
    "reversibility": 84
  },
  "assumption_ids": [],
  "knowledge_gap_ids": []
}
```

Do not flatten biologically distinct confidence values into one aggregate value.

---

# Search and Curation Separation

External search endpoints must not automatically convert search results into accepted scientific records.

The workflow should remain:

```text id="q6qt5m"
search
    ->
retrieve
    ->
extract
    ->
normalize
    ->
validate
    ->
critic review
    ->
human review
    ->
export
```

Search results are candidate evidence only.

---

# Idempotency

Where practical, create/import endpoints should support idempotent behavior.

For example:

```text id="bkiqrz"
POST /publications/import/pubmed
```

with the same PMID should return the existing publication if already present rather than create a duplicate.

The same principle applies to reliable external identifiers.

---

# Concurrency

The API must handle concurrent read operations safely.

Write operations involving:

```text id="iu6nzn"
curation-state transitions
confidence recalculation
entity deduplication
review events
```

must use database transactions.

Review-state transitions should use row-level locking or another safe concurrency strategy where appropriate.

---

# Authentication

Authentication may be minimal in the initial local-development version.

However, the architecture must distinguish at least:

```text id="hj18fb"
system
machine reviewer
human reviewer
administrator
```

The application must not rely solely on client-supplied `reviewer_type` in a production deployment.

Human-only actions must eventually be enforced through authentication and authorization.

---

# Authorization Requirements

Only authorized human reviewers may:

```text id="ftu0mb"
set HUMAN_ACCEPTED

approve modeling assumptions

reject human-reviewed records

override scientific validation warnings
```

Automated agents may:

```text id="2lmc4k"
create PROPOSED records

add evidence

run validation

run critic review

calculate confidence

set MACHINE_REVIEWED

set NEEDS_REVIEW
```

Automated agents must not set `HUMAN_ACCEPTED`.

---

# OpenAPI

FastAPI's generated OpenAPI schema must remain enabled.

The API should provide development documentation through:

```text id="zco6ld"
/docs

/redoc
```

unless disabled in a production deployment.

All endpoints must include:

```text id="g6sjca"
summary
description
response model
documented error behavior
```

---

# API Versioning

Breaking changes require a new API version.

Example:

```text id="qq1hf5"
/api/v2
```

Do not silently change response semantics in `/api/v1`.

---

# API Logging

Log at minimum:

```text id="8sk849"
request ID
endpoint
HTTP method
response status
duration
authenticated actor
```

For scientific write operations also log:

```text id="lq4q41"
entity type
entity ID
curation state before
curation state after
reviewer
```

Do not log secrets.

---

# External Connector Failures

When an external scientific source fails, the API must distinguish source failure from an empty search result.

Example failure response:

```json id="eqvklc"
{
  "detail": {
    "code": "EXTERNAL_SOURCE_FAILURE",
    "message": "PubMed could not be reached.",
    "context": {
      "source": "PUBMED"
    }
  }
}
```

The application must not interpret this as:

```text id="h877g7"
no literature exists
```

---

# Long-Running Operations

The following may become long-running:

```text id="z00127"
pathway curation
citation expansion
full literature extraction
bulk database retrieval
large exports
```

These should use task-oriented endpoints and return `202 Accepted` where appropriate.

Do not hold an HTTP request open for an entire pathway-level curation workflow.

---

# Initial API Scope

For version 0.1, prioritize implementation in this order:

```text id="tupmoe"
1. /health

2. organisms

3. genes

4. proteins

5. compounds

6. compartments

7. reactions

8. claims

9. evidence

10. publications

11. kinetic-measurements

12. regulatory-interactions

13. knowledge-gaps

14. review endpoints

15. curation-tasks

16. external search endpoints

17. validation endpoints

18. export endpoints
```

Do not block initial development on every future API feature.

---

# Required API Tests

At minimum implement:

```text id="5f59q1"
test_health

test_create_organism

test_get_organism

test_create_gene

test_create_compound

test_create_reaction

test_add_reaction_participant

test_invalid_negative_stoichiometry_rejected

test_create_claim

test_add_evidence_to_claim

test_supported_claim_without_evidence_rejected

test_import_pubmed_is_idempotent

test_create_kinetic_measurement

test_original_kinetic_unit_preserved

test_conflicting_claims_can_be_retrieved

test_machine_cannot_set_human_accepted

test_human_can_set_human_accepted

test_review_event_created

test_rejected_record_not_in_default_export

test_default_export_contains_only_human_accepted

test_external_source_failure_not_treated_as_empty_result

test_invalid_curation_transition_rejected

test_pagination

test_openapi_schema_available
```

---

# Security Requirements

Never return:

```text id="fpyo8z"
API keys
database passwords
LLM credentials
BRENDA credentials
NCBI API keys
internal secrets
```

Use environment variables or a secrets-management system.

Do not store credentials in the database.

Do not commit credentials to Git.

---

# Configuration

API configuration should use environment variables.

Examples:

```text id="ny42ny"
DATABASE_URL

LLM_PROVIDER
LLM_MODEL
LLM_API_KEY

NCBI_EMAIL
NCBI_TOOL_NAME
NCBI_API_KEY

BRENDA_USERNAME
BRENDA_PASSWORD

KEGG_BASE_URL

LOG_LEVEL
```

Provide a:

```text id="iccszs"
.env.example
```

containing variable names only, never real credentials.

---

# Definition of Done

The API layer is complete when:

1. FastAPI starts successfully.

2. `/health` responds correctly.

3. CRUD access exists for core scientific entities.

4. Reaction participants can be represented structurally.

5. Claims can be linked to evidence.

6. Kinetic measurements can be stored without merging.

7. Review-state transitions are enforced.

8. Automated agents cannot set `HUMAN_ACCEPTED`.

9. Deterministic reaction validation can be invoked.

10. Scientific critic review can be invoked.

11. Knowledge gaps can be stored and retrieved.

12. External search connectors can be called through controlled endpoints.

13. External-source failures are distinguishable from negative scientific results.

14. Default exports contain only approved records.

15. OpenAPI documentation is generated.

16. Required API tests pass.

17. `pytest` passes without errors.

18. `ruff` reports no errors.

---

# Final API Principle

The API is not merely a CRUD layer over a database.

It is a scientific-control boundary.

Every endpoint must preserve the distinction among:

```text id="41hyob"
retrieved information

proposed scientific claims

evidence-supported claims

machine-reviewed claims

human-accepted knowledge

modeling assumptions
```

No API operation may collapse these distinctions for convenience.

The central rule is:

```text id="jaq37v"
Search results are not facts.

LLM output is not evidence.

Evidence is not automatically consensus.

Machine review is not human approval.

Human-approved knowledge is the default input to downstream models.
```
