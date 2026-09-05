# Agent 1 Single-Evidence Assessment Contract

**Document:** `docs/13_single_evidence_confidence_contract.md`

**Status:** Authoritative specification for `app/confidence/` (Increment 17).

**Increment 17 does not compute final Claim confidence.** This document
describes a deterministic, auditable *assessment* of one `CandidateClaim`
+ one `EvidenceExtraction`, produced for a future multi-evidence
aggregator (Increment 18) to consume.

---

## 1. Purpose

`app.confidence.assess_single_evidence_claim` answers exactly one
question, deterministically:

> Given this one source-grounded observation, what does it say about
> evidence strength and entity-identity resolution for this one candidate
> claim?

It does **not** answer:

> What is this claim's final 0-100 confidence score, or which
> `ConfidenceClass` does it belong to?

That is an aggregate-claim question, answered later, from a *set* of
assessments like this one (§3).

---

## 2. Pipeline position

```text
single evidence
    |
    v
SingleEvidenceAssessment (this package, Increment 17)
    |
    v
future multi-evidence aggregation (Increment 18)
    |
    v
0-100 Claim confidence / ConfidenceClass
```

Increment 17 may use only information already present on one
`CandidateClaim`, its originating `EvidenceExtraction`, and its own entity-
resolution provenance (`CandidateEntityReference.normalization_result` /
`.mention_resolution_result`). It never inspects another `CandidateClaim`,
another `EvidenceExtraction`, a database row, another paper, a review-
dependency graph, or a citation network.
`assess_single_evidence_claim(claim: CandidateClaim)` takes exactly one
argument — enforced structurally
(`tests/confidence/test_scoring.py::test_assessment_function_signature_takes_only_one_claim`).

---

## 3. Why final Claim confidence is deferred

An earlier revision of this increment attempted to compute a final 0-100
score directly. Doing so exposed a genuine specification mismatch, not a
bug to patch over:

- `docs/03_agent_behavior.md`'s confidence formula
  (`base_score × organism_relevance × experimental_relevance +
  replication_bonus − conflict_penalty`) is an **aggregate**-claim
  calculation — it includes a replication bonus (`+5`/`+5`, capped `+10`)
  that only exists once more than one evidence item supports a claim.
- The maximum authoritative single-evidence base score (`DIRECT_BIOCHEMICAL`,
  45) sits **below** that same specification's own MODERATE floor (50).
  A single evidence item, however direct, cannot reach MODERATE — let
  alone HIGH or VERY_HIGH — without a replication bonus that single-
  evidence scope structurally cannot produce.
- `Directness`, organism-context relevance for a single claim, and
  entity-resolution quality all have **no authoritative numeric source
  anywhere in this repository**. Assigning them provisional numbers
  (as the earlier revision did) meant every claim's final score depended
  partly on invented policy, not on this repository's actual specification.

Rather than compute a final score built partly on invented numbers, this
increment records **categorical state only** — see §4 — leaving the
numeric aggregation policy (including the replication bonus and conflict
penalty `docs/03_agent_behavior.md` already specifies) to Increment 18,
which can consult a full evidence set when it runs.

---

## 4. `SingleEvidenceAssessment` schema

```python
SingleEvidenceAssessment(
    evidence_type: EvidenceType,
    evidence_base_score: int | None,
    scoring_status: ScoringStatus,
    directness: Directness,
    subject_resolution: EntityResolutionQuality,
    object_resolution: EntityResolutionQuality | None,
    organism_resolution: OrganismAssessment,
    compartment_resolution: EntityResolutionQuality | None,
    reason_codes: tuple[str, ...] = (),
    explanation: str = "",
)
```

No field named `score` or `confidence_class` exists anywhere on this type
— checked structurally by test, not just by convention. Frozen and
immutable; never stored in the database (no persistence of any kind
occurs in this package); self-validating in `__post_init__`.

---

## 5. Authoritative EvidenceType base-score table

Transcribed verbatim from `docs/03_agent_behavior.md`, unchanged from the
prior revision of this increment:

| `EvidenceType` | `evidence_base_score` | Table entry used |
|---|---|---|
| `DIRECT_BIOCHEMICAL` | 45 | "Direct biochemical evidence" |
| `DIRECT_IN_VIVO` | 40 | "Direct in-vivo evidence" |
| `GENETIC` | 25 | "Genetic evidence" |
| `CURATED_DATABASE` | 20 | "General curated biochemical DB" (conservative — see below) |
| `COMPUTATIONAL` | 10 | "Computational annotation" |
| `HOMOLOGY` | 5 | "Homology inference" |
| `AUTHOR_HYPOTHESIS` | 0 | "LLM inference" (conservative mapping — see below) |

**`CURATED_DATABASE`**: `docs/03`'s table lists two curated-database rows
("Organism-specific curated DB", 25; "General curated biochemical DB", 20)
but `EvidenceType` has only one `CURATED_DATABASE` value, and nothing on
any connector's normalized record lets this module determine organism-
specificity deterministically — the lower, conservative value is used
unconditionally.

**`AUTHOR_HYPOTHESIS`**: maps to the table's only "unvalidated
speculation" tier ("LLM inference", 0) — `AUTHOR_HYPOTHESIS` (used per
`docs/03`'s own Evidence Extraction Prompt when "the authors speculate")
occupies the same epistemic position, even though it is the study
authors' own speculation rather than an LLM's.

`evidence_base_score` is **not** a final Claim confidence value — it is
the authoritative starting point Increment 18 will combine with other
evidence, not a number this package treats as complete on its own.

---

## 6. Unscored EvidenceType handling

Eight of the fifteen current `EvidenceType` members have no entry in
`docs/03`'s table under any name:

```text
LOCALIZATION
PROTEOMICS
METABOLOMICS
FLUXOMICS
TRANSCRIPTOMICS
STRUCTURAL
REVIEW
OTHER
```

A `CandidateClaim` with one of these `evidence_type` values gets
`evidence_base_score=None`, `scoring_status=ScoringStatus
.UNSCORED_EVIDENCE_TYPE`, and reason code
`EVIDENCE_UNSCORED_<evidence_type>` — never a guessed number
(`.cursor/rules/00-agent1-core.mdc`: "Prefer UNKNOWN over an unsupported
conclusion"). `app.confidence.policy.EVIDENCE_TYPE_UNSCORED` records this
set explicitly; a test asserts
`EVIDENCE_TYPE_BASE_SCORES ∪ EVIDENCE_TYPE_UNSCORED == EvidenceType`, so a
future enum addition that is neither scored nor explicitly marked unscored
fails a test rather than silently falling through.

---

## 7. Directness as categorical metadata

`SingleEvidenceAssessment.directness` carries `CandidateClaim.directness`
through **unchanged** — no numeric multiplier is computed from it in this
package. No authoritative numeric source exists anywhere in this
repository for how `Directness` should affect confidence (an earlier
revision of this increment invented one; it has been removed entirely,
per this increment's own instructions). `AUTHORS_OBSERVED`,
`AUTHORS_INFERRED`, `AUTHORS_PROPOSED`, `AUTHORS_DISCUSSED`,
`REVIEW_SUMMARIZES`, and `DATABASE_ANNOTATES` are all preserved with equal
structural weight in this package — the ordering/weighting a future
aggregator applies to them is that aggregator's decision to make, informed
by whatever specification governs Increment 18.

---

## 8. Entity-resolution assessment

`subject_resolution`/`object_resolution`/`compartment_resolution` each
carry an `EntityResolutionQuality` — a purely categorical state, **no
numeric weight, cap, or penalty attached**:

| Value | Meaning |
|---|---|
| `RESOLVED` | A canonical entity was matched (direct-normalization `MATCHED` or Entity-Resolution `RESOLVED`) |
| `VERIFIED_NEW` | A single, verified, unconflicted external record with no existing canonical row (§10) |
| `AMBIGUOUS` | Multiple unconfirmed candidates, including a `NEW_CANDIDATE` outcome with more than one distinct verified record |
| `CONFLICTED` | An active identity conflict |
| `UNRESOLVED` | Resolution was attempted (or could not be attempted) and no verified candidate emerged |
| `NO_CANDIDATE` | An Entity-Resolution search found zero candidates (§12) |
| `SOURCE_FAILURE` | An external connector call itself failed (§11) |
| `UNSUPPORTED` | No resolution capability exists for this kind at all (no connector configured, or Entity Resolution reported `UNSUPPORTED_ENTITY_KIND`) |
| `UNKNOWN_KIND` | (subject only) the mention's `EntityKind` was never typed |
| `NOT_APPLICABLE` | (object only) a reference exists but was never typed as an entity |

**Roles assessed:**

- **Subject** — always (`subject_resolution` is never `None`;
  `CandidateClaim.subject` is never `None`).
- **Object** — `object_resolution is None` when `claim.object is None`
  (a literal-value claim's legitimately missing object — never penalized
  or even mentioned in `reason_codes`); `NOT_APPLICABLE` when a reference
  exists but was never typed as an entity (`entity_kind is
  EntityKind.UNKNOWN`); otherwise a full `EntityResolutionQuality`
  assessment.
- **Compartment** — `compartment_resolution is None` when
  `claim.compartment is None`; otherwise a full assessment (compartment is
  always typed `COMPARTMENT` when present, so `NOT_APPLICABLE` never
  occurs here).

No role's state is collapsed into any other's, and no minimum/maximum is
computed across roles — each is reported independently for Increment 18
to combine however its own specification requires.

---

## 9. Organism assessment

`organism_resolution` uses a separate, coarser vocabulary,
`OrganismAssessment`, rather than `EntityResolutionQuality`:

| Value | Meaning |
|---|---|
| `NOT_STATED` | `claim.organism is None` — no organism was ever reported. Never a penalty-worthy state. |
| `EXPLICIT_RESOLVED` | An organism was stated and matched to a canonical id |
| `EXPLICIT_UNRESOLVED` | An organism was stated but no canonical match was established |
| `CONFLICTED` | An organism was stated and its resolution was an active identity conflict |
| `SOURCE_FAILURE` | An organism was stated and its Entity-Resolution lookup itself failed |
| `UNKNOWN` | Defensive fallback; not expected given organism's own typing |

**What is deliberately not implemented.** `docs/03`'s own organism table
(same strain 1.00 / same species 0.95 / same genus 0.70 / other fungus
0.55 / other eukaryote 0.40 / bacterium 0.25) requires taxonomic-distance
data this repository has nowhere (no NCBI-taxonomy connector exists — see
`docs/12_entity_resolution_architecture.md` §11/§21); no genus/kingdom/
evolutionary-distance vocabulary exists anywhere in `app.confidence`,
checked structurally by test. "Organism mismatch" is also not
representable: `CandidateClaim.organism` is built directly from the same
`EvidenceExtraction.organism_text` the claim itself reports — there is no
independent "expected organism" within one claim to mismatch against.

---

## 10. `VERIFIED_NEW` semantics

A single verified `NEW`/`NEW_CANDIDATE` outcome means: strong external
identity was verified, but no canonical Agent 1 row currently exists yet.
This is **not** the same as `AMBIGUOUS` — an important distinction first
identified in Increment 14 and preserved here categorically, with no
numeric penalty attached: `app.confidence` never equates "not yet
persisted" with "uncertain identity". A `MentionResolutionStatus
.NEW_CANDIDATE` outcome carrying **more than one** distinct verified
candidate is reclassified as `AMBIGUOUS` instead — genuine uncertainty
about which record is canonical, distinguished directly from
`mention_resolution_result.candidates` since
`app.entity_resolution.ranking.classify_outcome` itself does not make
this distinction. How `VERIFIED_NEW` ultimately affects a final
confidence score, and whether/how it interacts with persistence, is left
entirely to Increment 18 and a future persistence increment respectively.

---

## 11. `SOURCE_FAILURE` semantics

`SOURCE_FAILURE` records an infrastructure/retrieval limitation — an
external connector call itself failed. It is never scientific counter-
evidence, is never converted to `NO_CANDIDATE`, and receives no numeric
penalty in this package (`evidence_base_score` is entirely unaffected by
any role's `SOURCE_FAILURE` state).

---

## 12. `NO_CANDIDATE` semantics

`NO_CANDIDATE` records that an Entity-Resolution search was performed and
returned zero candidates — unresolved canonical identity, never evidence
that the biological entity does not exist
(`.cursor/rules/01-scientific-integrity.mdc`: "Unknown Versus Negative
Evidence"). No numeric penalty is applied.

---

## 13. Negative claims

Predicate polarity (`"binds"` vs. `"did not bind"`) receives no special
handling of any kind — this module never inspects `claim.predicate`'s
text at all, only `evidence_type`/`directness`/entity references.

---

## 14. Measurements

The mere presence of `value_text`/`value_numeric` never alters
`evidence_base_score` or any other field — no precision/decimal-place
scoring exists. A measurement's assessment comes entirely from its
`EvidenceType` and `Directness`, exactly like any other claim.

---

## 15. Reason codes

`SingleEvidenceAssessment.reason_codes` is a deterministic, controlled
audit trail (never prose-only), built from small closed enums:

- `EVIDENCE_<EvidenceType>` (scored) or `EVIDENCE_UNSCORED_<EvidenceType>`
  (unscored).
- `DIRECTNESS_<Directness>`.
- `SUBJECT_<EntityResolutionQuality>` (always present).
- `OBJECT_<EntityResolutionQuality>` (only when `object_resolution` is not
  `None`).
- `ORGANISM_<OrganismAssessment>` (always present).
- `COMPARTMENT_<EntityResolutionQuality>` (only when `compartment_resolution`
  is not `None`).

No reason code implies a numeric penalty or cap — every code describes
state only (e.g. `SUBJECT_AMBIGUOUS` says "the subject's identity is
ambiguous", not "this claim lost 40 points").

---

## 16. Deterministic explanation

`explanation` is a deterministic, human-readable rendering of the same
information — never generated by an LLM, and never claiming to state a
final confidence:

```text
Evidence type DIRECT_BIOCHEMICAL has authoritative base score 45.
Directness is AUTHORS_OBSERVED.
Subject identity is RESOLVED.
Organism context is EXPLICIT_RESOLVED.
Final aggregate Claim confidence is not computed at the single-evidence stage.
```

---

## 17. Explicitly deferred numeric policies

None of the following are computed anywhere in this package:

- a final 0-100 Claim confidence score,
- a `ConfidenceClass` for a single evidence item,
- any numeric `Directness` multiplier,
- any numeric organism-relevance multiplier,
- any numeric entity-resolution cap or penalty,
- experimental-relevance modifiers (`docs/03`'s "physiological in vivo /
  cell lysate / purified native enzyme / ..." table) — no deterministic
  field on `EvidenceExtraction` classifies an assay into one of those
  buckets (`experimental_system`/`assay` are free text),
- organism taxonomic-distance modifiers — no taxonomic-distance data
  exists anywhere in this repository,
- independent replication bonus,
- same-paper multiple experiments,
- derivative-source detection (review/database citing a primary paper),
- conflict penalties across claims,
- contextual conflict resolution,
- score aggregation of any kind,
- source-count caps.

All of the above remain `docs/03_agent_behavior.md`'s authoritative
target specification for a future aggregate-confidence increment — this
document does not contradict or narrow that specification, only describes
what this increment actually computes today.

---

## 18. Relationship to Increment 18

A future multi-evidence aggregator is expected to consume one
`SingleEvidenceAssessment` per claim/evidence pair (potentially many per
`Claim`, once persistence exists) as its own input, then apply
`docs/03_agent_behavior.md`'s full formula — replication bonus, conflict
penalty, organism/experimental-relevance modifiers once those are
deterministically specified — across the resulting set to produce
`Claim.confidence_score`/`confidence_class`. This package's output is
designed to be that input, not to anticipate or duplicate that logic
itself.

---

## 19. Testing philosophy

Tests in `tests/confidence/` verify: every authoritative `EvidenceType`
base score exactly; every unscored `EvidenceType` reports `None`/
`UNSCORED_EVIDENCE_TYPE`; every `Directness` value is preserved exactly
with no numeric side effect; every `EntityResolutionQuality`/
`OrganismAssessment` state is reachable and correctly classified from both
the direct-normalization and Entity-Resolution provenance paths; object/
compartment absence vs. untyped-presence are distinguished; negative
claims and bare numeric measurements receive no special treatment;
repeated assessment of an identical claim is bit-for-bit identical; no
input object is mutated; and — structurally — that no field or exported
name anywhere in this package computes or exposes a final score or
`ConfidenceClass`, and that no database session, connector, HTTP client,
or LLM call is reachable from this package.

---

## 20. Final architectural rule

> Single-evidence assessment describes the strength and state of one
> evidence item.
>
> It does not decide final Claim confidence.
>
> Final Claim confidence is an aggregate property of the available
> evidence set.
