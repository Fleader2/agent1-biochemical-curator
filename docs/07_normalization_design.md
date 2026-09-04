# Agent 1: Biochemical Evidence Curator
## Phase 4 Normalization — Design Notes and Open Policy Questions

**Document:** `docs/07_normalization_design.md`

**Status:** Living implementation notes, not an authoritative specification.

---

# Purpose

The six numbered documents in `docs/` (`01_overview.md` through
`06_export_format.md`) are this project's authoritative specifications
(`CONTRIBUTING.md`). Phase 4 (`app/normalization/`) has, in the course of
implementation, surfaced policy decisions that are not fully settled by
those specifications — either because the schema underdetermines them, or
because they depend on a later increment (persistence, relationship
reconciliation) that does not exist yet.

This document records:

1. Open policy questions that a later increment must resolve explicitly,
   rather than by accretion.
2. A summary of finalized normalization policy per entity, for quick
   reference alongside the module docstrings in `app/normalization/`, which
   remain the detailed source of truth for each entity's exact rules.

Nothing in this document changes normalization behavior. It is a record of
decisions already made in code (with their rationale) and decisions
explicitly deferred.

---

# Open Policy Questions

## A. Protein UniProt uniqueness

`protein.uniprot_id` is currently indexed but **not** database-unique
(`app/models/protein.py`). Protein normalization
(`app.normalization.protein`) treats the UniProt accession as the sole
Level-1 Protein identifier and reconciles collisions — including
cross-organism collisions — at the application layer, via a global exact
lookup and explicit `MATCHED`/`AMBIGUOUS`/`CONFLICTED` reconciliation.

**Before Protein persistence/import is implemented**, decide explicitly
whether `protein.uniprot_id` should receive a database-level partial unique
constraint where non-null (the same pattern already used for
`gene.sgd_id`/`gene.ncbi_gene_id`/`gene.kegg_gene_id`). Until that decision
is made, application-layer reconciliation remains the only safeguard against
a duplicate accession being persisted twice.

## B. Gene↔Protein relationship reconciliation

`Protein.gene_id` is currently carried as relationship metadata only and is
explicitly **not** part of Protein identity (`app.normalization.protein`).
A Level-1 UniProt identity match with a differing `gene_id` — an existing
candidate's `gene_id` disagreeing with an incoming record's supplied
`gene_id` — does **not** currently cause `CONFLICTED`.

**Before persistence/relationship reconciliation is implemented**, define
whether such a disagreement should be represented as:

- a relationship-level conflict,
- a claim/evidence conflict,
- a review condition (e.g. `NEEDS_REVIEW`), or
- another explicitly specified state.

Do not redefine this as a Protein *identity* conflict without an explicit
specification change — Increment 5's design deliberately keeps identity
(is this the same Protein row?) and relationship (which Gene, if any, does
it belong to?) as separate questions, since one Gene may correspond to more
than one Protein (`app.normalization.gene`'s own "Schema-policy mismatch"
note on `Gene.uniprot_id` records the same one-to-many concern from the Gene
side).

---

# Finalized Normalization Policy: Protein (Increment 5)

Full rationale lives in `app/normalization/protein.py`'s module docstring;
this is a summary for cross-reference.

- **Level 1** (authoritative identity): `uniprot_id` only.
- **Level 3** (candidate generation only, never `MATCHED`): organism-scoped
  exact `name`. Protein has no alias/synonym column, so no Level-3 alias
  signal exists.
- **EC number**: non-identity metadata. Never queried, never causes
  `MATCHED`/`CONFLICTED`, never counts toward creation completeness.
- **`gene_id`**: non-identity relationship metadata. Never queried, never
  compared. See Open Question B above.
- **UniProt isoforms**: remain literal, distinct accessions (`"P12345"` vs.
  `"P12345-2"`) — no suffix stripping, no canonicalization, no equivalence
  inference.
- **Cross-organism UniProt collision**: always `CONFLICTED`, never `NEW`,
  never silently dropped — including when every colliding row is outside
  the requested organism.
- **Name candidates**: never produce `MATCHED` on their own, even a single
  unique candidate, even when multiple weak signals agree.
- **Connector adapters**: intentionally deferred. No `protein_identity_from_sgd`
  (SGD's UniProt cross-reference is gene-side; converting it directly would
  assert a one-gene-one-protein relationship this design rejects) and no
  BRENDA adapter (BRENDA exposes no true Protein identifier). Protein
  normalization remains source-neutral until a genuinely protein-scoped
  connector exists.
