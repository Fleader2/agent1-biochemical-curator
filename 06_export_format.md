# Agent 1: Biochemical Evidence Curator
## Export Format Specification

**Document:** `docs/06_export_format.md`

**Version:** 0.1

**Status:** Implementation Specification

---

# Purpose

This document defines the canonical export format produced by Agent 1 for downstream modeling agents.

The primary consumer is Agent 2, which will construct mechanistic models in the Antimony language.

The export format must preserve:

- biological entities,
- reaction topology,
- stoichiometry,
- cellular compartments,
- enzyme assignments,
- kinetic measurements,
- regulation,
- evidence provenance,
- confidence,
- assumptions,
- uncertainty,
- knowledge gaps,
- curation state.

The export must not transform uncertain biological information into false certainty.

The export is therefore a scientific handoff contract, not merely a serialization format.

---

# Primary Export Format

Version 0.1 shall use JSON as the canonical exchange format.

The primary file should be:

```text id="h7dy4m"
agent1_export.json
```

The system may also produce separate JSON or CSV files for convenience, but the canonical representation is the complete JSON export defined in this document.

---

# Export Version

Every export must include a schema version.

Example:

```json id="7y0dx6"
{
  "schema_version": "0.1"
}
```

Breaking changes to the structure require a new schema version.

Agent 2 must reject unsupported schema versions rather than silently attempting to interpret them.

---

# General Export Principles

The following rules are mandatory.

1. Stable internal identifiers must be preserved.

2. Relationships must be expressed through identifiers rather than names alone.

3. Unknown values must be represented as `null`.

4. Unsupported values must not be filled with defaults.

5. Modeling assumptions must remain distinct from evidence-backed knowledge.

6. Conflicting evidence must remain visible.

7. Kinetic measurements must remain separate.

8. Experimental conditions must remain attached to measurements.

9. Confidence must remain claim-specific.

10. Curation state must remain visible.

11. Evidence links must be preserved.

12. Human approval state must remain visible.

13. LLM hypotheses must never appear as accepted biological knowledge.

14. Rejected records must not appear in default production exports.

---

# Default Export Policy

The default export must include only biological records with:

```text id="f171uj"
HUMAN_ACCEPTED
```

curation state.

The default export must exclude:

```text id="a3g7eq"
PROPOSED
MACHINE_REVIEWED
NEEDS_REVIEW
REJECTED
```

unless an explicit non-production export mode is requested.

---

# Research Export Mode

A research or debugging export may optionally include:

```text id="mz194s"
MACHINE_REVIEWED
NEEDS_REVIEW
```

records.

Such records must preserve their curation state.

Agent 2 must not silently treat them as equivalent to `HUMAN_ACCEPTED`.

---

# Rejected Records

Records in:

```text id="nlq7lt"
REJECTED
```

must not be exported as valid biological knowledge.

They may optionally be included in a separate audit section if explicitly requested.

---

# Top-Level Structure

The canonical export should have the following structure:

```json id="e3xx40"
{
  "schema_version": "0.1",
  "metadata": {},
  "organisms": [],
  "compartments": [],
  "compounds": [],
  "genes": [],
  "proteins": [],
  "enzyme_complexes": [],
  "reactions": [],
  "claims": [],
  "kinetic_measurements": [],
  "regulatory_interactions": [],
  "evidence": [],
  "publications": [],
  "modeling_assumptions": [],
  "knowledge_gaps": [],
  "conflicts": []
}
```

---

# Metadata Object

The `metadata` object must describe the provenance of the export itself.

Required structure:

```json id="ndrdkr"
{
  "export_id": "uuid",
  "generated_at": "2026-08-31T00:00:00Z",
  "agent1_version": "0.1.0",
  "schema_version": "0.1",
  "prompt_version": "0.1",
  "llm_provider": "provider-name",
  "llm_model": "model-name",
  "software_commit": "git-commit-hash",
  "curation_states_included": [
    "HUMAN_ACCEPTED"
  ],
  "organism_scope": [
    "uuid"
  ],
  "biological_scope": "Free fatty acid metabolism",
  "export_mode": "PRODUCTION"
}
```

Allowed export modes:

```text id="w38u90"
PRODUCTION
RESEARCH
AUDIT
```

---

# Organism Export

Each organism record should contain:

```json id="3r1pdb"
{
  "id": "uuid",
  "scientific_name": "Saccharomyces cerevisiae",
  "common_name": "budding yeast",
  "ncbi_taxonomy_id": 4932,
  "strain": "S288C",
  "kegg_code": "sce",
  "biocyc_id": null
}
```

Organism and strain identity must remain explicit.

Do not export only the species name if strain-specific data are present elsewhere.

---

# Compartment Export

Each compartment should contain:

```json id="3fvmmv"
{
  "id": "uuid",
  "organism_id": "uuid",
  "name": "cytosol",
  "abbreviation": "cyt",
  "ontology_id": null
}
```

Compartment IDs must be used by reaction participants and localization claims.

---

# Compound Export

Each compound should contain:

```json id="cdx4qp"
{
  "id": "uuid",
  "canonical_name": "acetyl-CoA",
  "formula": "C23H38N7O17P3S",
  "charge": -4,
  "molecular_weight": null,
  "chebi_id": "CHEBI:15351",
  "kegg_compound_id": null,
  "pubchem_cid": null,
  "metacyc_id": null,
  "inchi": null,
  "inchikey": null,
  "smiles": null,
  "is_generic": false,
  "synonyms": [
    "AcCoA"
  ]
}
```

Unknown chemical properties must remain `null`.

---

# Gene Export

Each gene should contain:

```json id="1msxcn"
{
  "id": "uuid",
  "organism_id": "uuid",
  "symbol": "ACC1",
  "systematic_name": "YNR016C",
  "name": "acetyl-CoA carboxylase",
  "sgd_id": null,
  "ncbi_gene_id": null,
  "uniprot_id": null,
  "kegg_gene_id": null,
  "aliases": []
}
```

---

# Protein Export

Each protein should contain:

```json id="49zpwj"
{
  "id": "uuid",
  "gene_id": "uuid",
  "organism_id": "uuid",
  "name": "Acetyl-CoA carboxylase",
  "uniprot_id": null,
  "ec_number": "6.4.1.2",
  "subunit_state": null,
  "localization_consensus": null
}
```

`localization_consensus` is informational only.

Agent 2 should rely on explicit accepted localization claims where available.

---

# Enzyme Complex Export

Each enzyme complex should contain:

```json id="w31c6m"
{
  "id": "uuid",
  "organism_id": "uuid",
  "name": "Fatty acid synthase",
  "description": null,
  "members": [
    {
      "protein_id": "uuid",
      "stoichiometry": 6,
      "required": true
    },
    {
      "protein_id": "uuid",
      "stoichiometry": 6,
      "required": true
    }
  ]
}
```

Complex composition must not be inferred by Agent 2.

---

# Reaction Export

Each reaction must contain structured participants.

Example:

```json id="6afj9h"
{
  "id": "uuid",
  "internal_id": "FFA_R0001",
  "name": "Acetyl-CoA carboxylase reaction",
  "organism_id": "uuid",
  "reaction_type": "BIOCHEMICAL",
  "ec_number": "6.4.1.2",
  "kegg_reaction_id": null,
  "metacyc_reaction_id": null,
  "rhea_id": null,
  "reversible": false,
  "balanced_mass": true,
  "balanced_charge": true,
  "curation_state": "HUMAN_ACCEPTED",
  "participants": [
    {
      "compound_id": "uuid-acetylcoa",
      "compartment_id": "uuid-cytosol",
      "role": "REACTANT",
      "stoichiometry": 1
    },
    {
      "compound_id": "uuid-atp",
      "compartment_id": "uuid-cytosol",
      "role": "REACTANT",
      "stoichiometry": 1
    },
    {
      "compound_id": "uuid-bicarbonate",
      "compartment_id": "uuid-cytosol",
      "role": "REACTANT",
      "stoichiometry": 1
    },
    {
      "compound_id": "uuid-malonylcoa",
      "compartment_id": "uuid-cytosol",
      "role": "PRODUCT",
      "stoichiometry": 1
    },
    {
      "compound_id": "uuid-adp",
      "compartment_id": "uuid-cytosol",
      "role": "PRODUCT",
      "stoichiometry": 1
    },
    {
      "compound_id": "uuid-pi",
      "compartment_id": "uuid-cytosol",
      "role": "PRODUCT",
      "stoichiometry": 1
    }
  ],
  "enzymes": [
    {
      "protein_id": "uuid",
      "complex_id": null,
      "relationship": "CATALYZES"
    }
  ],
  "claim_ids": [],
  "kinetic_measurement_ids": [],
  "regulatory_interaction_ids": [],
  "modeling_assumption_ids": [],
  "knowledge_gap_ids": []
}
```

---

# Reaction Requirements

Agent 2 must not infer missing reaction participants.

If stoichiometry is incomplete, the export must preserve that uncertainty.

For example:

```json id="ti37d1"
{
  "balanced_mass": null,
  "balanced_charge": null
}
```

is preferable to an unsupported correction.

---

# Reversibility Export

Reversibility must be represented using:

```text id="55wp7q"
true
false
null
```

where:

```text id="0b26p4"
true = supported reversible
false = supported irreversible
null = unresolved
```

Agent 2 must not convert `null` to `false`.

---

# Claim Export

Claims should be exported when needed to preserve confidence, provenance, conflict, or model relevance.

Example:

```json id="ybpa6u"
{
  "id": "uuid",
  "subject_type": "reaction",
  "subject_id": "uuid",
  "predicate": "LOCALIZED_IN",
  "object_type": "compartment",
  "object_id": "uuid",
  "value_text": null,
  "value_numeric": null,
  "unit": null,
  "organism_id": "uuid",
  "strain": "S288C",
  "claim_category": "LOCALIZATION",
  "status": "SUPPORTED",
  "confidence_score": 91,
  "confidence_class": "VERY_HIGH",
  "curation_state": "HUMAN_ACCEPTED",
  "evidence_ids": [
    "uuid"
  ],
  "conflict_ids": []
}
```

---

# Confidence Export

Confidence must be preserved at the claim level.

Do not replace claim-specific confidence with one reaction-wide confidence score.

For convenience, a reaction may include a derived summary:

```json id="g0z4em"
{
  "confidence_summary": {
    "reaction_identity": 96,
    "stoichiometry": 94,
    "enzyme_assignment": 97,
    "compartment": 91,
    "reversibility": 84,
    "kinetics": 62
  }
}
```

This summary must be derived from underlying claims and must not replace them.

---

# Confidence Meaning

Agent 2 must interpret confidence as:

```text id="pksqhw"
curation strength
```

not:

```text id="dq1k84"
mathematical probability that the claim is true
```

---

# Kinetic Measurement Export

Every kinetic measurement must remain independent.

Example:

```json id="5l4tk6"
{
  "id": "uuid",
  "reaction_id": "uuid",
  "protein_id": "uuid",
  "complex_id": null,
  "parameter_type": "Km",
  "parameter_value": 0.42,
  "unit": "mM",
  "original_value": 420,
  "original_unit": "uM",
  "normalized_value": 0.42,
  "normalized_unit": "mM",
  "substrate_id": "uuid",
  "organism_id": "uuid",
  "strain": "S288C",
  "temperature_c": 30.0,
  "ph": 7.0,
  "ionic_strength": null,
  "ionic_strength_unit": null,
  "buffer": "phosphate buffer",
  "enzyme_concentration": null,
  "enzyme_concentration_unit": null,
  "substrate_concentrations": {},
  "protein_form": "native",
  "purification_state": "purified enzyme",
  "assay_type": "enzyme activity assay",
  "publication_id": "uuid",
  "evidence_id": "uuid",
  "measurement_confidence_score": 91,
  "measurement_confidence_class": "VERY_HIGH",
  "model_applicability_score": 78
}
```

---

# Multiple Kinetic Measurements

Agent 1 may export:

```json id="nwoiak"
[
  {
    "parameter_type": "Km",
    "normalized_value": 0.42,
    "normalized_unit": "mM",
    "temperature_c": 30,
    "ph": 7.0
  },
  {
    "parameter_type": "Km",
    "normalized_value": 1.2,
    "normalized_unit": "mM",
    "temperature_c": 25,
    "ph": 8.0
  }
]
```

Agent 1 must not export:

```json id="squ8ky"
{
  "Km": 0.81
}
```

as an automatically averaged value.

Any derived or selected kinetic value must be created explicitly by a downstream modeling step and must preserve the source measurements used.

---

# Missing Kinetic Parameters

Missing parameters must remain absent or `null`.

Agent 1 must not create placeholder values such as:

```text id="v7ttip"
Km = 1.0
kcat = 1.0
Vmax = 1.0
```

for convenience.

If Agent 2 requires a value, it must create a modeling assumption or parameter-estimation task.

---

# Regulatory Interaction Export

Example:

```json id="9yk6qg"
{
  "id": "uuid",
  "regulator_type": "protein",
  "regulator_id": "uuid-snf1",
  "target_type": "protein",
  "target_id": "uuid-acc1",
  "effect": "PHOSPHORYLATION",
  "mechanism": "Direct phosphorylation",
  "direct": true,
  "condition_dependent": true,
  "organism_id": "uuid",
  "claim_id": "uuid",
  "curation_state": "HUMAN_ACCEPTED"
}
```

If phosphorylation and functional inhibition are independently supported, export them as separate regulatory interactions.

Do not collapse:

```text id="er9cpw"
Snf1 phosphorylates Acc1
```

and:

```text id="n32ma1"
Acc1 activity decreases after Snf1-dependent phosphorylation
```

into one unsupported mechanistic statement.

---

# Evidence Export

Evidence records must remain linked to claims.

Example:

```json id="ak26m7"
{
  "id": "uuid",
  "claim_id": "uuid",
  "publication_id": "uuid",
  "source_type": "PUBMED",
  "source_id": "12345678",
  "database_name": null,
  "database_accession": null,
  "evidence_type": "DIRECT_BIOCHEMICAL",
  "organism": "Saccharomyces cerevisiae",
  "strain": "S288C",
  "experimental_system": "purified native enzyme",
  "assay_type": "enzyme activity assay",
  "directness": "DIRECT",
  "curator_summary": "The study directly measured enzyme activity.",
  "page": "123",
  "figure": null,
  "table_reference": "Table 2",
  "date_accessed": "2026-08-31"
}
```

---

# Publication Export

Example:

```json id="cjesxa"
{
  "id": "uuid",
  "pmid": "12345678",
  "pmcid": null,
  "doi": "10.xxxx/example",
  "title": "Example publication",
  "journal": "Example Journal",
  "year": 2001,
  "authors": [
    "Author A",
    "Author B"
  ],
  "open_access": false,
  "full_text_available": false
}
```

Do not export fabricated identifiers.

---

# Modeling Assumption Export

Assumptions must be explicit.

Example:

```json id="0ixb7y"
{
  "id": "uuid",
  "subject_type": "reaction",
  "subject_id": "uuid",
  "assumption": "Treat this reaction as irreversible under glucose-rich conditions.",
  "reason": "Required for initial ODE construction because physiological reversibility is unresolved.",
  "required_for_model": true,
  "confidence": 60,
  "human_approved": true
}
```

Agent 2 must never treat a modeling assumption as experimentally established biology.

---

# Unapproved Assumptions

In production export mode:

```text id="a1aqeh"
human_approved = false
```

assumptions required for modeling should not silently become active model assumptions.

They may be included as unresolved items, but must not be automatically applied.

---

# Knowledge Gap Export

Example:

```json id="9vqb68"
{
  "id": "uuid",
  "subject_type": "reaction",
  "subject_id": "uuid",
  "missing_information": "No organism-specific kcat was found.",
  "importance": "HIGH",
  "model_impact": "Limits prediction of maximal reaction rate.",
  "suggested_experiment": "Measure turnover of purified enzyme under physiological conditions.",
  "priority": 2,
  "status": "OPEN"
}
```

Knowledge gaps are not errors.

They are explicit descriptions of uncertainty relevant to modeling.

---

# Conflict Export

Conflicts must be represented explicitly.

Example:

```json id="nwysfu"
{
  "id": "uuid",
  "claim_ids": [
    "uuid-claim-a",
    "uuid-claim-b"
  ],
  "conflict_type": "LOCALIZATION",
  "status": "UNRESOLVED",
  "summary": "One study reports cytosolic localization while another reports mitochondrial localization.",
  "context_explanation": null,
  "requires_human_review": true
}
```

If an apparent conflict is explained by context, record that explanation.

Example:

```json id="jn2lwn"
{
  "status": "CONTEXT_RESOLVED",
  "context_explanation": "Localization differs between logarithmic and stationary growth phases."
}
```

---

# Unknown Values

Unknown values must use JSON:

```text id="e8jk4r"
null
```

Do not use ambiguous strings such as:

```text id="8unkvz"
"unknown"
"N/A"
"?"
"not available"
```

for schema fields that support `null`.

Human-readable notes may still explain why a value is unknown.

---

# Enumerated Values

Enumerations must be exported exactly as defined by the API and database schema.

Examples:

```text id="0n2d3g"
HUMAN_ACCEPTED
DIRECT_BIOCHEMICAL
VERY_HIGH
REACTANT
PRODUCT
ACTIVATION
INHIBITION
```

Agent 2 must reject unknown enum values unless explicitly designed for forward compatibility.

---

# Stable Identifiers

Internal UUIDs must remain stable within Agent 1.

Reaction `internal_id` values such as:

```text id="a4ooln"
FFA_R0001
```

must also remain stable once assigned.

Agent 2 should use stable identifiers rather than matching records by names.

---

# Referential Integrity

All exported references must resolve.

For example, if a reaction contains:

```json id="zdf5zw"
{
  "compound_id": "uuid-compound-1"
}
```

then `"uuid-compound-1"` must exist in the exported `compounds` collection unless the export explicitly documents partial-reference mode.

Production exports must not contain dangling references.

---

# Export Validation

Before writing an export, Agent 1 must validate:

```text id="tey4x3"
schema validity
referential integrity
allowed curation states
absence of rejected biology
absence of unsupported LLM hypotheses
kinetic measurement provenance
approved assumption status
stable identifier uniqueness
required metadata
```

If validation fails, the export operation must fail rather than emit a partially unsafe production export.

---

# Export Determinism

Where practical, exports should be deterministic.

For identical database content and configuration:

```text id="y2k5jw"
entity ordering
field ordering
normalized serialization
```

should remain stable.

This facilitates:

```text id="ktp5g0"
Git diff
testing
hashing
reproducibility
```

---

# Canonical Ordering

For deterministic output, arrays should be sorted using stable keys where practical.

Recommended order:

```text id="pkm8fe"
organisms              by scientific_name, strain, id
compartments            by name, id
compounds               by canonical_name, id
genes                   by symbol, systematic_name, id
proteins                by name, id
enzyme_complexes        by name, id
reactions               by internal_id
claims                  by subject_type, subject_id, predicate, id
kinetic_measurements    by reaction_id, parameter_type, id
regulatory_interactions by target_id, regulator_id, id
evidence                by claim_id, id
publications            by year, pmid, id
knowledge_gaps          by priority, id
```

---

# JSON Numeric Handling

Scientific numeric values must not be converted to formatted strings.

Use:

```json id="ebpeps"
{
  "parameter_value": 0.42
}
```

not:

```json id="64k1rg"
{
  "parameter_value": "0.42 mM"
}
```

Value and unit must remain separate.

---

# Precision

Do not arbitrarily round scientific measurements during export.

Preserve stored numeric precision.

If display rounding is required, it belongs in user-interface code, not the scientific export.

---

# Units

Every dimensional numeric measurement must include a unit unless the quantity is dimensionless.

Example:

```json id="nnfz47"
{
  "parameter_type": "Km",
  "normalized_value": 0.42,
  "normalized_unit": "mM"
}
```

For dimensionless values:

```json id="s6xybn"
{
  "parameter_type": "Hill_coefficient",
  "normalized_value": 2.1,
  "normalized_unit": null
}
```

---

# Derived Values

Agent 1 should avoid exporting derived modeling parameters unless they are explicitly stored as derived records.

A derived value must include:

```text id="2vfupe"
derivation method
source measurement IDs
software version
timestamp
assumption IDs
```

Derived values must never overwrite original measurements.

---

# Agent 2 Interpretation Rules

Agent 2 must follow these rules when consuming Agent 1 exports.

## Rule 1

Do not create a biological reaction that is absent from the export unless explicitly creating a modeling hypothesis.

## Rule 2

Do not replace `null` with arbitrary defaults.

## Rule 3

Do not average kinetic measurements automatically.

## Rule 4

Do not treat modeling assumptions as experimental facts.

## Rule 5

Do not treat `MACHINE_REVIEWED` records as `HUMAN_ACCEPTED`.

## Rule 6

Do not remove conflicting evidence.

## Rule 7

Do not invent missing compartments.

## Rule 8

Do not infer reaction reversibility from Antimony arrow syntax.

## Rule 9

Do not select kinetic values without recording the selection logic.

## Rule 10

Every Antimony reaction should be traceable back to one or more Agent 1 reaction IDs.

---

# Agent 2 Parameter Selection

Agent 2 may need to choose among multiple kinetic measurements.

When doing so, it must create a separate parameter-selection record containing:

```text id="ymcuu2"
selected measurement ID
alternative measurement IDs
selection criterion
organism match
strain match
temperature match
pH match
model applicability
human approval if required
```

Agent 1 must not perform this selection during export.

---

# Agent 2 Assumption Creation

If Agent 2 requires a missing parameter or unresolved mechanism, it must create an explicit modeling assumption.

Example:

```text id="sjit9m"
No kcat is available.

Agent 2 must not silently assign kcat = 1.

Instead:

create modeling assumption
or
request parameter estimation
or
create knowledge-gap dependency
```

---

# Antimony Traceability Requirements

Although Agent 1 does not generate Antimony, its export must support downstream traceability.

Every Antimony reaction generated by Agent 2 should eventually be traceable using metadata such as:

```text id="u59ekf"
agent1_reaction_id
agent1_claim_ids
agent1_kinetic_measurement_ids
agent1_assumption_ids
```

For example, Agent 2 may later generate:

```text id="d6ufmq"
# Agent1 reaction: FFA_R0001
# Evidence-backed enzyme: ACC1
# Kinetic source: <measurement UUID>

J_ACC1:
    acetylCoA + ATP + bicarbonate -> malonylCoA + ADP + Pi;
    rate_expression
```

The exact Antimony annotation format belongs in Agent 2's specification, not this document.

---

# Split Export Files

In addition to the canonical single-file JSON export, Agent 1 may optionally produce:

```text id="4x5kj0"
exports/
├── metadata.json
├── organisms.json
├── compartments.json
├── compounds.json
├── genes.json
├── proteins.json
├── enzyme_complexes.json
├── reactions.json
├── claims.json
├── kinetics.json
├── regulation.json
├── evidence.json
├── publications.json
├── assumptions.json
├── knowledge_gaps.json
└── conflicts.json
```

These files must contain the same scientific information as the canonical export.

---

# CSV Export

CSV export may be provided for human inspection.

Recommended files:

```text id="85vw7f"
reactions.csv
reaction_participants.csv
genes.csv
proteins.csv
compounds.csv
kinetic_measurements.csv
regulatory_interactions.csv
claims.csv
evidence.csv
knowledge_gaps.csv
```

CSV is not the canonical machine-to-machine format because nested relationships and provenance are more naturally represented in JSON.

---

# Export Manifest

Every multi-file export should include:

```text id="p24ip2"
manifest.json
```

Example:

```json id="8r7pae"
{
  "schema_version": "0.1",
  "export_id": "uuid",
  "generated_at": "timestamp",
  "files": [
    {
      "name": "reactions.json",
      "sha256": "hash"
    },
    {
      "name": "kinetics.json",
      "sha256": "hash"
    }
  ]
}
```

Hashes provide corruption detection and reproducibility.

---

# Export Hashing

For canonical exports, compute:

```text id="65vwgs"
SHA-256
```

after deterministic serialization where practical.

Store the hash with export metadata.

---

# Production Export Validation Rules

A `PRODUCTION` export must fail if any of the following occur:

```text id="0w2vpf"
dangling references

duplicate stable identifiers

invalid enum values

unsupported schema fields

REJECTED biology included as valid data

LLM_HYPOTHESIS included as accepted biology

required modeling assumption is unapproved but marked active

kinetic measurement lacks required provenance

claim marked SUPPORTED has no evidence

export metadata incomplete
```

---

# Research Export Validation Rules

A `RESEARCH` export may include unresolved material, but each unresolved record must retain:

```text id="4wstf7"
curation_state
claim_status
confidence
assumption state
conflict state
```

Research mode must not erase uncertainty.

---

# Audit Export

An `AUDIT` export may include:

```text id="4r5q90"
REJECTED records
review history
external retrieval records
machine hypotheses
validation warnings
critic findings
```

Audit export is intended for provenance and debugging.

It must not be used as direct input to Agent 2 without additional filtering.

---

# Export API

The API endpoint:

```text id="1fur0o"
POST /api/v1/exports
```

must support at least:

```json id="g9ndfw"
{
  "organism_id": "uuid",
  "format": "JSON",
  "export_mode": "PRODUCTION",
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

---

# Export Pydantic Models

Create Pydantic schemas for the complete export.

Recommended structure:

```text id="9au0rk"
ExportMetadata

OrganismExport

CompartmentExport

CompoundExport

GeneExport

ProteinExport

EnzymeComplexExport

ReactionParticipantExport

ReactionEnzymeExport

ReactionExport

ClaimExport

KineticMeasurementExport

RegulatoryInteractionExport

EvidenceExport

PublicationExport

ModelingAssumptionExport

KnowledgeGapExport

ConflictExport

Agent1Export
```

The final schema:

```text id="9lnj6y"
Agent1Export
```

must validate the complete export object.

---

# Export Schema File

Generate and version-control a machine-readable JSON Schema from the Pydantic model.

Recommended location:

```text id="x7i2f2"
schemas/agent1_export_v0.1.schema.json
```

This schema becomes part of the Agent 1 / Agent 2 interface contract.

---

# Agent 2 Input Validation

Before Agent 2 processes an Agent 1 export, it must validate the document against:

```text id="pvm23k"
agent1_export_v0.1.schema.json
```

If validation fails, Agent 2 must stop and report the problem.

It must not attempt to guess the intended structure.

---

# Backward Compatibility

Minor additions that do not alter existing field semantics may remain within a schema version only if Agent 2 can safely ignore unknown optional fields.

Changes that alter:

```text id="u3dijw"
field meaning
required fields
identifier semantics
relationship semantics
enum semantics
```

require a new schema version.

---

# Required Export Tests

Implement at least:

```text id="k0k1z4"
test_export_schema_valid

test_export_metadata_present

test_export_stable_ids_preserved

test_export_references_resolve

test_default_export_human_accepted_only

test_rejected_record_excluded

test_llm_hypothesis_excluded_from_production_export

test_unknown_values_remain_null

test_reversibility_null_preserved

test_kinetic_measurements_not_averaged

test_kinetic_original_values_preserved

test_kinetic_conditions_preserved

test_measurement_confidence_preserved

test_model_applicability_preserved

test_unapproved_assumption_not_activated

test_approved_assumption_exported

test_knowledge_gaps_preserved

test_conflicts_preserved

test_claim_evidence_links_preserved

test_export_no_dangling_references

test_export_stable_ordering

test_export_hash_stable_for_same_input

test_production_export_validation_failure_blocks_output
```

---

# Golden Export Fixture

Create a small, manually reviewed fixture at:

```text id="eynh6l"
tests/fixtures/exports/agent1_export_v0.1.json
```

The fixture should contain:

```text id="fp96ir"
1 organism

2 compartments

5 compounds

2 genes

2 proteins

1 enzyme complex

2 reactions

multiple reaction participants

3 claims

multiple evidence records

3 kinetic measurements

1 regulatory interaction

1 modeling assumption

2 knowledge gaps

1 conflict
```

The fixture should exercise:

```text id="dbz5ub"
null values
multiple kinetic measurements
confidence
human approval
conflicting evidence
cross references
```

---

# Example Minimal Complete Export

A minimal valid export should conceptually resemble:

```json id="xm4dv4"
{
  "schema_version": "0.1",
  "metadata": {
    "export_id": "uuid",
    "generated_at": "2026-08-31T00:00:00Z",
    "agent1_version": "0.1.0",
    "schema_version": "0.1",
    "prompt_version": "0.1",
    "llm_provider": "provider",
    "llm_model": "model",
    "software_commit": "commit",
    "curation_states_included": [
      "HUMAN_ACCEPTED"
    ],
    "organism_scope": [
      "uuid-org"
    ],
    "biological_scope": "Example pathway",
    "export_mode": "PRODUCTION"
  },
  "organisms": [
    {
      "id": "uuid-org",
      "scientific_name": "Saccharomyces cerevisiae",
      "common_name": "budding yeast",
      "ncbi_taxonomy_id": 4932,
      "strain": "S288C",
      "kegg_code": "sce",
      "biocyc_id": null
    }
  ],
  "compartments": [],
  "compounds": [],
  "genes": [],
  "proteins": [],
  "enzyme_complexes": [],
  "reactions": [],
  "claims": [],
  "kinetic_measurements": [],
  "regulatory_interactions": [],
  "evidence": [],
  "publications": [],
  "modeling_assumptions": [],
  "knowledge_gaps": [],
  "conflicts": []
}
```

Empty collections are valid.

Missing required top-level collections are not valid.

---

# Definition of Done

The export system is complete when:

1. A canonical `Agent1Export` Pydantic model exists.

2. JSON Schema can be generated from it.

3. Production exports include only permitted curation states.

4. Stable identifiers are preserved.

5. All references resolve.

6. Unknown values remain `null`.

7. Reversibility uncertainty remains visible.

8. Kinetic measurements remain separate.

9. Original and normalized kinetic values are both preserved.

10. Measurement confidence and model applicability remain distinct.

11. Modeling assumptions remain separate from biological facts.

12. Unapproved assumptions cannot silently become active.

13. Knowledge gaps are exported.

14. Conflicts are exported.

15. Evidence provenance is preserved.

16. Export validation runs before writing files.

17. Invalid production exports fail safely.

18. A version-controlled JSON Schema exists.

19. Golden export tests pass.

20. Agent 2 can validate the export before processing it.

---

# Final Export Principle

The export must preserve the distinction between:

```text id="vfo7jm"
what is known

what is measured

what is inferred

what is conflicting

what is assumed

what is missing
```

Agent 1 must never simplify the export merely to make downstream model generation easier.

If downstream modeling requires information that Agent 1 does not know, the export should say:

```text id="8efsj4"
null
```

or provide an explicit knowledge gap.

It must not silently manufacture a value.

The scientific handoff from Agent 1 to Agent 2 must preserve uncertainty with the same care that it preserves reaction stoichiometry.
