# Agent 1 Evidence Extraction Contract

**Document:** `docs/09_evidence_extraction_contract.md`

**Status:** Authoritative specification for `app/extraction/` (Increment 12).
This document is the canonical design contract for every later
Claim-generation, Evidence-persistence, and Confidence-scoring increment —
those increments must consume `EvidenceExtraction` exactly as specified
here, not as they imagine it might work.

---

## 1. Purpose

Evidence Extraction is responsible for exactly one thing: converting
scientific text into a deterministic, source-grounded intermediate
representation.

It is **not** responsible for:

- normalization
- persistence
- claim generation
- confidence scoring
- contradiction resolution
- cross-paper synthesis

Its output is a validated, immutable `EvidenceExtraction` object. Nothing
downstream should expect this layer to have done more than that, and
nothing in this layer should be extended to do more than that without a
new, explicit increment.

---

## 2. Pipeline position

```text
Scientific document
        ↓
Evidence Extraction
        ↓
EvidenceExtraction
        ↓
Normalization
        ↓
Candidate Claim
        ↓
Confidence Scoring
        ↓
Persistence
```

Evidence Extraction never writes to the database. `app/extraction/`
imports no `Session`, issues no `INSERT`/`UPDATE`/`DELETE`, and calls
nothing in `app.persistence.*`. It sits entirely upstream of
normalization: `app.normalization.*` consumes already-decomposed identity
claims, and this layer is what produces the text that a later step will
turn into those claims.

---

## 3. Core philosophy

Evidence Extraction:

- **extracts** — pulls out statements a source passage explicitly makes,
- **validates** — rejects a statement that fails required-field or
  structural checks,
- **grounds** — ties every statement to one exact, verifiable location in
  one document,
- **preserves** — keeps quoted wording, structure, and uncertainty intact.

It never:

- **concludes** — it does not synthesize an "overall finding" across
  multiple statements or experiments,
- **infers** — it does not fill in a subject, mechanism, organism, or
  identifier the passage does not state,
- **normalizes** — it does not resolve `subject_text`/`organism_text`/etc.
  to a database entity,
- **merges** — it does not combine evidence from two locations into one
  extraction,
- **ranks** — it does not decide which of several extractions is more
  important or more reliable,
- **scores** — it assigns no confidence value of any kind.

One extracted statement represents exactly one grounded observation, tied
to exactly one passage, in exactly one document. Two observations remain
two `EvidenceExtraction` objects, never one merged record — even when they
describe what looks, to a human, like "the same experiment."

---

## 4. EvidenceExtraction object

`EvidenceExtraction` (`app.extraction.types.EvidenceExtraction`) is a
frozen, self-validating dataclass. Its fields, grouped by role:

### Required

| Field | Type | Purpose |
|---|---|---|
| `source` | `SourceType` | Which external source system this passage came from (`app.models.enums.SourceType`, reused directly). |
| `source_identifier` | `str` | The source-level identifier for the document (e.g. a PMID string). Must equal `span.document_identifier` (see §6). |
| `span` | `SourceSpan` | The one grounded location this extraction comes from (§5). |
| `subject_text` | `str` | The subject of the statement, exactly as named in the passage. |
| `predicate_text` | `str` | The relationship or action asserted, exactly as stated. |
| `evidence_type` | `EvidenceType` | The category of evidence (`app.models.enums.EvidenceType`, reused directly — not reinvented). |
| `directness` | `Directness` | The observation-vs-interpretation classification (§8). |

### Optional

| Field | Purpose |
|---|---|
| `publication_id` | `UUID \| None`. Always `None` when produced by this layer — see "Document that UUIDs are intentionally absent" below. |
| `object_text` | The object of the statement. `None` when the predicate is not transitive in a way that needs one — never filled with an invented object just to complete the record. |
| `qualifier_text` | An explicit qualifier (e.g. "in vitro", "under anaerobic conditions"). |
| `normalized_text` | Formatting-artifact-only cleanup of the quoted text, or a curator paraphrase — never a summary that changes meaning (§7 of the increment's own instructions; enforced by convention and documentation, not mechanically checkable). |

### Experimental context

| Field | Purpose |
|---|---|
| `organism_text` | The organism named in the passage, exactly as named — never the organism the caller was curating for, if the passage names a different one. |
| `strain_text` | The strain, exactly as named. |
| `compartment_text` | The subcellular compartment, exactly as named. |
| `experimental_system` | The experimental system or method used. |
| `assay` | The specific assay performed. |
| `perturbation` | An explicit genetic or chemical intervention (knockout, overexpression, a named inhibitor). |
| `claim_category` | Free text describing what kind of statement this is, in the passage's own terms. **No controlled vocabulary exists for this field anywhere in the repository** (see §20) — it is preserved as free text rather than forced into an invented enum. |

### Measurements

| Field | Purpose |
|---|---|
| `measured_quantity` | The name of the quantity measured (e.g. "specific activity"). |
| `measurement_value` | The reported value, as a string, exactly as printed — including any reported uncertainty. |
| `measurement_units` | The reported units, exactly as printed. May be `None` when a value is legitimately unitless (a fold-change, a ratio); may never be non-`None` while `measurement_value` is `None` (§9, §18). |
| `statistical_support` | Any reported p-value, confidence interval, or replicate count, exactly as printed. |

### References

| Field | Purpose |
|---|---|
| `figure_reference` | e.g. `"Figure 2A"`. Text only — see §11. |
| `table_reference` | e.g. `"Table 1"`. |
| `supplementary_reference` | e.g. `"Supplementary Figure 3"`. |

### Notes

| Field | Purpose |
|---|---|
| `notes` | Anything else explicitly stated that does not fit another field. Never a place to add inference or interpretation the passage does not itself state. |

**UUIDs are intentionally absent.** There is no `gene_id`, `protein_id`,
`compound_id`, `reaction_id`, `organism_id`, `compartment_id`, or any other
normalized-entity foreign key anywhere on `EvidenceExtraction`. The one
UUID-shaped field that does exist, `publication_id`, exists purely for
forward compatibility (so a later increment does not need a breaking
schema change to attach a resolved publication once one exists) and is
never set to anything but `None` by this layer. Every entity this
extraction concerns is represented as free text (`subject_text`,
`organism_text`, ...) precisely because resolving that text to a real
database row is normalization's job, not this layer's (§14).

---

## 5. SourceSpan contract

`SourceSpan` (`app.extraction.types.SourceSpan`) describes exactly one
grounded location in one document:

- **`document_identifier`** — the identifier of the document this span
  belongs to. Required.
- **`section`** / **`subsection`** — optional, human-readable structural
  location (e.g. "Results", "3.2 Enzyme kinetics"). Not every source
  exposes this structure, so both are optional.
- **`paragraph_index`** / **`sentence_index`** — optional, zero-based
  positional location, when available.
- **`character_start`** / **`character_end`** — required. The exact
  half-open character range in the document's text that this span covers.
- **`quoted_text`** — required. The exact text at that range.

**Exactly one `SourceSpan` belongs to each `EvidenceExtraction`.** This is
a structural guarantee, not merely a convention: `EvidenceExtraction.span`
is a single field, never a list or a collection — there is no way to
construct an extraction that references two locations at once.

**Quoted text is stored verbatim.** `SourceSpan.quoted_text` is never
trimmed, case-folded, or otherwise altered by this layer — even leading or
trailing whitespace is preserved exactly, because grounding depends on
`quoted_text` matching a literal slice of the source document
character-for-character (§6). Every other free-text field on
`EvidenceExtraction`/`CandidateStatement` *is* trimmed and blank-becomes-
`None` (the same convention `app.normalization.*` already uses for its own
text fields) — `quoted_text` is the deliberate exception.

**Quoted text is never paraphrased.** It is copied, not summarized. A
paraphrase or curator summary belongs in `normalized_text`, a distinct
field, and even that field may only remove formatting artifacts — never
change scientific meaning (§9 of the increment's own instructions).

---

## 6. Grounding guarantees

Every `EvidenceExtraction` has:

- **one source document** (`span.document_identifier`, which must equal
  the extraction's own `source_identifier` — checked in
  `EvidenceExtraction.__post_init__`),
- **one source span** (§5),
- **one quotation** (`span.quoted_text`).

Grounding is verified, not assumed. `app.extraction.extractor.
ground_candidate` resolves a candidate statement's quoted text against the
real source document text in one of two ways:

1. If the candidate already supplies explicit `character_start`/
   `character_end`, this layer verifies that `text[start:end]` equals the
   candidate's own `quoted_text` exactly. Any mismatch fails grounding.
2. If the candidate supplies no offsets, this layer searches for the exact
   quoted text in the document via literal substring search — it never
   asks an upstream model to invent numeric character offsets, since those
   are exactly the kind of detail language models fabricate unreliably.

**If the quotation cannot be uniquely grounded, the extraction fails.**
Two distinct failure modes both raise `GroundingError` (§17):

- the quoted text does not appear in the document at all, or
- the quoted text appears more than once and the candidate supplied no
  explicit offsets to say which occurrence it means.

**No fallback guessing exists.** This layer never picks the first
occurrence of an ambiguous quote, never widens or narrows a mismatched
offset range to make it fit, and never substitutes a "close enough" span.
A candidate that cannot be grounded exactly produces no
`EvidenceExtraction` — the caller must supply a disambiguating offset or
drop the candidate.

---

## 7. Statement decomposition

Every statement is decomposed into:

- **subject** (`subject_text`, required),
- **predicate** (`predicate_text`, required),
- **object** (`object_text`, optional),
- **qualifiers** (`qualifier_text`, optional),

rather than stored as one undifferentiated sentence of prose.

This decomposition exists specifically to simplify later normalization.
`app.normalization.*` already expects a source-neutral identity claim
built from discrete fields (an organism name, a gene symbol, a compound
identifier) — not a sentence a normalizer would first have to parse itself
to find the entity name inside it. By decomposing at extraction time, a
later Claim-generation increment can feed `subject_text` directly into
gene/protein/compound normalization, `organism_text` directly into
organism normalization, and so on, without re-deriving structure that
extraction already determined and validated.

---

## 8. Observation vs interpretation

`Directness` (`app.extraction.types.Directness`) is a six-value controlled
vocabulary, transcribed verbatim from `docs/03_agent_behavior.md`'s
"Evidence Extraction Behavior" section — not invented for this increment:

| Value | Meaning |
|---|---|
| `AUTHORS_OBSERVED` | The passage states this as something the authors directly observed or measured. |
| `AUTHORS_INFERRED` | The authors drew this conclusion from their own data, but it is a step of reasoning beyond a raw observation. |
| `AUTHORS_PROPOSED` | The authors put this forward as a hypothesis or proposal, using speculative language ("may", "might", "suggests", "could", "possibly", "appears to", "is consistent with"). |
| `AUTHORS_DISCUSSED` | The passage discusses this idea (e.g. citing others, or exploring a possibility) without asserting it as the authors' own finding or proposal. |
| `REVIEW_SUMMARIZES` | The passage is a review article summarizing work done elsewhere. |
| `DATABASE_ANNOTATES` | The passage is a curated-database annotation rather than narrative text. |

**Interpretation is represented through `Directness`, not through a
separate `interpretation_text` field.** Increment 12 evaluated adding a
distinct field to hold "the author's interpretation" separately from "the
observation," and deliberately chose not to: no column in the schema
backs a distinct "interpretation" concept, and `Directness`'s own
`AUTHORS_PROPOSED`/`AUTHORS_DISCUSSED`/`REVIEW_SUMMARIZES` values already
capture exactly the observation-vs-interpretation distinction the
increment's instructions asked for. A statement whose directness is
anything other than `AUTHORS_OBSERVED` or `DATABASE_ANNOTATES` should be
read by every later consumer as interpretation, not as a directly observed
fact, regardless of how confidently `subject_text`/`predicate_text`/
`object_text` happen to be phrased.

---

## 9. Measurements

Measurements are stored literally. During extraction, no:

- unit conversion,
- averaging across replicates or across statements,
- normalization of a value to a canonical form,
- unit harmonization (e.g. converting `nmol/min/mg` to `s^-1`),

ever occurs. `measurement_value`, `measurement_units`, and
`statistical_support` are all plain strings holding exactly what the
source reported — including any reported uncertainty (e.g.
`"12.3 +/- 0.4"`) — never coerced to `float`/`Decimal` at this layer. Two
measurements of "the same" quantity under different conditions remain two
separate `EvidenceExtraction` objects; this layer never combines them.

---

## 10. Experimental context

Supported, explicitly-extracted context:

- `organism_text`
- `strain_text`
- `compartment_text`
- `perturbation` (mutation, knockout, overexpression, a named inhibitor)
- `experimental_system`
- `assay`

**Only explicit context is extracted.** None of these fields is ever
inferred from surrounding text, filled in from a default, or copied from
the organism/strain the caller happened to be curating for. **Missing
context is left missing** — a field the passage does not state is `None`,
never a guess. General growth-condition detail not covered by a dedicated
field (medium, temperature, pH, time point) has no single dedicated slot
on `EvidenceExtraction`; when parsed from the extraction prompt's
`EXPERIMENTAL_CONDITIONS` field it is captured in `notes` rather than
inventing new dedicated fields this increment's field list did not
provide for (see `app.extraction.prompts`'s module docstring for the exact
mapping).

---

## 11. Figures and tables

`figure_reference`, `table_reference`, and `supplementary_reference` are
free-text reference fields only (e.g. `"Figure 2A"`, `"Table 1"`,
`"Supplementary Figure 3"`). This layer performs **no figure parsing** and
**no image interpretation** of any kind — it records that a statement
cites a figure or table, never what that figure or table actually shows.

---

## 12. Determinism

**Repeated extraction of identical input must produce identical
`EvidenceExtraction` objects.** `extract_evidence` is a pure function with
respect to its inputs: given the same `source`, `source_identifier`,
`text`, and `candidates`, it always produces the same output.

**Ordering follows source order.** The returned list is always sorted by
`(character_start, character_end)`, regardless of the order `candidates`
were supplied in. Output ordering depends only on where a statement sits
in the source document — never on input order, dictionary/set iteration,
or any other incidental, nondeterministic signal.

---

## 13. No inference rule

Extraction never invents:

- identifiers of any kind,
- UUIDs,
- EC numbers,
- pathways,
- reactions,
- genes,
- proteins,
- compounds,
- mechanisms,

unless explicitly stated in the source passage. Every one of these, if
mentioned at all, is captured only as the free text the passage itself
uses (e.g. `subject_text="FadD"`), never resolved, expanded, or
substituted.

**Pronouns remain unresolved.** If a passage says "it retained activity"
and the antecedent is ambiguous, `subject_text` is `"it"` — this layer
never guesses which noun "it" refers to.

**Abbreviations remain unexpanded.** A passage's own abbreviation (e.g.
"FadD") is preserved exactly as written, never expanded to a systematic
name or a normalized symbol — that resolution, if it happens at all,
belongs to normalization (§14).

---

## 14. Relationship to normalization

Later normalization (`app.normalization.*`) consumes the free-text fields
this layer produces:

- `subject_text` / `object_text` → gene, protein, compound, or reaction
  normalization, depending on what kind of entity the text names.
- `organism_text` → organism normalization.
- `compartment_text` → compartment normalization.
- `strain_text` → carried alongside organism normalization, per the same
  organism/strain handling `app.normalization.organism` already
  implements.

**Extraction itself never resolves entities.** `EvidenceExtraction` is
handed to normalization exactly as produced — normalization is the first
point at which any of this free text is looked up against existing
`Organism`/`Gene`/`Protein`/`Compound`/`Compartment`/`Reaction` rows, and
the first point at which a real UUID enters the pipeline.

---

## 15. Relationship to Claim generation

A later Claim-generation increment will:

- consume `EvidenceExtraction` objects (typically after normalization has
  resolved their free-text fields to entity identities),
- produce Candidate Claims from them,

but must preserve, without modification:

- **quoted support** — `span.quoted_text`, carried through unchanged as
  the claim's evidentiary quotation,
- **source span** — the exact document/location grounding, so a later
  reviewer can always trace a claim back to precisely where it came from,
- **grounding** — the guarantee that this claim traces to one document,
  one span, one quotation (§6) must not be weakened or merged away during
  claim generation, even when multiple `EvidenceExtraction` objects are
  later associated with one `Claim`.

Claim generation may add fields extraction never had (confidence,
normalized subject/object UUIDs, claim status) — it must not alter or
discard what extraction already established.

---

## 16. Relationship to persistence

Persistence occurs only after normalization, claim generation, and
confidence scoring have all run. Evidence Extraction performs no writes:
`app/extraction/` has no dependency on `app.db.*` or `app.persistence.*`,
and no function in this package accepts a `Session`. An
`EvidenceExtraction` object, by itself, is never sufficient to create an
`Evidence` row — it must first pass through every intervening step this
document describes.

---

## 17. Error model

All errors live in `app.extraction.errors`, under one base class,
`ExtractionError`:

| Error | Raised when |
|---|---|
| `UnsupportedInputError` | The call to `extract_evidence` itself is unusable — empty source text, an empty `source_identifier`, or a `candidates` entry that is not a `CandidateStatement` at all. The call cannot proceed. |
| `MalformedSpanError` | A span's own offsets are internally inconsistent, independent of any real document text — a negative `character_start`, an `character_end` not after `character_start`, or a `quoted_text` whose length does not match the offset range. |
| `GroundingError` | A candidate's quoted text could not be grounded in the real source text — absent entirely, present more than once with no disambiguating offsets, or present at the given offsets but not matching the candidate's own quoted text. |
| `ExtractionValidationError` | A candidate or extraction fails required-field or content validation — an empty `subject_text`/`predicate_text`, or `measurement_units` supplied with no `measurement_value`. |
| `LLMFormattingError` | A raw prompt-contract payload could not be parsed into a `CandidateStatement` at all — an unrecognized field key, a missing required key, or an `EVIDENCE_TYPE`/`DIRECTNESS` value outside its controlled vocabulary. Raised only for problems with the payload's *shape*; once parsed, a genuine content problem raises `ExtractionValidationError`/`MalformedSpanError` instead, never `LLMFormattingError`. |

---

## 18. Validation

Validation guarantees, enforced at construction time (in
`__post_init__`, via `app.extraction.validation`'s pure helper functions)
and at extraction time (in `app.extraction.extractor`):

- **Required fields**: `subject_text`, `predicate_text`, `quoted_text`,
  `evidence_type`, `directness` must all be present and non-blank; a blank
  optional field is normalized to `None` rather than stored as an empty
  string.
- **Offset checks**: `character_start >= 0`; `character_end >
  character_start`; `paragraph_index`/`sentence_index`, when given, must
  be `>= 0`.
- **Quotation verification**: a span's `quoted_text` length must exactly
  equal `character_end - character_start` (a text-independent structural
  check); separately, at extraction time, the quoted text must exactly
  match the real source document at those offsets, or be uniquely
  locatable within it (§6).
- **Measurement validation**: `measurement_units` may never be supplied
  without an accompanying `measurement_value` (a unit with no value makes
  no sense); the reverse is permitted, since some quantities are
  legitimately unitless.
- **Grounding verification**: see §6 — enforced by
  `app.extraction.extractor.ground_candidate`, never skipped.

---

## 19. Testing philosophy

`tests/extraction/` exercises this contract entirely against small,
synthetic, deterministic fixtures (`tests/extraction/fixtures.py`) — no
external API, no live paper, no network access. Coverage spans:

- construction-time validation of `SourceSpan`, `CandidateStatement`, and
  `EvidenceExtraction` (required fields, offset/length checks, type
  checks, measurement-field checks),
- grounding (exact match, ambiguous-quote rejection, explicit-offset
  disambiguation, mismatched-offset rejection),
- statement decomposition (subject/predicate/object/qualifier extracted
  separately, never concatenated back into prose),
- observation vs. interpretation (`Directness` values distinguished across
  a directly-observed finding and a review-style discussion passage),
- measurement handling (values/units/statistical support preserved
  literally, as strings, never coerced),
- experimental context (organism, strain, mutation/perturbation,
  compartment, assay),
- multiple statements from one passage, including out-of-source-order
  input still producing source-ordered output,
- ambiguity preservation (an unresolved pronoun left as-is),
- a negative experimental finding handled as an ordinary extraction, not a
  special case,
- figure references,
- determinism (repeated extraction of identical input compared for
  equality),
- the prompt contract itself (`app.extraction.prompts`): required-field
  parsing, `"NOT STATED"` handling, historical field-name aliases,
  unrecognized fields/values, and a content check that the prompt text
  itself contains the constraints this document requires of it,
- a structural check that no field on `EvidenceExtraction` could hold a
  fabricated entity identifier.

Test counts are intentionally not recorded here — they belong to the test
suite itself, which is authoritative, not to this document, which would
otherwise drift out of date the next time a test is added.

---

## 20. Known limitations

The following are intentional limitations of this increment, not
oversights. They belong to later increments:

- No OCR.
- No PDF parsing.
- No figure interpretation.
- No table parsing.
- No retrieval.
- No embeddings.
- No normalization (entity resolution to UUIDs).
- No confidence scoring.
- No persistence (no database writes of any kind).
- No scientific reasoning — this layer records what a passage says, and
  makes no judgment about whether it is true, well-supported, or
  reproducible.

Additionally, two specific gaps are recorded for whoever picks up the next
increment, rather than silently worked around:

- **`claim_category` has no controlled vocabulary.** `docs/03_agent_behavior.md`
  lists "claim category" as a field every extracted claim must include,
  but no enum or documented value set exists anywhere in this repository
  for it (`Claim.claim_category` is a plain, nullable `VARCHAR`). This
  field is preserved as free text pending that decision.
- **`normalized_text`'s "formatting artifacts only" rule is not
  mechanically enforced.** This layer cannot verify programmatically that
  a supplied `normalized_text` value did not change scientific meaning or
  summarize beyond formatting cleanup — that discipline currently depends
  on whatever produces the `CandidateStatement` (an LLM following
  `EVIDENCE_EXTRACTION_PROMPT`, or a human curator) following the rule
  correctly.

---

## 21. Final architectural rule

> Evidence Extraction preserves exactly what the scientific source
> explicitly states.
>
> It never changes scientific meaning.
>
> It never decides biological truth.
>
> It produces a complete, deterministic, source-grounded observation that
> later Agent 1 components may analyze.
