# Agent 1: Review Workflow Contract (Increment 20)

## 1. Purpose

Provide a deterministic, auditable state machine around the `Claim`/
`Evidence` records `app.persistence.claim` already writes. This is a
workflow increment, not an AI increment: no LLM call, no connector call, no
randomness, nothing beyond a confidence-driven policy and human-supplied
decisions.

## 2. Pipeline location

```text
EvidenceExtraction -> CandidateClaim -> SingleEvidenceAssessment
    -> EvidenceContribution (+ AggregateClaimConfidence)
    -> Claim / Evidence persistence (Increment 19)
    -> Review workflow (this increment)
```

Review acts on a `Claim` already persisted by `app.persistence.claim`. It
never creates a `Claim`, never touches `Evidence`, and never recomputes
confidence.

## 3. Workflow overview

Two entry points:

* `machine_review_claim(claim, confidence, *, session)` -- deterministic,
  confidence-driven first-pass triage of a `PROPOSED` claim.
* `human_review_claim(decision, *, claim, session)` -- applies one
  authorized human `ReviewDecision`.

Both derive the claim's current curation state from its own `ReviewEvent`
history (§6), validate the requested transition against an explicit state
machine (§7/§8), and -- for a real transition -- create exactly one
`ReviewEvent` row and update `Claim.status` where a defensible mapping
exists (§9).

## 4. `ReviewDecision` contract

```python
ReviewDecision(
    claim_id: UUID,
    decision: CurationState,
    reviewer: str,
    reason: str,
    timestamp: datetime,       # required, no default -- see below
    notes: str | None = None,
    created_by: str | None = None,
)
```

* `decision` reuses the schema's own `CurationState` enum directly rather
  than inventing a parallel vocabulary.
* `reviewer`/`reason` are required, non-blank.
* `timestamp` is required and must be timezone-aware -- this package never
  reads the wall clock itself (Increment 20 instructions, Step 14). It is
  written verbatim to `ReviewEvent.created_at`, overriding that column's
  `server_default`, because "when the change occurred" (`docs/03`'s
  Provenance Behavior) is the actual decision moment, not merely the moment
  this code happened to run.
* `notes` is optional and, when present, appended to `reason` in
  `ReviewEvent.comment` (both preserved, never one silently dropped).
* `created_by` **has no destination on `ReviewEvent`** -- `review_event`
  has no column for "who invoked this recording", only `reviewer_type`/
  `reviewer_id` (who made the *decision*). Accepted (per this increment's
  field list) but never persisted. Disclosed gap, §17.

## 5. `ReviewWorkflowResult` contract

```python
ReviewWorkflowResult(
    old_state: CurationState,
    new_state: CurationState,
    review_event_id: UUID | None,
    changed: bool,
    reason: str,
)
```

`review_event_id` is populated if and only if `changed` is `True`.
`changed=False` covers two distinct, both-conservative outcomes reported
through `reason`'s text: the requested state already equalled the current
state (a no-op), or an `IntegrityError` rolled the attempt back.

## 6. State machine: how "current state" is derived

**The central schema finding this workflow is built around.** `Claim` has
**no `curation_state` column** -- verified directly against
`app/models/claim.py` (`Reaction`/`RegulatoryInteraction` each have a real
`curation_state` column; `Claim` does not). `ReviewEvent.previous_state`/
`new_state` are typed `CurationState`; `Claim.status` is typed `ClaimStatus`
-- two non-overlapping vocabularies, sharing only the word `REJECTED`.

This workflow does **not** invent a `Claim.curation_state` column. A
claim's current curation state is derived entirely from its own
`ReviewEvent` history: the most recent row's `new_state`, ordered by
`created_at` (see §14 for the ordering caveat), or `CurationState.PROPOSED`
when no `ReviewEvent` exists yet -- the same implicit "not yet reviewed"
starting point `Reaction`/`RegulatoryInteraction` already default their own
real `curation_state` column to, not an invented default.

## 7. Allowed transitions

Built from `docs/03_agent_behavior.md`'s "Human Review Behavior" section
("Curation states should progress through: PROPOSED, MACHINE_REVIEWED,
NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED") and Step 6's grant ("Machine
review may: request human review, reject, leave unchanged").

```text
PROPOSED         -> MACHINE_REVIEWED, NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED
MACHINE_REVIEWED -> NEEDS_REVIEW, HUMAN_ACCEPTED, REJECTED
NEEDS_REVIEW     -> HUMAN_ACCEPTED, REJECTED
HUMAN_ACCEPTED   -> (terminal)
REJECTED         -> (terminal)
```

Each actor may only target a subset of this table:

* **Machine review** may only ever target `MACHINE_REVIEWED`/
  `NEEDS_REVIEW`.
* **Human review** may only ever target `HUMAN_ACCEPTED`/`NEEDS_REVIEW`/
  `REJECTED`.

A request whose target equals the current state is a no-op (§10), checked
only *after* confirming the actor may legally target that state at all --
a human `ReviewDecision` requesting `PROPOSED` is always illegal, even on a
claim that happens to already be `PROPOSED`.

## 8. Forbidden transitions

* Neither actor may target `HUMAN_ACCEPTED`... except human review may
  (machine review never; this is a structural guarantee -- `HUMAN_ACCEPTED`
  is not a member of machine review's allowed-target set at all, not merely
  a runtime check).
* No transition out of `HUMAN_ACCEPTED` or `REJECTED` -- both are terminal.
  Nothing in the schema or `docs/03_agent_behavior.md` represents
  reopening either, so none is implemented (Increment 20 instructions,
  Step 5).
* `NEEDS_REVIEW -> MACHINE_REVIEWED` does not exist: once escalated, only a
  human (accept or reject) resolves it -- machine review never "un-flags"
  its own escalation.
* A human `ReviewDecision` may never target `PROPOSED` or
  `MACHINE_REVIEWED` -- those are not human decisions.

Every violation raises `app.review.errors.InvalidReviewTransitionError`,
always *before* any `SAVEPOINT` is opened -- nothing is written, nothing is
silently corrected.

## 9. Machine review

`machine_review_claim(claim, confidence, *, session)`:

1. Only acts when the claim's current curation state is `PROPOSED` --
   otherwise a harmless no-op ("leave unchanged," Step 6), deliberately
   never re-litigating a state already reached by machine or human action.
2. Maps `confidence.confidence_class` to a target state:

   | `ConfidenceClass` | Target |
   |---|---|
   | `UNKNOWN` | `NEEDS_REVIEW` |
   | `LOW` | `NEEDS_REVIEW` |
   | `MODERATE` | `MACHINE_REVIEWED` |
   | `HIGH` | `MACHINE_REVIEWED` |
   | `VERY_HIGH` | `MACHINE_REVIEWED` |

   `UNKNOWN`/`LOW` route to a human rather than being auto-rejected --
   "unknown" is never treated as "false" (`docs/03`'s "Unknown vs Negative
   Evidence").
3. **This policy never selects `REJECTED`.** The state machine's table
   permits `PROPOSED -> REJECTED` (Step 6 grants machine review that
   capability in principle), but no authoritative, deterministic signal
   exists today in `AggregateClaimConfidence` to justify automatic
   rejection without performing conflict/contradiction resolution, which
   this increment is explicitly forbidden from implementing. Disclosed
   policy restriction, not a schema limitation -- a future increment could
   extend the mapping once such a signal exists, with no state-machine
   change required.
4. **Never sets `HUMAN_ACCEPTED` under any circumstance** -- structurally
   impossible, since `HUMAN_ACCEPTED` is not in machine review's own
   allowed-target set.
5. Updates `Claim.status` only when the target is `REJECTED` (§11); leaves
   it untouched (`ClaimStatus.UNKNOWN`, from Increment 19's own default)
   otherwise.

## 10. Human review

`human_review_claim(decision, *, claim, session)`:

1. Validates `decision.claim_id == claim.id`.
2. Validates `decision.decision` is a target human review may legally
   reach (§7/§8).
3. If the requested state already equals the current state: no-op, no
   `ReviewEvent` created (duplicate/repeated decisions are idempotent, not
   erroneous).
4. Otherwise validates the `(old_state, new_state)` edge against the state
   machine, then creates exactly one `ReviewEvent` with
   `reviewer_type=HUMAN`, `reviewer_id=decision.reviewer`,
   `created_at=decision.timestamp` (verbatim, §4).
5. Updates `Claim.status` only when the target is `REJECTED` (§11).

## 11. `Claim.status` mapping (the one defensible bridge between vocabularies)

`Claim.status` is updated **only** when the workflow's target
`CurationState` is `REJECTED`, in which case
`Claim.status = ClaimStatus.REJECTED`. Every other transition
(`MACHINE_REVIEWED`, `NEEDS_REVIEW`, `HUMAN_ACCEPTED`) leaves `Claim.status`
untouched. `HUMAN_ACCEPTED` means "a human has signed off on this curation
record" -- a workflow/process fact -- not "the evidence supports this
claim," which is a scientific determination (`ClaimStatus.SUPPORTED`)
requiring duplicate/contradiction analysis this increment does not
perform. Mapping `HUMAN_ACCEPTED -> SUPPORTED` would be exactly the kind of
invented, unsupported mapping this pipeline's integrity rules forbid.

## 12. `ReviewEvent` creation

Every successful transition creates exactly one row:

| Column | Source |
|---|---|
| `entity_type` | Always `"claim"` |
| `entity_id` | `claim.id` |
| `previous_state` | The derived current state (§6) |
| `new_state` | The validated target state |
| `reviewer_type` | `DETERMINISTIC_VALIDATOR` (machine) / `HUMAN` (human) |
| `reviewer_id` | `None` (machine) / `decision.reviewer` (human) |
| `comment` | A deterministic confidence-class explanation (machine) / `decision.reason` (+ `decision.notes`, §4) (human) |
| `created_at` | `server_default` (machine) / `decision.timestamp` (human, §4) |

No metadata is ever fabricated: a field this workflow cannot derive from
its own inputs is left at its schema default (`reviewer_id=None`,
`comment=None` never occurs since a comment is always available), never
invented.

## 13. Transaction ownership

Exactly mirrors `app.persistence.reaction`: one `SAVEPOINT`
(`session.begin_nested()`) per call, wrapping only the `ReviewEvent`
insert and the `Claim.status` update. This module never commits or rolls
back the session it is given -- transaction ownership belongs to the
caller. No `SourceCrossReference`/`ExternalRecord` is created here; there
is nothing new for those helpers to attach to.

## 14. Audit guarantees

Every transition records previous state, new state, reviewer identity
(type + id where applicable), timestamp, and reason/comment -- exactly
`docs/03_agent_behavior.md`'s "who made the change / when the change
occurred / what changed / why it changed."

**Disclosed limitation.** `ReviewEvent` has no monotonic sequence/version
column. `get_review_history`/`_current_curation_state` order by
`created_at` (with `id`, a random UUID, as a tiebreaker for *stable, not
chronologically correct* result ordering only). Two `ReviewEvent` rows
created inside the same database transaction could receive an identical
`created_at` (PostgreSQL's `now()` is fixed for the duration of one
transaction) and this workflow cannot then determine their true order.
This never arises from this workflow's own calls (each creates at most one
`ReviewEvent`), but is disclosed rather than worked around by inventing a
new column.

## 15. History retrieval

`get_review_history(session, *, entity_type, entity_id) -> tuple[ReviewEvent, ...]`
-- read-only, oldest to newest (§14 for the ordering caveat). Generic over
`entity_type` (not hardcoded to `"claim"`), since `ReviewEvent` is already
a polymorphic table and nothing about a plain `SELECT` needs to be
claim-specific.

## 16. Error model

* `app.review.errors.ReviewError` -- base class.
* `app.review.errors.InvalidReviewTransitionError` -- an illegal
  transition was requested. Always raised before any write; never
  swallowed, never auto-corrected to the nearest legal state.
* Malformed input (wrong type, mismatched `claim_id`) raises `TypeError`/
  `ValueError` directly, matching every other data-contract module in this
  repository.
* An `IntegrityError` raised while writing is caught and converted to a
  `ReviewWorkflowResult(changed=False, ...)`; every other exception
  propagates.

## 17. Testing summary

`tests/review/`: `test_types.py` (dataclass self-validation),
`test_validation.py` (shared helpers), `test_history.py` (ordering,
scoping, read-only), `test_workflow.py` (every legal transition
parametrized, every illegal transition, machine review's
`HUMAN_ACCEPTED`/`REJECTED` exclusions, human acceptance/rejection,
`ReviewEvent` audit fields, duplicate-transition no-ops, `SAVEPOINT`/
`IntegrityError` handling, no-commit/no-rollback/no-duplicated-persistence-
logic source checks, determinism across equivalent calls). 76 tests, all
passing; full repository suite (1753 tests) and the `SAWarning`-strict
pass both remain green.

## 18. Open architecture questions

1. **No `Claim.curation_state` column** (§6/§11) -- the workflow-state and
   scientific-status vocabularies remain structurally disconnected except
   at `REJECTED`. Adding a real `curation_state` column to `Claim` (mirroring
   `Reaction`/`RegulatoryInteraction`) would let "current state" be read
   directly instead of derived from history on every call, and is the
   natural fix -- out of scope for this increment.
2. **`ReviewDecision.created_by` has no persisted destination** (§4).
3. **No monotonic ordering column on `ReviewEvent`** (§14) -- a same-
   transaction timestamp tie cannot be resolved correctly today.
4. **Machine review's rejection capability is unused** (§9) pending a
   deterministic, non-conflict-resolution signal for automatic rejection.
5. **`reviewer_type` is an unenforced `VARCHAR`** (`app/models/review_event.py`'s
   own docstring) -- nothing at the schema level stops a future caller from
   fabricating `reviewer_type="HUMAN"` without real human authorization;
   that enforcement remains, as documented there, an API/auth-layer
   responsibility this increment does not implement.
6. **Should `Claim` eventually have a persisted `curation_state` column?**
   Item 1 above records the finding that no such column exists today and
   that this workflow derives current state from `ReviewEvent` history
   instead. Whether to add one -- trading a per-call history scan for a
   directly-readable column, at the cost of a schema migration and keeping
   a denormalized value in sync with the event log -- is an architectural
   decision for a future increment, not resolved here.
7. **Should `MACHINE_REVIEWED` remain a long-lived workflow state, or
   become an internal audit milestone that immediately transitions to
   `NEEDS_REVIEW`?** This increment treats `MACHINE_REVIEWED` as a state a
   claim can rest in indefinitely once machine review has passed it (§7/§9).
   An alternative design would use `MACHINE_REVIEWED` only as a transient,
   recorded step -- every machine-reviewed claim immediately continuing on
   to `NEEDS_REVIEW` so a human always sees it -- collapsing the practical
   distinction between "passed automated triage" and "awaiting a human"
   into a single queue. Both are defensible; this is a policy choice for a
   future increment, not an implementation defect in this one.
