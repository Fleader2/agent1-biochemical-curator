# Agent 1: Biochemical Evidence Curator
## Project Overview

**Project Name:** Agent 1 – Biochemical Evidence Curator

**Version:** 0.1

**Status:** Design Specification

---

# Purpose

Agent 1 is the first component of a multi-agent platform for constructing predictive, mechanistic models of microbial cells.

Its purpose is **not** to build mathematical models.

Its purpose is to collect, organize, evaluate, and curate biochemical knowledge so that downstream agents can automatically construct high-quality kinetic models in the Antimony language.

Agent 1 should function as an evidence-based scientific curator rather than as a scientific author.

---

# Overall Vision

The long-term goal of this project is to enable construction of a predictive virtual microbial cell.

The complete system will ultimately consist of multiple specialized agents.

Agent 1 is responsible for scientific knowledge acquisition.

Later agents will perform:

- Antimony model generation
- SBML generation
- ODE simulation
- Parameter estimation
- Model validation
- Experimental design
- Knowledge-gap analysis

Agent 1 is therefore the scientific foundation of the entire platform.

Every downstream result depends upon the quality of Agent 1's curated knowledge.

---

# Initial Biological Scope

Version 1 of Agent 1 focuses exclusively on:

**Organism**

- *Saccharomyces cerevisiae*

**Biological process**

- Free fatty acid metabolism

including

- acetyl-CoA production
- malonyl-CoA synthesis
- fatty acid synthase
- desaturation
- elongation
- acyl-CoA formation
- phospholipid remodeling
- triacylglycerol synthesis
- triacylglycerol degradation
- lipid droplets
- peroxisomal β-oxidation
- transport between cellular compartments

Future versions should be able to support arbitrary organisms and pathways without redesigning the software architecture.

---

# Guiding Philosophy

Agent 1 is fundamentally an evidence management system.

It stores:

- scientific claims
- supporting evidence
- confidence assessments
- provenance
- experimental context

It does **not** store unsupported conclusions.

Every biological assertion must be traceable to one or more sources.

---

# Scientific Principles

The following principles are mandatory.

## Evidence before conclusions

Scientific claims must always be supported by evidence.

The agent must never generate biological facts simply because they are plausible.

---

## Preserve uncertainty

Unknown information should remain unknown.

The system should never invent values or silently fill missing information.

---

## Preserve disagreements

Scientific disagreement is valuable information.

Conflicting publications should both be preserved.

The system should never overwrite one publication with another simply because it appears newer.

---

## Preserve experimental context

Biological measurements depend upon experimental conditions.

Examples include

- strain
- temperature
- pH
- nutrient conditions
- assay type
- purification state

Measurements performed under different conditions are not interchangeable.

---

## Separate observations from assumptions

The database must distinguish between

- experimentally observed biology
- database annotations
- computational predictions
- homology inference
- model assumptions
- AI-generated hypotheses

These categories must never be merged.

---

# Primary Responsibilities

Agent 1 is responsible for:

1. Discovering biochemical reactions

2. Identifying participating metabolites

3. Identifying catalytic enzymes

4. Recording cellular compartment

5. Recording gene associations

6. Recording regulatory interactions

7. Recording kinetic measurements

8. Recording experimental conditions

9. Recording literature evidence

10. Recording database provenance

11. Detecting conflicting information

12. Identifying knowledge gaps

13. Producing structured outputs for downstream agents

---

# Responsibilities Explicitly Excluded

Agent 1 shall NOT:

- generate Antimony
- generate SBML
- estimate kinetic parameters
- optimize model parameters
- simulate ODEs
- perform flux balance analysis
- redesign pathways
- invent missing reactions
- invent missing parameters
- modify accepted biological knowledge

These tasks belong to later agents.

---

# Data Sources

Agent 1 should retrieve information from trusted scientific resources.

Primary sources include:

- PubMed
- PubMed Central
- KEGG
- BRENDA
- BioCyc
- MetaCyc
- Saccharomyces Genome Database (SGD)
- UniProt
- ChEBI
- Rhea

Priority should always be given to primary experimental literature whenever available.

Curated databases should be treated as valuable summaries rather than unquestionable truth.

---

# Software Philosophy

The software should be:

- modular
- testable
- deterministic where possible
- reproducible
- fully typed
- extensively documented

Large monolithic functions should be avoided.

Every connector should be isolated.

Every scientific transformation should be testable.

---

# AI Philosophy

The language model is used for reasoning.

It is **not** used as a source of scientific evidence.

The LLM may:

- summarize publications
- identify claims
- classify evidence
- normalize terminology
- suggest search queries
- identify contradictions

The LLM must never:

- invent literature references
- invent PMIDs
- invent reactions
- invent kinetic parameters
- invent localization
- invent regulatory mechanisms

---

# Validation Philosophy

Whenever deterministic software can perform a task, deterministic software should be preferred over AI.

Examples include:

- mass balance
- charge balance
- unit conversion
- identifier validation
- duplicate detection
- schema validation

AI should only be used where interpretation of scientific language is required.

---

# Human Oversight

Agent 1 is intended to accelerate scientific curation rather than replace expert judgment.

Scientific experts remain responsible for approving curated biological knowledge.

The software should therefore support explicit review states such as:

- Proposed
- Machine Reviewed
- Human Accepted
- Rejected

Downstream modeling agents should operate only on approved records unless explicitly instructed otherwise.

---

# Expected Outputs

Agent 1 produces a structured biochemical knowledge base.

This includes:

- reactions
- compounds
- genes
- proteins
- enzyme complexes
- compartments
- kinetic measurements
- regulatory interactions
- evidence records
- publications
- confidence scores
- modeling assumptions
- knowledge gaps

These outputs form the canonical input for Agent 2, which generates mechanistic models in the Antimony language.

---

# Long-Term Goal

The long-term objective is to create a reusable, extensible biochemical knowledge platform capable of supporting predictive virtual-cell modeling across diverse organisms.

The design should therefore prioritize extensibility, reproducibility, provenance, and scientific rigor over short-term implementation convenience.
