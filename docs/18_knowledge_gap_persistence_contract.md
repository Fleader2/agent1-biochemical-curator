# Agent 1 KnowledgeGap Persistence Contract

## 1. Purpose

Harden the `KnowledgeGap` schema so Increment 21's deterministic
`KnowledgeGapCandidate` output can be persisted without losing its
audit-critical structure, and provide a safe, idempotent persistence layer
for it. Faithful storage only — no new detection, no experiment design, no
hypothesis generation, no LLM.

## 2. Pipeline position

```text
Knowledge Gap Detection (Increment 21, app.knowledge_gaps.analysis)
    -> KnowledgeGap persistence (this increment, app.persistence.knowledge_gap)
```

Consumes an already-produced `KnowledgeGapCandidate`/`KnowledgeGapAnalysisResult`
as a read-only input. Never calls `analyze_knowledge_gaps`, never
recomputes confidence, never normalizes or resolves an entity.

## 3. Schema hardening

Original schema (migration `0007_regulation_assumptions_gaps`): `id`,
`subject_type` (`VARCHAR NOT NULL`), `subject_id` (`UUID`, no FK),
`missing_information` (`TEXT NOT NULL`), `importance` (`VARCHAR`),
`model_impact` (`TEXT`), `suggested_experiment` (`TEXT`), `priority`
(`INTEGER`, no range constraint), `status` (`VARCHAR`, no vocabulary),
`created_at`/`updated_at`. No indexes beyond the primary key, no CHECK
constraints. Verified directly against `app/models/knowledge_gap.py` and
`migrations/versions/0007_regulation_assumptions_gaps.py` before making
any change — nothing was assumed from an older design document.

Migration `0010_knowledge_gap_hardening` adds seven nullable columns:
`gap_type`, `severity`, `reason_codes_json`, `supporting_claim_ids_json`,
`supporting_evidence_ids_json`, `supporting_entity_ids_json`,
`identity_key` — plus two `CHECK` constraints (`gap_type`, `severity`) and
five indexes (§6/§9/§12). `status`/`importance`/`priority`/`model_impact`/
`suggested_experiment` are unchanged at the schema level. All seven new
columns are nullable so no historical row (there are none today, but the
design does not assume that) is ever forced to receive a fabricated value.

## 4. Persisted `KnowledgeGap` fields

| `KnowledgeGap` column | Source |
|---|---|
| `subject_type` | `KnowledgeGapCandidate.entity_type`, verbatim |
| `subject_id` | `KnowledgeGapCandidate.entity_id`, verbatim (never fabricated) |
| `missing_information` | `KnowledgeGapCandidate.explanation`, verbatim, never rewritten |
| `gap_type` | `KnowledgeGapCandidate.gap_type.value` |
| `severity` | `KnowledgeGapCandidate.severity.value` |
| `reason_codes_json` | `list(KnowledgeGapCandidate.reason_codes)` |
| `supporting_claim_ids_json` | `[str(i) for i in candidate.supporting_claim_ids]` |
| `supporting_evidence_ids_json` | `[str(i) for i in candidate.supporting_evidence_ids]` |
| `supporting_entity_ids_json` | `[str(i) for i in candidate.supporting_entity_ids]` |
| `identity_key` | `compute_identity_key(candidate)` (§10/§11) |
| `status` | Caller-supplied, default `"OPEN"` (§12) |
| `priority` | Caller-supplied only; `NULL` otherwise (§13) |
| `model_impact` | Caller-supplied only; `NULL` otherwise (§13) |
| `importance` | Never written by this module; always `NULL` (§13) |
| `suggested_experiment` | Never written by this module; always `NULL` (§13) |

`subject_text`/`predicate`/`object_text` on `KnowledgeGapCandidate` have no
destination on `KnowledgeGap` at all — the schema has no columns for them,
and `subject_text`/`object_text` are always `None` in the current
detection contract in any case (see `docs/17...` §4). Not a regression:
nothing audit-critical is lost, since `predicate` (the one of the three
ever populated) is not part of any current detection rule's identity or
explanation.

## 5. `GapType` persistence

Plain `VARCHAR`, guarded by a `CHECK` constraint listing the exact 11
current `GapType` members (`app/models/knowledge_gap.py`'s
`GAP_TYPE_VALUES`) — **not** a native PostgreSQL `ENUM`. This deliberately
follows the established convention for every other *local-only*,
upper-layer-defined controlled vocabulary already persisted in this schema
(`evidence.directness` for `app.extraction.types.Directness`,
`review_event.reviewer_type` for `app.review.types.ReviewerType`): both are
plain strings, not native enums, specifically because `app/models/` is the
lowest layer in this repository's import graph and must never import from
an upper-layer package such as `app.knowledge_gaps` (nor duplicate its enum
definition). A native `ENUM` remains available to a future migration if
this vocabulary ever earns first-class database status, as
`claim_status`/`curation_state` have, but that was not warranted here.

## 6. `GapSeverity` persistence

Identical treatment: plain `VARCHAR` with a `CHECK` constraint listing all
5 current `GapSeverity` members (`GAP_SEVERITY_VALUES`), `CRITICAL`
included even though no detection rule currently produces it — the
in-memory enum already reserves it, so the persisted vocabulary mirrors it
exactly (Increment 22 instructions, Step 5).

## 7. Reason-code persistence

`reason_codes_json`: a `JSONB` array of strings, order-preserving, exact
values, never concatenated into a single text blob. An empty list is
valid (matches `KnowledgeGapCandidate.reason_codes`'s own default `()`).

## 8. Supporting Claim/Evidence provenance

`supporting_claim_ids_json`/`supporting_evidence_ids_json`: independent
`JSONB` arrays of UUID strings, never merged into one generic bag (Step
8). `supporting_entity_ids_json` is persisted with the same treatment for
losslessness, even though **no currently-implemented detection rule ever
populates `KnowledgeGapCandidate.supporting_entity_ids`** (verified by
inspection of `app/knowledge_gaps/rules.py` — every rule uses the single
anchor `entity_id`, never the polymorphic `supporting_entity_ids` list).
This field's UUIDs are not typed to a specific table (unlike
`supporting_claim_ids`/`supporting_evidence_ids`, whose owning table is
always unambiguous) — persisting them losslessly as an opaque JSONB array
does not require resolving that ambiguity, since no foreign key or join is
created against them; a future rule that populates this field
meaningfully would still need to decide how a consumer disambiguates which
table each id belongs to before that data becomes actionable, but no data
is silently dropped in the meantime.

## 9. Subject mapping

`KnowledgeGapCandidate.entity_type` → `KnowledgeGap.subject_type`;
`KnowledgeGapCandidate.entity_id` → `KnowledgeGap.subject_id`. Both
verbatim; `subject_id` is `NULL` exactly when `entity_id` is `None`
(`KnowledgeGapCandidate` already permits this for a gap-type with no
single natural anchor — none of the 11 currently implemented rules
actually produce one, but the mapping does not assume otherwise).

## 10. Identity/deduplication policy

Reuses `KnowledgeGapCandidate.identity_key()` **exactly** —
`(gap_type, entity_type, entity_id, supporting_claim_ids)` — the same
tuple `app.knowledge_gaps.analysis._dedupe` already uses to deduplicate
gaps in memory. This module does not invent a second, conflicting identity
rule; it only turns that same tuple into a database-stable string (§11).

## 11. Identity-key algorithm/version

`app.persistence.knowledge_gap.compute_identity_key`:

1. Take `candidate.identity_key()`'s four ingredients.
2. Build a canonical dict: `gap_type.value`, `entity_type`, `str(entity_id)`
   (or `None`), and `supporting_claim_ids` **sorted** as strings (input
   ordering never changes the result).
3. Serialize with `json.dumps(..., sort_keys=True, separators=(",", ":"))`
   — a compact, deterministic, canonical form.
4. Digest with SHA-256 (never Python's built-in `hash()`, which is
   process-randomized for strings and not stable across runs/processes).
5. Prefix with the version tag `"kg-v1:"`.

`identity_key` is persisted (§3) and carries a partial unique index
(`WHERE identity_key IS NOT NULL`) — the version prefix means a future
change to this algorithm produces keys that can never collide with
historically-computed ones.

## 12. Status vocabulary

`app.persistence.knowledge_gap_types.KnowledgeGapStatus`: `OPEN` (default),
`RESOLVED`, `DISMISSED`. Enforced **only** at the persistence-API boundary
(`persist_knowledge_gap` raises `ValueError` for any other string) — `
KnowledgeGap.status` itself remains an unconstrained `VARCHAR` at the
schema level, deliberately: this is a project-invented workflow vocabulary
with no authoritative specification anywhere in `docs/`, unlike
`ClaimStatus`/`CurationState`, and adding a database `CHECK`/`ENUM` for it
would overreach what this increment was asked to harden. Default for every
newly created gap: `OPEN`.

## 13. Priority / importance / model_impact / suggested_experiment policies

* **`priority`**: never inferred from `severity` (no `HIGH = 80` style
  mapping exists or is invented). `NULL` unless the caller passes
  `priority=` explicitly.
* **`importance`**: never written by this module at all, under any
  circumstance — `KnowledgeGapCandidate` has no corresponding field, and
  `severity` is never duplicated into it.
* **`model_impact`**: never derived from `severity` or anything else.
  `NULL` unless the caller passes `model_impact=` explicitly.
* **`suggested_experiment`**: never populated automatically, never a
  placeholder such as `"TBD"`. Always `NULL` from this module. Experiment
  design remains explicitly out of scope for this and every prior
  increment.

## 14. Idempotency

Persisting the identical deterministic gap (same `identity_key`) twice: the
first call returns `CREATED`; every subsequent call returns
`REUSED_EXISTING` with the same `knowledge_gap_id`, and never overwrites
the existing row's structured fields — a different `explanation` or a
later timestamp never creates a second row or rewrites the first (Step 25
and Step 26's explicit conservative policy).

## 15. Terminal-gap behavior

If a row with the identical `identity_key` already exists but its `status`
is `RESOLVED` or `DISMISSED`, `persist_knowledge_gap` returns
`REQUIRES_REVIEW` (carrying that row's id) rather than silently reopening
it or creating a duplicate. No automatic reopen workflow exists in this
increment — resolving that is left to a human or a future increment.

## 16. Transaction ownership

Exactly mirrors `app.persistence.reaction`: one `SAVEPOINT`
(`session.begin_nested()`) wraps the insert attempt only. `IntegrityError`
is caught and resolved via a post-failure re-query by `identity_key`
(§17); every other exception propagates. This module never calls
`session.commit()`/`session.rollback()` — the caller owns the transaction.

## 17. Concurrency guarantees

The partial unique index on `identity_key` is the real concurrency
authority. Two sessions racing to persist the identical deterministic gap:
the loser's `INSERT` raises `IntegrityError`; this module catches it inside
its own `SAVEPOINT`, re-queries by `identity_key`, and returns
`REUSED_EXISTING` pointing at the winner's row. Verified directly against
real concurrent PostgreSQL connections
(`tests/persistence/test_knowledge_gap.py::test_concurrent_same_gap_creation_yields_one_row`,
mirroring `tests/persistence/test_concurrency.py`'s existing pattern) — one
row created, one `REUSED_EXISTING`, zero duplicates, zero unhandled
exceptions.

**Remaining TOCTOU risk**: none for any row this module itself creates —
`compute_identity_key` always returns a non-null string, so every row this
module writes is protected by the unique index. A row manually inserted
with `identity_key IS NULL` is not covered by the unique index (by
definition — the index is partial), but no code path in this repository
ever creates such a row through `persist_knowledge_gap`.

## 18. Read API

`get_knowledge_gap(session, knowledge_gap_id) -> KnowledgeGap | None` and
`list_open_knowledge_gaps(session, *, limit=None) -> tuple[KnowledgeGap, ...]`
(status `OPEN` only, ordered by `created_at` ascending with `id` as a
stable-not-chronological tiebreaker — the identical, disclosed convention
`app.review.history.get_review_history` already uses). Both read-only.

## 19. Explicit non-responsibilities

No new detection rule, no experiment design, no suggested-experiment
generation, no hypothesis generation, no review UI, no LLM call, no
confidence recalculation, no entity normalization, no `Claim`/`Evidence`
mutation. `app.persistence.knowledge_gap` never imports
`app.knowledge_gaps.analysis`, `app.confidence`, `app.normalization`, or
any connector — verified directly by source-level tests.

## 20. Migration/backward compatibility

`0010_knowledge_gap_hardening` (revises `0009_persistence_hardening`) adds
only nullable columns and new constraints/indexes on the existing
`knowledge_gap` table — no existing row (there are none today) is
mutated, no destructive backfill is performed, and `downgrade()` removes
exactly what `upgrade()` added, in reverse order. Verified via the
existing `tests/database/test_migrations.py` suite (empty-database-to-head
and full downgrade-to-base round trips) plus new schema-level assertions
in `tests/database/test_group_f_models.py` for the two new `CHECK`
constraints, the five new indexes, and literal `JSONB`/nullable-column
round-tripping.

## 21. Open architecture questions

1. Should `KnowledgeGap.status` eventually earn a real database `CHECK`/
   `ENUM`, the way `Claim.status`/`Reaction.curation_state` have? Deferred
   — this vocabulary has no authoritative specification today (§12).
2. Should `gap_type`/`severity` be promoted to native PostgreSQL `ENUM`
   types once/if this vocabulary is considered stable enough to warrant
   first-class database status, mirroring `claim_status`/`curation_state`?
   Deferred by deliberate choice (§5/§6), not an oversight.
3. `supporting_entity_ids_json` is persisted but currently always empty in
   practice (§8) — a future rule that populates it meaningfully will need
   to resolve the "which table does this UUID belong to" ambiguity before
   the field becomes actionable for a join or a UI.
4. No automatic reopen workflow exists for `RESOLVED`/`DISMISSED` gaps
   (§15) — a future increment may want one, explicitly human-gated.

## 22. Final architectural rule

> KnowledgeGap persistence records deterministic analysis output.
>
> It does not reinterpret the gap.
>
> It does not propose an experiment.
>
> It preserves enough structure to explain exactly why the gap exists.
