# Agent 1 Normalization Persistence Layer

**Document:** `docs/08_normalization_persistence.md`

**Status:** Living implementation notes for Increment 10 (`app/persistence/`), not an
authoritative specification. See `docs/07_normalization_design.md` for the
equivalent record of `app/normalization/`'s own design decisions and open
questions.

---

## Purpose

Normalization and persistence are two separate stages, with a hard boundary
between them:

- **Normalization** (`app/normalization/`) decides identity. It is
  read-only, pure, and never touches the database — it resolves a
  source-supplied record against existing candidates and returns a
  `NormalizationResult` describing what was found.
- **Persistence** (`app/persistence/`) applies only the database action
  that a given `NormalizationResult` permits. It does not decide identity
  itself.

Persistence must not re-run scientific identity inference, and it must not
silently override a normalization result. A `MATCHED` result is reused
as-is; a `NEW` result is created only if it is still safe and complete at
write time; every other status is handled conservatively (see "Status-to-
action matrix" below). Nothing in this layer re-derives or second-guesses
what normalization already decided.

## Supported entity types

This increment implements persistence for exactly the eight entity types
that `app/normalization/` currently normalizes:

- Organism
- Publication
- Gene
- Protein
- Compound
- Compartment
- Reaction
- ReactionEnzyme association

Evidence and claim persistence are explicitly **out of scope** for this
increment. No `Claim`/`Evidence` table is read or written anywhere in
`app/persistence/`.

## Persistence result types

`app.persistence.types.PersistenceAction` enumerates the database actions
a persist function can take:

- `CREATED`
- `REUSED_EXISTING`
- `NO_ACTION`
- `REQUIRES_REVIEW`
- `FAILED`

`PersistenceResult` is a distinct type from `NormalizationStatus`, on
purpose. **Identity decision != database write action.** A `MATCHED`
normalization status, for example, always maps to a `REUSED_EXISTING`
persistence action, never to `CREATED` — the two enumerations describe two
different questions ("what is this record?" vs. "what did the database
just do about it?") and are never collapsed into one.

## Status-to-action matrix

```
MATCHED
  -> REUSED_EXISTING

NEW
  -> CREATED   if creation is safe and complete
  -> FAILED    if required persistence conditions are missing,
               or a stale/collision recheck fails

AMBIGUOUS
  -> REQUIRES_REVIEW

CONFLICTED
  -> REQUIRES_REVIEW

UNRESOLVED
  -> NO_ACTION
```

This matrix is identical across all eight entity types.

## MATCHED policy

- Reuse the existing entity. The `entity_id` returned is the row
  normalization already resolved to.
- Never create a duplicate entity for a `MATCHED` result.
- Never overwrite the matched row's identity or metadata fields, even when
  the incoming record's metadata disagrees with what is already stored.
- Optionally attach a `SourceCrossReference`, where appropriate for that
  entity type (see "SourceCrossReference policy" below — `ReactionEnzyme`
  is the one exception).
- Optionally append `ExternalRecord` provenance, when the caller supplies
  it.
- Never change human-review state. No `ReviewEvent` is read, written, or
  implied by a `MATCHED` persist call.

**Incoming metadata differences do not mutate the matched row in
Increment 10.** This is a deliberate non-destructive-reuse policy, not an
oversight: reconciling or merging differing metadata onto an existing row
is a separate, unimplemented concern (see "Open architecture questions").

## NEW policy

- Creation is attempted only when the normalization status is `NEW`.
  Every other status has its own fixed action and never reaches the
  creation path.
- The schema-required fields for that entity must be present on the
  supplied identity before a row is created. If they are not, persistence
  returns `FAILED` rather than guessing.
- Persistence never invents a missing scientific value. It writes exactly
  what identity normalization has already resolved and nothing else.
- Immediately before insert, persistence re-runs a freshness/collision
  recheck against the database, using the same strong identifier(s)
  normalization itself used to reach `NEW` (see "Stale normalization
  result policy" below).
- Creation is conservative wherever the database provides no uniqueness
  backing for an identifier: the recheck is treated as the only available
  safeguard, and any hit is treated as disqualifying rather than
  something to reconcile.

## AMBIGUOUS / CONFLICTED / UNRESOLVED

**AMBIGUOUS**
- No biological entity write of any kind.
- Action: `REQUIRES_REVIEW`.

**CONFLICTED**
- No biological entity write of any kind.
- Action: `REQUIRES_REVIEW`.

**UNRESOLVED**
- No biological entity write of any kind.
- Action: `NO_ACTION`.

No `ReviewEvent` rows are automatically created in this increment for any
of these three statuses. `REQUIRES_REVIEW` is a signal returned to the
caller, not a side effect performed against the database.

## Transaction policy

- `persist_*` functions do not call `session.commit()`.
- `persist_*` functions do not call `session.rollback()`.
- Transaction ownership belongs entirely to the calling service layer,
  matching the existing convention documented on `app/db/session.py`'s
  session dependency.
- All entity creation and all provenance attachment (`SourceCrossReference`,
  `ExternalRecord`) performed by a single `persist_*` call happen within
  the caller's own transaction — persistence never opens, commits, or
  rolls back a transaction of its own.

## SourceCrossReference policy

- Idempotent on `(entity_type, entity_id, source, external_id)` — the
  table's one real unique constraint.
- If an exact cross-reference already exists for that tuple, it is reused;
  no duplicate row is ever created for the same tuple.
- No LLM-generated identifier may be stored as scientific source identity.
  This is a structural guarantee, not a runtime check: `SourceType` has no
  LLM/CLAUDE/OPENAI member, so there is no value that could be passed as
  `source` to represent one.
- `ReactionEnzyme` does not receive a `SourceCrossReference`, for either
  `MATCHED` reuse or `NEW` creation. Its `source_identifier` is
  request-tracking metadata only, not a genuine external identifier for
  the reaction/enzyme relationship itself — no connector in this
  repository exposes one. Attaching a cross-reference from it would
  misrepresent a synthetic tracking value as a real external identifier.

## ExternalRecord policy

- `ExternalRecord` is an append-only retrieval history, not deduplicated
  storage.
- Each explicit provenance event a caller supplies may create a new
  `ExternalRecord` row.
- Prior retrieval history is never overwritten or updated by a later call.
- No raw payload is ever fabricated by persistence — a record is only
  written when the caller explicitly supplies one; persistence never
  synthesizes `raw_response_json`/`raw_response_text`/`raw_response_hash`
  on its own.

## Stale normalization result policy

Normalization results are optimistic snapshots: they describe what was
true in the database at the moment normalization ran, not necessarily at
the moment persistence executes.

Before a `NEW` insert, persistence rechecks the same strong identifier(s)
normalization used to reach that `NEW` verdict, querying the database
directly, immediately before the insert.

If a collision is found:

- do not insert,
- return `FAILED` (or the equivalent conservative result already
  implemented for that entity).

**This recheck does not fully eliminate concurrent write races when the
database lacks a uniqueness constraint on the identifier in question.** It
narrows the race window considerably (recheck and insert both occur inside
one transaction, back-to-back), but it is not equivalent to a database
constraint enforced atomically by PostgreSQL itself. Where no such
constraint exists, two writers can still both pass the recheck and both
insert, if their transactions interleave exactly right. See "Concurrency
guarantees" below for exactly which identifiers this applies to.

## Concurrency guarantees

**Updated by Increment 11** -- see that section below for the full
before/after picture. Current state:

### DB-backed for at least one strong anchor

**Publication**
- PMID
- PMCID
- DOI

**Gene**
- SGD ID
- NCBI Gene ID
- KEGG Gene ID

**Organism**
- `(scientific_name, strain)` where applicable (only when `strain` is
  supplied — the partial unique index does not cover `strain IS NULL`
  rows)

**Reaction**
- `internal_id` (allocated by a PostgreSQL sequence as of Increment 11 --
  see "Increment 11" below; this was already DB-unique, but had no safe
  allocator before this increment)

**ReactionEnzyme** *(as of Increment 11)*
- `(reaction_id, protein_id)`
- `(reaction_id, complex_id)`

### Application-discipline-only / TOCTOU risk remains

**Organism**
- external identifier anchors lacking DB uniqueness (`ncbi_taxonomy_id`,
  `kegg_code`, `biocyc_id`) -- indexed as of Increment 11, still not unique

**Protein**
- UniProt ID -- reviewed in Increment 11, deliberately left non-unique

**Compound**
- ChEBI ID
- KEGG compound ID
- PubChem CID *(indexed as of Increment 11)*
- MetaCyc ID *(indexed as of Increment 11)*
- InChIKey

**Compartment**
- ontology/name/abbreviation identifiers -- indexed as of Increment 11
  (`ontology_id` alone, plus `(organism_id, name)`/`(organism_id,
  abbreviation)` composites), still not unique

### Reaction (NEW support)

`NEW` persistence is supported as of Increment 11 -- see that section
below.

## Entity-specific behavior

### Organism
- `scientific_name` required for creation.
- `strain` is preserved exactly as supplied, never inferred or defaulted.
- Freshness checks use the same anchors normalization itself uses:
  `ncbi_taxonomy_id`, `kegg_code`, `biocyc_id`, and `(scientific_name,
  strain)` together when `strain` is supplied.

### Publication
- `title` required for creation.
- Stable publication identifiers (PMID/PMCID/DOI) are reused safely —
  Publication is the one entity where every strong anchor is also
  database-unique.
- No PMID/PMCID/DOI is ever fabricated; each is written only if supplied.

### Gene
- Organism scope is preserved: `organism_id` is required and passed
  through exactly as given.
- No unsupported identifier inference — `kegg_gene_id` alone (a Level 1
  identity signal) is not treated as sufficient for creation, since it is
  not part of the schema's own creation-completeness rule.
- `uniprot_id` is never written from Gene normalization/persistence —
  UniProt identity belongs to Protein, not Gene, and `GeneIdentity` itself
  carries no such field.

### Protein
- `name` required for creation.
- `gene_id` is persisted only if explicitly supplied on the identity —
  never derived from UniProt data, gene-side metadata, or any other
  inference.
- No gene inference of any kind.

### Compound
- `canonical_name` required for creation.
- Chemistry (`formula`, `charge`, `inchi`) is preserved exactly as
  supplied, literally.
- No protonation, charge, or stereochemistry normalization is ever
  performed by persistence.
- Synonyms (`CompoundSynonym` rows) are written only at creation time in
  Increment 10 — an existing `MATCHED` compound's synonym set is never
  extended or modified.

### Compartment
- `name` required for creation.
- An explicit `organism_id=None` reference scope is preserved as a
  genuine reference-scope row, not treated as missing or invalid data.
- No auto-cloning of reference compartments into organism-specific copies
  — the caller decides scope by which `organism_id` it passes.

### Reaction
- `MATCHED`/`AMBIGUOUS`/`CONFLICTED`/`UNRESOLVED` are all fully supported.
- `NEW` is supported as of Increment 11 (see that section below) --
  `internal_id` is allocated via a PostgreSQL sequence, the reaction row,
  its participants, its `SourceCrossReference`, and its `ExternalRecord`
  are created together inside one `SAVEPOINT`.
- Participants (`reaction_participant` rows) are never inferred by this
  layer, and are still not required for `NEW` (unchanged by Increment 11).
- Existing Reaction participants are not changed on `MATCHED` reuse — a
  matched row's recorded structure is never extended, corrected, or
  reconciled against the incoming record's own participants.

### ReactionEnzyme
- Exactly one of `protein_id` / `complex_id` is required — enforced by
  `ReactionEnzymeIdentity` itself (application layer), and, as of
  Increment 11, by a database `CHECK` constraint as well.
- `relationship` is required for `NEW` creation (the only `NOT NULL`,
  non-identity column on this table).
- No organism inference — this table has no organism column, and no
  organism-consistency check is performed between a reaction and its
  associated protein/complex.
- No `SourceCrossReference` attachment (see "SourceCrossReference
  policy").
- As of Increment 11, `(reaction_id, protein_id)`/`(reaction_id,
  complex_id)` are each database-unique; the freshness recheck remains as
  a fast first line of defense, with the database constraint as the actual
  concurrency authority.

## Reaction internal_id issue -- RESOLVED (Increment 11)

This was previously a blocking architecture decision. See "Increment 11"
below for the finalized mechanism. The facts and rejected approaches that
motivated the original decision are kept here for historical record:

- `Reaction.internal_id` is `NOT NULL`.
- `Reaction.internal_id` is database-unique.
- Normalization intentionally does not assign it — `ReactionIdentity` has
  no `internal_id` field at all, since it is a persistence identifier, not
  incoming biological identity.
- No production-safe allocator existed anywhere in this repository prior
  to Increment 11.

Rejected then and still rejected now:

- `MAX + 1` (races under concurrent inserts)
- test-only counters (`itertools.count()`-style, as seen in
  `tests/database/test_group_c_models.py`, explicitly not production-safe)
- process-local counters (do not survive multiple processes/workers)

Of the potential solutions originally listed (a database sequence, an
application-level reservation table, or an explicit allocator service with
retry-on-uniqueness-violation), Increment 11 selected **a database
sequence** as the simplest robust option consistent with the existing
schema. See "Increment 11" below for the exact mechanism.

## Review boundary

Persistence never sets `HUMAN_ACCEPTED`.

`AMBIGUOUS` and `CONFLICTED` produce caller-visible review requirements
only — a `PersistenceResult` with `action=REQUIRES_REVIEW` and
`review_required=True`. Nothing is written to the database as a result of
either status.

No automatic `ReviewEvent` rows or human-review-state transitions are
created by this layer, for any status.

## Errors

- `EntityTypeMismatchError` represents programmer/API misuse: a caller
  passed a `NormalizationResult` for the wrong entity type into a given
  `persist_*` function. This is a defect in the calling code, not a data
  condition, so it is raised as an exception rather than returned as a
  result.
- Expected scientific/data failures (a missing required field, a stale
  collision, Reaction `NEW`) return a structured
  `PersistenceResult(action=FAILED, reason=...)` — never an exception.
- Partial persistence is not allowed: a `persist_*` call either completes
  its full unit of work (row creation plus any provenance attachment) or
  fails before writing anything for that call. There is no code path that
  leaves a half-written entity behind.

## Increment 11: Persistence Schema Hardening and Reaction internal_id Allocation

This section documents the finalized outcome of the schema-hardening
increment that followed Increment 10. It resolves the concrete
persistence/concurrency issues Increment 10 discovered and reported --
it does not redesign the schema generally, and it does not touch
evidence/claim persistence, biological inference, or anything else out of
this increment's stated scope.

### Reaction `internal_id` allocation

**Mechanism**: a PostgreSQL sequence, `reaction_internal_id_seq`, created
by migration `0009_persistence_hardening`. `app.persistence.
reaction_id_allocator.allocate_reaction_internal_id` calls `nextval()` on
it and formats the result as `FFA_R####` (e.g. `FFA_R0001`), zero-padded
to at least four digits with no fixed upper bound.

**Why a sequence**: `nextval()` is atomic and safe under arbitrarily many
concurrent PostgreSQL sessions by construction, with no application-level
locking required. This was chosen over the two explicitly-rejected
alternatives (`SELECT MAX(...) + 1`, which races under concurrent
transactions; and a process-local/`itertools.count()` counter, safe only
within one Python process) as the simplest robust option consistent with
the existing schema -- no new table, no row-locking scheme, no retry loop.

**Non-transactional, on purpose**: a sequence's `nextval()` advance is not
undone by `ROLLBACK`. If a caller's transaction that allocated an id is
later rolled back (for example because a supplied participant's
`compound_id` doesn't exist), that number is permanently consumed and
never reused -- a gap, never a duplicate. `docs/02_database_schema.md`
requires ids to "remain stable after creation," not gapless numbering, so
this is an accepted trade-off.

**Adoption onto existing data**: the same migration advances the sequence
past the highest existing `FFA_R####`-formatted `internal_id` already
present, in one `setval()` call executed once during the migration itself
-- not a per-insert allocator, and not a re-introduction of the rejected
`MAX + 1` pattern (a one-time schema-migration-time adoption, run outside
concurrent write traffic, is the standard safe way to put a sequence under
a table that may already have manually-numbered rows). On an empty table
this is a no-op and the sequence starts at 1.

**Format**: `FFA_R####` is preserved unchanged from
`docs/02_database_schema.md`/`docs/04_api_spec.md`/
`docs/06_export_format.md`'s existing documented convention. No existing
`Reaction` row is renumbered. The `FFA_` prefix is project-scope-specific
(the initial yeast free-fatty-acid project) -- whether a future,
differently-scoped project should use a different prefix, and how that
would be configured, is unresolved and explicitly out of scope here; the
allocator hard-codes `FFA_R` rather than inventing an unrequested
configuration mechanism for a problem that does not exist yet (see Open
Question 10 below).

### Reaction `NEW` persistence, now supported

`persist_reaction` creates the `Reaction` row, its `reaction_participant`
rows, its `SourceCrossReference`, and its `ExternalRecord` together inside
one `SAVEPOINT` (`session.begin_nested()`). An `IntegrityError` raised
anywhere in that block (an invalid `compound_id`/`compartment_id` on a
participant, or -- vanishingly unlikely given the sequence, but still
handled -- an `internal_id` collision) rolls back only that unit of work
and is converted to a conservative `FAILED` result; nothing else is
caught, so an unrelated bug is not silently absorbed as if it were an
expected data condition. The caller's own outer transaction is untouched
either way, consistent with this package never committing or rolling it
back itself.

Participants are persisted exactly as supplied: literal multiplicity,
literal `Decimal` stoichiometry, literal `compartment_id` (including
`None`) -- no aggregation, no proportional-ratio reduction, no orientation
reversal, no compartment inference. Participants remain **not required**
for `NEW` -- this increment does not add that stricter rule (Open Question
J, `docs/07_normalization_design.md`, remains open).

### ReactionEnzyme: database-enforced XOR and pairwise uniqueness

Migration `0009_persistence_hardening` adds:

- `ck_reaction_enzyme_exactly_one_target`, a `CHECK` constraint enforcing
  `(protein_id IS NOT NULL AND complex_id IS NULL) OR (protein_id IS NULL
  AND complex_id IS NOT NULL)` -- promoting `app.normalization.
  reaction_enzyme`'s already-finalized application-layer XOR rule to a
  database guarantee that holds even outside the ORM/normalization layer.
- `uq_reaction_enzyme_reaction_id_protein_id` (partial unique index,
  `WHERE protein_id IS NOT NULL`) and
  `uq_reaction_enzyme_reaction_id_complex_id` (partial unique index,
  `WHERE complex_id IS NOT NULL`) -- enforcing that a given
  `(reaction_id, protein_id)`/`(reaction_id, complex_id)` pair is recorded
  at most once, regardless of `relationship` (which
  `app.normalization.reaction_enzyme` already treats as inert metadata for
  identity purposes).

`persist_reaction_enzyme`'s freshness recheck remains in place as a fast
first line of defense, but the two unique indexes are now the actual
concurrency authority: its `NEW` path wraps the insert in a `SAVEPOINT`
and catches the residual-race `IntegrityError`, converting it to `FAILED`
rather than letting it escape as a raw exception (verified directly by a
two-connection concurrency test, see "Testing summary").

### Reviewed and deliberately left non-unique

Each of the following was evaluated individually against three questions
(does normalization treat it as global Level-1 identity; can one external
identifier legitimately map to multiple rows for a real reason; does
existing code/tests rely on that ambiguity being possible) and left
unchanged:

- **`Protein.uniprot_id`**: an existing database test,
  `test_protein_uniprot_id_is_not_unique`
  (`tests/database/test_group_b_models.py`), directly encodes
  non-uniqueness as a deliberate, tested design decision, and no proof was
  found that one UniProt accession must map to exactly one `Protein` row
  globally.
- **Compound's `chebi_id`/`kegg_compound_id`/`pubchem_cid`/`metacyc_id`/
  `inchikey`**: `app.normalization.compound` explicitly treats a duplicate
  row sharing any of these as a live, expected `AMBIGUOUS` outcome, and an
  existing database test
  (`test_compound_external_identifiers_are_not_unique`) already encodes
  that as intentional.
- **Compartment's `ontology_id`/`name`/`abbreviation`**: reference rows
  (`organism_id IS NULL`) and organism-specific rows may legitimately
  coexist and even share any of these three fields (Open Questions F/G/H,
  `docs/07_normalization_design.md`) -- collapsing that distinction was
  never in scope here.
- **Reaction's `kegg_reaction_id`/`metacyc_reaction_id`/`rhea_id`**:
  `app.normalization.reaction` explicitly allows duplicate rows for a
  strong external ID and classifies them `AMBIGUOUS`.

### New non-unique indexes

Added purely to back existing persistence freshness-recheck queries and
normalization lookup methods that previously had no index at all:
`organism.kegg_code`, `organism.biocyc_id`, `compound.pubchem_cid`,
`compound.metacyc_id`, `compartment.ontology_id`,
`compartment(organism_id, name)`, `compartment(organism_id,
abbreviation)`, `reaction.metacyc_reaction_id`. None of these is a
uniqueness constraint.

### Organism, Gene, Publication

`Organism`'s `(scientific_name, strain)` partial unique index is
unchanged. `kegg_code`/`biocyc_id` gained non-unique indexes (matching
`ncbi_taxonomy_id`'s pre-existing one) but not uniqueness: multiple
strain-specific `Organism` rows for one species may legitimately share a
species-level KEGG code or BioCyc ID, the same rationale the model's own
docstring already gives for `scientific_name` itself. `Gene` and
`Publication` already had database uniqueness matching their normalizers'
identity rules (Increment 4's own correction, and Publication's
partial-unique PMID/PMCID/DOI indexes) and needed no change; both were
reviewed and confirmed still correct.

### Existing-data safety

Neither the `ck_reaction_enzyme_exactly_one_target` CHECK nor the two new
partial unique indexes on `reaction_enzyme` include any automatic cleanup
or deduplication logic. No seed migration creates a `ReactionEnzyme` row
of any kind, so a project-controlled development/test database migrates
cleanly. If a real deployment's existing data violates either constraint,
migration `0009_persistence_hardening` is expected to fail loudly at that
`ALTER TABLE`/`CREATE UNIQUE INDEX` statement -- that data must be resolved
by a human before upgrading, not silently discarded by this migration.

### Migration mechanics note

`op.create_check_constraint`/`op.create_index` apply
`app/db/base.py`'s custom naming convention (because `migrations/env.py`
sets `target_metadata = Base.metadata`) to any string name passed in,
re-wrapping it in the `ck_%(table_name)s_%(constraint_name)s` template a
second time unless the name is marked already-final with
`sqlalchemy.sql.naming.conv(...)` -- the same pitfall already documented
on `reaction_participant`'s stoichiometry `CheckConstraint` (migration
`0004_reaction`). Migration `0009_persistence_hardening` uses `conv()` for
`ck_reaction_enzyme_exactly_one_target` for exactly this reason; the
partial unique indexes did not need it (`Index`'s naming-convention
substitution only applies to auto-generated, unnamed indexes).

## Open architecture questions

1. ~~Reaction `internal_id` allocation strategy.~~ **Resolved, Increment
   11**: a PostgreSQL sequence. See "Increment 11" below.
2. Which currently non-unique scientific identifiers should receive
   database-level uniqueness constraints. **Reviewed, Increment 11**:
   `Protein.uniprot_id`, Compound's five identifiers, Compartment's three
   fields, and Reaction's three external identifiers were each evaluated
   individually and deliberately left non-unique (see "Increment 11"
   below for the per-identifier reasoning). Still open for any of them if
   future evidence changes the answer.
3. ~~Whether `ReactionEnzyme` should receive pairwise uniqueness
   constraints~~. **Resolved, Increment 11**: yes, on both
   `(reaction_id, protein_id)` and `(reaction_id, complex_id)`,
   independent of `relationship`.
4. ~~Whether `ReactionEnzyme` should receive an XOR CHECK constraint~~.
   **Resolved, Increment 11**: yes,
   `ck_reaction_enzyme_exactly_one_target`.
5. Whether matched entities should ever accept non-destructive metadata
   enrichment in a later increment (as opposed to today's pure reuse with
   no field updates at all). **Still open** -- unaffected by Increment 11.
6. Whether `AMBIGUOUS`/`CONFLICTED` persistence should later create
   explicit machine `ReviewEvent` records, rather than only returning
   `REQUIRES_REVIEW` to the caller. **Still open.**
7. Whether a `SourceCrossReference` conflict (an external identifier
   already attached to a *different* entity than the one being persisted)
   should become `FAILED` or `REQUIRES_REVIEW`. **Still open.**
8. What `Reaction.organism_id = NULL` means — no documented "standard/
   reference reaction" concept exists the way Compartment's seeded
   reference rows do. **Still open** -- deliberately left undefined by
   Increment 11.
9. Whether the system expects concurrent multi-writer ingestion; if yes,
   freshness rechecks alone are insufficient and the entities listed under
   "Application-discipline-only / TOCTOU risk remains" above need real
   database constraints before that can be considered safe. **Partially
   addressed**: Reaction `internal_id` and `ReactionEnzyme`'s pair
   uniqueness now have real constraints (Increment 11); Organism's
   external identifiers, Protein's UniProt ID, Compound's five
   identifiers, and Compartment remain application-discipline-only.
10. *(New, Increment 11)* Whether a future, differently-scoped project
    should use a Reaction `internal_id` prefix other than `FFA_`, and how
    that would be configured -- unresolved; the allocator hard-codes
    `FFA_R####` for this project (see "Increment 11" below).

## Testing summary

`tests/persistence/` and `tests/database/` cover:

- the complete status-to-action matrix, for every supported entity
- `MATCHED` non-destructive reuse (incoming metadata differences never
  mutate the matched row)
- `NEW` creation
- stale-`NEW` collision detection
- `SourceCrossReference` idempotency
- `ExternalRecord` append-only behavior
- transaction ownership (`persist_*` never commits)
- entity-type safety (`EntityTypeMismatchError`)
- ReactionEnzyme's one-target (`protein_id` XOR `complex_id`) rule, now
  also proven at the database level (`tests/database/test_group_c_models.py`)
- the absence of any `HUMAN_ACCEPTED` transition anywhere in this layer

**Added in Increment 11** (see that section below for full detail):
Reaction `NEW` creation succeeding end to end (allocated `internal_id`,
correct `FFA_R####` format, distinct ids across repeated creations,
literal participant persistence, idempotent cross-reference attachment,
`ExternalRecord` provenance, and a forced-participant-failure rollback
test proving the whole reaction is rolled back as a unit without poisoning
the caller's transaction); direct database tests for the new
`ck_reaction_enzyme_exactly_one_target` CHECK and the two new partial
unique indexes; database tests proving each reviewed-and-declined
identifier (Protein UniProt ID, Compound's five identifiers, Reaction's
three external identifiers, Organism's `kegg_code`/`biocyc_id`,
Compartment's `ontology_id`/`name`/`abbreviation`) remains non-unique;
database tests proving the new non-unique indexes exist via
`inspect(...).get_indexes(...)`; and two genuine multi-connection
PostgreSQL concurrency tests (`tests/persistence/test_concurrency.py`) --
concurrent `Reaction.internal_id` allocation producing no duplicates
across many real connections, and two independent sessions racing to
create the same `(reaction_id, protein_id)` pair yielding exactly one
`CREATED` and one structured `FAILED` result, never a raw unhandled
exception.

Increment 10 validation passed, and Increment 11 re-validated all of it
plus the additions above:

- `pytest tests/database/`
- `pytest tests/persistence/`
- `pytest tests/normalization/`
- `pytest` (full suite)
- SQLAlchemy warnings-as-errors (`-W error::sqlalchemy.exc.SAWarning`)
- `ruff check .`
- `git diff --check`
- `alembic upgrade head` from an empty database, `alembic upgrade` from
  the prior head (`0008_external_records_reviews`) to the new head, and a
  downgrade/upgrade round trip

---

## Final rule

Persistence may apply a normalization decision, but it may not reinterpret
it.

When safe database enforcement is absent, fail conservatively rather than
relying on optimistic application behavior as if it were a hard guarantee.
