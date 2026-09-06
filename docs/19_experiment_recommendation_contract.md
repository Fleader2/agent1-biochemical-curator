# Agent 1 Deterministic Experiment Recommendation Contract

## 1. Purpose

Convert an already-detected, deterministic knowledge gap
(`KnowledgeGapCandidate` or a persisted `KnowledgeGap`) into a structured,
auditable `ExperimentRecommendation` using explicit, versioned, rule-based
templates. Answers exactly one question: *given this specific,
already-detected gap, what class of experiment would directly reduce it?*
Never invents novel biology, never generates free-form hypotheses, never
uses an LLM.

## 2. Pipeline position

```text
Knowledge Gap Detection (Increment 21, app.knowledge_gaps.analysis)
    -> KnowledgeGap persistence (Increment 22, app.persistence.knowledge_gap)
    -> Experiment Recommendation (this increment, app.experiment_recommendation)
```

Consumes an already-produced `KnowledgeGapCandidate` (in-memory) or
`KnowledgeGap` (persisted) as a read-only input, plus an optional
`ExperimentRecommendationContext` of already-loaded `Claim`/`Evidence`
rows. Writes nothing -- `KnowledgeGap.suggested_experiment` remains
completely untouched (§18).

## 3. Gap-driven recommendation principle

A recommendation may propose *how* to measure or validate information the
knowledge base has already demonstrated is missing. It may never invent:
a new biological mechanism, a new pathway, a new protein function, a new
regulatory relationship, a new compound, or an interaction not already
implied by the gap. Every `objective`/`success_criterion` string in this
package is a fixed template constant (§7); the only things ever
substituted in are structural facts already present on the gap itself
(ids), never generated prose.

## 4. `ExperimentRecommendation` schema

```python
ExperimentRecommendation(
    knowledge_gap_identity: str,
    gap_type: GapType,
    gap_severity: GapSeverity,
    status: RecommendationStatus,
    objective: str,
    rationale: str,
    template_id: str,
    template_version: str,
    experiment_class: ExperimentClass | None = None,
    target_entity_type: str | None = None,
    target_entity_id: UUID | None = None,
    required_measurement: str | None = None,
    required_comparison: str | None = None,
    experimental_context_requirements: tuple[str, ...] = (),
    success_criterion: str | None = None,
    supporting_claim_ids: tuple[UUID, ...] = (),
    supporting_evidence_ids: tuple[UUID, ...] = (),
    reason_codes: tuple[str, ...] = (),
)
```

`experiment_class`/`success_criterion` are populated if and only if
`status is RECOMMENDED` -- a structural guarantee (`__post_init__`
enforces it), not merely documentation. Deliberately excluded: free-form
hypothesis text, protocol steps, reagent catalog numbers, vendor names,
cost, schedule, and any confidence score not already a direct field
inherited from the gap (`gap_severity`).

## 5. `RecommendationStatus`

* `RECOMMENDED` -- a specific `ExperimentClass` is deterministically
  justified.
* `NOT_APPLICABLE` -- this gap is not an experimental opportunity at all
  (a provenance or connectivity observation).
* `REQUIRES_HUMAN_DESIGN` -- a real experimental gap, but no specific
  class is deterministically justified.
* `INSUFFICIENT_INFORMATION` -- the supplied context did not contain the
  claim/evidence data this gap references; a context-completeness
  problem, distinct from a scientific-judgment one.

## 6. `ExperimentClass` taxonomy

Only 5 members -- every one actually assigned by a template (§9):
`REPLICATION_EXPERIMENT`, `PROTEIN_LOCALIZATION_ASSAY`,
`EXPERIMENTAL_CONTEXT_CHARACTERIZATION`, `REACTION_VALIDATION`,
`ENZYME_SUBSTRATE_ASSAY`.

**Excluded, and why**: `KINETIC_MEASUREMENT`, `GENETIC_PERTURBATION`,
`PROTEIN_INTERACTION_ASSAY`, `METABOLITE_MEASUREMENT`,
`FLUX_MEASUREMENT`, `EXPRESSION_MEASUREMENT`, `ENTITY_ANNOTATION_REVIEW`.
No currently-implemented `GapType` policy can select any of these without
inventing a claim/entity "topic" distinction the schema does not
structurally support -- `Claim.claim_category`/`predicate` are free text
with no controlled vocabulary (verified against
`app.claim_generation.types`), and nothing else on `Claim`/`Evidence`
closes that gap except the one narrow exception described in §9/§11
(`object_type == "COMPARTMENT"`).

## 7. Template registry

`app.experiment_recommendation.templates.TEMPLATE_REGISTRY`: 19 fixed,
versioned `ExperimentTemplate` records (18 gap-type-specific + 1 generic
`INSUFFICIENT_INFORMATION` fallback), each holding `template_id`,
`version`, `supported_gap_types`, `status`, `objective`, `reason_code`,
and (when `status is RECOMMENDED`) `experiment_class`/
`success_criterion`/`experimental_context_requirements`. Validated at
import time: no duplicate `template_id`, no blank `version`/`objective`/
`reason_code`, and `experiment_class`/`success_criterion` populated if and
only if `status is RECOMMENDED` -- violations raise
`TemplateApplicationError` immediately, not silently.
`app.experiment_recommendation.rules` selects a template purely from
`GapType` + already-persisted reason codes/claim/evidence composition; it
never falls through to an unrelated template for an unhandled `GapType`
(`UnsupportedGapTypeError` instead, verified unreachable given `GapType`'s
current closed membership).

## 8. Template versioning

Every `template_id` embeds its version (`kg-exp-conflict-replication-v1`),
and `ExperimentTemplate.version` restates it as an independent field.
Changing a template's scientific content must create a new
`template_id`/`version` rather than redefining an existing one's meaning
-- the same discipline `app.confidence.aggregate_policy.ALGORITHM_VERSION`
already established. Recommendation identity (§11) is likewise
version-derived, never content-derived from prose.

## 9. `GapType` policy table

| GapType | RecommendationStatus | ExperimentClass | Template ID(s) |
|---|---|---|---|
| `CONFLICTING_CLAIMS` (value disagreement) | RECOMMENDED | REPLICATION_EXPERIMENT | `kg-exp-conflict-replication-v1` |
| `CONFLICTING_CLAIMS` (status-only) | REQUIRES_HUMAN_DESIGN | -- | `kg-exp-conflict-human-v1` |
| `LOW_CONFIDENCE_CLAIM` (primary evidence present) | RECOMMENDED | REPLICATION_EXPERIMENT | `kg-exp-lowconf-replication-v1` |
| `LOW_CONFIDENCE_CLAIM` (no primary, object=COMPARTMENT) | RECOMMENDED | PROTEIN_LOCALIZATION_ASSAY | `kg-exp-lowconf-localization-v1` |
| `LOW_CONFIDENCE_CLAIM` (unmappable) | REQUIRES_HUMAN_DESIGN | -- | `kg-exp-lowconf-human-v1` |
| `SINGLE_SOURCE_SUPPORT` (primary evidence present) | RECOMMENDED | REPLICATION_EXPERIMENT | `kg-exp-singlesource-replication-v1` |
| `SINGLE_SOURCE_SUPPORT` (no primary, object=COMPARTMENT) | RECOMMENDED | PROTEIN_LOCALIZATION_ASSAY | `kg-exp-singlesource-localization-v1` |
| `SINGLE_SOURCE_SUPPORT` (unmappable) | REQUIRES_HUMAN_DESIGN | -- | `kg-exp-singlesource-human-v1` |
| `NO_PRIMARY_EXPERIMENTAL_EVIDENCE` (object=COMPARTMENT) | RECOMMENDED | PROTEIN_LOCALIZATION_ASSAY | `kg-exp-noprimary-localization-v1` |
| `NO_PRIMARY_EXPERIMENTAL_EVIDENCE` (unmappable) | REQUIRES_HUMAN_DESIGN | -- | `kg-exp-noprimary-human-v1` |
| `MISSING_PUBLICATION` | NOT_APPLICABLE | -- | `kg-exp-missingpub-na-v1` |
| `MISSING_EXPERIMENTAL_CONTEXT` | RECOMMENDED | EXPERIMENTAL_CONTEXT_CHARACTERIZATION | `kg-exp-context-characterization-v1` |
| `REACTION_WITHOUT_ENZYME` (structured candidates present) | RECOMMENDED | ENZYME_SUBSTRATE_ASSAY | `kg-exp-enzyme-candidate-v1` |
| `REACTION_WITHOUT_ENZYME` (no candidates -- always, today) | REQUIRES_HUMAN_DESIGN | -- | `kg-exp-enzyme-human-v1` |
| `PROTEIN_WITHOUT_REACTION` | NOT_APPLICABLE | -- | `kg-exp-protein-na-v1` |
| `GENE_WITHOUT_PROTEIN` | NOT_APPLICABLE | -- | `kg-exp-gene-na-v1` |
| `REACTION_WITHOUT_PARTICIPANTS` | RECOMMENDED | REACTION_VALIDATION | `kg-exp-reaction-validation-v1` |
| `ISOLATED_COMPOUND` | NOT_APPLICABLE | -- | `kg-exp-compound-na-v1` |
| *(any gap, missing context)* | INSUFFICIENT_INFORMATION | -- | `kg-exp-insufficient-context-v1` |

Every current `GapType` is classified explicitly (Increment 23
instructions, Step 9): A (deterministic class possible) --
`MISSING_EXPERIMENTAL_CONTEXT`, `REACTION_WITHOUT_PARTICIPANTS`, and the
comparable-conflict/primary-evidence/compartment-object branches of
`CONFLICTING_CLAIMS`/`LOW_CONFIDENCE_CLAIM`/`SINGLE_SOURCE_SUPPORT`/
`NO_PRIMARY_EXPERIMENTAL_EVIDENCE`; B (human design required) -- the
remaining branches of those same four, plus `REACTION_WITHOUT_ENZYME` in
current practice; C (not an experimental gap) --
`MISSING_PUBLICATION`, `PROTEIN_WITHOUT_REACTION`, `GENE_WITHOUT_PROTEIN`,
`ISOLATED_COMPOUND`; D (insufficient structured data) -- any gap type when
the supplied context omits the claim/evidence it references.

## 10. Conflict-gap recommendations

`CONFLICTING_CLAIMS` never picks a winner. The `LITERAL_VALUE_DISAGREEMENT`
signal (already computed by `app.knowledge_gaps.rules.detect_conflicting_claims`,
never re-derived here) with 2+ supporting claims recommends a
`REPLICATION_EXPERIMENT` whose objective is *"discriminate between the
conflicting observations under controlled conditions"* -- never "prove
Claim A." `required_comparison` names the claim ids being compared,
structurally, never a value judgment about which is correct. The
`CLAIM_STATUS_CONFLICTED` signal (a single claim, no comparison partner
available) has no comparison structure to build a recommendation from, so
it returns `REQUIRES_HUMAN_DESIGN`.

## 11. Evidence-quality-gap recommendations

`LOW_CONFIDENCE_CLAIM`/`SINGLE_SOURCE_SUPPORT`/
`NO_PRIMARY_EXPERIMENTAL_EVIDENCE` share one decision sequence: (1) if any
resolved supporting evidence is primary (reusing
`app.knowledge_gaps.rules.NON_PRIMARY_EVIDENCE_TYPES`/
`AMBIGUOUS_EVIDENCE_TYPES`/`DERIVATIVE_DIRECTNESS` verbatim, never a second
policy), recommend `REPLICATION_EXPERIMENT`; (2) otherwise, if the claim's
`object_type == "COMPARTMENT"` (a closed `EntityKind` value set by claim
generation, never inferred here), recommend `PROTEIN_LOCALIZATION_ASSAY`;
(3) otherwise `REQUIRES_HUMAN_DESIGN`. **This is the one narrow claim-
structure inference this package makes** -- Step 13's own illustrative
examples ("reaction activity claim -> biochemical activity assay",
"expression claim -> expression measurement", "metabolite claim ->
metabolite measurement", "flux claim -> flux measurement") are **not**
implemented, because nothing else on `Claim`/`Evidence` provides an
equally unambiguous, already-persisted, non-free-text signal to
distinguish those topics -- attempting to would require parsing
`claim_category`/`predicate` free text, exactly the "fuzzy semantic NLP"
Step 13 forbids. Disclosed here, not silently narrowed.

## 12. Provenance/context gaps

`MISSING_PUBLICATION` is always `NOT_APPLICABLE` -- a provenance recovery
task, explicitly never called an experiment (`objective` says "recover or
verify," never "measure"). `MISSING_EXPERIMENTAL_CONTEXT` is always
`RECOMMENDED`/`EXPERIMENTAL_CONTEXT_CHARACTERIZATION`, since the gap
itself already deterministically identifies which evidence lacks
recorded conditions -- no guess about *which* context field matters is
needed, only a request to record the standard set (§15).

## 13. Reaction completeness gaps

`REACTION_WITHOUT_PARTICIPANTS` is always
`RECOMMENDED`/`REACTION_VALIDATION` -- objective and success criterion
transcribed directly from the increment's own instructions (Steps 19/27),
since establishing "what are the participants and their stoichiometry" is
itself the deterministic, gap-implied task, with no substrate/product
ever named. `REACTION_WITHOUT_ENZYME` never invents a candidate enzyme:
`REQUIRES_HUMAN_DESIGN` unless `ExperimentRecommendationContext
.reaction_candidate_protein_ids` already names structured candidates for
that reaction -- **no code anywhere in this repository populates that
field today**, so this is `REQUIRES_HUMAN_DESIGN` in every case reachable
in current practice; the field exists purely as a forward-compatibility
hook.

## 14. Entity connectivity gaps

`PROTEIN_WITHOUT_REACTION`/`GENE_WITHOUT_PROTEIN`/`ISOLATED_COMPOUND` are
all `NOT_APPLICABLE` -- none of these gaps demonstrates a confirmed
experimental deficiency (a protein may legitimately be non-enzymatic, a
gene may legitimately have no curated protein product, a compound may be
a legitimate but currently-unconnected reference record). `Protein
.ec_number` is deliberately **not** used to promote `PROTEIN_WITHOUT_REACTION`
to a `RECOMMENDED` biochemical assay, per the increment's own explicit
instruction (Step 17: "Do not infer from EC number if the gap detector
did not persist such structured reasoning") -- even though it is a
tempting, already-persisted signal.

## 15. Experimental context requirements

Two fixed field-name tuples, never invented values:

* Replication-style recommendations: `("organism", "strain")` -- already-
  available `Claim`/`Evidence` columns.
* `MISSING_EXPERIMENTAL_CONTEXT`: the exact `ExperimentalCondition` column
  names (`medium`, `carbon_source`, `nitrogen_source`, `oxygen_status`,
  `temperature_c`, `ph`, `growth_phase`, `culture_mode`), transcribed
  verbatim from `app.models.experimental_condition`.

No recommendation ever fills in a value (`temperature_c = 30`) -- only
field *names* that should be explicitly controlled/reported.

## 16. Success criteria

Every `RECOMMENDED` template carries a fixed, qualitative
`success_criterion` string -- no numeric threshold is ever invented
(verified directly: `tests/experiment_recommendation/test_rules.py`
asserts no digit appears in any registered `success_criterion`).

## 17. Determinism

Same gap + same context + the same template registry always produce a
byte-identical `ExperimentRecommendation` (verified directly, repeated-call
equality tests). No randomness, no wall-clock read, no LLM call anywhere
in `app.experiment_recommendation`.

## 18. Explicit non-responsibilities

No LLM generation, no hypothesis generation, no experiment execution, no
instrument control, no scheduling, no cost estimation, no procurement, no
laboratory robotics, no literature retrieval, no new connectors, no
persistence into `KnowledgeGap.suggested_experiment` (verified by a
source-level test that the string `"suggested_experiment"` does not even
appear in `app.experiment_recommendation.recommender`), no model
simulation, no model-impact scoring, no review-workflow change.

## 19. Known limitations

* Only one narrow claim-topic inference exists (`object_type ==
  "COMPARTMENT"`, §11) -- reaction-activity/expression/metabolite/flux
  claim topics fall back to `REQUIRES_HUMAN_DESIGN` for lack of an
  equally unambiguous signal.
* `REACTION_WITHOUT_ENZYME`'s candidate-protein branch is unreachable in
  current practice (§13) -- no upstream process populates candidates.
* Recommendations for a persisted `KnowledgeGap` predating Increment 22's
  schema hardening (no `gap_type`/`severity`/`identity_key`) are refused
  outright (`InvalidRecommendationContextError`), not guessed.
* `ExperimentRecommendationContext` must be built and supplied explicitly
  for context-dependent gap types; omitting it degrades those gaps to
  `INSUFFICIENT_INFORMATION` rather than `REQUIRES_HUMAN_DESIGN` -- a
  deliberate distinction (§5), not a defect.

## 20. Relationship to future recommendation persistence

Deliberately deferred. `compute_recommendation_identity` (versioned
`experiment-rec-v1:<sha256>`, derived from `knowledge_gap_identity` +
`template_id` + `template_version` only, never from `rationale`) is ready
for a future persistence increment to use as a stable row identity, the
same way `app.persistence.knowledge_gap.compute_identity_key` already
serves `KnowledgeGap` itself -- but no such table or write path exists
yet, and `KnowledgeGap.suggested_experiment` remains untouched (§18).

## 21. Relationship to future hypothesis generation

None of this package's outputs constitute a hypothesis. A
`RECOMMENDED` recommendation names a class of measurement, not a
predicted result; `REQUIRES_HUMAN_DESIGN` explicitly defers judgment to a
human. Structured hypothesis generation (if ever built) would consume
accepted experimental *results*, not this package's recommendations,
which describe only what evidence to go gather.

## 22. Testing

`tests/experiment_recommendation/`: `test_types.py` (dataclass
self-validation, including the `RECOMMENDED` <-> `experiment_class`/
`success_criterion` structural guarantee), `test_templates.py` (registry
invariants: no duplicate ids, every `GapType` covered, `RECOMMENDED`-status
consistency), `test_rules.py` (every `GapType` branch, DB-free in-memory
ORM fixtures, no-bias/no-fuzzy-NLP/no-invented-threshold assertions,
determinism), `test_recommender.py` (both public entry points, real-session
context building, recommendation identity stability/versioning, source-level
safety checks for commits/writes/`suggested_experiment`/LLM/connector
imports). 84 tests, all passing; full repository suite (1977 tests) and
the `SAWarning`-strict pass both remain green.

## 23. Final architectural rule

> Experiment recommendation acts only on a demonstrated knowledge gap.
>
> It may recommend how to obtain missing evidence.
>
> It may not invent the biological answer that the experiment is intended
> to discover.
