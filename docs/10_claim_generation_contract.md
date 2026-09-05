# Agent 1 Claim Generation Contract

**Document:** `docs/10_claim_generation_contract.md`

**Status:** Authoritative specification for `app/claim_generation/` (Increment 13).
This document is the canonical design contract for every later Confidence
Scoring and Claim/Evidence-persistence increment — those increments must
consume `CandidateClaim` exactly as specified here.

---

## 1. Purpose

Claim Generation transforms validated, source-grounded `EvidenceExtraction`
objects (Increment 12) into immutable `CandidateClaim` objects.

It does **not**:

- decide biological truth
- persist `Claim` rows
- persist `Evidence` rows
- calculate confidence
- merge duplicate claims
- resolve contradictions
- canonicalize predicates
- perform cross-paper synthesis

---

## 2. Pipeline position

```text
Scientific source
    ↓
Evidence Extraction
    ↓
EvidenceExtraction
    ↓
Claim Generation
    ↓
CandidateClaim
    ↓
Confidence Scoring
    ↓
Claim/Evidence Persistence
```

Normalization is invoked *inside* Claim Generation only to resolve entity
references (subject, object, organism, compartment) — normalization
*policy* itself remains owned entirely by `app.normalization`. Claim
Generation calls `app.normalization.*`'s public `normalize_*` functions
directly; it does not reimplement, override, or loosen any identity rule
those modules already established.

---

## 3. CandidateClaim contract

### Required

| Field | Purpose |
|---|---|
| `source` | Which external source system the originating passage came from. Must equal `evidence_extraction.source`. |
| `source_identifier` | The source-level document identifier. Must equal `evidence_extraction.source_identifier`. |
| `evidence_extraction` | The originating `EvidenceExtraction`, retained in full — no information loss. |
| `subject` | A `CandidateEntityReference` for the statement's subject. |
| `predicate` | The relationship or action asserted, literal (§8). |
| `supporting_span` | The `SourceSpan` grounding this claim. Must equal `evidence_extraction.span`. |
| `evidence_type` | Carried unchanged from `evidence_extraction.evidence_type` (§14). |
| `directness` | Carried unchanged from `evidence_extraction.directness` (§14). |

### Optional

| Field | Purpose |
|---|---|
| `object` | A `CandidateEntityReference` for the statement's object, when the predicate has one. |
| `value_text` | The reported literal value, exactly as printed. |
| `value_numeric` | A `Decimal`, only when `value_text` parses cleanly as a plain number (§10). |
| `value_unit` | The reported unit, exactly as printed. |
| `organism` | A `CandidateEntityReference` (kind `ORGANISM`) for the claim's organism context. |
| `compartment` | A `CandidateEntityReference` (kind `COMPARTMENT`) for the claim's compartment context. |
| `strain` | Free text, carried straight through — not a reference (there is no `Strain` entity anywhere in this schema). |
| `claim_category` | Free text (§13). |
| `qualifiers` | A tuple of qualifier strings (today, zero or one element — see `app.claim_generation.types`'s own docstring on this). |

### Fields that do not exist

`CandidateClaim` deliberately has no:

- `claim_id`
- `evidence_id`
- `confidence_score`
- `confidence_class`
- any persistence state field

None of these can exist meaningfully before a `Claim`/`Evidence` row has
been persisted, or before confidence scoring has run — both are later,
separate increments.

---

## 4. CandidateEntityReference

Fields:

- **`original_text`** — the exact mention text, never altered.
- **`entity_kind`** — an `EntityKind`: `ORGANISM`, `PUBLICATION`, `GENE`,
  `PROTEIN`, `COMPOUND`, `COMPARTMENT`, `REACTION`, `UNKNOWN`.
- **`normalization_result`** — the full `NormalizationResult` returned by
  the relevant `app.normalization.*` module, or `None` if normalization
  was never attempted at all (kind `UNKNOWN`, no lookup available, or an
  organism-scoped kind with no resolved organism).
- **`normalized_id`** — a `UUID`, populated **only** when
  `normalization_result.status is NormalizationStatus.MATCHED`.

`AMBIGUOUS`, `CONFLICTED`, `NEW`, and `UNRESOLVED` states remain explicit
on `normalization_result` rather than being forced to a `UUID` —
`normalized_id` is `None` in every one of those cases, and
`normalization_result.candidate_entity_ids` (when populated) is preserved
in full.

---

## 5. Subject/object typing

Entity type is **never** inferred from naming conventions. Typing comes
only from an explicit `EntityTypingHint`, supplied by the caller (a
coordinator, or a later LLM-assisted step following
`app.claim_generation.prompts.CLAIM_GENERATION_PROMPT`). When type is
uncertain, `EntityKind.UNKNOWN` must be used — the default value of both
`EntityTypingHint` fields.

Do not infer:

- gene from capitalization,
- protein from a naming suffix,
- compound from chemical-looking text,
- reaction from a verb,
- organism from publication context.

Any of these would be exactly the "infer biological type from naming
conventions" behavior this increment's instructions forbid.

---

## 6. Normalization integration

Existing entity normalizers are called directly —
`app.claim_generation.mapping.resolve_entity_reference` builds the
minimal `*Identity` object the target normalizer needs and calls its
public `normalize_*` function. **No Claim-Generation-specific duplicate
normalization logic exists** anywhere in this package.

Organism resolution occurs first, whenever `organism_text` is present and
an `OrganismLookup` is available. A successfully `MATCHED` organism id
(never a `NEW`/`AMBIGUOUS`/`UNRESOLVED` one — those carry no confirmed id)
may then be passed into the scoped normalizers that require it:

- Gene
- Protein
- Reaction
- Compartment (accepted, but nullable — a `None` organism id is itself a
  legitimate compartment scope, per `app.normalization.compartment`'s own
  policy)

Global normalizers (Compound, Publication, and Organism itself) remain
global according to their own existing policies — they are never passed
an organism id, and their own identity rules are untouched.

---

## 7. Conservative name-resolution consequence

This is an architectural property of the pipeline, not a defect:

Most biological text provides **names**, not strong database identifiers.
The existing Gene, Protein, Compound, Compartment, and Reaction
normalizers intentionally treat name-only input as *weak, candidate-
generation-only* signal (their own "Level 3" identity tier) and — by
design, verified directly against each module — generally do **not**
return `MATCHED` from a name alone, even when exactly one candidate
shares that name.

Therefore Claim Generation frequently preserves `AMBIGUOUS`, `NEW`, or
`UNRESOLVED` entity references whenever only a textual name was available
in the source passage — this is expected, not a bug to be fixed inside
this package.

**Claim Generation does not weaken normalization rules to improve match
rates.** No shortcut, fuzzy match, or "if there's only one candidate, just
take it" override was added here, and none should be added here in the
future — that discipline belongs entirely to `app.normalization.*`, and
changing it there is a decision for that layer's own maintainers, not an
implicit side effect of a Claim Generation change.

A future identifier-enrichment or retrieval layer may supply stronger
identifiers (an SGD id, a UniProt accession, a KEGG compound id) before
final Claim persistence, which would let normalization reach `MATCHED`
through its own existing strong-identifier path — see Open Question B.

---

## 8. Predicate policy

- The predicate is carried literally from `EvidenceExtraction.predicate_text`.
- No canonicalization.
- No ontology mapping.
- No verb normalization.
- Negative predicates remain negative.

`"binds"` and `"did not bind"` remain two distinct, literal predicate
strings — this package never rewrites the second into a negated form of
the first, or into any other normalized shape.

---

## 9. Value policy

`CandidateClaim` distinguishes an **object entity reference**
(`object: CandidateEntityReference | None`) from a **literal value**
(`value_text`/`value_numeric`/`value_unit`). A `CandidateClaim` may
legally contain either, both, or neither, because the current `Claim` ORM
itself permits both an object reference and scalar value fields on one
row simultaneously (`Claim.object_type`/`object_id` and
`Claim.value_text`/`value_numeric`/`unit` are independent, all-nullable
columns with no mutual-exclusivity constraint between them). Claim
Generation introduces no artificial exclusivity beyond what the schema
already allows.

---

## 10. Numeric parsing

- A plain, explicitly numeric string may be parsed into a `Decimal`
  (`value_numeric`).
- The original `value_text` is always preserved, whether or not parsing
  succeeded.
- A compound or qualified string such as `"5.4-fold"` or `"12.3 +/- 0.4"`
  remains literal text only (`value_numeric=None`) unless it is safely,
  unambiguously parseable as a plain number — no partial parsing, no
  stripping of qualifying suffixes.
- No unit conversion.
- No averaging.
- No scientific reinterpretation of any reported value.

---

## 11. Organism policy

Organism is normalized only when `EvidenceExtraction.organism_text` is
explicitly present. Do not infer organism from:

- publication metadata,
- gene names,
- pathway familiarity,
- surrounding scientific knowledge.

An extraction with no stated organism produces a `CandidateClaim` with
`organism=None` — never a guessed or inherited organism.

---

## 12. Compartment policy

- Compartment is normalized independently of subject/object typing,
  whenever `EvidenceExtraction.compartment_text` is present.
- `CandidateClaim.compartment` is preserved **even though the current
  `Claim` ORM has no compartment column of any kind** (verified directly
  against `app/models/claim.py` — no `compartment_id`, no
  `compartment_text`, nothing).
- No compartment information is discarded in Claim Generation.

Where a compartment reference should ultimately be persisted is recorded
as an open architecture question (§25.A), not resolved here.

---

## 13. Claim category

`claim_category` remains free text because no controlled vocabulary exists
for it anywhere in the current ORM (`Claim.claim_category` is a plain,
nullable `VARCHAR`) or in any project specification document. Claim
Generation does not invent a taxonomy — see Open Question C.

---

## 14. Directness and evidence type

- `EvidenceType` is carried unchanged from `EvidenceExtraction.evidence_type`.
- `Directness` is carried unchanged from `EvidenceExtraction.directness`.

Claim Generation does not reinterpret either value in any way — a review
statement stays classified exactly as the extraction classified it (§17
of `docs/09_evidence_extraction_contract.md`; carried forward unchanged
here).

---

## 15. Grounding preservation

Every `CandidateClaim` retains its originating:

- `evidence_extraction` (the full object, not a summary of it),
- `SourceSpan` (as `supporting_span`, checked equal to
  `evidence_extraction.span`),
- exact quoted support (`supporting_span.quoted_text`, unchanged from
  extraction).

No grounding information may be changed or dropped between extraction and
claim generation — enforced structurally, not just by convention:
`CandidateClaim.__post_init__` raises if `supporting_span`,
`source`, or `source_identifier` disagree with the originating
`evidence_extraction`.

---

## 16. Multiple-claim behavior

**One `EvidenceExtraction` produces exactly one `CandidateClaim`.**

If one source sentence contains multiple scientific assertions (e.g. "A
activates B and inhibits C"), splitting them into separate
`EvidenceExtraction` records is the responsibility of Evidence Extraction
(Increment 12) — that layer already decomposes a passage into one
extraction per independently grounded statement. Claim Generation does
not re-parse or further split an extraction's own text; doing so would
duplicate work extraction already does and risks exactly the kind of ad
hoc inference this package's instructions forbid.

---

## 17. Negative findings

Explicit negative observations remain negative. No polarity inversion and
no positive reformulation occurs anywhere in this package — a predicate
like `"did not bind"` or `"failed to detect"` is carried through exactly
as extracted (§8).

---

## 18. Ambiguity policy

`AMBIGUOUS` and `CONFLICTED` normalization results are preserved
completely on `CandidateEntityReference.normalization_result`, including
`candidate_entity_ids`. Claim Generation never selects one candidate from
an ambiguous or conflicted set — `normalized_id` stays `None` in both
cases (§4).

---

## 19. Validation guarantees

- `subject` is required (a `CandidateEntityReference`, never `None`).
- `predicate` must be non-empty.
- `source`, `source_identifier`, and `supporting_span` must match the
  originating `EvidenceExtraction` exactly.
- An `organism` reference, if present, must have `entity_kind is
  EntityKind.ORGANISM`.
- A `compartment` reference, if present, must have `entity_kind is
  EntityKind.COMPARTMENT`.
- `normalized_id` requires a `MATCHED` `normalization_result` that agrees
  with it exactly (§4).
- `value_numeric` requires `value_text`; `value_unit` requires either
  `value_text` or `value_numeric` — the same "no orphaned value field"
  discipline `app.extraction.validation` already applies one layer up.

---

## 20. Public API

```python
generate_candidate_claims(
    extractions: Sequence[EvidenceExtraction],
    *,
    lookups: NormalizationLookups | None = None,
    typing_hints: Sequence[EntityTypingHint | None] | None = None,
) -> list[CandidateClaim]
```

- **Input**: a list of `EvidenceExtraction`, plus optional
  `NormalizationLookups` and positionally-paired `EntityTypingHint`s.
- **Output**: a list of `CandidateClaim`, one per input extraction.
- **Output ordering follows input ordering exactly** — this function
  never reorders, merges, or drops an extraction.
- **No database access** — any I/O a supplied `Lookup` performs is
  entirely the caller's concern; this package itself imports no
  `Session` and issues no query.

---

## 21. Prompt contract

`app.claim_generation.prompts.CLAIM_GENERATION_PROMPT` requires:

- entity kind classified only from explicit context in the passage (e.g.
  "the fadD gene" vs. "the FadD protein"),
- no biological inference of any kind,
- negative statements preserved exactly, never rewritten positive,
- `UNKNOWN` whenever the passage's own wording does not make the kind
  unambiguous,
- exactly one typing result per `EvidenceExtraction` — no merging or
  splitting of statements at this step,
- no confidence judgment of any kind — this prompt classifies entity
  kind only, never how trustworthy or well-supported a statement is.

---

## 22. Error model

| Error | Meaning |
|---|---|
| `ClaimGenerationError` | Base class; also raised directly for a malformed top-level call (mismatched `typing_hints` length, a non-`EvidenceExtraction` item). |
| `EntityTypingError` | An entity-typing hint (or raw `SUBJECT_TYPE`/`OBJECT_TYPE` prompt value) does not name a kind this module can dispatch. |
| `NormalizationFailureError` | Calling into `app.normalization.*` failed unexpectedly — never raised for the ordinary "this text alone was not sufficient identity signal" case (a plain `ValueError` from an `Identity` dataclass), which instead leaves the entity unresolved. |
| `ClaimValidationError` | A `CandidateClaim`/`CandidateEntityReference` fails required-field or consistency validation (§19). |

Where the current implementation calls into normalization, it wraps any
unexpected exception in `NormalizationFailureError` with context — raw
normalization exceptions are not exposed to callers of this package
except where the exception is itself the well-defined "insufficient
identity signal" `ValueError`, which is treated as an ordinary unresolved
outcome rather than surfaced as an error at all.

---

## 23. Relationship to confidence scoring

The next stage may consume, per `CandidateClaim`:

- `evidence_type`,
- `directness`,
- each entity reference's normalization status (`MATCHED` /
  `AMBIGUOUS` / `CONFLICTED` / `NEW` / `UNRESOLVED`),
- ambiguity/unresolved state generally,
- source grounding (`supporting_span`, `evidence_extraction`).

Claim Generation itself assigns no score and no confidence class — this
document does not define the future scoring algorithm, only what data
will be available to it.

---

## 24. Relationship to persistence

Future Claim/Evidence persistence will map, where resolvable:

| `CandidateClaim` | → | `Claim`/`Evidence` |
|---|---|---|
| `subject.normalized_id` | → | `Claim.subject_id` |
| `object.normalized_id` | → | `Claim.object_id` |
| `predicate` | → | `Claim.predicate` |
| `value_text`/`value_numeric`/`value_unit` | → | `Claim.value_text`/`value_numeric`/`unit` |
| `organism.normalized_id` | → | `Claim.organism_id` |
| `strain` | → | `Claim.strain` |
| `supporting_span.quoted_text` | → | `Evidence.quoted_support` |
| `directness` | → | `Evidence.directness` |
| `evidence_type` | → | `Evidence.evidence_type` |

No persistence of any kind occurs in Increment 13.

---

## 25. Open architecture questions

### A. Compartment persistence

`Claim` currently has no compartment column. Decide whether compartment
context should eventually be stored as a compartment object reference on
`Claim`, an `Evidence`/`ExperimentalCondition` association, a dedicated
localization claim, or another structure entirely. **Not resolved now.**

### B. Strong-identifier enrichment

Text-only mentions rarely resolve to `MATCHED` under conservative
normalization (§7). Design a future, controlled identifier-enrichment or
retrieval step that may supply strong identifiers (external database
accessions) without weakening any existing normalization rule.
**Not implemented here.**

### C. Claim category vocabulary

Decide whether `claim_category` should remain free text or receive a
controlled vocabulary. **Not introduced now.**

### D. Predicate canonicalization

Decide whether a later semantic layer should map literal predicates to a
controlled predicate vocabulary. **Not canonicalized in Claim Generation.**

---

## 26. Testing summary

`tests/claim_generation/` covers: validation, entity references,
subject/object typing, normalization integration, ambiguity preservation,
unresolved entities, negative statements, numeric and literal values,
review statements, grounding preservation, deterministic ordering, no
persistence, no confidence assignment, and no UUID invention. Test counts
are intentionally not recorded here — they belong to the test suite
itself, which is authoritative.

---

## 27. Final architectural rule

> Claim Generation converts a grounded observation into a candidate
> scientific assertion.
>
> It preserves uncertainty rather than resolving it.
>
> It may structure what the source says, but it may not decide whether the
> source is correct.
