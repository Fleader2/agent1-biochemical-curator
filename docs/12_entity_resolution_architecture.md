# Agent 1 Entity Resolution Architecture

**Document:** `docs/12_entity_resolution_architecture.md`

**Status:** Authoritative architecture reference for `app/entity_resolution/`
as implemented across Increments 13-15, and for its integration into
`app/claim_generation/` implemented in Increment 16 (see §20).

---

## 1. Purpose

Ordinary scientific text supplies names, symbols, and labels — "FadR",
"acyl-CoA", "the fabA promoter" — not strong database identifiers. The
existing entity normalizers (`app.normalization.*`, Increment 4 onward)
are, by design, conservative about that: a bare name is treated as weak,
candidate-generation-only signal and rarely reaches `MATCHED` on its own
(Increment 13's own completion report flagged this explicitly as an
architectural consequence, not a defect).

The Entity Resolution architecture bridges that gap **without weakening
normalization**. It does not loosen any existing normalizer's matching
rule, invent a fuzzy-match shortcut, or introduce a second notion of
identity. It only retrieves *stronger* identifiers — an SGD id, a KEGG
compound/reaction id, a verified PMID, a verified UniProt accession — from
trusted external sources, so that the same unmodified normalizers have a
real chance of reaching `MATCHED` through the strong-identifier path they
already support.

Core rule:

> Retrieval proposes identifiers. Normalization establishes canonical
> identity.

---

## 2. Pipeline position

```text
Scientific source
    ↓
Evidence Extraction
    ↓
EvidenceExtraction
    ↓
Entity typing
    ↓
EntityMention
    ↓
Connector-based identifier enrichment
    ↓
IdentifierCandidate(s)
    ↓
Existing normalization
    ↓
MentionResolutionResult
    ↓
CandidateEntityReference
    ↓
CandidateClaim
```

Entity Resolution performs **no persistence** anywhere in this pipeline —
no database write, no `SourceCrossReference`, no `ExternalRecord`, no
`Claim`/`Evidence` row. Every stage above it is read-only with respect to
Agent 1's own database; the only I/O this architecture performs is
outbound HTTP to the external sources it enriches from.

---

## 3. Separation of responsibilities

- **Evidence Extraction** (`app.extraction`) preserves source-grounded
  text — subject/predicate/object/organism/compartment mentions, exactly
  as printed, with no normalization of any kind.
- **Entity typing** (a caller-supplied `EntityTypingHint`, or a future
  LLM-assisted step following `app.claim_generation.prompts`) classifies
  a mention's `EntityKind` conservatively — `UNKNOWN` whenever the
  passage's own wording does not make the kind unambiguous, never
  inferred from naming conventions.
- **Connector enrichment** (`app.entity_resolution`, this document)
  retrieves candidate strong identifiers from one trusted external source
  per supported kind. It never declares two entities identical.
- **Normalization** (`app.normalization.*`) decides canonical identity.
  It is the only layer in this entire pipeline authorized to produce a
  `MATCHED` verdict.
- **Claim Generation** (`app.claim_generation`) structures the assertion
  — predicate, value, evidence type, directness, grounding — around
  whatever entity references normalization (directly, or via Entity
  Resolution) produced.

No layer may silently absorb the scientific responsibility of another:
Entity Resolution does not normalize, normalization does not retrieve,
and Claim Generation does not decide identity.

---

## 4. EntityMention contract

One textual entity mention to resolve, together with whatever context is
already known about it (`app.entity_resolution.types.EntityMention`):

| Field | Meaning |
|---|---|
| `original_text` | The exact mention text, non-empty, never altered. |
| `entity_kind` | An `EntityKind` — never inferred by this module. |
| `source_context` / `source_context_identifier` | The document this mention came from — required together, and must agree with `evidence_extraction` when one is supplied. |
| `organism_context_text` | Free-text organism name (e.g. `"Saccharomyces cerevisiae"`), if known. |
| `organism_id` | An *already-resolved* Agent 1 organism UUID — never invented or looked up by this module. `None` means no resolved organism is available. |
| `strain_text` | Free-text strain, if known — carried for provenance, not used for identity. |
| `surrounding_text` | Optional broader context, for provenance only. |
| `evidence_extraction` | The originating `EvidenceExtraction`, if available — must agree with `source_context`/`source_context_identifier`. |

Deliberately excludes a normalized UUID for the mention itself (that is
this architecture's *output*, not its input) and any confidence score (no
confidence scoring exists anywhere in this pipeline yet — see §21).

---

## 5. IdentifierCandidate contract

One candidate strong-identifier bundle, already run through the existing
normalizer for its kind (`app.entity_resolution.types.IdentifierCandidate`):

| Field | Meaning |
|---|---|
| `entity_kind`, `source`, `source_identifier` | What kind this candidate is, and which external source and record it came from. |
| `original_mention`, `search_term` | The mention text, and the exact term the connector was searched with (which may include an organism filter — see §17). |
| `source_record_identifier` | The source's own stable record id (e.g. an SGD id, a KEGG entry id, a PMID, a UniProt accession). |
| `normalization_input` | **A genuine, already-validated `app.normalization.*` `*Identity` object** — never an untyped dictionary. |
| `normalization_result` | **A genuine `NormalizationResult`** — the actual verdict `app.normalization.*` returned for `normalization_input`. |
| `display_name` | Optional human-readable label, for provenance/display only. |
| `organism_context_text`, `organism_id` | Carried through from the originating mention, when relevant to this kind. |
| `retrieved_identifiers` | A flat, read-only audit view of whatever identifier-shaped fields the source record exposed — provenance only; `normalization_input` remains the single source of truth actually passed to the normalizer. |

`normalization_input.source`/`source_identifier` and
`normalization_result.source`/`source_identifier` are both checked in
`__post_init__` to equal this candidate's own `source`/`source_identifier`
— the identity actually submitted, and the result actually returned, must
both be for the record this candidate claims to represent. This is not a
convenience type: it is structurally impossible to construct an
`IdentifierCandidate` around a fabricated or mismatched identity.

---

## 6. MentionResolutionResult contract

The final, immutable output of resolving one `EntityMention`
(`app.entity_resolution.types.MentionResolutionResult`), carrying `mention`,
`status`, `candidates` (all of them, never narrowed), `resolved_entity_id`,
`sources_queried`, `reason`, and (for `SOURCE_FAILURE` only) `failed_source`/
`error_category`.

`MentionResolutionStatus` has eight values:

- **`RESOLVED`** — every candidate that reached `MATCHED` agrees on exactly
  one canonical entity id; `resolved_entity_id` is that id.
- **`AMBIGUOUS`** — at least one candidate's own normalization is
  `AMBIGUOUS`, and no candidate reached `MATCHED`.
- **`CONFLICTED`** — either a candidate's own normalization is itself
  `CONFLICTED`, or different candidates normalized to different `MATCHED`
  canonical ids.
- **`NO_CANDIDATE`** — either no external record was retrieved at all, or
  records were retrieved but none carried enough information to
  normalize. Never implies the entity does not exist (§9).
- **`UNSUPPORTED_ENTITY_KIND`** — no trustworthy connector exists for this
  `entity_kind` in this repository today (or none was supplied to this
  call).
- **`SOURCE_FAILURE`** — a connector call itself failed (network, HTTP,
  parse error). Distinct from every other status (§9).
- **`UNRESOLVED`** — resolution could not even be *attempted* because
  required context is missing (an organism-scoped kind with no resolved
  `organism_id`). Zero connector calls are made in this case.
- **`NEW_CANDIDATE`** — a real, verified external record was found and at
  least one candidate normalized to `NEW` (safe to create), with no
  `MATCHED`/`AMBIGUOUS`/`CONFLICTED` candidate present. Distinct from both
  `NO_CANDIDATE` (which would misrepresent "we found a real record" as "we
  found nothing") and `RESOLVED` (which requires an *existing* matched
  entity).

`UNRESOLVED` and `NEW_CANDIDATE` are additions introduced during
Increment 14 beyond that increment's own illustrative status list, for the
precision reasons given above — not narrowed or reduced since.

---

## 7. RESOLVED policy

`RESOLVED` requires normalization itself to have produced a consistent
`MATCHED` canonical entity — `app.entity_resolution.ranking.classify_outcome`
is a pure function of each candidate's own already-established
`NormalizationResult`; it contains no identity logic of its own. Entity
Resolution never invents `MATCHED`.

If multiple source candidates all normalize to the same UUID, they jointly
support one `RESOLVED` outcome — the id is the one every `MATCHED`
candidate already agrees on, and every candidate (not just one arbitrarily
chosen one) is preserved in `candidates` (§18).

---

## 8. Ambiguity/conflict policy

Every candidate, every source record, and every conflicting normalized
UUID Entity Resolution encounters is preserved in full on
`MentionResolutionResult.candidates` — sorted by a stable, source-defined
key (`(source, source_record_identifier)`, `app.entity_resolution.ranking
.sort_candidates`), never by retrieval/HTTP-response order. Normalization-
level ambiguity (a candidate's own `AMBIGUOUS`/`CONFLICTED` result) is
never resolved or hidden by this layer. The first result is never chosen
just because it arrived first.

---

## 9. Source failure versus no candidate

**`SOURCE_FAILURE != NO_CANDIDATE`.** An external API failure (timeout,
5xx, malformed response) is never converted into evidence that an entity
does not exist — `app.entity_resolution.resolver.resolve_entity_mention`
catches every `app.connectors.exceptions.ConnectorError` per source and
reports it as `SOURCE_FAILURE` (with `failed_source`/`error_category`
populated), structurally distinct from the zero-candidate,
successfully-queried `NO_CANDIDATE` outcome. This mirrors
`.cursor/rules/01-scientific-integrity.mdc`'s "Unknown Versus Negative
Evidence" rule directly.

---

## 10. Supported entity kinds

Real, trusted connector support exists today for:

| `EntityKind` | Source | Adapter reused |
|---|---|---|
| `GENE` | SGD | `gene_identity_from_sgd` |
| `COMPOUND` | KEGG | `compound_identity_from_kegg` |
| `REACTION` | KEGG | `reaction_identity_from_kegg` |
| `PUBLICATION` | PubMed | `publication_identity_from_pubmed` |
| `PROTEIN` | UniProt | `protein_identity_from_uniprot` |

Every adapter listed above already existed in `app.normalization.*` before
Entity Resolution was built (or, for Protein, was added directly to that
module in Increment 15) — nothing in `app.entity_resolution` reimplements
or duplicates one.

---

## 11. Unsupported or partially supported kinds

- **`ORGANISM`** has no dedicated enrichment connector in this repository
  — no NCBI-taxonomy connector, no organism-name-authority connector
  exists today. Organism mentions may still resolve through
  `app.normalization.organism.normalize_organism`'s own scientific-name
  (and strain) matching directly — that normalizer's exact-name behavior
  is often sufficient on its own, since organism names are a much smaller
  and more standardized vocabulary than gene/protein/compound names.
- **`COMPARTMENT`** has no ontology/definition connector (no GO Cellular
  Component connector, no SGD-compartment connector) — resolution remains
  whatever `app.normalization.compartment.normalize_compartment`'s own
  organism-scoped name matching can establish from bare text.
- **`UNKNOWN`** is intentionally unsupported — this is the safe default
  for "this module was not told what kind this is, and therefore never
  guessed" (see `app.claim_generation.types.EntityKind`'s own docstring),
  never a genuinely typeless entity to be resolved anyway.

All three route to `MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND` if a
caller nonetheless asks Entity Resolution to resolve one (`resolve_entity_
mention`'s `_NO_CONNECTOR_KINDS`) — there is no fabricated fallback
connector for any of them.

---

## 12. Protein resolution

```text
Protein mention
    ↓
resolved organism context required
    ↓
UniProt search
    ↓
exact fetch
    ↓
UniProtProteinRecord
    ↓
protein_identity_from_uniprot
    ↓
normalize_protein
```

`mention.organism_id` gates the whole path exactly like Gene/Reaction: if
it is `None`, `UNRESOLVED` is returned with **zero UniProt calls**.
`mention.organism_context_text`, when present, is folded into the UniProt
query as a best-effort `organism_name` filter — never a hard requirement
(see §17 and `docs/11_uniprot_connector_contract.md` §9 for the full
taxonomy-mapping design decision).

Every search hit is re-fetched by its exact accession before a
`ProteinIdentity` is ever constructed (a search hit alone is never
sufficient). Preserved throughout, with no collapsing or interpretation:

- the exact UniProt accession, including isoform suffixes (`"P12345"` and
  `"P12345-2"` always distinct),
- reviewed/unreviewed status (Swiss-Prot vs. TrEMBL) — metadata only,
  never used to prioritize one candidate over another,
- gene-name metadata — inert; `protein_identity_from_uniprot` always sets
  `gene_id=None`, and no Gene normalizer is ever called from this path,
- EC-number metadata — inert; `ProteinIdentity.ec_number` is set only when
  a record carries exactly one unambiguous EC number.

**No Gene↔Protein inference** occurs anywhere in this path — enforced both
by the adapter's own hardcoded `gene_id=None` and by structural tests
confirming no Gene-normalization function is ever referenced from the
Protein resolution code path.

---

## 13. Gene resolution

`resolve_gene_via_sgd` (`app.entity_resolution.adapters`) searches SGD by
the mention's exact text, fetches each hit's full locus record, and
constructs a `GeneIdentity` via the pre-existing `gene_identity_from_sgd`
adapter — never a bare symbol treated as a strong identifier on its own.
Requires `mention.organism_id` (Gene is organism-scoped in the schema);
`UNRESOLVED` with zero SGD calls if absent.

---

## 14. Compound resolution

`resolve_compound_via_kegg` searches KEGG's `compound` database, fetches
each hit's flat-file entry, and constructs a `CompoundIdentity` via
`compound_identity_from_kegg`. Compound is global (no organism scoping) —
protonation state, stereochemistry, and generic-vs-specific distinctions
are never collapsed: each KEGG entry becomes its own independent
candidate, and `normalize_compound` remains the sole authority on whether
any of them match an existing row.

---

## 15. Reaction resolution

`resolve_reaction_via_kegg` searches KEGG's `reaction` database, fetches
each hit's flat-file entry, and constructs a `ReactionIdentity` via
`reaction_identity_from_kegg`. Requires `mention.organism_id`, exactly
like Gene. The raw KEGG `equation` field is never parsed into structured
participants by this layer — `reaction_identity_from_kegg` itself already
leaves `participants` empty for this reason, and nothing in Entity
Resolution adds parsing on top of it.

---

## 16. Publication resolution

`resolve_publication_via_pubmed` searches PubMed (which returns only
PMIDs — no title/DOI on the search hit itself), fetches each PMID's full
article, and constructs a `PublicationIdentity` via
`publication_identity_from_pubmed` using the **verified** PMID from the
fetched record — never the bare search text. A title-like mention alone
is therefore never treated as a strong identifier; only a fetched,
verified PMID is.

---

## 17. Organism-context policy

Organism-scoped entity kinds must never silently search globally and
accept a foreign-organism candidate. The current organism-dependent kinds
are:

- `GENE`
- `PROTEIN`
- `REACTION`

If required organism context (`mention.organism_id`) is absent for any of
these, the result is `UNRESOLVED` — with **zero connector calls made** —
never a global, unscoped search. Where `organism_context_text` (free text)
is also available, it may be folded into the connector's own search query
as a best-effort filter (currently done for UniProt/Protein — see §12);
genuine cross-organism protection always comes from the downstream
normalizer's own organism-scoped lookup, never from the search filter
alone.

---

## 18. Multiple-source corroboration

Multiple candidates may jointly support the same canonical UUID — this is
not collapsed into "pick one" reporting. Every retrieved candidate's
provenance (`source`, `source_record_identifier`, `search_term`,
`retrieved_identifiers`) is preserved on `MentionResolutionResult
.candidates` regardless of how many there are or which status they carry.
No single connector record is ever forced to represent the entirety of a
mention's resolution — see also §14 of
`docs/11_uniprot_connector_contract.md` for how this looks concretely.

---

## 19. No LLM identifier invention

There is no code path anywhere in `app.entity_resolution` that accepts a
bare string as a strong identifier without it coming from a genuine
connector record fetch. Every `IdentifierCandidate.source_record_identifier`
traces to an actual `fetch()` call's returned record — never to LLM output,
never to a caller-supplied guess.

---

## 20. Relationship to Claim Generation

**As of Increment 16, this integration is implemented.**
`app.claim_generation.generate_candidate_claims` accepts an optional
`connectors: ConnectorBundle | None = None` parameter; when supplied, the
subject/object entity references for `GENE`/`PROTEIN`/`COMPOUND`/
`REACTION`/`PUBLICATION` mentions prefer `resolve_entity_mention` over
direct bare-text normalization, falling back to direct normalization only
when the specific connector a kind needs is absent from the supplied
bundle (`MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND`). Every other
outcome — including `SOURCE_FAILURE` and `UNRESOLVED` — is preserved on
`CandidateEntityReference.mention_resolution_result` rather than silently
falling back. See `docs/10_claim_generation_contract.md` §27 for the
consumer-facing contract, and this repository's Increment 16 completion
report for the full design rationale.

Before Increment 16, Claim Generation used only the direct-normalization
path described in `docs/10_claim_generation_contract.md` §6-7 — that path
still exists unchanged today as the fallback for organism/compartment/
unsupported-kind mentions, and as the exact behavior reproduced whenever
no `connectors` bundle is supplied at all.

---

## 21. Known limitations

- No Gene↔Protein reconciliation exists anywhere in this architecture — a
  UniProt record's gene-name metadata is preserved but never used to
  create or infer a Gene relationship.
- No Compartment enrichment connector exists (§11).
- No dedicated Organism enrichment connector exists (§11) — organism
  resolution relies entirely on `app.normalization.organism`'s own
  scientific-name/strain matching.
- No generalized UniProt ID Mapping (`/idmapping`) is implemented (see
  `docs/11_uniprot_connector_contract.md` §13) — explicitly deferred, not
  abandoned.
- No fuzzy matching exists anywhere in this architecture, at either the
  connector-search or normalization layer.
- No ontology expansion (synonym tables, cross-ontology mapping) exists.
- No persistence of any kind occurs in this architecture — every output
  described here is an in-memory, immutable value; writing any of it to
  the database is a distinct, later increment's responsibility.
- No confidence scoring exists yet — entity resolution status
  (`RESOLVED`/`AMBIGUOUS`/...) and evidence/claim-level scientific
  confidence remain entirely separate concepts, and nothing in this
  architecture conflates them.

---

## 22. Final architectural rule

> Text determines what Agent 1 should search for.
>
> Trusted sources provide candidate identifiers.
>
> Normalization decides canonical identity.
>
> No retrieval layer may bypass that boundary.
