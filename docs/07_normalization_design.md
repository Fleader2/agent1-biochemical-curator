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

**Reviewed in Increment 11, deliberately left unresolved**: a database test,
`test_protein_uniprot_id_is_not_unique`
(`tests/database/test_group_b_models.py`), directly encodes non-uniqueness
as a deliberate, tested design decision, and no proof was found that one
UniProt accession must map to exactly one `Protein` row globally. `Increment
11` (`docs/08_normalization_persistence.md`) does not add this constraint;
see that document's Increment 11 section for the full reasoning.

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

**Reviewed in Increment 11, deliberately left unresolved**: `pubchem_cid`/
`metacyc_id` were indexed (matching `chebi_id`/`kegg_compound_id`/
`inchikey`'s pre-existing indexes) to support persistence freshness-recheck
lookups, but none of the five received a uniqueness constraint --
`app.normalization.compound` still treats a duplicate row sharing any one of
them as a live, expected `AMBIGUOUS` outcome, and an existing database test
(`test_compound_external_identifiers_are_not_unique`) already encodes that
as intentional. See `docs/08_normalization_persistence.md`'s Increment 11
section.

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

**Reviewed in Increment 11, partially resolved**: all three received
non-unique indexes (`ontology_id` alone, plus composites on
`(organism_id, name)`/`(organism_id, abbreviation)`) to support persistence
and normalization lookup paths. **No uniqueness was added** -- reference
rows (`organism_id IS NULL`) and organism-specific rows may still
legitimately coexist and even share a `name`/`abbreviation`/`ontology_id`,
and this increment does not collapse that distinction (Questions F/G above
remain fully open). See `docs/08_normalization_persistence.md`'s
Increment 11 section.

## I. Null-organism Reaction semantics

`Reaction.organism_id` is nullable, but — unlike `Compartment`, whose
`organism_id IS NULL` meaning is explicitly documented and verified (model
docstring + migration `0002_reference_data`) — no repository documentation
defines what a null-organism `Reaction` row means.

**Define this explicitly** before any reaction with `organism_id = NULL`
needs normalizing:

- a generic/global biochemical reaction,
- an unknown organism,
- a reference/template reaction, or
- another explicit concept.

Do not resolve this now. `app.normalization.reaction` deliberately does
**not** replicate Compartment's "`None` is compatible with any requested
organism" reconciliation rule absent this evidence — a candidate with
`organism_id = NULL` is currently treated as a different (conflicting)
scope from any specific requested organism, exactly like any other
non-matching organism value.

## J. Reaction: participants required for NEW?

`app.normalization.reaction`'s `NEW` rule currently requires only a
Level 1 external identifier and a `name` — it does **not** require at
least one participant, because the ORM schema does not enforce that a
`Reaction` row have any `reaction_participant` rows at all.

**Before Reaction persistence/import**, decide whether `NEW` should
additionally require at least one normalized reactant/product participant,
even though the schema does not mandate it — creating a nameless-structure
"reaction" may be scientifically unsafe for downstream modeling
(``docs/03_agent_behavior.md``'s "Reaction Curation Behavior": stoichiometry
is one of the core things a reaction must have determined).

Do not resolve this now.

## K. Proportional stoichiometry equivalence

`app.normalization.reaction` treats stoichiometry with exact `Decimal`
comparison: `A + B -> C` and `2 A + 2 B -> 2 C` are different exact
structures, with no canonical-ratio reduction performed.

**Decide** whether reactions differing only by a common stoichiometric
scale factor should ever be treated as structurally equivalent. Do not
resolve this now — no existing specification defines a canonical-ratio
policy.

## L. Reversed orientation for reversible reactions

`app.normalization.reaction` never treats `A -> B` as equivalent to
`B -> A`, even when both the incoming identity and the resolved candidate
are marked `reversible = True` — reactant/product role is part of the exact
structural signature, and reversibility metadata is never used to reorder
or reinterpret it.

**Decide** whether a reversible `Reaction` should be considered
structurally equivalent to the same participants with reactant/product
roles reversed. Do not resolve this now.

## M. Structure-only duplicate discovery (required architectural follow-up)

`app.normalization.reaction` has no structural/participant lookup method at
all, because no existing persistence-layer API provides one and this
increment does not invent one. As a direct consequence, **this module
cannot currently discover a Reaction that shares an incoming record's exact
participant structure but no external identifier and no matching name** —
such a duplicate is architecturally invisible to it (proven explicitly by
`test_structure_only_duplicate_is_currently_undetectable_new_not_ambiguous`
in `tests/normalization/test_reaction.py`).

**Before high-volume Reaction persistence/import**, define and implement a
persistence-level structural lookup or canonical structural-signature
strategy. This is flagged as a **required architectural follow-up**, not
merely an optional enhancement — without it, structurally duplicate
reactions can be created undetected.

## N. Reaction indexing and uniqueness

`kegg_reaction_id`/`rhea_id` are indexed but not unique; `metacyc_reaction_id`
is neither indexed nor unique; `ec_number` is indexed but non-unique;
`reaction_participant` has no uniqueness constraint at all, so duplicate
identical participant rows are possible at the schema level. Only
`internal_id` is genuinely database-unique, and it is a persistence
identifier, never incoming identity (see the Finalized Policy section
below).

**Before Reaction persistence/import**, decide whether any external
identifiers should receive indexes and/or uniqueness constraints beyond the
current schema (mirroring the same still-open questions for
`protein.uniprot_id` (Question A), Compound's five external identifiers
(Question E), and Compartment (Question H)). Do not change the schema now.

**Reviewed in Increment 11, deliberately left unresolved**:
`metacyc_reaction_id` was indexed (matching `kegg_reaction_id`/`rhea_id`'s
pre-existing indexes) to support persistence freshness-recheck lookups, but
none of the three received a uniqueness constraint --
`app.normalization.reaction` still treats a duplicate row sharing one of
them as a live, expected `AMBIGUOUS` outcome. `internal_id`'s own
allocation gap (a *persistence* identifier, never incoming identity) was
separately resolved by migration `0009_persistence_hardening` -- see
`docs/08_normalization_persistence.md`'s Increment 11 section for both.

## O. Reaction↔enzyme relationship value and association identity

`app.normalization.reaction_enzyme` treats `ReactionEnzyme.relationship`
(e.g. `CATALYZES`, `REQUIRED_FOR`, `PUTATIVE_CATALYST`, `ISOENZYME`) as
inert metadata, not part of association identity: an incoming
`(reaction A, protein B, CATALYZES)` may `MATCHED` against an existing
`(reaction A, protein B, PUTATIVE_CATALYST)`, because the pair identity
`(reaction_id, protein_id)` is the same regardless of `relationship`.

**Decide** whether different `relationship` values for the same
`(reaction_id, protein_id)` or `(reaction_id, complex_id)` pair should
eventually represent:

- the same association with changing metadata (current behavior),
- distinct relationship records,
- conflicting claims about one association, or
- another explicitly modeled structure.

Do not resolve this now — recorded as an explicit unresolved policy
question, not permanent scientific semantics (the same conservative-default
treatment Gene's `symbol` and Compound/Compartment's own Level-2 metadata
fields already receive).

## P. Reaction↔enzyme organism consistency

`app.normalization.reaction_enzyme` does not enforce organism consistency
among a Reaction and its associated Protein/EnzymeComplex.
`ReactionEnzyme` itself contains no organism field, and this module does
not fetch `Reaction`, `Protein`, or `EnzymeComplex` records to inspect
their `organism_id` values.

**Define** where organism compatibility should be enforced, if at all —
possible locations include normalization, persistence validation,
deterministic scientific validation, or claim/evidence validation. Do not
add cross-entity lookup behavior to this normalizer unless the
architecture is explicitly changed. Do not resolve this now.

## Q. ReactionEnzyme uniqueness and constraints -- RESOLVED (Increment 11)

`reaction_enzyme` previously had no uniqueness constraints at all, and no
database `CHECK` constraint enforced "exactly one of `protein_id`/
`complex_id`" (the model docstring called this "soft language, not
'must'"). Duplicate association rows were therefore possible at the schema
level.

**Resolved by migration `0009_persistence_hardening`** (see
`docs/08_normalization_persistence.md`'s "Increment 11" section for full
rationale): a `CHECK` constraint (`ck_reaction_enzyme_exactly_one_target`)
now enforces exactly one target at the database level, and two partial
unique indexes (`uq_reaction_enzyme_reaction_id_protein_id`/
`uq_reaction_enzyme_reaction_id_complex_id`) now enforce uniqueness on
`(reaction_id, protein_id)`/`(reaction_id, complex_id)` respectively.
`relationship` is **not** part of either uniqueness index -- the pair alone
is identity, exactly as `app.normalization.reaction_enzyme` already
finalized (Question O, still separately open: whether different
`relationship` values for the same pair should ever mean something other
than "the same association with changing metadata," is unaffected by this
schema change and remains unresolved).

## R. Reaction↔enzyme relationship vocabulary

`relationship` is currently a free-form `VARCHAR` column, not backed by a
database enum, even though `docs/02_database_schema.md` lists example
values (`CATALYZES`, `REQUIRED_FOR`, `PUTATIVE_CATALYST`, `ISOENZYME`).

**Decide** whether the project should eventually define a controlled enum
or vocabulary for these values. Do not introduce an enum yet.

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

---

# Finalized Normalization Policy: Reaction (Increment 8)

Full rationale lives in `app/normalization/reaction.py`'s module docstring;
this is a summary for cross-reference. Reaction↔enzyme association
(`reaction_enzyme`) is out of scope for this increment.

## Schema semantics

- `Reaction.internal_id` is database-unique.
- `Reaction.organism_id` is nullable, but current repository documentation
  does not define the biological semantics of `organism_id = NULL` for
  `Reaction` (see Open Question I).
- `kegg_reaction_id` and `rhea_id` are indexed but not unique.
- `metacyc_reaction_id` is neither indexed nor unique.
- `ec_number` is indexed but non-unique.
- `reaction_participant` has no uniqueness constraint.
- Duplicate identical participant rows are therefore possible at the
  schema level.
- Participant `stoichiometry` is `Decimal`/`Numeric`, constrained to `> 0`
  by a database `CHECK` constraint.

## Identity hierarchy

- **Level 1** (symmetric exact external identifiers): `kegg_reaction_id`,
  `metacyc_reaction_id`, `rhea_id`.
- **Level 2** (exact normalized participant structure): corroborating/
  contradicting only in the current increment — does not independently
  discover or `MATCH` a Reaction (see Open Question M).
- **Level 3** (exact organism-scoped name): weak candidate generation
  only, never independently `MATCHED`.
- **Non-identity/inert metadata**: `ec_number`, `reaction_type`,
  `reversible`.
- **Internal persistence identifier**: `internal_id` is not part of
  incoming Reaction identity.

## Strong external-ID reconciliation

- Zero strong matches → continue to weak/`NEW` logic.
- One consistent candidate in the requested organism → `MATCHED`.
- One external ID resolving to multiple rows → `AMBIGUOUS`.
- Different external IDs resolving to different rows → `CONFLICTED`.
- A candidate with `organism_id != requested organism_id`, **including
  `NULL`**, → `CONFLICTED`.
- Never chosen arbitrarily among duplicate external-ID rows.

## Organism policy

`normalize_reaction` requires a non-null `organism_id`. Reaction
candidates with `organism_id = NULL` are **not** treated as global/
reference records, because the repository does not currently define that
meaning for `Reaction` (unlike the verified Compartment case). See Open
Question I.

## Participant identity and canonicalization

`ReactionParticipantIdentity`: `compound_id`, `role`, `stoichiometry`,
`compartment_id`. Canonical comparison is:

- order-independent,
- multiplicity-sensitive,
- exact on `compound_id`,
- exact on `role`,
- exact on `compartment_id`,
- exact on `stoichiometry`.

Duplicate identical participant rows are preserved, not combined.

## Stoichiometry policy

- Exact `Decimal` comparison.
- No floating-point tolerance.
- No proportional reduction — `1:1:1` is not automatically equivalent to
  `2:2:2` (see Open Question K).

## Direction/reversibility policy

- Role/direction is part of participant structure.
- `reversible` metadata is never used to reverse or reinterpret
  participants.
- `A -> B` is not treated as `B -> A`, even when both reactions are
  `reversible = True` (see Open Question L).
- `reversible = NULL` remains unresolved.

## Structural equality policy

- Exact participant structure may corroborate or contradict a Level-1
  match.
- Exact structure alone does not currently produce `MATCHED`.
- The current normalization layer has no structural lookup method.
- Structure-only duplicate discovery is therefore not implemented (see
  Open Question M — a required architectural follow-up).

## Strong-ID plus structural disagreement

If a Level-1 identifier resolves one candidate and incoming participants
are supplied:

- different compound → `CONFLICTED`
- different role → `CONFLICTED`
- different stoichiometry → `CONFLICTED`
- different compartment → `CONFLICTED`

If the candidate has no recorded participants, missing structure is
treated as missing metadata rather than contradiction.

## Compartment policy

- `compartment_id` is part of structural identity.
- Compartments are never dropped or defaulted.
- Transport and same-compartment reactions remain distinct:
  `A[c] -> A[m] != A[c] -> A[c]`.

## Generic-compound policy

- Normalized compound UUIDs are treated literally.
- Generic and specific compounds are not substituted.
- No class/ontology expansion occurs in Reaction normalization.

## Proton/water/charge convention policy

- No proton normalization.
- No water normalization.
- No charge-state normalization.
- No acid/base normalization.
- Explicit H+ or H2O differences make exact structures different.
- Mass/charge balancing belongs to a separate deterministic validation
  layer.

## Name policy

- Exact, organism-scoped lookup only.
- Never independently `MATCHED`.
- Any nonzero name candidate set blocks `NEW`.
- No fuzzy matching, no synonym expansion, no punctuation or case
  normalization beyond existing whitespace trimming.

## EC-number policy

- EC number is not a Reaction identity key.
- No `by_ec_number` lookup.
- Multiple reactions may share one EC number.
- EC disagreement does not currently override a Level-1 match.

## Reaction-type policy

- `reaction_type` is metadata only.
- It does not independently match or conflict in the current increment.

## NEW policy

`NEW` requires:

- at least one Level-1 external reaction identifier,
- no strong match, ambiguity, or conflict,
- no exact name collision,
- `name` present.

Participants are **not** currently required for `NEW`, because the ORM
schema does not require them and no repository policy yet imposes that
stricter rule (see Open Question J). No `internal_id` is generated by
normalization.

## Connector behavior

`reaction_identity_from_kegg` is implemented because an existing KEGG
reaction record exists. The KEGG equation is not parsed — participants
remain empty, because the connector exposes equation text, not a trusted
normalized participant structure. No Rhea or MetaCyc adapter is
implemented because compatible connectors do not exist.

## Proposed regression tests

Minimal Reaction-normalization test names proposed for
`docs/05_testing.md` (not yet added there):

```text
test_exact_strong_id_single_candidate_in_requested_organism_matched
test_multiple_rows_for_one_external_id_is_ambiguous
test_foreign_organism_candidate_is_conflicted_never_new
test_rhea_resolves_a_kegg_resolves_b_is_conflicted
test_same_external_id_different_compound_participants_is_conflicted
test_same_external_id_different_stoichiometry_is_conflicted
test_forward_direction_does_not_equal_reverse_direction
test_transport_reaction_differs_from_same_compartment_reaction
test_structure_only_duplicate_is_currently_undetectable_new_not_ambiguous
test_one_exact_name_candidate_is_ambiguous_never_matched
test_reactions_may_share_ec_number_without_being_considered_same
test_no_proportional_stoichiometry_reduction
```

All twelve already exist and pass in `tests/normalization/test_reaction.py`.

---

# Finalized Normalization Policy: Reaction↔Enzyme Association (Increment 9)

Full rationale lives in `app/normalization/reaction_enzyme.py`'s module
docstring; this is a summary for cross-reference. This increment is
relationship identity normalization only — it never determines whether a
protein truly catalyzes a reaction.

## Schema semantics

`ReactionEnzyme` contains:

- `id`
- `reaction_id` — non-null FK to `Reaction`
- `protein_id` — nullable FK to `Protein`
- `complex_id` — nullable FK to `EnzymeComplex`
- `relationship` — non-null plain string
- `confidence_summary` — nullable
- `notes` — nullable

The database currently has no uniqueness constraints on `ReactionEnzyme`,
and there is no database `CHECK` constraint enforcing exactly one of
`protein_id`/`complex_id`. Duplicate association rows are therefore
possible. Evidence and claims are stored separately (`Claim`/`Evidence`)
and are not part of the `ReactionEnzyme` table. The schema was not
changed.

## Association identity

Two mutually exclusive Level-1 association identities:

- Protein association: `(reaction_id, protein_id)`
- Complex association: `(reaction_id, complex_id)`

Exactly one target type is allowed in `ReactionEnzymeIdentity`. Protein and
EnzymeComplex associations are distinct relationship identities and are
never bridged automatically.

## ReactionEnzymeIdentity

Fields: `source`, `source_identifier`, `reaction_id`, `protein_id`,
`complex_id`, `relationship`.

Validation requires:

- `reaction_id` present,
- exactly one of `protein_id` or `complex_id`,
- never both,
- never neither.

`relationship` is required for `NEW` because it is the schema's only
non-null, non-identity field.

## Candidate identity

`ReactionEnzymeCandidate` fields: `id`, `reaction_id`, `protein_id`,
`complex_id`, `relationship`. Confidence and notes are not part of
normalization identity.

## Lookup API

Exactly two lookup operations:

- `by_reaction_and_protein(reaction_id, protein_id)`
- `by_reaction_and_complex(reaction_id, complex_id)`

No lookup exists by reaction alone, protein alone, complex alone, EC
number, gene, publication, claim, evidence, confidence, pathway, or free
text.

## Exact reconciliation

- Zero candidates → `NEW` or `UNRESOLVED` depending on creation
  completeness.
- One candidate → `MATCHED`.
- Multiple candidate rows for the same exact pair → `AMBIGUOUS`.
- Candidate ordering does not affect the result.

No cross-anchor reconciliation is needed, because each request has exactly
one mutually exclusive pair identity.

## Protein versus EnzymeComplex policy

Reaction + Protein and Reaction + EnzymeComplex are different assertions.
Equivalence is never inferred because the Protein is a member of the
EnzymeComplex, because the Protein and complex share an EC number, because
the Protein participates in the same pathway, or because the complex
contains only one known catalytic subunit. No membership expansion or
relationship bridging occurs in this layer.

## Relationship field policy

`relationship` is metadata, not part of association identity. Therefore an
incoming `(reaction A, protein B, CATALYZES)` may `MATCH` an existing
`(reaction A, protein B, PUTATIVE_CATALYST)`, because the pair identity is
the same. This is recorded as an explicit unresolved policy question (see
Open Question O), not permanent scientific semantics.

## Isoenzyme policy

Multiple different Protein IDs may independently associate with the same
Reaction. These associations are not duplicates and do not conflict merely
because they share the Reaction.

## Multi-function Protein policy

One Protein may independently associate with multiple Reaction IDs. These
relationships are not merged.

## EC-number policy

EC number plays no role in Reaction↔enzyme association normalization:
no EC lookup, no EC inference, no EC-based identity, no EC-based conflict.
EC-based biochemical reasoning belongs to evidence/curation layers, not
identity normalization.

## Evidence neutrality

Normalization does not inspect or use claims, evidence, publications,
confidence scores, reviewer state, experimental support, or notes. The
purpose of this increment is relationship identity only — whether the
relationship is scientifically supported is handled later.

## Organism policy

Reaction↔enzyme normalization does not enforce organism consistency.
`ReactionEnzyme` itself contains no organism field, and this module does
not fetch `Reaction`, `Protein`, or `EnzymeComplex` records to inspect
their organism IDs. See Open Question P.

## NEW policy

`NEW` requires:

- valid `reaction_id`,
- exactly one target (`protein_id` or `complex_id`),
- no existing exact pair,
- `relationship` present.

If `relationship` is missing after an exact pair lookup returns no match →
`UNRESOLVED`. Existing duplicate rows → `AMBIGUOUS`. Existing exact pair →
`MATCHED`.

## Connector policy

No connector adapter is implemented. No existing connector exposes a
trustworthy normalized `Reaction UUID + Protein UUID` or
`Reaction UUID + EnzymeComplex UUID` relationship record. In particular,
KEGG EC-number annotations are not converted into Reaction↔Protein
relationships, pathway membership is not treated as catalytic evidence,
and GO annotations are not converted into catalytic relationships.

## Proposed regression tests

The following tests are already implemented and passing in
`tests/normalization/test_reaction_enzyme.py`:

```text
test_exact_reaction_protein_pair_matched
test_exact_reaction_complex_pair_matched
test_duplicate_rows_for_same_pair_is_ambiguous
test_protein_association_and_complex_association_never_merged
test_differing_relationship_value_does_not_prevent_matched
test_two_proteins_catalyzing_one_reaction_both_allowed
test_one_protein_catalyzing_two_reactions_both_allowed
test_duplicate_blocks_new
test_missing_relationship_is_unresolved_not_new
```
