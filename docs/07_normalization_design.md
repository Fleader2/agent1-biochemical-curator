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

## C. Compound Level-2 chemical disagreement

`app.normalization.compound` treats `inchi`/`formula`/`charge`/`is_generic`
as Level 2 — corroborating metadata only. When a Level 1 identifier
(`chebi_id`/`kegg_compound_id`/`pubchem_cid`/`metacyc_id`/`inchikey`)
establishes a match but one of these Level 2 fields disagrees with the
resolved candidate's own value (e.g. supplied `formula` differs from the
matched compound's stored `formula`), the result currently remains
`MATCHED` — the disagreement is not surfaced as a discrepancy anywhere and
does not change the returned status.

**Before this is relied upon for scientific review**, define whether such a
disagreement should:

- remain `MATCHED` with the discrepancy surfaced (e.g. in `reason`, or a
  future dedicated field),
- become `CONFLICTED`, or
- enter another explicitly modeled review state.

Do not resolve this yet. No existing specification
(`docs/02_database_schema.md`, `docs/03_agent_behavior.md`) answers it.

## D. Compound generic versus specific compounds

`is_generic` is carried on both `CompoundIdentity` and `CompoundCandidate`
but plays no role in any lookup, match, conflict, or creation-completeness
decision. A generic candidate (e.g. a class-level ChEBI entry such as "fatty
acid") and a specific incoming identity are not merged via the Level 3
weak-name path (that path only ever produces `AMBIGUOUS`, never `MATCHED`,
regardless of genericness) — but a Level 1 strong-identifier match is not
currently checked against `is_generic` at all.

**Before later reconciliation is implemented**, define how `is_generic`
should participate. In particular, determine whether a generic-vs-specific
disagreement on an otherwise-matched compound is:

- an identity conflict,
- a semantic/class relationship (e.g. "is-a" rather than "is"),
- a review condition, or
- another explicitly modeled relationship.

Do not resolve this yet. No other code in this repository currently reads
`is_generic` for identity purposes.

## E. Compound external identifier uniqueness

`chebi_id`, `kegg_compound_id`, `pubchem_cid`, `metacyc_id`, and `inchikey`
are indexed in some cases (`chebi_id`, `kegg_compound_id`, `inchikey`) but
**none** carry a database-level uniqueness constraint (`app/models/
compound.py`'s own docstring states this is deliberate, unlike the
analogous columns on `gene`/`publication`). Compound normalization
(`app.normalization.compound`) reconciles collisions for all five
identifiers entirely at the application layer, via candidate-list
discipline (`AMBIGUOUS`/`CONFLICTED` are live, expected outcomes for any of
them, not defensive edge cases).

**Before Compound persistence/import is implemented**, decide explicitly
whether any of these identifiers should receive a database-level partial
unique constraint (mirroring `gene.sgd_id`/`gene.ncbi_gene_id`/
`gene.kegg_gene_id`, or `protein.uniprot_id`'s own still-open Question A
above), and whether such a constraint should differ by identifier or by
source. Do not change the schema now — this is a decision for a later
increment.

## F. Compartment reference-row weak lookup

`app.normalization.compartment`'s organism-scoped weak lookups (`by_name`/
`by_abbreviation`) require an *exact* match on the requested `organism_id`,
including matching `None` to `None` only — they do **not** fall back to
also surfacing standard/reference rows (`organism_id IS NULL`) as
collision candidates when a specific organism is requested.

Example: requested organism = *S. cerevisiae*, incoming `name = "cytosol"`,
and an existing reference compartment `name = "cytosol"`,
`organism_id = NULL`. The current implementation does not surface that
reference row through the weak lookup path at all.

**Before persistence and Reaction normalization are finalized**, decide
whether standard/reference rows should:

- participate in organism-scoped weak collision guarding,
- serve only as ontology-anchored reference entities (reachable solely via
  `by_ontology_id`, never via `name`/`abbreviation`),
- act as templates from which organism-specific compartments are later
  instantiated, or
- have another explicitly defined relationship.

Do not resolve this question here.

## G. Compartment ontology/name disagreement

Once `ontology_id` produces a clean `MATCHED` result in
`app.normalization.compartment`, a differing incoming `name`/`abbreviation`
does not currently change the status. `name`/`abbreviation` are weak
signals and are not used to overturn a Level 1 ontology identity match —
the same treatment Compound's own Level 2 fields receive (see Question C).

**Before this is relied upon for scientific review**, decide whether such a
disagreement should remain:

- `MATCHED` with the discrepancy surfaced,
- `CONFLICTED`,
- `NEEDS_REVIEW`, or
- another explicit condition.

Do not resolve this yet — this is recorded as an explicit open question,
not a settled behavior.

## H. Compartment indexing and uniqueness

`Compartment` currently has **no indexes or uniqueness constraints at all**
beyond its UUID primary key — not on `ontology_id`, not on `name`, not on
`abbreviation`, and no composite constraint on `organism_id` plus any of
them (verified in `app/models/compartment.py` and migration
`0002_reference_data.py`). This is the least-constrained schema of any
entity normalized so far.

**Before high-volume Compartment persistence/import**, decide whether to
add indexes and/or uniqueness rules for:

- `ontology_id` (global, mirroring `protein.uniprot_id`'s own still-open
  Question A),
- `organism_id` + `name`,
- `organism_id` + `abbreviation`.

Do not change the database schema now — this is a decision for a later
increment.

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

---

# Finalized Normalization Policy: Compound (Increment 6)

Full rationale lives in `app/normalization/compound.py`'s module docstring;
this is a summary for cross-reference. Unlike Gene/Protein, `Compound` has
no `organism_id` column at all — normalization is fully organism-agnostic,
like Publication.

## Identity hierarchy

- **Level 1** (exact strong identifiers, symmetric — no field is treated as
  more authoritative than another, and all supplied ones are reconciled
  together): `chebi_id`, `kegg_compound_id`, `pubchem_cid`, `metacyc_id`,
  `inchikey`.
- **Level 2** (corroborating metadata only — does not independently
  establish identity and does not currently change normalization status;
  see Open Questions C and D above): `inchi`, `formula`, `charge`,
  `is_generic`.
- **Level 3** (weak candidate generation only): `canonical_name`, compound
  synonyms (via `compound_synonym`, reached through `by_synonym`). A
  name/synonym match may produce `AMBIGUOUS` but never `MATCHED` — even a
  single unique candidate, even when multiple weak signals agree.

## Strong identifier reconciliation

- Zero strong matches (across every supplied Level 1 identifier) →
  continue to the Level 3 weak collision guard before considering `NEW`.
- Exactly one existing Compound consistently supported by every supplied
  Level 1 identifier (no disagreement among them) → `MATCHED`.
- One Level 1 identifier resolving to multiple existing rows → `AMBIGUOUS`
  — never chosen arbitrarily among duplicates (the schema does not enforce
  uniqueness on any of these columns, so this is an expected outcome, not a
  defensive edge case).
- Different supplied Level 1 identifiers resolving to different existing
  Compound rows → `CONFLICTED`.

## NEW policy

`NEW` requires all of:

1. at least one Level 1 identifier was supplied,
2. no strong-identifier match, ambiguity, or conflict,
3. no exact `canonical_name` or synonym collision,
4. `canonical_name` is present (the schema's only NOT NULL, non-identity
   column — `docs/02_database_schema.md`'s "Table: compound" section defines
   no other completeness rule).

`canonical_name` is never synthesized from an external identifier. Otherwise
`UNRESOLVED` applies, whenever no earlier verdict (`MATCHED`/`AMBIGUOUS`/
`CONFLICTED`/`NEW`) was reached.

## Chemical safety rules

- No fuzzy name matching.
- No formula-based matching (formula is never queried as an identifier).
- No charge-based matching (charge is never queried as an identifier).
- No protonation normalization, no charge neutralization.
- No stereochemistry stripping, no D/L collapsing, no cis/trans collapsing.
- No InChIKey canonicalization beyond exact string handling (no
  case-folding, no equivalence inference between different keys).
- Generic and specific compounds are not merged via weak names — the
  Level 3 path is always `AMBIGUOUS`, never `MATCHED`, regardless of
  `is_generic`.

## Non-identity fields

- `inchi`, `formula`, `charge`, `is_generic` are corroborating metadata
  only (Level 2) — carried on both `CompoundIdentity` and
  `CompoundCandidate`, never queried, never independently gating a status.
- `molecular_weight`, `smiles`, and `notes` are not used by Compound
  normalization at all — excluded from both types entirely.

## Connector behavior

- `compound_identity_from_kegg` is implemented: `app.connectors.kegg`
  already exposes a compound-level record (`KeggCompoundRecord`) at a
  compatible abstraction level, supporting a conservative, exact-copy
  mapping (`entry_id` → `kegg_compound_id`/`source_identifier`, first name
  → `canonical_name`, remaining names → `synonyms`, `formula` copied
  directly; molecular weight is not copied).
- ChEBI, PubChem, and MetaCyc adapters remain deferred: no connector for any
  of them exists yet in this repository, so no adapter is fabricated ahead
  of one existing.

## Proposed regression tests

Minimal Compound-normalization test names proposed for
`docs/05_testing.md` (not yet added there — `docs/05_testing.md` currently
defines only database-schema-level Compound tests, e.g.
`test_create_compound`; no normalization-level Compound test names exist to
preserve):

```text
test_chebi_id_single_candidate_matched
test_inchikey_single_candidate_matched
test_same_strong_identifier_on_two_rows_is_ambiguous
test_chebi_resolves_a_inchikey_resolves_b_is_conflicted
test_different_inchikeys_do_not_match
test_no_protonation_normalization_distinct_charge_states_stay_distinct
test_same_formula_on_two_compounds_does_not_make_them_identical
test_same_name_with_distinct_charge_states_does_not_silently_match
test_generic_and_specific_are_not_silently_merged_via_weak_name
test_canonical_name_only_one_candidate_is_ambiguous_not_matched
test_unmatched_strong_id_with_exact_name_collision_is_ambiguous_not_new
test_no_canonical_name_never_becomes_new_even_with_strong_id
```

All twelve already exist and pass in `tests/normalization/test_compound.py`.

---

# Finalized Normalization Policy: Compartment (Increment 7)

Full rationale lives in `app/normalization/compartment.py`'s module
docstring; this is a summary for cross-reference.

## Schema semantics

- `Compartment.organism_id` is nullable.
- `organism_id = NULL` is explicitly defined by the existing model
  docstring (`app/models/compartment.py`) and migration
  `0002_reference_data` as a **standard/reference compartment
  definition**, not as an unknown organism.
- The migration seeds 13 standard/reference compartments (`cytosol`,
  `mitochondrial matrix`, `mitochondrial intermembrane space`,
  `mitochondrial inner membrane`, `mitochondrial outer membrane`,
  `peroxisome`, `endoplasmic reticulum`, `Golgi`, `lipid droplet`,
  `nucleus`, `vacuole`, `plasma membrane`, `extracellular`) with
  `organism_id = NULL`.
- `Compartment` currently has **no indexes or uniqueness constraints** on
  `name`, `abbreviation`, `ontology_id`, or any `organism_id` combination
  — see Open Question H above.

These facts are recorded as verified, not reinterpreted.

## Identity hierarchy

- **Level 1** (the only signal that can independently produce `MATCHED`):
  `ontology_id`.
- **Level 2 / weak candidate generation** (may produce candidate
  collisions, never `MATCHED`): exact `name`, exact `abbreviation`.

## Ontology-ID policy

- Lookup is **global** (no organism parameter).
- Identifier comparison is literal: no case-folding, no prefix stripping,
  no namespace conversion, no ontology-equivalence inference.
- Multiple rows carrying one `ontology_id` are not assumed impossible — the
  schema does not prevent it (see Open Question H), so candidate-list
  discipline applies unconditionally.

Organism-aware reconciliation:

- one candidate in the requested organism → `MATCHED`
- one standard/reference candidate (`organism_id = NULL`) → `MATCHED`
- one candidate in a different, non-`NULL` organism → `CONFLICTED`
- multiple candidates all contained within `{requested organism, NULL}` →
  `AMBIGUOUS`
- multiple candidates including any foreign non-`NULL` organism →
  `CONFLICTED`

For a normalization request whose `organism_id` itself is `NULL`,
standard/reference rows (`organism_id = NULL`) are the matching scope.

## Name policy

- Exact, organism-scoped lookup only.
- No fuzzy matching, no synonym expansion, no case-folding, no substring
  matching.
- Even one exact name candidate → `AMBIGUOUS`, never `MATCHED`.

Examples verified to remain distinct: `cytosol` / `cytoplasm`,
`mitochondrion` / `mitochondrial matrix`, `mitochondrial matrix` /
`mitochondrial intermembrane space`, `mitochondrial inner membrane` /
`mitochondrial outer membrane`, `ER` / `Golgi`, `peroxisome` /
`mitochondrion`.

## Abbreviation policy

- Exact, organism-scoped lookup only.
- Candidate generation only — one or more candidates → `AMBIGUOUS`.
- Never independently produces `MATCHED`.
- No abbreviation expansion (e.g. `ER` is not automatically converted to
  `endoplasmic reticulum`).

## NEW policy

`NEW` requires:

- `ontology_id` supplied,
- no strong match, ambiguity, or conflict,
- no weak name/abbreviation collision in the requested scope,
- `name` present.

Weak-signal-only identities (no `ontology_id`) cannot produce `NEW`. No
name is synthesized from `ontology_id` or `abbreviation`. A request with
`organism_id = NULL` is a valid, explicit standard/reference scope and may
produce `NEW` if all normal `NEW` conditions are satisfied.

## Ontology/name disagreement

Once `ontology_id` produces a clean `MATCHED` result, differing incoming
`name` or `abbreviation` does not currently change the status. Name and
abbreviation are weak signals and are not used to overturn a Level 1
ontology identity match. **This is recorded as an explicit unresolved
policy question (see Open Question G above), not a permanently settled
behavior.**

## Connector behavior

No connector adapter was implemented. Existing SGD cellular-component/GO
annotations (`app.connectors.sgd.SgdGoAnnotation`) represent localization
*evidence* about genes/proteins, not standalone Compartment identity
records — `docs/03_agent_behavior.md`'s "Compartment Curation Behavior"
section is explicitly about localization claims, a distinct curation
concern. Localization evidence is not converted directly into
`CompartmentIdentity` in this normalization layer.

## Proposed regression tests

Minimal Compartment-normalization test names proposed for
`docs/05_testing.md` (not yet added there):

```text
test_ontology_id_single_candidate_in_requested_organism_matched
test_ontology_id_matches_standard_reference_compartment
test_ontology_id_candidate_in_different_organism_is_conflicted_never_new
test_ontology_id_candidates_spanning_requested_and_global_is_ambiguous_not_conflicted
test_exact_same_organism_name_single_candidate_is_ambiguous_never_matched
test_cytosol_does_not_match_cytoplasm
test_mitochondrion_does_not_match_mitochondrial_matrix
test_abbreviation_collision_prevents_new
test_ontology_match_stands_despite_differing_name
test_semantically_related_compartments_are_never_automatically_merged
test_global_scope_request_can_become_new
test_foreign_strong_id_collision_never_becomes_new
```

All twelve already exist and pass in `tests/normalization/test_compartment.py`.
