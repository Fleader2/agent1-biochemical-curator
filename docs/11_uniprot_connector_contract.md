# UniProt Connector and Protein Identifier Enrichment Contract

**Document:** `docs/11_uniprot_connector_contract.md`

**Status:** Authoritative specification for `app/connectors/uniprot.py` and the
Protein path of `app/entity_resolution/` (Increment 15).

---

## 1. Purpose

This increment adds a UniProtKB connector so that `EntityKind.PROTEIN`
mentions — which ordinary biological text names only by a protein or gene
name, never by a UniProt accession — can be enriched with a verified,
Level-1 UniProt accession before normalization is asked to decide identity.

**Core rule, unchanged from Increment 14 and restated here because it
governs everything in this document:**

> Retrieval proposes identity candidates. Normalization decides identity.

UniProt supplies a verified Level-1 UniProt accession. `app.normalization
.protein.normalize_protein` — untouched by this increment — still decides
canonical identity. The connector and its adapter never construct a
`MATCHED` result themselves; they only ever produce an `Identity` object
that is handed to the existing normalizer.

---

## 2. What this increment does **not** do

- Implement UniProt's generalized ID Mapping API (`/idmapping/*`). See §13.
- Implement UniRef, UniParc, sequence similarity, or BLAST-style lookups.
- Reconcile Gene and Protein records, or infer one from the other.
- Infer or persist a `ReactionEnzyme` relationship.
- Change Claim Generation's own behavior (only a documentation note is
  added — see §14).
- Perform confidence scoring.
- Write to the database in any way.

---

## 3. Pipeline position

```text
EntityMention (entity_kind=PROTEIN, organism_id, organism_context_text)
    ↓
resolve_entity_mention (app.entity_resolution.resolver)
    ↓
resolve_protein_via_uniprot (app.entity_resolution.adapters)
    ↓
UniProtConnector.search() → UniProtSearchHit[]
    ↓ (per hit)
UniProtConnector.fetch(accession) → UniProtEntryRecord | None
    ↓
UniProtConnector.normalize(entry) → UniProtProteinRecord
    ↓
protein_identity_from_uniprot(record) → ProteinIdentity
    ↓
app.normalization.protein.normalize_protein(identity, organism_id, lookup)
    ↓
NormalizationResult (MATCHED / AMBIGUOUS / CONFLICTED / NEW / UNRESOLVED)
    ↓
IdentifierCandidate
```

Every step above already existed except the top three layers (connector,
adapter, resolver dispatch branch) added in this increment.

---

## 4. UniProtKB REST API surface used

Two endpoints, both under a single configured base URL
(`Settings.uniprot_base_url`):

| Operation | Endpoint | Used for |
|---|---|---|
| Search | `GET {base_url}/uniprotkb/search?query=...&format=json&size=...` | `UniProtConnector.search()` |
| Exact fetch | `GET {base_url}/uniprotkb/{accession}` | `UniProtConnector.fetch()` |

No credential is required by UniProt's public REST API, matching SGD and
KEGG. **No default base URL is supplied** (`uniprot_base_url: str | None =
None`, mirroring `sgd_base_url`/`kegg_base_url` rather than PubMed's
hardcoded default) — this session did not live-verify the endpoint, so a
missing configuration fails loudly (`UniProtConnector.from_settings`
raises `ValueError`) instead of silently defaulting to an unverified URL.
This mirrors the caveat already carried by `app/connectors/kegg.py`'s own
module docstring.

All retry, exponential backoff, rate limiting, response caching, and HTTP
timeout behavior is inherited unmodified from the shared
`ConnectorHttpClient`/`RateLimiter`/`ResponseCache` (`app/connectors/http.py`,
`ratelimit.py`, `cache.py`). No new HTTP framework, retry loop, or cache
was written for UniProt.

---

## 5. Raw and normalized record shapes

### `UniProtSearchHit` (search result row — deliberately minimal)

`primary_accession`, `entry_name`, `reviewed`. A search hit alone is never
sufficient to build a `ProteinIdentity` — the connector always performs a
full `fetch()` of the exact accession before constructing any identity
(§8).

### `UniProtEntryRecord` (raw fetched entry — nothing discarded)

`primary_accession`, `entry_name`, `entry_type`, `secondary_accessions`,
`recommended_name`, `submitted_names`, `gene_names`, `organism_name`,
`organism_taxonomy_id`, `ec_numbers`, `sequence_length`,
`cross_references` (**all** cross-references present in the source
record, unfiltered), `raw: dict[str, Any]` (the parsed JSON body, kept for
future `ExternalRecord` provenance).

### `UniProtProteinRecord` (normalized — narrowed, still no identity decision)

`primary_accession`, `entry_name`, `reviewed`, `protein_name`,
`gene_names`, `organism_name`, `organism_taxonomy_id`, `ec_numbers`,
`sequence_length`, `secondary_accessions`, `cross_references` (narrowed —
see §11), `raw: UniProtEntryRecord`. This is the type
`protein_identity_from_uniprot` consumes.

---

## 6. Reviewed-status policy

`reviewed: bool | None` (`True` = Swiss-Prot, `False` = TrEMBL, `None` if
`entryType` is unparseable) is preserved end-to-end as metadata only.

- It is **never** used to decide identity — a reviewed and an unreviewed
  hit are both fetched, normalized, and passed into `normalize_protein`
  with no priority ordering between them.
- It is **never** folded into a confidence value anywhere in this
  increment.
- `_parse_reviewed()` deliberately checks the substring `"unreviewed"`
  before `"reviewed"`, since the former contains the latter as a
  substring in UniProt's own `entryType` string
  (`"UniProtKB unreviewed (TrEMBL)"`).

Multiple candidates that collapse to the same matched `Protein` row (e.g.
a reviewed canonical accession and a related unreviewed one that already
share a UUID in the lookup) are preserved and reported per the existing
same-UUID collapse policy already established for every other kind in
`app.entity_resolution.ranking.classify_outcome` — this increment adds no
protein-specific collapse rule.

---

## 7. Literal-accession and isoform policy

- Accessions are carried **exactly as returned**, with no case-folding
  and no isoform-suffix stripping.
- `"P12345"` and `"P12345-2"` are always treated as distinct
  `source_identifier`/`uniprot_id` values, at every layer (raw parsing,
  normalized record, `ProteinIdentity`, and `IdentifierCandidate`).
- No code in `app/connectors/uniprot.py`, `protein_identity_from_uniprot`,
  or `resolve_protein_via_uniprot` calls `.split("-")`,
  `.removesuffix(...)`, or any other isoform-stripping transform on an
  accession — verified structurally by
  `tests/connectors/test_uniprot.py`'s isoform tests and
  `tests/entity_resolution/test_resolver.py
  ::test_protein_never_triggers_gene_or_reaction_resolution`.

---

## 8. Search-then-fetch discipline

`UniProtConnector.search()` returns only `UniProtSearchHit` — accession,
entry name, and reviewed flag. It is never trusted alone. Every candidate
constructed by `resolve_protein_via_uniprot` first calls
`connector.fetch(hit.primary_accession)` to retrieve the full entry; a hit
whose accession can no longer be fetched (`fetch()` returns `None`) is
silently skipped rather than turned into a partial or fabricated
candidate. This exactly mirrors the SGD/KEGG/PubMed adapters' own
search-hit-then-exact-fetch pattern.

`fetch()` itself distinguishes "not found" (404 → `None`) from a genuine
connector failure (any other HTTP error or network failure → the
corresponding `ConnectorError` subclass propagates, caught by the resolver
as `SOURCE_FAILURE`, never conflated with a legitimate empty result).

---

## 9. Organism-context policy (search filtering) and the taxonomy-mapping decision

`EntityMention.organism_id` is an Agent-1-internal `UUID` identifying a row
in this repository's own `Organism` table. It is **not** an NCBI taxonomy
ID, and no mapping table between the two exists anywhere in this
repository. Inventing one was explicitly out of scope for this increment
and was not done.

Instead, Protein resolution uses two organism-related fields on
`EntityMention` for two entirely separate purposes:

1. **`organism_id`** is used only to satisfy `normalize_protein`'s own
   required, keyword-only `organism_id: UUID` parameter — exactly the same
   gate Gene and Reaction resolution already apply. If it is `None`,
   Protein resolution returns `UNRESOLVED` **before making any UniProt
   call at all** (verified by
   `test_protein_without_organism_id_is_unresolved_no_connector_call`).
2. **`organism_context_text`** (free-text organism name, e.g.
   `"Saccharomyces cerevisiae"`, already available on `EntityMention` with
   no new dependency) is folded into the UniProt search query itself as a
   best-effort `organism_name` field filter:
   `f'{query} AND organism_name:"{organism_context_text}"'`. This is a
   search-quality refinement only — it narrows what UniProt returns, but
   it is never treated as a hard identity requirement or a substitute for
   real cross-organism protection.

Genuine cross-organism protection is provided the same way it already is
for every other candidate this module produces: `normalize_protein`'s own
organism-scoped `ProteinLookup.by_name` lookup. A UniProt hit that happens
to belong to the wrong organism does not silently match — the existing
normalizer's own organism-scoped comparison is what actually prevents
that, exactly as for Gene and Reaction today.

---

## 10. Protein-name policy

`_parse_protein_names()` prefers
`proteinDescription.recommendedName.fullName.value`; if no recommended
name exists (common for unreviewed TrEMBL entries), it falls back to the
first `proteinDescription.submissionNames[].fullName.value`. If neither is
present, `protein_name` is `None`. The protein name is **never**
constructed or guessed from the gene symbol.

---

## 11. Gene-name and cross-reference policy (mandatory non-inference)

`gene_names` (from UniProt's `genes[].geneName.value`) is preserved on
`UniProtEntryRecord`/`UniProtProteinRecord` as inert descriptive metadata
only. It is:

- **never** used to set `ProteinIdentity.gene_id` (`protein_identity_from_uniprot`
  hardcodes `gene_id=None` unconditionally — verified structurally by
  `test_uniprot_adapter_never_calls_gene_normalization`), and
- **never** used to call any Gene normalization function.

Cross-references are narrowed for the *normalized* record only, to a safe
set with no relationship-creation risk:

```python
_SAFE_CROSS_REFERENCE_DATABASES = frozenset({"SGD", "GeneID", "KEGG"})
```

`UniProtEntryRecord.cross_references` (raw) keeps **all** cross-references
UniProt returned, for future `ExternalRecord` provenance.
`UniProtProteinRecord.cross_references` (normalized) keeps only entries
whose `database` is in the safe set above. Neither layer creates an Agent 1
`Gene` relationship, a `ReactionEnzyme` row, or any other cross-entity
link — cross-references remain descriptive metadata, exactly like
`gene_names`.

---

## 12. EC-number policy

`ProteinIdentity.ec_number` is set **only when the record has exactly one
EC number**:

```python
ec_number = record.ec_numbers[0] if len(record.ec_numbers) == 1 else None
```

Zero EC numbers and multiple EC numbers (a genuinely multi-functional
enzyme) both leave `ec_number = None` — there is no single unambiguous
value to represent in either case, and this increment does not invent a
"primary" EC number by picking the first of several. `ec_numbers` (the
full tuple) remains available on `UniProtProteinRecord` for future use;
only the single-value `ProteinIdentity.ec_number` field is deliberately
this conservative.

---

## 13. ID Mapping deferral (explicit scope boundary)

UniProt's `/idmapping/*` REST API (submit a mapping job, poll for
completion, fetch results — used for bulk translation between UniProt
accessions and other database identifiers) is **not implemented** in this
increment. `UniProtConnector` exposes no `map_ids`/`id_mapping` method or
attribute, and none of `search()`/`fetch()`/`normalize()`/`__init__()`
reference the `/idmapping` path — enforced structurally by
`tests/connectors/test_uniprot.py::test_uniprot_connector_never_calls_idmapping`.
This is a deliberate scope boundary for this increment, not an oversight;
a bulk ID-mapping capability may be added in a later, separate increment
if a concrete need for it arises.

---

## 14. Entity Resolution integration

- `app.entity_resolution.resolver.ConnectorBundle` gained one new optional
  field: `uniprot: UniProtSearchAndFetch | None = None`.
- `EntityKind.PROTEIN` was removed from `_NO_CONNECTOR_KINDS` (it is no
  longer structurally unsupported) and given its own dispatch branch,
  following exactly the same three-step shape as Gene/Reaction: connector
  and lookup presence check → `organism_id` presence check → call the
  adapter, catching `ConnectorError` as `SOURCE_FAILURE`.
- No change was made to how Gene, Compound, Reaction, or Publication
  resolution behave — verified by the full pre-existing
  `tests/entity_resolution/` suite continuing to pass unmodified alongside
  the new Protein tests.
- `app.claim_generation.mapping.NormalizationLookups.protein` already
  existed (added when `app.normalization.protein` was built) and required
  no change; Entity Resolution's Protein branch reuses it directly.

`docs/10_claim_generation_contract.md` is updated with a one-line note
(§7) that Protein mention enrichment is now available through Entity
Resolution's UniProt path — Claim Generation's own behavior, contract, and
test suite are otherwise unchanged by this increment (see §7 of this
document and the diff itself for confirmation).

---

## 15. `SourceType` / schema changes

None. `SourceType.UNIPROT` already existed in `app/models/enums.py` before
this increment began; no ORM, migration, or enum change was required or
made.

---

## 16. Error model

No new exception types were introduced. `UniProtConnector` raises the
existing `app.connectors.exceptions` hierarchy exactly as SGD/KEGG/PubMed
do:

| Error | When |
|---|---|
| `ConnectorParseError` | A search or fetch response is not the expected JSON shape (not a dict, missing `results`, missing `primaryAccession`, etc.). |
| `ConnectorHTTPError` | Any non-404 HTTP error status from a fetch or search call. |
| `ConnectorNetworkError` | A transport-level failure (handled/retried by the shared `ConnectorHttpClient` before ever reaching connector code). |

A 404 on `fetch()` is not an error — it returns `None`, exactly like SGD's
`fetch()`.

---

## 17. Testing summary

- `tests/connectors/test_uniprot.py` (32 tests): source attribute; exact
  fetch behavior (accession/name/reviewed/gene/EC/organism/cross-reference
  parsing, cross-reference narrowing, 404 vs. failure, malformed response
  rejection); search behavior (query construction, organism filter, limit,
  empty results, malformed shapes, failure vs. empty distinction); shared
  retry/rate-limit/cache behavior via `ConnectorHttpClient`; isoform
  preservation; ID Mapping deferral.
- `tests/normalization/test_protein.py` (11 new tests, 57 total):
  `protein_identity_from_uniprot` field mapping, `gene_id` always `None`,
  EC-number policy (zero/one/multiple), isoform preservation, successful
  `MATCHED` normalization through the existing unmodified
  `normalize_protein`, and a structural check that the adapter never
  references Gene normalization.
- `tests/entity_resolution/test_adapters.py` (+5 tests):
  `resolve_protein_via_uniprot` matched flow, organism-context query
  construction, skipping an unfetchable hit, multiple candidates
  preserved, gene-name non-inference.
- `tests/entity_resolution/test_resolver.py` (+11 tests): unsupported
  without a connector, `UNRESOLVED` with zero connector calls when
  `organism_id` is missing, `RESOLVED`/`NEW_CANDIDATE`/`CONFLICTED`
  outcomes, same-UUID collapse to `RESOLVED`, reviewed/unreviewed both
  preserved, `SOURCE_FAILURE`, `NO_CANDIDATE`, and a structural safety
  check that the Protein path never references Gene/Reaction
  normalization, the ID Mapping API, or isoform stripping.

All pre-existing tests in every one of these files continue to pass
unmodified.

---

## 18. Known limitations / deferred work

- No live verification of the UniProtKB JSON response shape was performed
  this session — field names are drawn from documented UniProtKB REST API
  conventions, mirroring the same disclosed caveat already carried by
  `app/connectors/kegg.py`. `Settings.uniprot_base_url` has no default for
  exactly this reason.
- ID Mapping, UniRef, UniParc, and sequence-similarity/BLAST lookups
  remain unimplemented (§13, §2).
- Gene↔Protein reconciliation and `ReactionEnzyme` inference from UniProt
  cross-references remain open architecture questions, not resolved here
  (§11).
- Protein persistence (writing a `Protein` row from a `NEW`/`UNRESOLVED`
  UniProt candidate) is not implemented — this increment produces
  `IdentifierCandidate`s only, exactly like every other Entity Resolution
  kind.
