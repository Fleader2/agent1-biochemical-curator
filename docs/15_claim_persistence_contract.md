# Agent 1: Claim/Evidence Persistence Contract (Increment 19)

## 1. Purpose

Persist the fully-completed upstream pipeline for one logical claim into
the database:

```text
EvidenceExtraction -> CandidateClaim -> SingleEvidenceAssessment
    -> EvidenceContribution (+ AggregateClaimConfidence)
    -> Claim / Evidence / EvidenceCondition rows
```

`app.persistence.claim.persist_claim_with_evidence` is the only entry
point. It writes exactly what upstream objects already determined -- it
never reinterprets evidence, renormalizes an entity, recalculates a
confidence score, modifies a predicate, canonicalizes a value, or infers an
organism, a compartment, an experiment, or a publication that upstream did
not already resolve.

## 2. Public API

```python
def persist_claim_with_evidence(
    contributions: Sequence[EvidenceContribution],
    confidence: AggregateClaimConfidence,
    *,
    session: Session,
    primary_index: int = 0,
    experimental_conditions: Mapping[int, Sequence[ExperimentalConditionInput]] | None = None,
    provenance: ExternalRecordProvenance | None = None,
    created_by: str | None = None,
) -> ClaimPersistenceResult
```

`contributions`/`confidence` must be exactly the pair
`app.confidence.aggregation.aggregate_claim_confidence` was given and
returned -- this module never re-runs aggregation, and structurally
cross-checks the two arguments agree (§4) before writing anything.

**Why `Sequence[EvidenceContribution]`, not a bare `CandidateClaim` +
`Sequence[EvidenceExtraction]`.** `EvidenceContribution` is already the type
this pipeline uses to bundle one `CandidateClaim` with its own
`SingleEvidenceAssessment` for multi-evidence purposes, and it is literally
what `aggregate_claim_confidence` consumes. Reusing it here means the exact
inputs that were aggregated are the exact inputs persisted -- no second,
parallel evidence-list shape to keep in sync.

## 3. No Claim identity / deduplication mechanism

Verified directly against `app/models/claim.py`: `claim` has no unique
constraint or index on any combination of `subject_type`/`subject_id`/
`predicate`/`object_type`/`object_id`/`organism_id`. Unlike every other
`app.persistence.*` module, there is no `NormalizationResult`-driven
MATCHED/NEW/AMBIGUOUS/CONFLICTED/UNRESOLVED decision to apply here.

**This is not solved by this increment.** `persist_claim_with_evidence`
always either creates exactly one new `Claim` row, or fails outright
(`ClaimPersistenceResult.action` is only ever `CREATED` or `FAILED`).
Calling it twice for "the same" logical claim creates two `Claim` rows.
Inventing a fuzzy-matching heuristic here would be exactly the kind of
unsupported mapping this pipeline's scientific-integrity rules forbid.
**Open architecture gap**, carried forward from the Increment 13 Claim
Generation contract, not resolved here.

## 4. Input agreement checks (fail early)

Some agreements are already guaranteed by upstream types' own
`__post_init__` and are not re-checked here: `CandidateClaim.source`/
`predicate`/`supporting_span` always agree with its own
`evidence_extraction`. What this module *does* check, because nothing
upstream cross-validates it:

* `len(contributions) == confidence.evidence_count == len(confidence.contribution_breakdown)`.
* For every index `i`: `contributions[i].source_identifier == contributions[i].candidate_claim.source_identifier`.
* For every index `i`: `confidence.contribution_breakdown[i]`'s `source_identifier`/`publication_identifier`/`evidence_type` match `contributions[i]`'s own.
* `0 <= primary_index < len(contributions)`.

A violation raises `app.persistence.errors.ContributionConfidenceMismatchError`
-- always a caller bug (the wrong confidence result routed to the wrong
evidence set), never an ordinary data condition.

## 5. The primary contribution

Exactly one contribution (`primary_index`, default `0`) supplies every
`Claim`-row scalar field: `subject`, `predicate`, `object`, `value_text`/
`value_numeric`/`unit`, `organism`, `strain`, `claim_category`. This module
never merges these fields across several `CandidateClaim`s -- every other
contribution supplies only its own `Evidence` row. A caller with a single
piece of evidence (the common case today, since nothing in this repository
yet groups multiple `CandidateClaim`s under one logical claim) passes a
single-element sequence and never has to think about this.

## 6. Claim field mapping

| `Claim` column | Source | Notes |
|---|---|---|
| `subject_type` | `primary.subject.entity_kind.value` | Always populated |
| `subject_id` | `primary.subject.normalized_id` | `NULL` when unresolved -- never blocked |
| `predicate` | `primary.predicate` | Verbatim |
| `object_type`/`object_id` | `primary.object.entity_kind`/`normalized_id` | `NULL` when no object reference |
| `value_text`/`value_numeric`/`unit` | `primary.value_text`/`value_numeric`/`value_unit` | Verbatim, no conversion/rounding |
| `organism_id` | `primary.organism.normalized_id` | `NULL` when organism reference unresolved (free text preserved separately on `Evidence.organism`) |
| `strain` | `primary.strain` | Free text, verbatim |
| `claim_category` | `primary.claim_category` | Free text, no canonicalization |
| `status` | Always `ClaimStatus.UNKNOWN` | See §9 |
| `confidence_score`/`confidence_class` | `confidence.score`/`confidence.confidence_class` | Verbatim, never recomputed |
| `created_by` | Caller-supplied `created_by`, or `NULL` | Never inferred |

**Compartment is never persisted.** `CandidateClaim.compartment` has no
destination: `Claim` has no compartment column of any kind. Open since the
Increment 13 Claim Generation contract; unchanged here.

**Subject/object resolution is never performed here.** `normalized_id` is
read directly off the supplied `CandidateEntityReference` -- this module
never calls normalization, entity resolution, or uses `original_text` as a
fallback identity.

## 7. Evidence field mapping (one row per non-duplicate contribution)

| `Evidence` column | Source | Notes |
|---|---|---|
| `claim_id` | The newly created `Claim.id` | |
| `publication_id` | `extraction.publication_id` | Always `NULL` in the current pipeline (`EvidenceExtraction` never sets it) -- attached, never resolved, per Step 14 |
| `source_type`/`source_id` | `extraction.source`/`source_identifier` | Verbatim |
| `evidence_type` | `candidate_claim.evidence_type` | Verbatim |
| `organism`/`strain` | `extraction.organism_text`/`strain_text` | Free text; distinct from `Claim.organism_id` |
| `experimental_system` | `extraction.experimental_system` | Verbatim |
| `assay_type` | `extraction.assay` | Verbatim |
| `directness` | `candidate_claim.directness.value` | Verbatim |
| `quoted_support` | `candidate_claim.supporting_span.quoted_text` | Verbatim, never paraphrased |
| `curator_summary` | `extraction.normalized_text` | **See §8 -- required, no fallback** |
| `figure`/`table_reference` | `extraction.figure_reference`/`table_reference` | Verbatim |
| `page` | Always `NULL` | `EvidenceExtraction` carries no `page` field at all -- disclosed gap, not inferred |
| `database_name`/`database_accession` | Always `NULL` | No deterministic upstream source distinct from `source_type`/`source_id` -- disclosed gap |
| `date_accessed` | Always `NULL` | No retrieval timestamp exists on `EvidenceExtraction` -- that is a connector-layer concern this module never fabricates |

## 8. `Evidence.curator_summary` (`NOT NULL`) -- resolved mapping, disclosed limitation

No field on `EvidenceExtraction` is literally named "curator summary", but
`docs/09_evidence_extraction_contract.md` (Sec. 5) states explicitly: *"A
paraphrase or curator summary belongs in `normalized_text`, a distinct
field."* This module maps `EvidenceExtraction.normalized_text` directly onto
`Evidence.curator_summary` -- already-sourced text, never newly fabricated.

`normalized_text` is optional on `EvidenceExtraction`; `curator_summary` is
`NOT NULL` on `Evidence`. When a non-duplicate contribution's extraction has
no `normalized_text`, **the entire call fails** (`FAILED`, nothing written)
rather than inventing placeholder text for one row or silently dropping
that piece of evidence. **This is a disclosed architecture gap**: the
Evidence Extraction contract (Increment 12) does not guarantee
`normalized_text` is populated even though the database schema requires a
non-null curator summary for every evidence row. Closing it properly would
mean either making `normalized_text` required upstream or making
`curator_summary` nullable -- neither decision belongs to this increment.

## 9. `supplementary_reference` has no destination column

Verified directly against `app/models/claim.py`: `Evidence` has `page`/
`figure`/`table_reference` but no `supplementary_reference` column, even
though `EvidenceExtraction.supplementary_reference` exists and is
extracted. This data is currently unreachable by this schema -- not
silently dropped by this module's own choice, but a genuine schema gap
disclosed here rather than worked around by inventing a column or
overloading an existing free-text field.

## 10. `ExperimentalCondition`/`EvidenceCondition` -- no automatic creation

No field on `EvidenceExtraction` maps deterministically onto
`ExperimentalCondition`'s structured columns (`medium`, `carbon_source`,
`carbon_concentration`, `nitrogen_source`, `oxygen_status`, `temperature_c`,
`ph`, `growth_phase`, `growth_rate`, `culture_mode`) -- only free-text
`experimental_system`/`assay`/`perturbation` exist upstream. Turning free
text into structured fields would be inference this pipeline's fundamental
rule forbids.

`persist_claim_with_evidence` therefore **never creates an
`ExperimentalCondition` row on its own initiative.** A caller may optionally
supply already-fully-specified `ExperimentalConditionInput` values per
contribution index via `experimental_conditions`; these are attached
exactly as given. **In the current pipeline, no upstream step produces such
structured data at all**, so this parameter is expected to be omitted in
practice today -- this is a major, disclosed architecture gap, not a
silent omission.

**Deduplication.** `ExperimentalCondition` has no unique constraint of any
kind (the same absence-of-identity finding as `Claim` itself, §3). This
module deduplicates only byte-identical `ExperimentalConditionInput` values
*within one call* (never a database-wide search, never a fuzzy match) --
two condition inputs with the same field values supplied for different
`Evidence` rows in the same call reuse one `ExperimentalCondition` row.
`EvidenceCondition`'s own `UniqueConstraint(evidence_id,
experimental_condition_id)` remains the authoritative backstop against a
literal duplicate association.

## 11. Duplicate evidence

`confidence.contribution_breakdown[i].is_duplicate` -- already computed by
aggregation's own dedup logic (source, source identifier, span offsets,
quoted text) -- is reused directly to decide which contributions get an
`Evidence` row. This module never re-derives duplicate status itself. A
contribution flagged duplicate contributes nothing (no `Evidence` row, no
`ExperimentalCondition`/`EvidenceCondition` rows for it) and its index is
reported in `ClaimPersistenceResult.skipped_duplicate_indices`.

If every supplied contribution is a duplicate, the call fails conservatively
(`FAILED`, nothing written) rather than creating a `Claim` with zero
`Evidence` rows, which would violate `docs/02_database_schema.md`'s "every
supported scientific claim must have at least one evidence record."

## 12. Initial `ClaimStatus`; no `ReviewEvent`

`Claim.status` is always `ClaimStatus.UNKNOWN` -- the schema's own default,
and the only defensible choice available to this module: `SUPPORTED`/
`CONFLICTED` require duplicate/contradiction detection this module does not
perform (out of scope, per the increment's own instructions), and
`HUMAN_ACCEPTED` is never set by automated code anywhere in this pipeline
(`app/models/review_event.py`'s own docstring: that enforcement belongs to
a later API/auth layer). **No `ReviewEvent` row is ever created here.**

## 13. Confidence

`Claim.confidence_score`/`confidence_class` are written verbatim from
`confidence.score`/`confidence.confidence_class`. This module never
recomputes, rescales, or adjusts them -- it does not import
`app.confidence.aggregate_policy` or reference `ALGORITHM_VERSION` at all.
No column exists on `Claim` to record which `algorithm_version` produced a
given score (`AggregateClaimConfidence.algorithm_version` is not persisted
anywhere). **Disclosed architecture gap**: a future confidence-formula
revision would leave existing persisted `Claim` rows with no on-record
indication of which formula scored them.

## 14. Provenance

* `SourceCrossReference` -- one row is attached for the `Claim`
  (`entity_type="claim"`) and one for every created `Evidence` row
  (`entity_type="evidence"`), via the existing, idempotent
  `app.persistence.provenance.attach_source_cross_reference` -- reused
  verbatim, never reimplemented.
* `ExternalRecord` -- created only when a caller supplies an
  `ExternalRecordProvenance` explicitly, via the existing
  `app.persistence.provenance.record_external_record` -- never fabricated.

## 15. Transaction handling

The `Claim` row, every `Evidence` row, every `ExperimentalCondition`/
`EvidenceCondition` row, and every `SourceCrossReference`/`ExternalRecord`
this call creates are written inside one `SAVEPOINT`
(`session.begin_nested()`), mirroring `app.persistence.reaction`. An
`IntegrityError` raised anywhere inside is caught and converted to a
conservative `FAILED` result; nothing else is caught, so an unrelated bug
surfaces rather than being silently absorbed. This module never calls
`session.commit()`/`session.rollback()` -- transaction ownership belongs to
the caller.

## 16. Explicit limitations (not solved by this increment)

* No Claim-identity/deduplication mechanism (§3).
* `curator_summary` has no fallback when `normalized_text` is absent (§8).
* `supplementary_reference` is unreachable by the current schema (§9).
* No automatic `ExperimentalCondition` creation from `EvidenceExtraction`
  free text (§10).
* `ExperimentalCondition` deduplication is in-call-only, never cross-call
  (§10).
* Evidence-level duplicate detection is scoped to one call only -- the same
  extraction persisted via two separate calls produces two `Evidence` rows.
* `algorithm_version` is not persisted anywhere on `Claim` (§13).
* Compartment context is not persisted anywhere on `Claim` (§6).
* Conflict discovery, contradiction resolution, claim merging, and the
  human review workflow are all explicitly out of scope for this increment.

## 17. Final architectural rule

Persistence writes exactly what upstream produced. It never reinterprets
evidence, never recalculates confidence, and it never invents an
identifier, a status, or a piece of missing data to make an incomplete
record look complete.
