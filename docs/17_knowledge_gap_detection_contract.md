# Agent 1 Knowledge Gap Detection Contract

## 1. Purpose

Identify missing, weak, conflicting, or disconnected knowledge that can be
**demonstrated from the current curated database**, for later human review
or research planning. This is deterministic detection, not hypothesis
generation.

## 2. Pipeline position

```text
Claim / Evidence persistence (Increment 19)
    -> Review workflow (Increment 20)
    -> Knowledge Gap Detection (this increment)
```

Runs over already-persisted `Claim`/`Evidence`/`Reaction`/`Protein`/
`Gene`/`Compound` rows and the `ReviewEvent` history Increment 20 already
produces. It does not persist `KnowledgeGap` rows, generate suggested
experiments, or use an LLM.

## 3. Read-only architecture

`app.knowledge_gaps.analysis.analyze_knowledge_gaps` issues only `SELECT`
statements. It never calls `session.add`/`session.delete`/`session.commit`/
`session.rollback`, never recomputes confidence, never renormalizes an
entity, and never mutates a loaded row's attributes. Verified both by
direct test assertion (`tests/knowledge_gaps/test_analysis.py`'s safety
tests, which read this module's own source) and by re-fetching rows after
analysis and asserting they are byte-for-byte unchanged.

## 4. `KnowledgeGapCandidate` contract

```python
KnowledgeGapCandidate(
    gap_type: GapType,
    severity: GapSeverity,
    entity_type: str,
    entity_id: UUID | None,
    explanation: str,
    subject_text: str | None = None,
    predicate: str | None = None,
    object_text: str | None = None,
    supporting_claim_ids: tuple[UUID, ...] = (),
    supporting_evidence_ids: tuple[UUID, ...] = (),
    supporting_entity_ids: tuple[UUID, ...] = (),
    reason_codes: tuple[str, ...] = (),
)
```

`entity_type`/`entity_id` mirror `KnowledgeGap.subject_type`/`subject_id`'s
polymorphic shape, without an FK. `subject_text`/`object_text` are always
`None` in this increment: `Claim` carries no resolved entity *name* after
persistence, only `subject_type`/`subject_id`, and this package never
fabricates display text from a bare UUID. `predicate` is populated
directly from `Claim.predicate` for claim-anchored gaps. `explanation`
states only what is missing (§17). Raises
`app.knowledge_gaps.errors.InvalidGapCandidateError` on any validation
failure — this package's own explicit exception, not the generic
`TypeError`/`ValueError` most other data-contract modules use.

`KnowledgeGapAnalysisResult` carries `gaps` (deterministically ordered,
§16), `analyzed_claim_count`/`analyzed_evidence_count` (the eligible-claim
set actually examined, §7), `analyzed_entity_count` (total `Reaction` +
`Protein` + `Gene` + `Compound` rows examined — entity-connectivity rules
are not review-scoped, §7), and `summary_statistics` (an immutable
`MappingProxyType` with every implemented `GapType` present, `0` when
absent).

## 5. Gap taxonomy

**Implemented** (11 `GapType` members): `CONFLICTING_CLAIMS`,
`LOW_CONFIDENCE_CLAIM`, `SINGLE_SOURCE_SUPPORT`,
`NO_PRIMARY_EXPERIMENTAL_EVIDENCE`, `MISSING_PUBLICATION`,
`MISSING_EXPERIMENTAL_CONTEXT`, `REACTION_WITHOUT_ENZYME`,
`PROTEIN_WITHOUT_REACTION`, `GENE_WITHOUT_PROTEIN`,
`REACTION_WITHOUT_PARTICIPANTS`, `ISOLATED_COMPOUND`.

**Deliberately not implemented:**

* **`UNRESOLVED_ENTITY`** — `Claim.subject_id`/`object_id` are nullable
  UUIDs with no companion column recording *why* they are null.
  `CandidateEntityReference`'s rich `NormalizationResult`/
  `MentionResolutionResult` state (`UNRESOLVED` vs. `AMBIGUOUS` vs.
  `CONFLICTED` vs. "no `Lookup` configured for this kind" vs. "this claim
  genuinely has no object") is never persisted onto `Claim` (Increment 19's
  own finding). A null `subject_id`/`object_id` is therefore ambiguous
  after persistence, and reconstructing "this was specifically unresolved"
  from it would be inventing identity uncertainty the database no longer
  demonstrates. Reported as unavailable, not implemented.
* **A generic `ISOLATED_ENTITY`** — every entity type with a clear,
  useful, non-redundant isolation definition already has its own specific
  `GapType` (`ISOLATED_COMPOUND`, `PROTEIN_WITHOUT_REACTION`,
  `GENE_WITHOUT_PROTEIN`). No other entity type in this increment's
  inspected schema has an equally clear connectivity definition; adding a
  catch-all would either duplicate an existing rule or invent one.
* **Regulatory-effect conflicts** (`RegulatoryInteraction`) — outside this
  increment's inspected-schema scope (Step 2 lists `Claim`/`Evidence`/
  `Publication`/`ExperimentalCondition`/`EvidenceCondition`/`ReviewEvent`/
  `Reaction`/`ReactionParticipant`/`ReactionEnzyme`/`Protein`/`Gene`/
  `Compound`/`Compartment` — not `RegulatoryInteraction`). Not implemented
  here; a candidate extension for a future increment.

## 6. Severity policy

**No authoritative severity specification exists anywhere in this
repository** (contrast `ConfidenceClass`'s exact 0-100 thresholds in
`docs/03_agent_behavior.md`). `app.knowledge_gaps.rules.GAP_SEVERITY` is
this package's own conservative, disclosed policy:

| `GapType` | Severity | Rationale |
|---|---|---|
| `REACTION_WITHOUT_PARTICIPANTS` | HIGH | Structural impossibility for downstream modeling |
| `CONFLICTING_CLAIMS` | HIGH | An explicit, persisted scientific conflict |
| `LOW_CONFIDENCE_CLAIM` | MODERATE | Accepted knowledge with weak/absent numeric support |
| `SINGLE_SOURCE_SUPPORT` | MODERATE | Accepted knowledge lacking independent replication |
| `NO_PRIMARY_EXPERIMENTAL_EVIDENCE` | MODERATE | Accepted knowledge resting only on secondary evidence |
| `MISSING_PUBLICATION` | LOW | Informational provenance omission |
| `MISSING_EXPERIMENTAL_CONTEXT` | LOW | Informational provenance omission |
| `REACTION_WITHOUT_ENZYME` | LOW | Connectivity gap of uncertain scientific meaning (§13) |
| `PROTEIN_WITHOUT_REACTION` | LOW | Connectivity gap, not a demonstrated deficiency (§14) |
| `GENE_WITHOUT_PROTEIN` | LOW | Connectivity gap; `Protein.gene_id` is optional by design (§15) |
| `ISOLATED_COMPOUND` | LOW | Connectivity gap; generic compounds excluded (§17) |

`GapSeverity.CRITICAL` remains a member of the closed vocabulary (mirroring
`ConfidenceClass`'s own reserved members) but **no rule in this increment
ever produces it** — nothing in this rule set's conservative philosophy
warrants it.

## 7. Review eligibility

`Claim` has no `curation_state` column (Increment 20's own central
finding). Current curation state is derived from `ReviewEvent` history —
the latest `new_state` for `entity_type="claim"`, or `CurationState.PROPOSED`
implicitly when no `ReviewEvent` exists — computed via one batched query
(`_batch_current_curation_states`) rather than one query per claim (Step
26: avoid N+1), using the identical ordering convention (and identical
same-transaction-tie limitation) as
`app.review.workflow._current_curation_state`.

**Eligible claims:** `HUMAN_ACCEPTED`, plus `MACHINE_REVIEWED` when
`include_machine_reviewed=True`. `NEEDS_REVIEW` is **never** eligible, even
with the flag — it already signals "awaiting human attention" through the
review workflow itself, so this module does not duplicate that as a
knowledge gap. `REJECTED`/`PROPOSED` claims are never treated as accepted
scientific knowledge.

**Entity-connectivity rules are not review-scoped.** `Reaction`/`Protein`/
`Gene`/`Compound` have no analogous curation-eligibility concept in this
increment's scope; every persisted row of each is examined regardless of
its own `curation_state` (where one exists, e.g. `Reaction.curation_state`)
or any claim's review state.

## 8. Claim conflict rules

Two independent, purely mechanical signals — no semantic reasoning, no
LLM:

1. **Explicit**: `Claim.status == ClaimStatus.CONFLICTED` — an
   already-persisted determination from elsewhere, reported as-is.
2. **Derived**: two or more eligible claims sharing an identical resolved
   identity key — `(subject_id, predicate, object_type, object_id,
   organism_id, strain)`, requiring `subject_id`/`organism_id` both
   non-`None` — whose literal `value_text`/`value_numeric` disagree
   (exact inequality only). Two unresolved subjects (`subject_id is None`)
   are never treated as "the same" entity. A difference in `strain`
   naturally places two claims in different groups rather than triggering
   a conflict — the same "consider context" principle
   `docs/03_agent_behavior.md`'s Contradiction Detection section states,
   applied only to already-stored fields, never inferred. **Known
   limitation**: `Claim` has no compartment column (Increment 19), so two
   claims that actually differ by compartment context are indistinguishable
   here.

## 9. Confidence-related gaps

`LOW_CONFIDENCE_CLAIM` fires when an eligible claim's persisted
`confidence_class` is `LOW` or `UNKNOWN` — never recomputed, read exactly
as `app.persistence.claim` wrote it. Both are treated identically: absence
of eligible evidence (`UNKNOWN`) is not evidence of falsity, but neither
demonstrates a strong evidentiary basis for already-accepted knowledge,
which is exactly what this gap reports.

## 10. Evidence-support gaps

* **`SINGLE_SOURCE_SUPPORT`**: an eligible claim's evidence all shares one
  "source identity" — `Evidence.publication_id` when resolved, else
  `(source_type, source_id)` (the only provenance currently populated;
  `publication_id` is always `None` in the present pipeline per
  `docs/15_claim_persistence_contract.md`). Multiple `Evidence` rows from
  the same source count as one. Independence is never inferred beyond what
  is actually stored.
* **`NO_PRIMARY_EXPERIMENTAL_EVIDENCE`**: an eligible claim's evidence is
  supported only by `EvidenceType` values in `{REVIEW, CURATED_DATABASE,
  COMPUTATIONAL, HOMOLOGY, AUTHOR_HYPOTHESIS}` (transcribed from Increment
  21's own instructions and `.cursor/rules/01-scientific-integrity.mdc`'s
  "Evidence Categories"), or from a nominally-primary type whose
  `Directness` is `REVIEW_SUMMARIZES`/`DATABASE_ANNOTATES` (reusing the
  identical derivative-directness set `app.confidence.aggregation` already
  established, not re-derived). `EvidenceType.OTHER` is ambiguous: any
  claim with `OTHER`-typed evidence is skipped entirely for this rule
  (preserve uncertainty, per Step 14 — never labeled primary or
  non-primary).

## 11. Provenance gaps

`MISSING_PUBLICATION` fires for an eligible claim's `Evidence` rows whose
`source_type` is `PUBMED`/`PMC` (literature citations) and
`publication_id is None`. Every other `SourceType` (KEGG, BRENDA, BioCyc,
MetaCyc, SGD, UniProt, ChEBI, Rhea, NCBI) is a curated-database record, not
a literature citation — the schema does not require a `Publication` row
for these (`Evidence.database_name`/`database_accession` serve that role
instead). `SourceType.OTHER` is ambiguous and never triggers this rule.
**Expected under the current pipeline**: since `publication_id` resolution
is not yet wired into evidence persistence at all (Increment 19), this
rule will flag most PUBMED/PMC evidence today — an accurate reflection of
current curation completeness, not a defect in the rule.

## 12. Experimental-context gaps

`MISSING_EXPERIMENTAL_CONTEXT` fires for an eligible claim's `Evidence`
rows whose `evidence_type` is in `{DIRECT_BIOCHEMICAL, DIRECT_IN_VIVO,
GENETIC, LOCALIZATION, PROTEOMICS, METABOLOMICS, FLUXOMICS,
TRANSCRIPTOMICS, STRUCTURAL}` (genuine experimental-measurement
categories) and carry zero `EvidenceCondition` rows. `CURATED_DATABASE`/
`COMPUTATIONAL`/`REVIEW`/`HOMOLOGY`/`AUTHOR_HYPOTHESIS` never trigger this
rule (not experiments); `OTHER` is ambiguous and never triggers it either.

## 13. Reaction completeness gaps

* **`REACTION_WITHOUT_PARTICIPANTS`** (HIGH): any `Reaction` with zero
  `ReactionParticipant` rows. Fully deterministic, no inference — this is
  the one rule this increment's own instructions designate a structural
  completeness gap for downstream modeling.
* **`REACTION_WITHOUT_ENZYME`** (LOW): any `Reaction` with zero
  `ReactionEnzyme` rows. `Reaction.reaction_type` is free text with **no
  controlled vocabulary** (verified directly: `app.normalization.reaction`
  states it is "NOT identity, ever"), so there is no reliable way to
  exclude reactions that are genuinely nonenzymatic or transport
  processes. This rule therefore flags conservatively — every such
  reaction, not only those inferred to need a catalyst — at LOW severity
  to reflect that uncertainty, rather than fabricating a distinction the
  schema cannot support.

## 14. Entity connectivity gaps

* **`PROTEIN_WITHOUT_REACTION`** (LOW): any `Protein` with zero
  `ReactionEnzyme` rows. The schema cannot distinguish a catalytic protein
  from a structural/regulatory one, so this is reported as a connectivity
  observation, never a claim that the protein is scientifically deficient.
* **`GENE_WITHOUT_PROTEIN`** (LOW): any `Gene` with zero `Protein` rows.
  `Protein.gene_id` is optional by design (not every gene corresponds to a
  curated protein product) — this never asserts every gene should have
  one.
* **`ISOLATED_COMPOUND`** (LOW): any non-`is_generic` `Compound` with zero
  `ReactionParticipant` rows. `is_generic` compounds (broad placeholder/
  reference species) are excluded — their intended role does not require
  direct participation in a specific curated reaction.

## 15. Gap deduplication

Identity key: `(gap_type, entity_type, entity_id, supporting_claim_ids)` —
never the explanation text. `app.knowledge_gaps.analysis._dedupe` applies
first-occurrence-wins deduplication as an explicit final pass over every
rule's combined output.

## 16. Deterministic ordering

`gaps` is sorted by: severity (most severe first: CRITICAL, HIGH,
MODERATE, LOW, INFO), then `gap_type.value`, then `entity_type`, then
`entity_id` (a `None` id sorts after every real id at the same
severity/type/entity_type). The same database state always produces an
identical `gaps` tuple, verified directly by a repeated-analysis test.

## 17. Explicit non-responsibilities

* No suggested experiments. `explanation` states only what is missing
  ("Reaction X has no associated ReactionParticipant rows"), never a
  proposed remedy ("perform LC-MS flux analysis...").
* No speculative biological hypotheses.
* No LLM call, no connector call, no network access.
* No confidence recomputation, no normalization, no entity resolution.
* No `KnowledgeGap` row is ever created — this increment produces an
  immutable in-memory result only.
* No mutation of any `Claim`/`Evidence`/entity/`ReviewEvent`/confidence
  field.

## 18. Testing

`tests/knowledge_gaps/`: `test_types.py` (dataclass self-validation),
`test_rules.py` (every rule's positive/negative cases, DB-free/in-memory
ORM objects), `test_analysis.py` (review eligibility, determinism,
end-to-end structural gaps, deduplication, safety — including source-level
checks that no INSERT/UPDATE/DELETE/commit/rollback/LLM/connector code
exists in `app.knowledge_gaps.analysis`). 92 tests, all passing; full
repository suite (1845 tests) and the `SAWarning`-strict pass both remain
green.

## 19. Known limitations

* Unresolved-entity identity is unavailable after persistence (§5).
* `Claim` has no compartment column, limiting conflict-detection precision
  (§8).
* `ReviewEvent` has no monotonic ordering column; a same-transaction
  timestamp tie is inherited from Increment 20 and disclosed there.
* `MISSING_PUBLICATION` will fire broadly today because publication
  resolution is not yet wired into evidence persistence (§11) — expected,
  not a defect.
* `REACTION_WITHOUT_ENZYME`/`PROTEIN_WITHOUT_REACTION` cannot distinguish
  genuinely nonenzymatic/noncatalytic entities from under-curated ones,
  because `reaction_type` has no controlled vocabulary and no functional-
  class column exists on `Protein`.

## 20. Relationship to `KnowledgeGap` persistence

**Superseded by Increment 22.** `KnowledgeGap` has since been hardened
(migration `0010_knowledge_gap_hardening`) with `gap_type`/`severity`/
`reason_codes_json`/`supporting_claim_ids_json`/
`supporting_evidence_ids_json`/`supporting_entity_ids_json`/`identity_key`,
and a faithful, idempotent persistence API
(`app.persistence.knowledge_gap.persist_knowledge_gap`) now exists. See
`docs/18_knowledge_gap_persistence_contract.md` for the complete, current
mapping, identity/deduplication policy, and status vocabulary — this
section's original "no destination columns today" analysis is no longer
current and is kept only as historical context for why the hardening was
needed.

**See also**: `docs/19_experiment_recommendation_contract.md` (Increment
23) converts a detected gap into a structured, deterministic experiment
recommendation — a downstream consumer of this contract's output, never a
change to detection semantics.

## 21. Final architectural rule

> Knowledge-gap detection reports what the curated knowledge base
> demonstrably lacks.
>
> It does not invent explanations for those gaps.
>
> It does not propose biology that is absent from the evidence.
