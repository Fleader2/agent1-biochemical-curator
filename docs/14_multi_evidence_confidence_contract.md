# Agent 1 Multi-Evidence Claim Confidence Contract

**Document:** `docs/14_multi_evidence_confidence_contract.md`

**Status:** Authoritative specification for `app/confidence/aggregation.py`,
`aggregate_types.py`, and `aggregate_policy.py` (Increment 18, revised by
Increment 18A).

This is the layer that finally produces a real 0-100 `Claim.confidence_score`
and `ConfidenceClass` — Increment 17's `SingleEvidenceAssessment`
deliberately never did (`docs/13_single_evidence_confidence_contract.md`).

**Increment 18A note:** Increment 18 identified that the authoritative
specification never says how *multiple, differing* evidence base scores
combine, and declined to invent a rule — heterogeneous eligible evidence
produced `score=None`/`UNKNOWN`. Increment 18A resolves that gap with the
canonical aggregation mathematics in §15a below. Every other section of
this document (compatibility, eligibility, organism/experimental
relevance, replication, conflict, duplicates) is unchanged from
Increment 18.

---

## 1. Purpose

`app.confidence.aggregation.aggregate_claim_confidence` answers:

> Given every available, independently-assessed piece of evidence for one
> logical claim, how strong is the evidence *set* as a whole?

Final Claim confidence is a property of an **evidence set**, not a single
observation. This layer distinguishes evidence strength, replication,
source independence, source dependence, conflict, organism relevance,
experimental relevance, and entity-resolution limitations — it never
simply sums evidence scores.

---

## 2. Pipeline position

```text
CandidateClaim + EvidenceExtraction
        |
        v
SingleEvidenceAssessment (Increment 17)
        |
        v
EvidenceContribution (adds publication/independence/conflict provenance)
        |
        +-- (one or more, for the same logical claim) --+
        v
aggregate_claim_confidence
        |
        v
AggregateClaimConfidence
        |
        v
0-100 score / ConfidenceClass
```

No Claim/Evidence persistence occurs anywhere in this layer — it produces
an in-memory `AggregateClaimConfidence`, a later increment's
responsibility to write to `Claim.confidence_score`/`confidence_class`.

---

## 3. Aggregate input contract

`EvidenceContribution` wraps one evidence item's `CandidateClaim` + its own
`SingleEvidenceAssessment`, plus what aggregation needs that a single
assessment does not carry:

| Field | Meaning |
|---|---|
| `candidate_claim` | The claim this contribution supports. |
| `single_evidence_assessment` | Must describe this exact claim (`evidence_type`/`directness` cross-checked). |
| `publication_identifier` | Caller's best stable identifier (resolved `Publication` UUID, PMID/PMCID/DOI, or the originating `source_identifier` — never a bare title). |
| `source_identifier` | The originating document identifier. |
| `experiment_identifier` | `None` unless genuinely known to be a distinct same-paper experiment (§9). |
| `organism_relevance` / `experimental_relevance` | See §7/§8 — best constructed via `build_evidence_contribution`, which derives them deterministically rather than requiring the caller to know the policy. |
| `independence_group` | Optional override of the default independence grouping (§9). |
| `conflict_status` | Explicit, externally-supplied `ConflictSeverity` (§12) — never discovered here. |

Every field that cannot be deterministically derived today defaults to an
explicit `UNKNOWN`/`None` — never invented.

---

## 4. Claim compatibility

Aggregation may only combine contributions that represent the same
logical claim (`app.confidence.aggregation._are_compatible`). Compared,
per `CandidateClaim`:

- **Subject** — exact identity-key match: both resolved to the identical
  canonical UUID, or both unresolved with identical exact text. A
  resolved/unresolved mismatch is **not** "clearly compatible" and is
  rejected (fail closed).
- **Predicate** — exact string equality. **Never canonicalized** here.
- **Object** — same identity-key rule as subject, including "both absent"
  as a valid match; one present and the other absent is a shape mismatch
  and rejected.
- **Value shape** — both claims must either carry a literal value or
  neither must (exact reported *values* are not required to match — that
  variation is exactly what multiple evidence should be aggregated over,
  not gated on).
- **Organism / compartment** — an absent reference is compatible with
  anything (unknown is never a mismatch); two *explicitly different*
  references are incompatible.
- **Strain** — two explicitly different strains are incompatible; an
  absent strain on either side is compatible with anything.

Incompatible contributions raise `IncompatibleClaimsError` — claims are
never merged because they "sound similar."

---

## 5. Numeric eligibility

An `EvidenceContribution` may contribute numerically only if **all** of:

- `single_evidence_assessment.scoring_status is SCORED_BASE` (an
  authoritative `EvidenceType` base score exists),
- `subject_resolution` is `RESOLVED` or `VERIFIED_NEW`,
- `object_resolution` (if not `None`) is `RESOLVED`, `VERIFIED_NEW`, or
  `NOT_APPLICABLE`,
- `organism_resolution` is not `EXPLICIT_UNRESOLVED`/`CONFLICTED`/
  `SOURCE_FAILURE`/`UNKNOWN` (an organism that was never *stated*,
  `NOT_STATED`, never blocks eligibility),
- `compartment_resolution` (if not `None`) is `RESOLVED`, `VERIFIED_NEW`,
  or `NOT_APPLICABLE`.

This is a **binary eligibility gate, never a numeric penalty or cap** —
no authoritative entity-resolution scoring rule exists anywhere, and this
increment does not invent one (Step 17 of its own instructions). An
ineligible contribution is never deleted — it remains fully visible in
`contribution_breakdown` with its `ineligibility_reasons`.

---

## 6. Evidence base scores

Reused unchanged from Increment 17 (`app.confidence.policy
.EVIDENCE_TYPE_BASE_SCORES`/`EVIDENCE_TYPE_UNSCORED`) — the same
authoritative table, the same eight unscored `EvidenceType` members. Only
`SCORED_BASE` contributions may contribute numerically; `UNSCORED_
EVIDENCE_TYPE` contributions remain fully visible in the audit trail with
`evidence_base_score=None`, never an invented number.

---

## 7. Organism relevance

`OrganismRelevance` is an **evidence-specific** property (Step 6's own
header) — an intrinsic property of *one* claim's own organism/strain
specificity, not a pairwise comparison against sibling evidence:

```python
if claim.organism is None:
    return UNKNOWN
if claim.strain is not None and claim.organism.normalized_id is not None:
    return SAME_STRAIN      # 100%
return SAME_SPECIES          # 95%
```

`SAME_GENUS`/`OTHER_FUNGUS`/`OTHER_EUKARYOTE`/`BACTERIUM` (70%/55%/40%/25%,
transcribed from `docs/03_agent_behavior.md`) are never produced: they
would require taxonomic-distance data (genus/kingdom/evolutionary
distance) this repository has nowhere, and are structurally moot within
one aggregate group anyway — claim compatibility (§4) already requires
every contribution's organism to agree (or be unstated), so two
*different* organisms never coexist in one aggregation. `UNKNOWN` is
applied as a neutral 100% multiplier, never a fabricated reduction.

**A rejected alternative, disclosed for transparency:** comparing each
contribution's organism against a single fixed "target model organism"
(e.g. *Saccharomyces cerevisiae*, this project's Version 0.1 focus) was
considered, since it would make the authoritative genus/kingdom tiers
meaningful. It was rejected because `.cursor/rules/00-agent1-core.mdc`
explicitly requires this architecture to "remain organism-agnostic" and
forbids hard-coding organism-specific behavior into generic
infrastructure "unless necessary" — it is not necessary here, since the
evidence-specific interpretation above is fully sufficient and organism-
agnostic.

---

## 8. Experimental relevance

Only one deterministic mapping is implemented:

```python
if assessment.evidence_type is EvidenceType.COMPUTATIONAL:
    return COMPUTATIONAL_ONLY   # 40%
return UNKNOWN
```

`PHYSIOLOGICAL_IN_VIVO`/`CELL_LYSATE`/`PURIFIED_NATIVE_ENZYME`/
`RECOMBINANT_ENZYME`/`HETEROLOGOUS_EXPRESSION` (100%/90%/90%/80%/70%,
transcribed authoritatively) are never produced: classifying them would
require parsing `EvidenceExtraction.experimental_system`/`assay` — both
plain free text with no controlled vocabulary
(`docs/09_evidence_extraction_contract.md`) — which this increment's own
instructions explicitly forbid ("Do not invent classification from vague
prose"). `EvidenceType.DIRECT_IN_VIVO` was considered and **rejected** as
a proxy for `PHYSIOLOGICAL_IN_VIVO`: "in vivo" alone does not confirm a
physiological (unperturbed, native-context) system — a heterologous-
expression assay can also be reported as "in vivo". `UNKNOWN` applies a
neutral 100% multiplier.

---

## 9. Evidence independence

The **effective independence group** for replication counting
(`app.confidence.aggregation._effective_independence_group`):

1. `contribution.independence_group`, if explicitly supplied.
2. Otherwise `f"{publication_identifier}:{experiment_identifier}"`, if
   `experiment_identifier` is supplied (Step 12's conservative allowance
   for a genuinely known, distinct same-paper experiment).
3. Otherwise `publication_identifier` alone.

**Same-publication evidence defaults to one shared independence group**
(Step 12: "treat same-paper evidence as one replication unit") — same-
paper contributions never automatically earn a replication bonus merely
by being separate `EvidenceContribution` records.

---

## 10. Replication bonus

Authoritative, transcribed exactly: **+5 for the first additional
independent source, +5 for the next, capped at +10** total. Implemented
from the count of **distinct independence groups among numerically
eligible, non-derivative contributions** (§11):

```python
steps = min(independent_group_count - 1, 2)
replication_bonus = steps * 5   # 0, 5, or 10
```

A single evidence item never earns a bonus (`independent_group_count <= 1`
gives 0). The bonus is computed and reported even when the final `score`
itself cannot be (§14's blocking case) — it is independently well-defined
regardless.

---

## 11. Derivative-source policy

A review summarizing a primary paper, or a curated-database annotation
derived from one, is not an independent replication of that primary
result (Step 13). Detected the only deterministic way available — via
`Directness`, not inferred citation relationships (Step 32):
contributions whose `directness` is `REVIEW_SUMMARIZES` or
`DATABASE_ANNOTATES` are excluded from the independence-group count for
replication purposes, **even if** their own `evidence_base_score` is
otherwise numerically eligible (e.g. a `CURATED_DATABASE` contribution
with `directness=DATABASE_ANNOTATES` still contributes its own base score,
but never counts as a "new" independent replicating source). No citation
graph is inspected or inferred — this is a purely local, per-contribution
classification from already-known fields.

---

## 12. Conflict input model

`ConflictSeverity` (`NONE`/`MINOR`/`MAJOR`/`UNKNOWN`) is always an
**explicit, externally-supplied** input — this increment never discovers
a conflict from claim text or predicate comparison (Step 15). `UNKNOWN`
means "conflict status was not determined," distinct from `NONE`
("determined: no conflict") — both apply a zero penalty, never a
fabricated one. A negative predicate polarity (`"did not bind"`) is never,
by itself, treated as a conflict signal (Step 33).

---

## 13. Conflict penalties

Authoritative, transcribed exactly: **MINOR → -10, MAJOR → -25**. When
multiple contributions in one aggregation carry different conflict
severities, the **single most severe one is applied once — never
stacked/summed** (Step 16: "Do not stack unlimited conflict penalties
unless spec explicitly says to" — it does not).

---

## 14. Duplicate evidence

Two contributions are the exact same evidence item iff their originating
document, character offsets, and quoted text all agree
(`app.confidence.aggregation._dedup_key`) — never guessed from claim
content alone. An exact duplicate is **deduplicated, not deleted**: it is
excluded from numeric aggregation (marked `is_duplicate=True`,
`ineligibility_reasons=("DUPLICATE_EVIDENCE",)`) but remains fully visible
in `contribution_breakdown`.

---

## 15. Aggregate formula (historical: Increment 18's blocking finding)

Per-contribution adjustment (fully specified, computed for every
numerically eligible contribution, unchanged since Increment 18):

```text
adjusted_contribution = evidence_base_score x organism_relevance x experimental_relevance
```

**Increment 18's mandatory finding**, preserved here for the historical
record (§15a below resolves it): `docs/03_agent_behavior.md`'s own
formula contains exactly **one** `base_evidence_score` term. It never
specifies how to combine *multiple, differing* evidence items' own base
contributions — "Agent 1 must not simply sum all evidence scores" rules
out summation, but neither maximum, weighted maximum, nor averaging is
ever stated for genuinely disagreeing evidence (e.g. one
`DIRECT_BIOCHEMICAL` item and one `GENETIC` item for the same claim).
Increment 18 declined to invent that rule: heterogeneous eligible
evidence produced `score=None`/`UNKNOWN` with reason code
`AGGREGATE_BASE_COMBINATION_UNSPECIFIED`. **Increment 18A removes this
limitation entirely — that reason code no longer exists anywhere in this
codebase** — see §15a for the canonical resolution.

---

## 15a. Canonical Aggregation Mathematics (Increment 18A)

Increment 18A establishes the canonical, deterministic algorithm for
combining any number of numerically eligible contributions — identical or
heterogeneous `EvidenceType`s alike — without inventing an unjustified
rule. It satisfies eight scientific design principles: more evidence never
reduces confidence (except explicit conflict penalties); multiple weak
observations gradually strengthen confidence; one very strong experiment
remains more influential than many speculative ones; confidence exhibits
diminishing returns; confidence asymptotically approaches but never
exceeds 100; replication and conflict each act independently of raw
evidence strength; and every contribution remains individually auditable.

### Step 1 — Adjusted score (unchanged from Increment 18)

For every numerically eligible contribution:

```text
AdjustedScore = BaseScore x OrganismModifier x ExperimentalModifier
```

Kept as an exact `Decimal` (not floor-divided to an `int`, as Increment 18
did) — the diminishing-return sum below must never compound rounding
error across several contributions. Only the *final* aggregate is
rounded, once (Step 6).

### Step 2 — Order contributions

Every numerically eligible, non-duplicate contribution is sorted by
`AdjustedScore` descending. Ties (the common case — e.g. two independent
reports of the same `EvidenceType` with neutral relevance modifiers) are
broken by a stable, input-order-independent canonical key, so the same
evidence set in any original input order always assigns the same rank —
and therefore the same weight — to the same specific contribution.

### Step 3 — Diminishing-return accumulation

The `k`-th ranked contribution (1-indexed) is weighted:

```text
Weight_k = WEIGHT_BASE ** (k - 1)     # 1, 1/2, 1/4, 1/8, ... (WEIGHT_BASE = 0.5)
```

```text
AggregateEvidence = sum(AdjustedScore_k * Weight_k for k in 1..n)
```

This is defined for *any* sequence of adjusted scores, identical or not —
there is no combination *choice* left to make, which is exactly how this
resolves Increment 18's blocking finding. It also directly delivers three
of the eight design principles: the single strongest contribution is
never diminished (its own weight is always exactly 1); every additional
contribution still adds something, but strictly less than the previous
one; and the sum can never explode linearly with contribution count.

### Step 4 — Replication bonus (unchanged from Increment 18)

Applied **on top of**, never inside, the weighted sum: `+5` for the first
additional independent, non-derivative source beyond the first, `+5` for
the next, capped at `+10` total (§10-11).

### Step 5 — Conflict penalty (unchanged from Increment 18)

The single most severe supplied `ConflictSeverity` across all
contributions, applied once, never stacked: `MINOR -10` / `MAJOR -25`
(§12-13).

### Step 6 — Round and clamp

```text
raw_score = AggregateEvidence + replication_bonus - conflict_penalty
final_score = clamp(round_half_up(raw_score), 0, 100)
```

Rounding uses **round-half-up** (ties round away from zero), not Python's
default banker's rounding — `67.5 -> 68`, and (after replication/
conflict) `62.5 -> 63`. Rounding integer bonuses/penalties commutes with
rounding the weighted sum itself, so `base_evidence_total` (the rounded
`AggregateEvidence`, reported independently on `AggregateClaimConfidence`)
plus `replication_bonus` minus `conflict_penalty` always equals
`final_score` exactly — no double-rounding discrepancy is possible.

### Step 7 — ConfidenceClass (unchanged — see §16)

### Why the ceiling holds without a separate rule

Today's highest authoritative `EvidenceType` base score is 45
(`DIRECT_BIOCHEMICAL`). The weight sequence `1, 1/2, 1/4, ...` sums to
strictly less than 2 for any finite contribution count (a geometric
series whose infinite sum is exactly 2). So `AggregateEvidence < 45 * 2 =
90` for *any* number of same-strength eligible contributions — before the
replication bonus is even added. Design principle 5 ("asymptotically
approach but never exceed 100") is therefore a direct mathematical
consequence of the weight sequence and today's base-score ceiling, not a
separate rule bolted on — the final `[0, 100]` clamp (Step 6) remains only
a defensive backstop.

### Algorithm versioning

`AggregateClaimConfidence.algorithm_version` (default, and today's only
value, `"confidence-v1"` — `app.confidence.aggregate_policy
.ALGORITHM_VERSION`) identifies which formula produced a given result. A
genuine future revision of this formula must introduce a new version
string; `"confidence-v1"` itself must never be silently redefined.

### Worked example

Three contributions, adjusted scores 45.0 / 40.0 / 10.0 (already sorted
descending), one `MINOR` conflict, and exactly two independent,
non-derivative sources (so the replication bonus is `+5`, not `+10`):

```text
Weighted contributions: 45.0*1 = 45.0 ; 40.0*0.5 = 20.0 ; 10.0*0.25 = 2.5
Aggregate evidence:     45.0 + 20.0 + 2.5 = 67.5
Replication bonus:      +5       ->  72.5
Conflict penalty:       -10      ->  62.5
Final score (round-half-up): 63
Confidence class:       MODERATE
Algorithm:               confidence-v1
```

---

## 16. ConfidenceClass thresholds

Authoritative, transcribed exactly (same as `docs/03_agent_behavior.md`,
and identical to Increment 17's now-removed single-evidence version):

```text
90-100     VERY_HIGH
75-89      HIGH
50-74      MODERATE
0-49       LOW
None       UNKNOWN
```

This is the *only* place in the codebase that maps a score to a class —
Increment 17 deliberately has none.

---

## 17. UNKNOWN behavior

**Increment 18A narrows this.** `score is None` (and `confidence_class is
ConfidenceClass.UNKNOWN`) now occurs in exactly **one** case: no
numerically eligible contribution exists at all
(`AGGREGATE_NO_ELIGIBLE_EVIDENCE`) — e.g. every contribution's
`EvidenceType` is unscored, or every contribution failed entity-resolution
eligibility. Heterogeneous eligible evidence no longer produces `UNKNOWN`
(§15a) — `AGGREGATE_BASE_COMBINATION_UNSPECIFIED` no longer exists
anywhere in this codebase. Never fabricated as a zero — zero is a known,
extremely weak confidence; `None` means "not responsibly scoreable."

---

## 18. Contribution breakdown

`AggregateClaimConfidence.contribution_breakdown` contains exactly one
`ContributionBreakdown` per supplied `EvidenceContribution` — including
duplicates and ineligible ones, never deleted. Each entry carries: source/
publication identifier, `EvidenceType`, `evidence_base_score` (or `None`),
organism/experimental relevance, duplicate/eligibility flags and reasons,
independence group, whether it was counted for replication, conflict
status, and the originating `SingleEvidenceAssessment`'s own
`reason_codes`.

**Increment 18A addition — every diminishing-return weighting step is
individually inspectable:**

| Field | Meaning |
|---|---|
| `adjusted_contribution` | `evidence_base_score x organism_relevance x experimental_relevance`, an exact `Decimal` (§15a Step 1) |
| `weight` | This contribution's rank-based weight, `WEIGHT_BASE ** (rank - 1)` (§15a Step 3) |
| `weighted_contribution` | `adjusted_contribution x weight` |
| `cumulative_score` | The running sum of `weighted_contribution` up to and including this entry, in descending-rank order — the last (weakest-ranked) eligible entry's `cumulative_score` equals `AggregateEvidence` before replication/conflict |

All four are `None` together, exactly when `numerically_eligible` is
`False` — an ineligible or duplicate contribution has no numeric
contribution to report.

---

## 19. Reason codes

Aggregate-level codes (`app.confidence.aggregation`): `AGGREGATE_
NO_ELIGIBLE_EVIDENCE`, `AGGREGATE_BASE_EVIDENCE_APPLIED`,
`REPLICATION_BONUS_<n>`, `CONFLICT_PENALTY_<severity>`.
**`AGGREGATE_BASE_COMBINATION_UNSPECIFIED` no longer exists (Increment
18A) — heterogeneous eligible evidence now always produces
`AGGREGATE_BASE_EVIDENCE_APPLIED`.** Per-contribution ineligibility codes
(`app.confidence.aggregate_policy.IneligibilityReason`):
`EVIDENCE_TYPE_UNSCORED`, `SUBJECT_NOT_RESOLVED`, `OBJECT_NOT_RESOLVED`,
`ORGANISM_NOT_RESOLVED`, `COMPARTMENT_NOT_RESOLVED`, `DUPLICATE_EVIDENCE`
— the finer-grained *why* (e.g. `SUBJECT_AMBIGUOUS`) remains available on
each breakdown entry's own inherited `reason_codes`.

---

## 20. Deterministic explanation

A plain, deterministic string, never LLM-generated — see §15a's worked
example for the full format (adjusted scores, weighted contributions,
aggregate evidence, replication bonus, conflict penalty, final score,
confidence class, algorithm version).

---

## 21. Explicit limitations

- No taxonomic-distance organism modifiers (no data exists anywhere in
  this repository).
- No free-text experimental-system classification beyond the one
  deterministic `EvidenceType.COMPUTATIONAL` mapping.
- No citation-graph-based derivative detection — only the `Directness`-
  based heuristic (§11).
- No conflict *discovery* — `ConflictSeverity` is always an external input.
- No Claim/Evidence persistence.
- No automatic claim merging across "similar-sounding" claims (§4's strict
  compatibility gate).

---

## 22. Relationship to future Claim persistence

A future persistence increment is expected to: (a) group already-persisted
`Evidence` rows (and their own stored `SingleEvidenceAssessment`-equivalent
data) by canonical `Claim`, (b) build `EvidenceContribution`s from them
(supplying real `publication_identifier`/conflict information from a
conflict-detection layer once one exists), (c) call
`aggregate_claim_confidence`, and (d) write the resulting `score`/
`confidence_class` onto the `Claim` row. None of that persistence exists
yet — this increment produces the pure, in-memory
`AggregateClaimConfidence` only.

---

## 23. Open policy questions

1. ~~How should genuinely differing eligible base values combine?~~
   **Resolved by Increment 18A** — see §15a's diminishing-return weighting
   algorithm.
2. Should `docs/03`'s organism/experimental-relevance multipliers apply to
   *confidence* at all, given the same document's own "Measurement
   Confidence vs Model Applicability" section states organism/strain/
   temperature/pH/substrate/"physiological environment" are *Model
   Applicability* factors that "must never be conflated" with confidence?
   This increment implements the "Confidence Scoring Behavior" formula
   literally, per its own explicit instructions, but this tension is
   reported here for a human decision, not resolved unilaterally. **Still
   open.**
3. Should a genuinely known same-paper distinct experiment (via
   `experiment_identifier`) really earn a full replication-bonus step, or
   only partial credit? This increment allows it at full weight (§9),
   conservatively gated behind requiring genuine, explicit knowledge.
   **Still open.**
4. Is the diminishing-return weight base (`WEIGHT_BASE = 0.5`, halving per
   rank) the right decay rate, or should it be a tunable/authoritative
   parameter? No specification gives an exact decay rate; `0.5` was chosen
   as the smallest, cleanest value satisfying every stated design
   principle (§15a) — this is disclosed as this increment's own choice,
   not an authoritative number. **Open for review.**

---

## 24. Final architectural rule

> Single evidence describes one observation.
>
> Aggregate confidence describes the strength of the evidence set
> supporting one canonical Claim.
>
> Replication increases confidence only when evidence is meaningfully
> independent.
>
> Conflict reduces confidence only when conflict is explicitly
> established.
>
> Unknown policy or unresolved identity must remain unknown rather than
> being converted into arbitrary numbers.
