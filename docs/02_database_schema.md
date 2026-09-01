# Agent 1: Biochemical Evidence Curator
## Database Schema Specification

**Document:** `docs/02_database_schema.md`

**Version:** 0.1

**Status:** Implementation Specification

---

# Purpose

This document defines the relational database schema for Agent 1.

The database must store biochemical entities, reactions, evidence, kinetic measurements, regulatory interactions, experimental conditions, uncertainty, provenance, modeling assumptions, and knowledge gaps.

The database is the canonical scientific record for Agent 1.

The system must be designed around the principle that scientific knowledge consists of **claims supported by evidence**, rather than only tables of accepted facts.

PostgreSQL shall be used as the primary database.

SQLAlchemy 2.x shall be used for ORM models.

Alembic shall be used for schema migrations.

Pydantic shall be used for API and validation schemas.

---

# Core Design Principles

The schema must satisfy the following requirements.

1. Every biological claim must be traceable to evidence.

2. Multiple sources may support the same claim.

3. Conflicting claims must be preserved.

4. Experimental conditions must be preserved.

5. Kinetic measurements must be stored individually.

6. Measurements from different organisms, strains, constructs, or conditions must never be silently merged.

7. Biological entities must use normalized identifiers wherever possible.

8. External database identifiers must be preserved.

9. AI-generated interpretations must never be stored as primary evidence.

10. Human review state must be explicit.

11. Records must be auditable.

12. Accepted records must never be silently overwritten.

13. Unknown values must remain NULL rather than being guessed.

14. Scientific provenance must be retained permanently.

---

# Naming Conventions

Database table names shall use lowercase snake_case.

Primary keys shall use:

```text
id
```

Foreign keys shall use:

```text
<table_name>_id
```

Timestamps shall use UTC.

Every major curated entity should include:

```text
created_at
updated_at
```

where appropriate.

UUIDs are preferred for internal primary keys.

External identifiers must never be used as primary keys.

---

# Enumerated Types

The following logical enumerations shall be implemented either as PostgreSQL enums or application-level validated string enums.

## CurationState

```text
PROPOSED
MACHINE_REVIEWED
NEEDS_REVIEW
HUMAN_ACCEPTED
REJECTED
```

## ConfidenceClass

```text
VERY_HIGH
HIGH
MODERATE
LOW
UNKNOWN
```

## EvidenceType

```text
DIRECT_BIOCHEMICAL
DIRECT_IN_VIVO
GENETIC
LOCALIZATION
PROTEOMICS
METABOLOMICS
FLUXOMICS
TRANSCRIPTOMICS
STRUCTURAL
CURATED_DATABASE
COMPUTATIONAL
HOMOLOGY
REVIEW
AUTHOR_HYPOTHESIS
OTHER
```

## ClaimStatus

```text
SUPPORTED
CONFLICTED
UNRESOLVED
REJECTED
UNKNOWN
```

## ReactionParticipantRole

```text
REACTANT
PRODUCT
MODIFIER
```

## RegulatoryEffect

```text
ACTIVATION
INHIBITION
INDUCTION
REPRESSION
PHOSPHORYLATION
DEPHOSPHORYLATION
STABILIZATION
DESTABILIZATION
DEGRADATION
TRANSLOCATION
UNKNOWN
OTHER
```

## SourceType

```text
PUBMED
PMC
KEGG
BRENDA
BIOCYC
METACYC
SGD
UNIPROT
CHEBI
RHEA
NCBI
OTHER
```

---

# Table: organism

Stores organism and strain information.

## Columns

```text
id                      UUID PRIMARY KEY

scientific_name         VARCHAR NOT NULL
common_name             VARCHAR
ncbi_taxonomy_id        INTEGER
kegg_code               VARCHAR
biocyc_id               VARCHAR

strain                  VARCHAR
strain_parent           VARCHAR

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Constraints

The combination

```text
scientific_name
strain
```

should be unique when strain is present.

Multiple strain-specific records for the same species are permitted and expected.

---

# Table: gene

Stores organism-specific genes.

## Columns

```text
id                      UUID PRIMARY KEY

organism_id             UUID NOT NULL REFERENCES organism(id)

symbol                  VARCHAR
systematic_name         VARCHAR
name                    VARCHAR
description             TEXT

sgd_id                  VARCHAR
ncbi_gene_id            VARCHAR
uniprot_id              VARCHAR
kegg_gene_id            VARCHAR

chromosome              VARCHAR

aliases_json            JSONB

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Constraints

At least one of the following should normally be present:

```text
symbol
systematic_name
ncbi_gene_id
sgd_id
```

Uniqueness should be enforced where reliable external identifiers exist.

---

# Table: protein

Stores protein products associated with genes.

A protein record must not assume that every gene corresponds to exactly one active enzyme.

## Columns

```text
id                      UUID PRIMARY KEY

gene_id                 UUID REFERENCES gene(id)
organism_id             UUID NOT NULL REFERENCES organism(id)

name                    VARCHAR NOT NULL
uniprot_id              VARCHAR
ec_number               VARCHAR

subunit_state           VARCHAR
localization_consensus  VARCHAR

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

---

# Table: enzyme_complex

Stores multi-subunit enzyme complexes.

## Columns

```text
id                      UUID PRIMARY KEY

organism_id             UUID NOT NULL REFERENCES organism(id)

name                    VARCHAR NOT NULL
description             TEXT

stoichiometry_json      JSONB

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

---

# Table: enzyme_complex_member

Associates proteins with enzyme complexes.

## Columns

```text
id                      UUID PRIMARY KEY

complex_id              UUID NOT NULL REFERENCES enzyme_complex(id)
protein_id              UUID NOT NULL REFERENCES protein(id)

stoichiometry           NUMERIC
required                BOOLEAN NOT NULL DEFAULT TRUE
```

## Constraints

The combination

```text
complex_id
protein_id
```

must be unique.

---

# Table: compound

Stores normalized chemical species.

## Columns

```text
id                      UUID PRIMARY KEY

canonical_name          VARCHAR NOT NULL

formula                 VARCHAR
charge                  INTEGER
molecular_weight        NUMERIC

chebi_id                VARCHAR
kegg_compound_id        VARCHAR
pubchem_cid             VARCHAR
metacyc_id              VARCHAR

inchi                   TEXT
inchikey                VARCHAR
smiles                  TEXT

is_generic              BOOLEAN NOT NULL DEFAULT FALSE

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Notes

Compounds with different protonation states or chemically distinct molecular forms must not be merged merely because they have similar names.

---

# Table: compound_synonym

Stores synonyms for compounds.

## Columns

```text
id                      UUID PRIMARY KEY

compound_id             UUID NOT NULL REFERENCES compound(id)

synonym                 VARCHAR NOT NULL
source                  VARCHAR
```

## Constraints

The combination

```text
compound_id
synonym
```

must be unique.

---

# Table: compartment

Stores cellular compartments.

## Columns

```text
id                      UUID PRIMARY KEY

organism_id             UUID REFERENCES organism(id)

name                    VARCHAR NOT NULL
abbreviation            VARCHAR
ontology_id             VARCHAR

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Examples

```text
cytosol
mitochondrial matrix
mitochondrial inner membrane
peroxisome
endoplasmic reticulum
lipid droplet
nucleus
extracellular
```

---

# Table: reaction

Stores biochemical reactions.

The reaction table must not encode stoichiometry as a free-text reaction equation.

Stoichiometry must be represented through `reaction_participant`.

## Columns

```text
id                      UUID PRIMARY KEY

internal_id             VARCHAR NOT NULL UNIQUE
name                    VARCHAR NOT NULL

organism_id             UUID REFERENCES organism(id)

reversible              BOOLEAN

reaction_type           VARCHAR
ec_number               VARCHAR

kegg_reaction_id        VARCHAR
metacyc_reaction_id     VARCHAR
rhea_id                 VARCHAR

balanced_mass           BOOLEAN
balanced_charge         BOOLEAN

status                  VARCHAR
curation_state          CurationState NOT NULL DEFAULT PROPOSED

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Internal ID format

For the initial yeast free-fatty-acid project, reaction IDs should follow:

```text
FFA_R0001
FFA_R0002
FFA_R0003
...
```

Reaction IDs must remain stable after creation.

---

# Table: reaction_participant

Stores reactants, products, and modifiers.

## Columns

```text
id                      UUID PRIMARY KEY

reaction_id             UUID NOT NULL REFERENCES reaction(id)

compound_id             UUID NOT NULL REFERENCES compound(id)

compartment_id          UUID REFERENCES compartment(id)

role                    ReactionParticipantRole NOT NULL

stoichiometry           NUMERIC NOT NULL
```

## Rules

Stoichiometry must always be positive.

Reaction direction is determined by `role`.

Do not encode negative stoichiometric values.

---

# Table: reaction_enzyme

Associates reactions with catalytic proteins or enzyme complexes.

## Columns

```text
id                      UUID PRIMARY KEY

reaction_id             UUID NOT NULL REFERENCES reaction(id)

protein_id              UUID REFERENCES protein(id)
complex_id              UUID REFERENCES enzyme_complex(id)

relationship            VARCHAR NOT NULL

confidence_summary      NUMERIC

notes                   TEXT
```

## Rules

Exactly one of the following should normally be populated:

```text
protein_id
complex_id
```

Valid relationship examples include:

```text
CATALYZES
REQUIRED_FOR
PUTATIVE_CATALYST
ISOENZYME
```

---

# Table: publication

Stores publication metadata.

## Columns

```text
id                      UUID PRIMARY KEY

pmid                    VARCHAR
pmcid                   VARCHAR
doi                     VARCHAR

title                   TEXT NOT NULL
journal                 VARCHAR
year                    INTEGER

authors_json            JSONB

abstract                TEXT

open_access             BOOLEAN
full_text_available     BOOLEAN

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Constraints

PMID, PMCID, and DOI should each be unique when present.

---

# Table: claim

This is a central table.

A claim represents one scientific assertion.

Examples:

```text
ACC1 localizes to cytosol

SNF1 inhibits ACC1

OLE1 catalyzes reaction FFA_R0042

Km(Acc1, acetyl-CoA) = 0.4 mM
```

## Columns

```text
id                      UUID PRIMARY KEY

subject_type            VARCHAR NOT NULL
subject_id              UUID

predicate               VARCHAR NOT NULL

object_type             VARCHAR
object_id               UUID

value_text              TEXT
value_numeric           NUMERIC
unit                    VARCHAR

organism_id             UUID REFERENCES organism(id)

strain                  VARCHAR

claim_category          VARCHAR

status                  ClaimStatus NOT NULL DEFAULT UNKNOWN

confidence_score        NUMERIC
confidence_class        ConfidenceClass

created_by              VARCHAR

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Notes

The schema intentionally supports both entity-to-entity claims and scalar claims.

Examples:

Entity claim:

```text
subject_type = protein
predicate = LOCALIZED_IN
object_type = compartment
```

Scalar claim:

```text
subject_type = protein
predicate = KM
value_numeric = 0.4
unit = mM
```

Kinetic data should still be stored in the dedicated `kinetic_measurement` table.

The claim table is the semantic representation.

---

# Table: evidence

Associates scientific evidence with claims.

Every supported scientific claim must have at least one evidence record.

## Columns

```text
id                      UUID PRIMARY KEY

claim_id                UUID NOT NULL REFERENCES claim(id)

publication_id          UUID REFERENCES publication(id)

source_type             SourceType NOT NULL

source_id               VARCHAR

database_name           VARCHAR
database_accession      VARCHAR

evidence_type           EvidenceType NOT NULL

organism                VARCHAR
strain                  VARCHAR

experimental_system     TEXT
assay_type              TEXT

directness              VARCHAR

quoted_support          TEXT
curator_summary         TEXT NOT NULL

page                    VARCHAR
figure                  VARCHAR
table_reference         VARCHAR

date_accessed           DATE

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Rules

`quoted_support` is optional.

The database must not depend on long verbatim excerpts from publications.

`curator_summary` should be the primary human-readable description.

---

# Table: experimental_condition

Stores biological or experimental context.

## Columns

```text
id                      UUID PRIMARY KEY

medium                  VARCHAR

carbon_source           VARCHAR
carbon_concentration    NUMERIC
carbon_concentration_unit VARCHAR

nitrogen_source         VARCHAR

oxygen_status           VARCHAR

temperature_c           NUMERIC
ph                      NUMERIC

growth_phase            VARCHAR
growth_rate             NUMERIC
growth_rate_unit        VARCHAR

culture_mode            VARCHAR

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Examples

```text
YPD
synthetic complete medium
glucose-limited chemostat
aerobic batch culture
stationary phase
mid-log phase
```

---

# Table: evidence_condition

Many-to-many relationship between evidence records and experimental conditions.

## Columns

```text
id                      UUID PRIMARY KEY

evidence_id             UUID NOT NULL REFERENCES evidence(id)

experimental_condition_id
                        UUID NOT NULL REFERENCES experimental_condition(id)
```

## Constraints

The combination must be unique.

---

# Table: kinetic_measurement

Stores individual kinetic measurements.

This table must never contain averaged values derived from multiple papers unless explicitly marked as derived data.

## Columns

```text
id                      UUID PRIMARY KEY

reaction_id             UUID REFERENCES reaction(id)

protein_id              UUID REFERENCES protein(id)
complex_id              UUID REFERENCES enzyme_complex(id)

parameter_type          VARCHAR NOT NULL

parameter_value         NUMERIC NOT NULL
unit                    VARCHAR NOT NULL

original_value          NUMERIC
original_unit           VARCHAR

normalized_value        NUMERIC
normalized_unit         VARCHAR

substrate_id            UUID REFERENCES compound(id)

organism_id             UUID REFERENCES organism(id)

strain                  VARCHAR

temperature_c           NUMERIC
ph                      NUMERIC
ionic_strength          NUMERIC
ionic_strength_unit     VARCHAR

buffer                   TEXT

enzyme_concentration    NUMERIC
enzyme_concentration_unit VARCHAR

substrate_concentrations_json JSONB

protein_form            VARCHAR
purification_state      VARCHAR

assay_type              VARCHAR

publication_id          UUID REFERENCES publication(id)
evidence_id             UUID REFERENCES evidence(id)

confidence_score        NUMERIC
confidence_class        ConfidenceClass

model_applicability_score NUMERIC

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Allowed parameter types

Examples include:

```text
Km
kcat
Vmax
Ki
Ka
Kd
Hill_coefficient
equilibrium_constant
```

Do not restrict the database so tightly that future parameter types cannot be added.

---

# Table: regulatory_interaction

Stores biochemical, signaling, transcriptional, and post-translational regulation.

## Columns

```text
id                      UUID PRIMARY KEY

regulator_type          VARCHAR NOT NULL
regulator_id            UUID

target_type             VARCHAR NOT NULL
target_id               UUID

effect                  RegulatoryEffect NOT NULL

mechanism               TEXT

direct                  BOOLEAN
condition_dependent     BOOLEAN

organism_id             UUID REFERENCES organism(id)

claim_id                UUID REFERENCES claim(id)

curation_state          CurationState NOT NULL DEFAULT PROPOSED

notes                   TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Examples

```text
Snf1 -> Acc1
effect = PHOSPHORYLATION

Snf1 -> Acc1
effect = INHIBITION
```

These may represent separate claims if the evidence differs.

---

# Table: modeling_assumption

Stores assumptions required for downstream model construction.

Assumptions must never be stored as evidence-backed facts.

## Columns

```text
id                      UUID PRIMARY KEY

subject_type            VARCHAR NOT NULL
subject_id              UUID

assumption              TEXT NOT NULL

reason                  TEXT

required_for_model      BOOLEAN NOT NULL DEFAULT FALSE

confidence              NUMERIC

human_approved          BOOLEAN NOT NULL DEFAULT FALSE

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Examples

```text
Assume reaction is irreversible under glucose-rich conditions.

Assume cytosolic metabolite concentrations are spatially uniform.

Use mammalian Km value temporarily because no yeast value is available.
```

Such assumptions must be clearly labeled and must never silently replace missing biological data.

---

# Table: knowledge_gap

Stores missing information that limits model construction or predictive accuracy.

## Columns

```text
id                      UUID PRIMARY KEY

subject_type            VARCHAR NOT NULL
subject_id              UUID

missing_information     TEXT NOT NULL

importance              VARCHAR

model_impact            TEXT

suggested_experiment    TEXT

priority                INTEGER

status                  VARCHAR

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
updated_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Suggested priority scale

```text
1 = critical
2 = high
3 = moderate
4 = low
5 = informational
```

---

# Table: external_record

Stores raw or normalized responses from external databases and APIs.

This table provides reproducibility and auditability.

## Columns

```text
id                      UUID PRIMARY KEY

source                  SourceType NOT NULL

external_id             VARCHAR

retrieval_date          TIMESTAMP WITH TIME ZONE NOT NULL

request_url             TEXT

raw_response_hash       VARCHAR NOT NULL

raw_response_json       JSONB

raw_response_text       TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Rules

External records should be append-only where practical.

The system must not silently replace prior retrieved records.

---

# Table: source_cross_reference

Stores external identifiers associated with internal entities.

## Columns

```text
id                      UUID PRIMARY KEY

entity_type             VARCHAR NOT NULL
entity_id               UUID NOT NULL

source                  SourceType NOT NULL

external_id             VARCHAR NOT NULL

url                     TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Constraints

The combination

```text
entity_type
entity_id
source
external_id
```

must be unique.

---

# Table: review_event

Stores review history.

## Columns

```text
id                      UUID PRIMARY KEY

entity_type             VARCHAR NOT NULL
entity_id               UUID NOT NULL

previous_state          CurationState
new_state               CurationState NOT NULL

reviewer_type           VARCHAR NOT NULL
reviewer_id             VARCHAR

comment                 TEXT

created_at              TIMESTAMP WITH TIME ZONE NOT NULL
```

## Examples

```text
reviewer_type = AI_CRITIC
reviewer_type = HUMAN
reviewer_type = DETERMINISTIC_VALIDATOR
```

This table provides an audit trail of curation decisions.

---

# Confidence Representation

Confidence must normally be associated with claims rather than entire biological entities.

For example, the same reaction may have:

```text
reaction existence      98
enzyme assignment       96
compartment             84
reversibility           61
kinetics                47
```

The database must allow these values to remain distinct.

A single aggregate reaction confidence score should not replace claim-level confidence.

---

# Confidence Score Range

Confidence scores shall use:

```text
0 through 100
```

Confidence classes shall map as follows:

```text
90–100     VERY_HIGH

75–89      HIGH

50–74      MODERATE

0–49       LOW

NULL       UNKNOWN
```

Confidence score represents curation strength, not mathematical probability.

---

# Relationships

The primary entity relationships are:

```text
organism
   |
   +-- gene
         |
         +-- protein
               |
               +-- enzyme_complex_member
                        |
                        +-- enzyme_complex
```

Reaction structure:

```text
reaction
   |
   +-- reaction_participant
   |       |
   |       +-- compound
   |       |
   |       +-- compartment
   |
   +-- reaction_enzyme
           |
           +-- protein
           |
           +-- enzyme_complex
```

Evidence structure:

```text
claim
   |
   +-- evidence
           |
           +-- publication
           |
           +-- experimental_condition
```

Kinetics:

```text
kinetic_measurement
   |
   +-- reaction
   +-- protein / enzyme_complex
   +-- compound
   +-- publication
   +-- evidence
```

Regulation:

```text
regulatory_interaction
   |
   +-- claim
           |
           +-- evidence
```

---

# Required Indexes

Create indexes for commonly searched fields.

At minimum:

```text
organism.ncbi_taxonomy_id

gene.symbol
gene.systematic_name
gene.sgd_id
gene.ncbi_gene_id

protein.uniprot_id
protein.ec_number

compound.canonical_name
compound.chebi_id
compound.kegg_compound_id
compound.inchikey

reaction.internal_id
reaction.kegg_reaction_id
reaction.rhea_id
reaction.ec_number

publication.pmid
publication.pmcid
publication.doi

claim.subject_type
claim.subject_id
claim.predicate

evidence.claim_id
evidence.source_type

kinetic_measurement.parameter_type
kinetic_measurement.reaction_id
kinetic_measurement.protein_id

source_cross_reference.external_id
```

JSONB indexes may be added later based on observed query patterns.

---

# Delete Behavior

Scientific provenance must be protected.

Use conservative delete behavior.

Prefer:

```text
ON DELETE RESTRICT
```

for scientific records.

Cascade deletion may be permitted only for subordinate records that have no independent scientific meaning, such as:

```text
compound_synonym
enzyme_complex_member
```

Publication, evidence, claim, and kinetic records must never disappear automatically because another entity was deleted.

Hard deletion of curated scientific records should be rare.

Soft deletion or deprecation should be preferred where appropriate.

---

# Audit Requirements

All major mutable tables must include timestamps.

Changes to curation state must create a `review_event`.

The application must preserve:

```text
who made the change
when the change occurred
what changed
why it changed
```

Future versions may implement a complete event-sourcing or history table system, but the first version must at least preserve review-state history and source provenance.

---

# Scientific Integrity Constraints

The application layer must enforce the following rules.

## Rule 1

A claim with status `SUPPORTED` must have at least one evidence record.

## Rule 2

An LLM response must never be represented as an evidence source.

## Rule 3

A kinetic measurement must preserve its original unit.

## Rule 4

A normalized kinetic value must not replace the original value.

## Rule 5

Measurements from different strains must remain separate.

## Rule 6

Measurements from different assay conditions must remain separate.

## Rule 7

Conflicting claims must coexist.

## Rule 8

A conflict must not be resolved by deleting one side.

## Rule 9

Unknown values must remain NULL.

## Rule 10

Modeling assumptions must not be stored as claims with experimental evidence.

## Rule 11

Reaction stoichiometry must be represented structurally.

## Rule 12

Agent 2 must not receive records in `REJECTED` state.

## Rule 13

Records in `PROPOSED` or `NEEDS_REVIEW` state must not be exported as approved biological knowledge unless explicitly requested.

---

# SQLAlchemy Requirements

Implement SQLAlchemy models using SQLAlchemy 2.x declarative style.

Use:

```python
Mapped[]
mapped_column()
relationship()
```

Avoid legacy SQLAlchemy query syntax.

Relationships must be explicitly defined.

Use bidirectional relationships where useful.

Avoid eager-loading large collections by default.

---

# Pydantic Requirements

Create Pydantic models for:

```text
Create
Read
Update
Export
```

for major entities.

API schemas must not expose internal implementation details unnecessarily.

Validation should reject invalid enum values and structurally invalid input.

---

# Alembic Requirements

Every schema change must include an Alembic migration.

Cursor must never modify SQLAlchemy models without creating or updating the corresponding migration.

Migrations must be reversible whenever practical.

---

# Initial Seed Data

The first migration or seed script should create standard compartments for *Saccharomyces cerevisiae*:

```text
cytosol
mitochondrial matrix
mitochondrial intermembrane space
mitochondrial inner membrane
mitochondrial outer membrane
peroxisome
endoplasmic reticulum
Golgi
lipid droplet
nucleus
vacuole
plasma membrane
extracellular
```

Do not seed reactions or kinetic parameters until they have gone through the curation pipeline.

---

# Required Database Tests

The implementation must include tests for at least the following:

```text
test_create_organism

test_create_gene

test_gene_requires_organism

test_create_compound

test_compound_synonym_uniqueness

test_create_reaction

test_reaction_participant_requires_positive_stoichiometry

test_reaction_enzyme_requires_valid_target

test_supported_claim_requires_evidence

test_conflicting_claims_can_coexist

test_kinetic_measurements_are_not_merged

test_original_kinetic_unit_is_preserved

test_strain_specific_measurements_remain_distinct

test_modeling_assumption_is_not_evidence

test_publication_identifier_uniqueness

test_review_event_created_on_state_change

test_external_records_preserve_retrieval_history

test_rejected_records_not_exported

test_unknown_values_remain_null
```

---

# Deferred Features

Do not implement the following in the initial database unless required by another specification:

```text
graph database replication

vector embeddings

full document storage

full publication PDF storage

knowledge graph inference

automatic parameter estimation

ODE models

Antimony models

SBML models

simulation results

experimental-design optimization
```

The schema should remain extensible enough to support these later.

---

# Definition of Done

The database layer is complete when:

1. All tables in this document exist as SQLAlchemy models.

2. Alembic can build the database from an empty PostgreSQL instance.

3. All foreign-key relationships are functional.

4. All enumerations are validated.

5. Core scientific integrity constraints are enforced.

6. Seed compartments can be loaded.

7. Required database tests pass.

8. `pytest` passes without errors.

9. `ruff` reports no errors.

10. Database models contain type hints.

11. Public classes and important fields are documented.

12. No biological reaction or kinetic data have been invented or hard-coded as placeholder content.

---

# Final Implementation Rule

When this specification conflicts with developer convenience, preserve scientific provenance and uncertainty.

The primary objective of the database is not to minimize tables or code.

The primary objective is to ensure that every downstream mechanistic model can answer:

```text
Where did this biological claim come from?

What evidence supports it?

Under what conditions was it measured?

How confident are we?

What conflicting evidence exists?

What assumptions were introduced?
```

If the database cannot answer those questions, the schema is incomplete.
