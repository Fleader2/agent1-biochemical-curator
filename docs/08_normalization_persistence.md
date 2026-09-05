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

### Application-discipline-only / TOCTOU risk remains

**Organism**
- external identifier anchors lacking DB uniqueness (`ncbi_taxonomy_id`,
  `kegg_code`, `biocyc_id`)

**Protein**
- UniProt ID

**Compound**
- ChEBI ID
- KEGG compound ID
- PubChem CID
- MetaCyc ID
- InChIKey

**Compartment**
- ontology/name/abbreviation identifiers (this table carries no index or
  uniqueness constraint of any kind)

**ReactionEnzyme**
- `(reaction_id, protein_id)`
- `(reaction_id, complex_id)`

### Reaction

`NEW` persistence is currently unsupported because no safe production
allocator exists for the unique `internal_id` column. See "Reaction
internal_id issue" below.

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
- `NEW` currently always returns `FAILED` (see "Reaction internal_id
  issue").
- Participants (`reaction_participant` rows) are never inferred by this
  layer.
- Existing Reaction participants are not changed on `MATCHED` reuse — a
  matched row's recorded structure is never extended, corrected, or
  reconciled against the incoming record's own participants.

### ReactionEnzyme
- Exactly one of `protein_id` / `complex_id` is required — enforced by
  `ReactionEnzymeIdentity` itself, never both, never neither.
- `relationship` is required for `NEW` creation (the only `NOT NULL`,
  non-identity column on this table).
- No organism inference — this table has no organism column, and no
  organism-consistency check is performed between a reaction and its
  associated protein/complex.
- No `SourceCrossReference` attachment (see "SourceCrossReference
  policy").

## Reaction internal_id issue

This is a blocking architecture decision, not resolved in this increment.

Current facts:

- `Reaction.internal_id` is `NOT NULL`.
- `Reaction.internal_id` is database-unique.
- Normalization intentionally does not assign it — `ReactionIdentity` has
  no `internal_id` field at all, since it is a persistence identifier, not
  incoming biological identity.
- No production-safe allocator for it exists anywhere in this repository.

Do not use:

- `MAX + 1` (races under concurrent inserts)
- test-only counters (`itertools.count()`-style, as seen in
  `tests/database/test_group_c_models.py`, explicitly not production-safe)
- process-local counters (do not survive multiple processes/workers)

Potential future solutions include:

- a database sequence
- an application-level reservation table
- an explicit allocator service with retry-on-uniqueness-violation

This document does not select one. Until one is chosen and implemented,
`persist_reaction` returns `FAILED` for every `NEW` case, with a reason
string pointing back to this limitation.

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

## Open architecture questions

1. Reaction `internal_id` allocation strategy.
2. Which currently non-unique scientific identifiers should receive
   database-level uniqueness constraints.
3. Whether `ReactionEnzyme` should receive pairwise uniqueness
   constraints (on `(reaction_id, protein_id)` / `(reaction_id,
   complex_id)`).
4. Whether `ReactionEnzyme` should receive an XOR CHECK constraint
   enforcing "exactly one of protein_id/complex_id" at the database level,
   rather than only at the application layer.
5. Whether matched entities should ever accept non-destructive metadata
   enrichment in a later increment (as opposed to today's pure reuse with
   no field updates at all).
6. Whether `AMBIGUOUS`/`CONFLICTED` persistence should later create
   explicit machine `ReviewEvent` records, rather than only returning
   `REQUIRES_REVIEW` to the caller.
7. Whether a `SourceCrossReference` conflict (an external identifier
   already attached to a *different* entity than the one being persisted)
   should become `FAILED` or `REQUIRES_REVIEW`.
8. What `Reaction.organism_id = NULL` means — no documented "standard/
   reference reaction" concept exists the way Compartment's seeded
   reference rows do.
9. Whether the system expects concurrent multi-writer ingestion; if yes,
   freshness rechecks alone are insufficient and the entities listed under
   "Application-discipline-only / TOCTOU risk remains" above need real
   database constraints before that can be considered safe.

## Testing summary

`tests/persistence/` covers:

- the complete status-to-action matrix, for every supported entity
- `MATCHED` non-destructive reuse (incoming metadata differences never
  mutate the matched row)
- `NEW` creation
- stale-`NEW` collision detection
- `SourceCrossReference` idempotency
- `ExternalRecord` append-only behavior
- transaction ownership (`persist_*` never commits)
- entity-type safety (`EntityTypeMismatchError`)
- Reaction `NEW` failure
- ReactionEnzyme's one-target (`protein_id` XOR `complex_id`) rule
- the absence of any `HUMAN_ACCEPTED` transition anywhere in this layer

Increment 10 validation passed:

- `pytest tests/persistence/`
- `pytest tests/normalization/`
- `pytest` (full suite)
- SQLAlchemy warnings-as-errors (`-W error::sqlalchemy.exc.SAWarning`)
- `ruff check .`
- `git diff --check`

---

## Final rule

Persistence may apply a normalization decision, but it may not reinterpret
it.

When safe database enforcement is absent, fail conservatively rather than
relying on optimistic application behavior as if it were a hard guarantee.
