# Agent 1 Experiment Execution and Result Capture Contract

## 1. Purpose

Record, durably and auditably, what was actually done to carry out an
accepted `ExperimentRecommendation`, and what was observed as a result:

```text
ExperimentRecommendation -> Human ACCEPTED decision -> ExperimentExecution -> ExperimentResult
```

Execution records describe actions and conditions, not scientific
conclusions. Result records describe observations and measurements, not
interpretation. Neither invents, resolves, or concludes anything.

## 2. Pipeline position

```text
Recommendation persistence (Increment 24, app.persistence.experiment_recommendation)
    -> Recommendation lifecycle: ACCEPTED (Increment 24, app.review.experiment_recommendation_workflow)
    -> Execution persistence (this increment, app.persistence.experiment_execution)
    -> Execution lifecycle (this increment, app.review.experiment_execution_workflow)
    -> Result recording (this increment, app.persistence.experiment_execution)
    -> future result interpretation / Evidence ingestion (not this increment)
```

## 3. Execution model

No prior execution/result model existed (verified by inspection: no
`experiment`/`execution`/`result`/`measurement`/`assay`/`instrument`/
`sample`/`replicate`/`time_point` table anywhere in `app/models/`/
`migrations/` before this increment; `KineticMeasurement` and
`ExperimentalCondition` are pre-existing but serve a different purpose,
§14). Three new tables, added by migration `0012_experiment_execution`:
`experiment_execution` (one row per attempt to carry out an accepted
recommendation), `experiment_execution_event` (append-only lifecycle audit
trail), and `experiment_result` (one row per observation/measurement,
append-only).

## 4. Execution identity

`execution_identity` (`"experiment-exec-v1:<sha256>"`,
`app.persistence.experiment_execution.compute_execution_identity`) is a
deterministic digest of `recommendation_id` + caller-supplied
`execution_identifier` only -- never Python's built-in `hash()`, the same
canonical sorted-keys-JSON-then-SHA-256 convention every other identity
function in this repository already uses. A database-level unique index
(`uq_experiment_execution_execution_identity`) makes repeated persistence
of the same execution idempotent at the database layer.

## 5. Multiple executions

`recommendation_id` carries no uniqueness of its own -- only
`execution_identity` does. One accepted recommendation may have many
executions (replication is scientifically meaningful); each requires its
own distinct `execution_identifier`
(`test_multiple_executions_per_recommendation_allowed`).

## 6. Accepted-recommendation gate

Creating a **new** `ExperimentExecution` requires the linked
`ExperimentRecommendationRecord.lifecycle_status` to be `ACCEPTED` at
persist time. `PROPOSED`/`DEFERRED`/`REJECTED`/`SUPERSEDED` all yield
`PersistenceAction.REQUIRES_REVIEW`, no row created. This single,
current-state check also implements the supersession policy: if an
`ACCEPTED` recommendation later becomes `SUPERSEDED`, a *new* execution is
blocked (the check re-reads the recommendation's current status, not the
status at some earlier point), but **existing execution rows already
created while it was `ACCEPTED` remain untouched, un-deleted, and
un-invalidated** -- they are historical records
(`test_existing_execution_survives_later_supersession`).
`RecommendationNotAcceptedError` is defined for API completeness but never
raised by this increment's own code -- the same conservative-result policy
`TerminalKnowledgeGapError` already established.

## 7. Execution lifecycle

```text
PLANNED     -> IN_PROGRESS, CANCELLED
IN_PROGRESS -> COMPLETED, FAILED, CANCELLED
COMPLETED   -> (terminal)
FAILED      -> (terminal)
CANCELLED   -> (terminal)
```

`PARTIALLY_COMPLETED` is deliberately not introduced: nothing in this
increment gives it semantics `FAILED` (operationally did not finish) plus
whatever partial `ExperimentResult` rows were already recorded (§18)
cannot already represent -- see §27, question 1. No reopening transition
exists out of any terminal state. A same-state request is a harmless
no-op (`changed=False`, no event created).

**Human/machine actor policy.** Unlike the recommendation lifecycle (which
hardcodes every event's `actor_type` to `HUMAN`), this workflow makes no
assumption about who or what transitions an execution:
`ExperimentExecutionDecision.actor_id`/`actor_type` are both required,
non-blank, caller-supplied fields, stamped onto `ExperimentExecutionEvent`
verbatim -- no default, no workflow-level hardcoding of either
(`test_module_does_not_hardcode_a_single_actor_type`).

**Timestamp stamping.** When a transition's `new_status` is `IN_PROGRESS`
and `started_at` is not already set, `started_at` is set to
`decision.timestamp`. When `new_status` is a terminal status and
`completed_at` is not already set, `completed_at` is set to
`decision.timestamp`. Neither is ever overwritten once set, and neither is
ever read from the wall clock.

## 8. Execution audit history

`ExperimentExecutionEvent`: `id`, `execution_id` (FK, `ON DELETE
RESTRICT`), `previous_status`, `new_status` (VARCHAR + CHECK against the
execution status vocabulary), `actor_type`, `actor_id` (both plain,
unconstrained VARCHAR, mirroring `review_event.reviewer_type`/
`reviewer_id`), `comment` (reason + notes, concatenated the same way every
other lifecycle module in this repository already does), `created_at`
(append-only, no `updated_at`). `get_experiment_execution_history` returns
every event for one execution, oldest first (`created_at` ascending, `id`
as a stable tiebreaker).

## 9. Planned conditions

`planned_conditions_json` (`JSONB`, `NOT NULL`, defaults to `{}` if the
caller supplies nothing) holds only explicitly structured data the caller
provides -- for example `{"required_fields": ["organism", "strain",
"temperature"]}` copied from the originating recommendation's own
`experimental_context_requirements`. This module never fabricates a
concrete value (like `"temperature": "30 C"`) for a field the caller did
not supply.

## 10. Actual conditions

`actual_conditions_json` (`JSONB`, nullable) is caller-supplied at
execution-creation time only, preserved literally with no unit conversion,
averaging, normalization, or interpretation. **Open gap**: no API in this
increment updates it after creation -- see §27, question 2.

`app.persistence.experiment_execution.diff_planned_vs_actual_conditions`
is an optional, read-only, purely structural helper: it reports which
top-level keys are present in one side but not the other, and which shared
keys hold different values. It never classifies a difference as a
scientific failure or a deviation worth flagging -- the caller decides
what a reported difference means, if anything.

## 11. ExperimentalCondition reuse decision

Inspected directly (`app/models/experimental_condition.py`) and **not
reused**. `ExperimentalCondition` is a fixed-column table
(`medium`/`carbon_source`/`temperature_c`/`ph`/...) purpose-built for
`Evidence`'s biological context (`evidence_condition`), and using it here
would either force execution planning into that same fixed, evidence-shaped
column set (blurring the two concepts, exactly what Increment 25
instructions Step 2 warns against) or require altering a table shared with
`Evidence` for an unrelated purpose. `planned_conditions_json`/
`actual_conditions_json` (free-form `JSONB`) are used instead, conservatively
introduced rather than overloading the existing table.

## 12. ExperimentResult model

Append-only: no `updated_at` column exists at all (the same intentional
omission `ExperimentalCondition`/`ExperimentRecommendationEvent` already
use for a row this schema never expects to mutate after creation). No
column represents interpretation -- no `supports_claim`/`refutes_claim`/
`confidence_delta`/`gap_resolved`/`hypothesis` field exists anywhere in
this table, and none will (verified by source-level tests on
`app.persistence.experiment_execution`).

## 13. Result identity

`result_identity` (`"experiment-result-v1:<sha256>"`,
`app.persistence.experiment_execution.compute_result_identity`) is a
deterministic digest of provenance/classification fields only: the linked
execution's own `execution_identity`, `result_type`, `measurement_name`,
`sample_identifier`, `replicate_identifier`, `time_point`, and the
caller-supplied `result_identifier`. **Never** `value_text`/`value_numeric`
themselves -- a correction to a recorded value must not silently collide
with provenance identity.

## 14. Result immutability/revision policy

No revision/supersession mechanism exists in this increment (the "if not
implemented now" branch of the specification). Results are strictly
append-only:

* Identical identity + identical full content (every field) ->
  `REUSED_EXISTING`, content never overwritten.
* Identical identity + differing content ->
  `ExperimentResultIdentityConflictError` is raised. A correction requires
  a new, distinct `result_identifier` (and therefore a new
  `result_identity`), never an `UPDATE`.

See §27, question 3 for the deferred alternative (`supersedes_result_id`/
`revision_number`).

## 15. Quantitative measurements

`value_numeric` is `Numeric`/`Decimal`, never `float` --
`ExperimentResultInput.__post_init__` rejects a `float` outright, the same
precision discipline `KineticMeasurement.parameter_value` already enforces
at the schema level. `unit` is preserved literally with no conversion. A
`QUANTITATIVE_MEASUREMENT` additionally requires `value_numeric` to be
present (a structural completeness check, not scientific interpretation).

## 16. Qualitative/non-detection observations

`ResultType.QUALITATIVE_OBSERVATION`/`NON_DETECTION`/`DETECTION`/
`ASSAY_OUTCOME` all support a `value_text`-only result (`value_numeric`
stays `None`). `"No activity detected"` is stored verbatim as
`result_type=NON_DETECTION, value_text="No activity detected"` -- never
converted into a Claim conflict, a rejected mechanism, or a resolved gap
(verified: no such write exists anywhere in this module).

## 17. Replicates and time points

`sample_identifier`/`replicate_identifier`/`time_point`/`condition_label`
are all plain, unconstrained strings, stored and compared exactly as
supplied. `"24 h"` is never converted to seconds. One execution may have
arbitrarily many result rows (biological replicates, technical replicates,
time points, samples); nothing in this module aggregates or averages them.

## 18. Raw-data provenance

`raw_data_reference` is a plain, unparsed text reference (a URI, path, or
external identifier) the caller supplies. This module never fetches,
parses, or validates the existence of whatever it points to.

## 19. Result-recording lifecycle rules

Results may be recorded while the linked execution's status is
`IN_PROGRESS`, `COMPLETED`, or **`FAILED`** -- `FAILED` is deliberately
included: an operational failure does not mean the scientifically valid
partial measurements already observed should be discarded
(`test_failed_execution_allows_result`). Blocked for `PLANNED`/`CANCELLED`
(`PersistenceAction.REQUIRES_REVIEW`, no row created).
`ResultRecordingNotAllowedError` is defined for API completeness but never
raised by this increment's own code. A `COMPLETED` execution with zero
recorded results is explicitly allowed (`test_completed_with_zero_results_is_allowed`)
-- some workflows record results later, and this module never fabricates
one to fill the gap. The gate is checked only when *creating* a new row --
an identical-identity replay is always `REUSED_EXISTING` regardless of the
execution's current status, mirroring the terminal-`KnowledgeGap`/
`REUSED_EXISTING` precedent in `app.persistence.experiment_recommendation`.

## 20. Idempotency

* **Execution**: same `execution_identity` -> `REUSED_EXISTING`, with **no
  content-equality check** against the repeat call's descriptive fields
  (`planned_conditions`/`performed_by`/`laboratory`/...). This is a
  deliberate difference from recommendation persistence: those fields are
  free-form caller input, not a deterministic function of the identity
  ingredients, so a mismatch is not necessarily a bug the way it is for a
  recommendation (§13 of `docs/20_experiment_recommendation_persistence_contract.md`).
* **Result**: same `result_identity` + identical content -> `REUSED_EXISTING`.
  Same identity + differing content -> `ExperimentResultIdentityConflictError`
  (§14). Neither table is ever updated in place.

## 21. Concurrency guarantees

Unique indexes on `execution_identity`/`result_identity` are the real
concurrency authority. Two sessions racing to persist the identical
execution or result: the loser's `INSERT` raises `IntegrityError`; this
module catches it inside its own `SAVEPOINT`, re-queries by identity, and
returns `REUSED_EXISTING` pointing at the winner's row. Verified directly
against real concurrent PostgreSQL connections
(`test_concurrent_persist_yields_one_row`,
`test_concurrent_result_insert_yields_one_row`).

## 22. Transaction ownership

Exactly mirrors `app.persistence.experiment_recommendation`/
`app.review.experiment_recommendation_workflow`: one `SAVEPOINT`
(`session.begin_nested()`) per write call, an `IntegrityError` caught and
resolved via a post-failure recheck (persistence) or converted to a
conservative "nothing changed" result (lifecycle), every other exception
(including `ExperimentResultIdentityConflictError`/
`InvalidExperimentExecutionTransitionError`) left to propagate. No function
in either module ever calls `session.commit()`/`session.rollback()`.

## 23. Read APIs

`get_experiment_execution`, `list_executions_for_recommendation`,
`get_experiment_result`, `list_results_for_execution`,
`diff_planned_vs_actual_conditions` (all in
`app.persistence.experiment_execution`); `get_experiment_execution_history`
in `app.review.experiment_execution_history`. All read-only.
`list_results_for_execution` orders by `observed_at` ascending (nulls
last, since not every result carries one), then `created_at` ascending,
then `id` as a final stable tiebreak.

## 24. Explicit non-responsibilities

No automated experiment execution, laboratory robotics, scheduling, or
instrument/vendor integration. No result interpretation, no Claim
generation, no Evidence generation from results, no `KnowledgeGap`
resolution or status change, no confidence recalculation, no hypothesis
generation, no LLM reasoning, no normalization or entity resolution
(verified: this module never imports `app.models.claim`,
`app.models.knowledge_gap`, `app.confidence`, or `app.normalization`).

## 25. Relationship to future result interpretation

See `docs/22_experiment_result_interpretation_contract.md` (Increment 26)
for that later, explicit increment -- it reads `ExperimentResult`/
`ExperimentExecution` rows and produces deterministic `EvidenceCandidate`
objects, never mutating anything in this contract.

A later, explicit increment is expected to read `ExperimentResult` rows and
decide what they mean scientifically -- whether a measurement supports or
contradicts a `Claim`, whether a `KnowledgeGap` should be resolved, whether
confidence should change. Nothing here performs any part of that job or
leaves a "half-interpreted" trace of it: every field on `ExperimentResult`
is either raw provenance or an unmodified, literally-preserved observation.

## 26. Relationship to future Evidence ingestion

A future increment may turn selected `ExperimentResult` rows into new
`EvidenceExtraction`/`Evidence` records, through the same deliberate,
explicit persistence path every other `Evidence` source already goes
through (`app.persistence.claim`). This increment creates no such path and
no implicit trigger for one -- `ExperimentResult` and `Evidence` remain
structurally unconnected today (no foreign key between them).

## 27. Open architecture questions

1. Should `PARTIALLY_COMPLETED` be added to the execution lifecycle for a
   run that produced some but not all planned measurements before
   stopping? Deferred -- `FAILED` plus recorded partial results already
   captures the same information without a new state (§7).
2. Should a dedicated API be added to update `actual_conditions_json`
   after execution creation (for example, once the run is actually under
   way and true conditions are known)? Deferred -- no field in this
   increment's specification names a decision point for this, so no
   workflow was invented (§10).
3. Should `ExperimentResult` gain a `supersedes_result_id`/
   `revision_number` pair for explicit correction chains, instead of the
   current "new `result_identifier`, no link back" policy (§14)? Deferred
   -- the current policy already prevents silent overwrites; a linked
   revision chain would only add value once a real correction workflow is
   designed.

## 28. Final architectural rule

> ExperimentExecution records what was done.
>
> ExperimentResult records what was observed.
>
> Neither record decides what the observation means scientifically.
>
> Interpretation, Evidence creation, Claim updates, confidence changes, and
> KnowledgeGap resolution belong to later explicit stages.
