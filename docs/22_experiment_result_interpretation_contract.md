# Agent 1 Experiment Result Interpretation Contract

## 1. Purpose

Transform a completed `ExperimentExecution` and its `ExperimentResult` rows
into deterministic, fully auditable `EvidenceCandidate` objects,
structurally compatible with the existing Evidence Extraction -> Claim
Generation pipeline. This is the final increment of Agent 1 Version 1's
experimental subsystem.

The output is intentionally only candidate evidence. Nothing in the
curated knowledge base is modified: no `Claim`, `Evidence`,
`KnowledgeGap`, `ExperimentRecommendation`, `ExperimentExecution`,
`ExperimentResult`, confidence score, or `ReviewEvent` is ever created,
updated, or read-then-mutated by this package.

## 2. Architecture

```text
ExperimentResult -> EvidenceCandidate
```

This package performs exactly one transformation and nothing else. It does
**not** perform normalization, claim generation, persistence, confidence
scoring, review, hypothesis generation, or biological reasoning of any
kind. It is a purely deterministic mapping layer: `app.result_interpretation`
accepts already-loaded ORM objects and returns plain, immutable dataclasses
-- no database `Session` parameter exists anywhere in its public or private
functions (`app.result_interpretation.interpreter`,
`app.result_interpretation.mapping`), which is how "no persistence, no
database writes" is enforced structurally, not merely by convention.

## 3. Pipeline position

```text
KnowledgeGap (Increment 21/22)
    -> ExperimentRecommendation (Increment 23/24)
        -> Recommendation Lifecycle: ACCEPTED (Increment 24)
            -> ExperimentExecution (Increment 25)
                -> ExperimentResult (Increment 25)
                    -> EvidenceCandidate (this increment)
                        -> [future, out of scope] Evidence ingestion
```

This increment closes the experimental subsystem. `EvidenceCandidate` is
not ingested into the knowledge base by anything in this repository today.

## 4. Architecture inspection findings

Verified directly before writing any code, not assumed:

* `app.extraction.types.EvidenceExtraction`/`CandidateStatement` are the
  existing pipeline's own "not yet a Claim" shape -- source-grounded,
  requiring a `SourceSpan` (a document/character-offset/quoted-text
  location). An `ExperimentResult` has no such document-grounded span (its
  provenance is `experiment_execution_id`/`experiment_result_id`, not a
  document), so `EvidenceCandidate` is deliberately a distinct type, not a
  subtype or repurposing of `EvidenceExtraction`.
* `app.extraction.types.Directness` (a local `StrEnum`, not a database
  enum) already backs `Evidence.directness` (a plain `VARCHAR` column) --
  reused verbatim here (§9).
* `app.models.enums.EvidenceType` (a native PostgreSQL `ENUM`, backing
  `Evidence.evidence_type`) is reused verbatim -- no new evidence
  vocabulary is invented (§8).
* `app.models.claim.Evidence` has columns named `quoted_support` (nullable
  `TEXT`), `curator_summary` (`NOT NULL` `TEXT`), `evidence_type`,
  `directness` -- `EvidenceCandidate`'s own field names for these four
  concepts match exactly, so a later, separate increment can persist one
  without renaming or reinterpreting any field. This package never
  constructs an `Evidence` row itself.
* `app.models.experiment_execution.ExperimentResult`/`ExperimentExecution`
  (Increment 25) supply every provenance/measurement field
  `EvidenceCandidate` copies verbatim (§7).
* `app.models.experiment_recommendation.ExperimentRecommendationRecord`
  has **no** subject/predicate/object text field of any kind -- only a
  polymorphic `target_entity_type`/`target_entity_id` pair (a category and
  an opaque UUID, not identifying text) and free-text `objective`/
  `rationale`. This is a genuine architecture gap (§10): nothing upstream
  of this package currently produces the text
  `candidate_subject_text`/`candidate_predicate_text`/
  `candidate_object_text` would need, so this package never derives them
  from the recommendation and instead accepts them only via an explicit,
  entirely caller-supplied `ExperimentInterpretationContext` (§10).
* `app.claim_generation.types.CandidateClaim` (Increment 13) is the
  existing pipeline's post-extraction, entity-resolved shape -- it is not
  what `EvidenceCandidate` mirrors; `EvidenceCandidate` sits at the
  `EvidenceExtraction`-like stage (raw, grounded, not yet entity-resolved),
  never at the `CandidateClaim` stage. This package never imports
  `app.claim_generation`.

No architectural mismatch beyond the disclosed subject/predicate/object gap
(§10) and the `EvidenceType` mapping's disclosed `OTHER` fallback (§8) was
found.

## 5. EvidenceCandidate schema

`app.result_interpretation.types.EvidenceCandidate` -- frozen, self-validating:

| Field | Source | Notes |
|---|---|---|
| `experiment_execution_id` | `execution.id` | UUID, required |
| `experiment_result_id` | `result.id` | UUID, required |
| `result_type` | `result.result_type` verbatim | plain `str`, not the closed `ResultType` enum -- see §8 |
| `measurement_name` | `result.measurement_name` verbatim | required, non-blank |
| `value_text` | `result.value_text` verbatim | |
| `value_numeric` | `result.value_numeric` verbatim | `Decimal` only, never `float` |
| `unit` | `result.unit` verbatim | |
| `statistical_support` | `result.statistical_support` verbatim | |
| `sample_identifier` | `result.sample_identifier` verbatim | |
| `replicate_identifier` | `result.replicate_identifier` verbatim | |
| `time_point` | `result.time_point` verbatim | |
| `condition_label` | `result.condition_label` verbatim | |
| `raw_data_reference` | `result.raw_data_reference` verbatim | |
| `observed_at` | `result.observed_at` verbatim | timezone-aware if present |
| `notes` | `result.notes` verbatim | |
| `candidate_subject_text` | caller-supplied `ExperimentInterpretationContext` only | §10 |
| `candidate_predicate_text` | caller-supplied `ExperimentInterpretationContext` only | §10 |
| `candidate_object_text` | caller-supplied `ExperimentInterpretationContext` only | §10 |
| `evidence_type` | deterministic mapping, §8 | `app.models.enums.EvidenceType` |
| `directness` | always `AUTHORS_OBSERVED` | `app.extraction.types.Directness` |
| `quoted_support` | deterministically generated, §11 | required, non-blank |
| `curator_summary` | deterministically generated, §12 | required, non-blank |
| `interpretation_status` | deterministic classification, §6 | `InterpretationStatus` |

At least one of `value_text`/`value_numeric` must be present -- a candidate
recording nothing would carry no information at all, the same structural
completeness check `app.persistence.experiment_execution_types
.ExperimentResultInput` already applies to its own source data.

## 6. InterpretationStatus

```text
DIRECT_OBSERVATION
NON_DETECTION
QUALITATIVE_OBSERVATION
INSUFFICIENT_INFORMATION
UNSUPPORTED_RESULT_TYPE
```

Exactly these five, no more. Classified purely from the recorded result's
own shape (`app.result_interpretation.mapping.classify_result`):

| `ExperimentResult.result_type` | Data shape | `InterpretationStatus` |
|---|---|---|
| `NON_DETECTION` | (any) | `NON_DETECTION` |
| `QUANTITATIVE_MEASUREMENT` / `DETECTION` | `value_numeric` present | `DIRECT_OBSERVATION` |
| `QUANTITATIVE_MEASUREMENT` / `DETECTION` | only `value_text` present | `QUALITATIVE_OBSERVATION` |
| `QUANTITATIVE_MEASUREMENT` / `DETECTION` | neither present | `INSUFFICIENT_INFORMATION` |
| `QUALITATIVE_OBSERVATION` / `ASSAY_OUTCOME` | either value present | `QUALITATIVE_OBSERVATION` |
| `QUALITATIVE_OBSERVATION` / `ASSAY_OUTCOME` | neither present | `INSUFFICIENT_INFORMATION` |
| anything not parseable as a known `ResultType` | (any) | `UNSUPPORTED_RESULT_TYPE` |

A result is **never silently dropped**: every `ExperimentResult`, including
one with an unrecognized `result_type`, produces exactly one
`EvidenceCandidate`.

## 7. Mapping rules

Every field listed "verbatim" in §5 is copied directly from the ORM row --
no reformatting, no unit conversion, no averaging, no rounding. `directness`
is always `Directness.AUTHORS_OBSERVED` -- these are laboratory
observations, never `AUTHORS_INFERRED`/`AUTHORS_PROPOSED`/any other value
(Increment 26 instructions, Step 12). Results are never aggregated across
an execution: `interpret_experiment_execution` produces exactly one
candidate per result, in the exact order supplied.

## 8. EvidenceType mapping (deliberate design decision)

`EvidenceType` (Increment 26 instructions, Step 11: reused verbatim, no new
vocabulary) is derived from the originating recommendation's own
`experiment_class` -- Increment 23's own deterministic categorization, not
invented biological reasoning about the measurement itself:

| `ExperimentClass` | `EvidenceType` |
|---|---|
| `ENZYME_SUBSTRATE_ASSAY` | `DIRECT_BIOCHEMICAL` |
| `REACTION_VALIDATION` | `DIRECT_BIOCHEMICAL` |
| `PROTEIN_LOCALIZATION_ASSAY` | `LOCALIZATION` |
| `EXPERIMENTAL_CONTEXT_CHARACTERIZATION` | `OTHER` (disclosed gap) |
| `REPLICATION_EXPERIMENT` | `OTHER` (disclosed gap) |
| no recommendation supplied, or `experiment_class is None` | `OTHER` |

Two of the five `ExperimentClass` values have no single-category match in
`EvidenceType`'s 15 members and deliberately fall back to `OTHER` -- a
disclosed limitation of this mapping (§14), not a guess.

## 9. Directness

Always `Directness.AUTHORS_OBSERVED`. Structurally verified: no code path
in `app.result_interpretation` ever constructs any other `Directness`
member.

## 10. Subject / predicate / object -- disclosed architecture gap

`candidate_subject_text`/`candidate_predicate_text`/`candidate_object_text`
are populated **only** from a caller-supplied
`ExperimentInterpretationContext` (all three fields default to `None`).
This package never infers or derives them from the recommendation,
execution, or result -- verified: `ExperimentRecommendationRecord` has no
text field that identifies a specific subject/predicate/object (only a
category, `target_entity_type`, and an opaque `target_entity_id`).
**In practice, every `EvidenceCandidate` this package produces today leaves
all three `None`** unless a caller explicitly constructs a context from
information this repository does not currently have anywhere upstream.
This is disclosed here and in this increment's completion report, not
silently resolved.

## 11. `quoted_support`

Constructed deterministically by `app.result_interpretation.mapping
.build_quoted_support`. When the result already carries descriptive text
(`value_text`), that text is quoted **exactly, unmodified** -- the least
embellished option available (Increment 26 instructions, Step 14: "Never
embellish. Never summarize. Never call an LLM."). A generated sentence is
used only when no descriptive text exists to quote:

```text
NON_DETECTION, value_text present:        <value_text verbatim>
NON_DETECTION, no value_text:             "No detectable {measurement_name} observed."
DIRECT_OBSERVATION:                       "Measured {measurement_name}: {value} {unit}."
QUALITATIVE_OBSERVATION, value_text:      <value_text verbatim>
QUALITATIVE_OBSERVATION, no value_text:   "Observed {measurement_name}: {value} {unit}."
INSUFFICIENT_INFORMATION:                 "Result recorded for {measurement_name} with no usable value or description."
UNSUPPORTED_RESULT_TYPE:                  "Result type {result_type!r} for {measurement_name} is not yet supported for interpretation."
```

## 12. `curator_summary`

One deterministic sentence, `app.result_interpretation.mapping
.build_curator_summary`, e.g. `"Experiment measured enzyme activity of 5.2
umol/min/mg."` / `"Experiment found no detectable activity."`. No
interpretation of significance, mechanism, or biological meaning.

## 13. Determinism guarantees

Every function in `app.result_interpretation.mapping`/`.interpreter` is a
pure function of its arguments: identical inputs always produce an
identical `EvidenceCandidate` (verified directly,
`test_interpretation_is_deterministic`). No randomness, no wall-clock
reads, no LLM calls, no network calls, no database reads or writes.

## 14. Explicitly prohibited biological reasoning

Never inferred: mechanism, causality, pathway, kinetics, regulation, gene
function, protein function, reaction direction, or metabolic significance.
`NON_DETECTION` results are never rewritten into absence, negative
biology, or failure (§6; verified,
`test_non_detection_never_rewritten_into_interpretation`).

## 15. Validation

`EvidenceCandidate.__post_init__` rejects:

* missing/blank required fields (`measurement_name`, `result_type`,
  `quoted_support`, `curator_summary`);
* `value_numeric` that is not a `Decimal` (a `float` is rejected outright,
  the same precision discipline `ExperimentResultInput` already enforces);
* neither `value_text` nor `value_numeric` present;
* `evidence_type`/`directness`/`interpretation_status` that are not
  instances of their respective closed enums;
* a naive (non-timezone-aware) `observed_at`.

`app.result_interpretation.validation.require_matching_execution`/
`require_matching_recommendation` reject a genuinely inconsistent
combination of caller-supplied objects (§16) before any `EvidenceCandidate`
is built at all.

## 16. Errors

* `ResultInterpretationError` -- base class.
* `InterpretationValidationError` -- raised when the objects passed to the
  public API are internally inconsistent: a `result.execution_id` that
  does not match the supplied `execution.id`, or a supplied
  `ExperimentRecommendationRecord` that does not match
  `execution.recommendation_id`. Always a caller bug.
* `UnsupportedResultTypeError` -- defined for API completeness but **never
  raised** by this increment's own code, the same conservative-result
  philosophy as `app.persistence.errors.TerminalKnowledgeGapError`: an
  unrecognized `result_type` is represented as data
  (`InterpretationStatus.UNSUPPORTED_RESULT_TYPE`), not a raised exception,
  so a batch of results is never aborted by one unrecognized row.

## 17. Public API

```python
def interpret_experiment_result(
    execution: ExperimentExecution,
    result: ExperimentResult,
    *,
    recommendation: ExperimentRecommendationRecord | None = None,
    context: ExperimentInterpretationContext | None = None,
) -> list[EvidenceCandidate]: ...

def interpret_experiment_execution(
    execution: ExperimentExecution,
    results: Sequence[ExperimentResult],
    *,
    recommendation: ExperimentRecommendationRecord | None = None,
    context: ExperimentInterpretationContext | None = None,
) -> list[EvidenceCandidate]: ...
```

Both are pure functions of already-loaded ORM objects. Neither accepts a
`Session`, neither issues a query, neither writes anything.
`interpret_experiment_result` always returns a list of exactly one element
(a uniform return shape with `interpret_experiment_execution`, Increment 26
instructions, Step 7). `interpret_experiment_execution` is a thin,
visible fan-out: one call per result, in the exact order supplied.

## 18. Examples

```python
result = ExperimentResult(
    result_type="QUANTITATIVE_MEASUREMENT",
    measurement_name="enzyme activity",
    value_numeric=Decimal("5.2"),
    unit="umol/min/mg",
    ...,
)
candidate = interpret_experiment_result(execution, result)[0]
# candidate.interpretation_status is InterpretationStatus.DIRECT_OBSERVATION
# candidate.quoted_support == "Measured enzyme activity: 5.2 umol/min/mg."
# candidate.curator_summary == "Experiment measured enzyme activity of 5.2 umol/min/mg."

non_detection = ExperimentResult(
    result_type="NON_DETECTION",
    measurement_name="activity",
    value_text="No activity detected",
    ...,
)
candidate = interpret_experiment_result(execution, non_detection)[0]
# candidate.interpretation_status is InterpretationStatus.NON_DETECTION
# candidate.quoted_support == "No activity detected"  (verbatim)
```

## 19. Testing

`tests/result_interpretation/test_interpreter.py` (38 tests): quantitative
measurements, qualitative observations (including `ASSAY_OUTCOME`),
non-detection (including the "never rewritten into interpretation" check),
unsupported result types, multiple results (ordering, no aggregation),
deterministic output, subject/predicate/object context handling, validation
failures, immutable outputs, no session/persistence/database-write source
patterns, no `Claim`/`Evidence` row-count change, no `KnowledgeGap`/
`ExperimentResult` mutation. Full repository suite (2181 tests) and the
`SAWarning`-strict pass both remain green.

## 20. Explicit non-goals

No normalization, no entity resolution, no claim generation, no
persistence, no confidence scoring, no review/lifecycle transitions, no
hypothesis generation, no autonomous experiment design, no LLM reasoning.
This package never imports `app.claim_generation`, `app.confidence`,
`app.review.workflow`, or any LLM client (verified by source-level tests).

## 21. Future expansion

Future work -- explicitly **not** part of Agent 1 Version 1 -- may:

* generate real `Claim`/`Evidence` rows from `EvidenceCandidate` (a
  dedicated persistence increment, mirroring how `app.persistence.claim`
  ingests `CandidateClaim` today);
* recompute confidence once such Evidence exists;
* close the originating `KnowledgeGap` once new evidence resolves it;
* generate hypotheses from patterns across many `EvidenceCandidate`s;
* design new experiments autonomously in response to interpreted results.

None of these belong to Agent 1 Version 1. This increment produces
candidate evidence and stops there.

## 22. Final architectural rule

> ExperimentExecution records what was done.
>
> ExperimentResult records what was observed.
>
> EvidenceCandidate restates that observation in a form the existing
> evidence pipeline can eventually consume.
>
> None of the three decides what the observation means scientifically.
