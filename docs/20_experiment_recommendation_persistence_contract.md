# Agent 1 Experiment Recommendation Persistence and Lifecycle Contract

## 1. Purpose

Persist Increment 23's deterministic `ExperimentRecommendation` output
durably, and add a minimal, auditable human lifecycle around it:

```text
KnowledgeGap -> ExperimentRecommendation -> Persistent Recommendation -> Human lifecycle decision
```

Persistence records what Agent 1 proposed. Lifecycle records what a human
decided to do with that proposal. Neither invents experiments, hypotheses,
or scientific conclusions.

## 2. Pipeline position

```text
KnowledgeGap persistence (Increment 22, app.persistence.knowledge_gap)
    -> Experiment Recommendation (Increment 23, app.experiment_recommendation)
    -> Recommendation persistence (this increment, app.persistence.experiment_recommendation)
    -> Recommendation lifecycle (this increment, app.review.experiment_recommendation_workflow)
```

## 3. Structured persistence model

No prior persistence model existed (verified by inspection: no
`experiment`/`recommendation`/`research plan`/`intervention`/`task` table
anywhere in `app/models/`/`migrations/` before this increment). Two new
tables, added by migration `0011_experiment_recommendation`:
`experiment_recommendation` (one row per persisted
`ExperimentRecommendation`) and `experiment_recommendation_event`
(append-only lifecycle audit trail). Neither reuses
`KnowledgeGap.suggested_experiment` (§22) nor `review_event` (§7 explains
why the latter was considered and rejected).

## 4. Generation status vs. lifecycle status

Kept as two independent columns on `experiment_recommendation`, never
conflated:

* `recommendation_status` -- Increment 23's own deterministic engine
  output (`RECOMMENDED`/`NOT_APPLICABLE`/`REQUIRES_HUMAN_DESIGN`/
  `INSUFFICIENT_INFORMATION`). Immutable once persisted; this increment's
  own code never rewrites it.
* `lifecycle_status` -- the separate human-workflow state this
  increment's transition API mutates (§12). A `RECOMMENDED` generation
  status never implies `ACCEPTED` lifecycle status. A `NOT_APPLICABLE`/
  `REQUIRES_HUMAN_DESIGN`/`INSUFFICIENT_INFORMATION` row is equally
  trackable through the same lifecycle -- even "no experiment recommended"
  is an auditable conclusion a human may want to formally accept-as-is or
  dismiss.

## 5. Recommendation identity

Reused exactly: `recommendation_identity` stores
`app.experiment_recommendation.recommender.compute_recommendation_identity`'s
own output (`"experiment-rec-v1:<sha256 of knowledge_gap_identity +
template_id + template_version>"`) verbatim. This module never recomputes
identity with a different algorithm. A database-level unique index on this
column (`uq_experiment_recommendation_recommendation_identity`) makes
repeated persistence of the same deterministic recommendation idempotent
at the database layer, not merely the application layer.

## 6. Template versioning

`template_id`/`template_version` are persisted verbatim from the
recommendation. A future change to a template's scientific content must
create a new `template_id`/`version` (and therefore a new
`recommendation_identity`) rather than silently rewriting an existing
row's meaning -- this increment enforces the negative case explicitly
(§11): an old identity paired with new content is a conflict, never a
silent update.

## 7. KnowledgeGap linkage

`knowledge_gap_id` is `NOT NULL` with `ON DELETE RESTRICT` -- a
recommendation can never exist without, or outlive, its originating
`KnowledgeGap` row, the same ownership-FK convention as
`evidence.claim_id`.

**Why `experiment_recommendation_event` is a dedicated table, not a reuse
of `review_event`.** `review_event.previous_state`/`new_state` are backed
by a native PostgreSQL `curation_state` `ENUM` whose fixed member set
(`PROPOSED`/`MACHINE_REVIEWED`/`NEEDS_REVIEW`/`HUMAN_ACCEPTED`/`REJECTED`)
has no room for `ACCEPTED`/`DEFERRED`/`SUPERSEDED` at all. Reusing it would
require either misusing `HUMAN_ACCEPTED`/`REJECTED` to mean something they
were never defined to mean (with no representation whatsoever for
`DEFERRED`/`SUPERSEDED`), or altering a shared enum type also used by
`Claim` review history for an unrelated entity's workflow. Both were
rejected as the exact semantic conflation
`docs/16_review_workflow_contract.md` already warns against. This decision
was made deliberately, after inspecting `review_event`'s actual schema, not
assumed.

## 8. Persisted recommendation fields

| `experiment_recommendation` column | Source | Type |
|---|---|---|
| `knowledge_gap_id` | caller-supplied | UUID, FK, NOT NULL |
| `recommendation_identity` | `compute_recommendation_identity(recommendation)` | VARCHAR, unique |
| `gap_type` | `recommendation.gap_type.value` | VARCHAR + CHECK |
| `gap_severity` | `recommendation.gap_severity.value` | VARCHAR + CHECK |
| `recommendation_status` | `recommendation.status.value` | VARCHAR + CHECK |
| `experiment_class` | `recommendation.experiment_class.value` or `NULL` | VARCHAR + CHECK, nullable |
| `objective` | `recommendation.objective` | TEXT, NOT NULL |
| `target_entity_type`/`target_entity_id` | `recommendation.target_entity_type`/`target_entity_id` | VARCHAR/UUID, nullable |
| `required_measurement` | `recommendation.required_measurement` | TEXT, nullable |
| `required_comparison` | `recommendation.required_comparison` | TEXT, nullable (see §9 — **not** JSONB) |
| `experimental_context_requirements_json` | `list(recommendation.experimental_context_requirements)` | JSONB, NOT NULL |
| `success_criterion` | `recommendation.success_criterion` | TEXT, nullable |
| `rationale` | `recommendation.rationale` | TEXT, NOT NULL |
| `supporting_claim_ids_json` | `[str(i) for i in ...]` | JSONB, NOT NULL |
| `supporting_evidence_ids_json` | `[str(i) for i in ...]` | JSONB, NOT NULL |
| `reason_codes_json` | `list(recommendation.reason_codes)` | JSONB, NOT NULL |
| `template_id`/`template_version` | verbatim | VARCHAR, NOT NULL |
| `lifecycle_status` | `"PROPOSED"` at creation; mutated only by the transition API | VARCHAR + CHECK, default `PROPOSED` |

**Deliberate deviation from the increment's own suggested schema**:
`required_comparison` is a plain `TEXT` column, not `required_comparison_json`
(`JSONB`). `ExperimentRecommendation.required_comparison` is a scalar
`str | None` in Increment 23's actual data contract (verified directly
against `app/experiment_recommendation/types.py`), never a list — storing
a single string as a one-element JSON array would not preserve anything
additional and would only complicate every reader. Every other list/tuple
field genuinely is a list and is stored as JSONB (§9).

## 9. JSON provenance fields

`experimental_context_requirements_json`/`supporting_claim_ids_json`/
`supporting_evidence_ids_json`/`reason_codes_json` are `JSONB` arrays,
never flattened into one text blob, preserving the exact order and exact
string/UUID values of their in-memory tuple counterparts.

## 10. Idempotency

Persisting the identical deterministic recommendation (same
`recommendation_identity`) twice: the first call returns `CREATED`; every
subsequent call returns `REUSED_EXISTING` with the same
`recommendation_id`, and never overwrites the existing row's content.

## 11. Identity-content conflict

`_content_matches` compares every field the identity logically represents
(`knowledge_gap_id`, `gap_type`, `gap_severity`, `recommendation_status`,
`experiment_class`, `objective`, `target_entity_type`/`target_entity_id`,
`required_measurement`, `required_comparison`,
`experimental_context_requirements` (as a set — see below),
`success_criterion`, `rationale`, `supporting_claim_ids`/
`supporting_evidence_ids`/`reason_codes` (as sets), `template_id`,
`template_version`). Id-list/reason-code fields are compared as sets, not
ordered sequences -- their order is not part of the identity computation
(§5 sorts them before hashing) and is not semantically meaningful for
conflict-detection purposes, even though storage itself preserves whatever
order the caller supplied (§9). An identity match with differing content
raises `RecommendationIdentityConflictError` -- a genuine invariant
violation (the three identity ingredients should always deterministically
produce identical content), never silently updated or silently reused.

## 12. Lifecycle state machine

```text
PROPOSED  -> ACCEPTED, REJECTED, DEFERRED, SUPERSEDED
DEFERRED  -> ACCEPTED, REJECTED, SUPERSEDED
ACCEPTED  -> SUPERSEDED
REJECTED  -> (terminal)
SUPERSEDED -> (terminal)
```

No reopening transition exists. `ACCEPTED -> SUPERSEDED` is the one
transition out of an otherwise-terminal-feeling state, kept deliberately:
an accepted recommendation can still be superseded later by a newer
template version's recommendation for the same gap (§6) without that
being a "reopening" of the original decision. A same-state request is a
harmless no-op (`changed=False`, no event created).

## 13. Human-only acceptance

Structurally enforced, not merely policy: there is exactly one public
transition function, `transition_experiment_recommendation`, and it always
requires an `ExperimentRecommendationDecision` (whose own construction
requires a non-blank `reviewer_id`). Every
`ExperimentRecommendationEvent` this function creates is stamped
`actor_type="HUMAN"` unconditionally -- no parameter, code path, or second
function anywhere in `app.review.experiment_recommendation_workflow`
could write any other `actor_type`. Neither
`app.experiment_recommendation.recommender` (Increment 23's deterministic
engine) nor `app.persistence.experiment_recommendation` (this increment's
own persistence layer) ever calls this module or constructs an event
directly -- verified by source-level tests
(`tests/review/test_experiment_recommendation_lifecycle.py`). Increment 24
instructions, Step 24's preferred "no machine lifecycle transitions"
policy is implemented exactly: there is no machine transition function at
all.

## 14. Rejection

`REJECTED` is terminal from either `PROPOSED` or `DEFERRED`. No automatic
rejection exists anywhere -- only an explicit human
`ExperimentRecommendationDecision`.

## 15. Supersession

No dedicated `supersede_experiment_recommendation` function exists;
supersession is the lifecycle transition API used with
`new_status=SUPERSEDED` (Increment 24 instructions, Step 25 explicitly
allows "handle through lifecycle transition API" as the alternative to a
dedicated helper). **Persisting a new recommendation for the same gap
never automatically supersedes an older row** — verified directly
(`test_persisting_new_recommendation_never_auto_supersedes_old_one`).
Superseding an existing row is always a separate, explicit, audited human
decision.

## 16. Audit events/history

`ExperimentRecommendationEvent`: `id`, `recommendation_id` (FK, `ON DELETE
RESTRICT`), `previous_status`, `new_status` (both VARCHAR + CHECK against
the lifecycle vocabulary), `actor_type`, `actor_id` (both plain,
unconstrained VARCHAR — mirroring `review_event.reviewer_type`/
`reviewer_id`'s identical trust-boundary design, since the schema cannot
authenticate a caller-supplied actor any more than `review_event` can),
`comment` (reason + notes, concatenated the same way
`app.review.workflow` already does for `Claim` review), `created_at`
(append-only, no `updated_at`). `get_experiment_recommendation_history`
returns every event for one recommendation, oldest first
(`created_at` ascending, `id` as a stable-not-chronological tiebreaker —
the same disclosed convention `app.review.history.get_review_history`
already uses).

## 17. Terminal KnowledgeGap behavior

Creating a **new** recommendation row for a gap whose persisted `status`
is `RESOLVED`/`DISMISSED` (Increment 22's own `KnowledgeGapStatus`) is
refused conservatively: `PersistenceAction.REQUIRES_REVIEW`, no row
created. Idempotent **reuse** of an already-existing identical
recommendation is never blocked by the gap's current status, since
nothing new is being created in that case
(`test_reuse_still_allowed_after_gap_becomes_terminal`).
`TerminalKnowledgeGapError` is defined for API completeness but never
raised by this increment's own code — the preferred conservative policy
(Step 21) represents this as data, not an exception.

## 18. Transaction ownership

Exactly mirrors `app.persistence.knowledge_gap`/`app.review.workflow`: one
`SAVEPOINT` (`session.begin_nested()`) per write call, an `IntegrityError`
caught and resolved via a post-failure recheck (persistence) or converted
to a conservative "nothing changed" result (lifecycle), every other
exception (including `RecommendationIdentityConflictError`/
`InvalidRecommendationTransitionError`) left to propagate. Neither module
ever calls `session.commit()`/`session.rollback()`.

## 19. Concurrency guarantees

The unique index on `recommendation_identity` is the real concurrency
authority. Two sessions racing to persist the identical deterministic
recommendation: the loser's `INSERT` raises `IntegrityError`; this module
catches it inside its own `SAVEPOINT`, re-queries by identity, and returns
`REUSED_EXISTING` pointing at the winner's row. Verified directly against
real concurrent PostgreSQL connections
(`tests/persistence/test_experiment_recommendation.py::test_concurrent_persist_yields_one_row`).

## 20. Read APIs

`get_experiment_recommendation`, `list_experiment_recommendations_for_gap`,
`list_open_experiment_recommendations` (`lifecycle_status` in
`{PROPOSED, DEFERRED}`), all in `app.persistence.experiment_recommendation`;
`get_experiment_recommendation_history` in
`app.review.experiment_recommendation_history`. All read-only, all
deterministically ordered by `created_at` ascending with `id` as a stable
tiebreaker.

## 21. Explicit non-responsibilities

No experiment execution, no scheduling, no laboratory protocol generation,
no hypotheses, no LLM reasoning, no automatic recommendation generation
beyond calling Increment 23's existing recommender, no cost estimation, no
vendor integration, no robotics, no result ingestion, no `KnowledgeGap`
auto-resolution, no `Claim`/`Evidence` mutation (verified: this package
never imports `app.models.claim`).

## 22. Legacy `suggested_experiment` policy

`KnowledgeGap.suggested_experiment` (and `model_impact`) remain completely
untouched by this increment — verified by source-level tests asserting
neither string appears anywhere in `app.persistence
.experiment_recommendation`'s source, and by direct database assertions
that both stay `NULL` after persisting and after every lifecycle
transition. The new `experiment_recommendation` table is the authoritative
home for structured recommendations; `suggested_experiment` is legacy,
unmanaged free text that a future migration may decide to deprecate, but
this increment does not touch it either way.

## 23. Migration compatibility

`0011_experiment_recommendation` (revises `0010_knowledge_gap_hardening`)
adds two new tables only — no existing table is altered. Verified via the
existing `tests/database/test_migrations.py` suite (empty-database-to-head
and full downgrade-to-base round trips) with the new migration in the
chain, plus new schema-level assertions in
`tests/persistence/test_experiment_recommendation.py` for the new tables,
FK, unique index, and CHECK constraints.

## 24. Testing

`tests/persistence/test_experiment_recommendation.py` (schema, creation for
every generation status, exact field preservation, idempotency,
identity-content conflict, terminal-gap behavior, concurrency,
transactions, read APIs, safety) and
`tests/review/test_experiment_recommendation_lifecycle.py` (every legal/
illegal transition, human-only acceptance structurally verified,
supersession, audit trail, transaction safety, `KnowledgeGap`
non-mutation). 67 new tests, all passing; full repository suite (2044
tests) and the `SAWarning`-strict pass both remain green.

## 25. Open architecture questions

1. Should `experiment_recommendation_event.actor_type` eventually gain a
   real, enforced machine-actor path (e.g. for a future automated
   triage step), and if so, under what authorization model? Deferred —
   Increment 24 deliberately implements none (§13).
2. Should `gap_type`/`gap_severity`/`recommendation_status`/
   `experiment_class`/`lifecycle_status` ever be promoted to native
   PostgreSQL `ENUM` types? Deferred by deliberate choice, mirroring
   `docs/18_knowledge_gap_persistence_contract.md`'s identical open
   question for `KnowledgeGap`.
3. No automatic supersession exists when a newer template version's
   recommendation is persisted for the same gap (§15) — a future
   increment may want an explicit, human-gated bulk-supersede helper.

## 26. Final architectural rule

> A persisted experiment recommendation records what Agent 1 proposed for
> a demonstrated KnowledgeGap.
>
> Human lifecycle state records what people decided to do with that
> proposal.
>
> Persistence never changes the recommendation's scientific meaning, and
> accepting a recommendation never by itself resolves the underlying
> KnowledgeGap.
