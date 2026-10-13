# Agent 1.x KEGG NETWORK Support Contract

## 1. Purpose

Import KEGG NETWORK records and convert their topology into explicit, queryable network nodes
and edges while preserving the original KEGG representation and provenance. KEGG NETWORK sits
above the existing reaction-centric biochemical schema as a higher-level topology layer linking
existing normalized entities -- it never replaces that schema.

## 2. Architectural principle

> A `Network` represents biological or regulatory topology. A `Reaction` represents a
> biochemical transformation. Network edges must not be treated as biochemical reactions unless
> explicitly linked to a normalized `Reaction` entity.

Network import is a knowledge-curation step, not an executable-model-generation step. No code in
this increment translates a network into Antimony, PySB, SBML, or an ODE system -- KEGG edges
generally do not provide sufficient kinetic information for that transformation, and this
increment never invents one to fill the gap.

## 3. Schema

Seven new tables (`docs/02_database_schema.md`: "Table: network" through "Table:
network_publication"), five new native enums (`NetworkType`, `NetworkNodeType`,
`NetworkNodeMappingStatus`, `NetworkEdgeInteractionType`, `NetworkPerturbationType`), migration
`0019_kegg_network`:

```text
Network
 ├── NetworkNode
 │    └── optional link to one of: Gene / Protein / EnzymeComplex / Compound / Reaction
 ├── NetworkEdge
 │    ├── source NetworkNode
 │    └── target NetworkNode
 ├── NetworkPerturbation
 │    └── optional target NetworkNode
 ├── NetworkPathway   (KEGG pathway id -- no Agent 1 Pathway entity exists)
 ├── NetworkDisease   (KEGG disease id -- no Agent 1 Disease entity exists)
 └── NetworkPublication (reuses the existing Publication table)
```

`network_node`'s own FK columns (`gene_id`/`protein_id`/`complex_id`/`compound_id`/
`reaction_id`) enforce "at most one set" at the application level
(`app.persistence.network`), not via a database `CHECK` across five nullable columns --
consistent with this schema's existing preference for application-level validation where a rigid
multi-column `CHECK` is not already an established pattern.

`network_edge` has no timestamp columns (same precedent as `reaction_participant`/
`reaction_enzyme`); `network`/`network_node`/`network_perturbation` have both, matching every
other primary/lifecycle entity in this schema.

## 4. Identity and node-mapping normalization

`app.normalization.network` (`normalize_network`, `map_network_node`) -- see
`docs/07_normalization_design.md`, "Finalized Normalization Policy: Network" for the complete
policy: identity hierarchy, node-mapping lookup support (`GENE`/`COMPOUND`/`REACTION` only),
conflict/idempotency handling.

## 5. Topology parsing

`app.connectors.kegg_network_topology.parse_topology` -- a pure, deterministic tokenizer/parser
(no fragile string splitting) from a `DEFINITION`/`EXPANDED` expression to an explicit tuple of
directed edges. Handles linear chains, branches, grouped-upstream elements (comma-separated
alternatives or plus-separated complex members), and nested grouping; preserves any unrecognized
or not-yet-promoted KEGG operator verbatim on `raw_operator`; reports a structurally invalid
expression as `incomplete=True` rather than ever inventing a speculative edge. See the module's
own docstring for the full grammar, and section 6 below ("Operator semantics") for the evidence
behind each operator's classification.

## 6. Operator semantics

Added by the "KEGG NETWORK operator-semantics increment." Establishes an evidence-backed mapping
from real KEGG NETWORK operators (as used in `DEFINITION`/`EXPANDED`) to Agent 1
`NetworkEdgeInteractionType` values, replacing the prior increment's deliberately minimal
`->`/`+`-only mapping. The table lives as a single centralized dict,
`_KNOWN_OPERATOR_INTERACTION_TYPES` in `app.connectors.kegg_network_topology`, not scattered
through parser logic.

**Primary source.** KEGG's own NETWORK notation reference:
<https://www.genome.jp/kegg/document/help_network.html>. KEGG splits its own operator table into
two sections -- "Signaling Network operators" and "Metabolic Network operators" -- and gives one
operator (`->`) two different documented meanings depending on which section applies. An operator
is promoted out of `UNKNOWN` here only when (a) KEGG's own documentation states a single,
unambiguous meaning, and (b) that meaning maps cleanly onto one of this schema's existing
`NetworkEdgeInteractionType` members. Visual similarity or biological intuition is never used as
a substitute for either condition.

| Operator | KEGG-documented meaning | Real-record evidence | Agent 1 mapping | Confidence |
|---|---|---|---|---|
| `->` | Signaling: "Activation". Metabolic: "Enzyme-product relation". | N00888 (`XDH -> Urate`, metabolic) | `UNKNOWN` | Not promoted -- genuinely context-dependent; the parser has no signal for which network type an expression came from, so guessing would silently pick one of two different real meanings. |
| `-\|` | "Inhibition (single-step)" | N00270 (`Core -\| (STAT1+STAT2+IRF9)`) | `INHIBITION` | High -- documented and corroborated by the record's own reference text. |
| `=>` | "Expression" | N00062 (`GLI => (GLI1,PTCH,HHIP)`) | `EXPRESSION` | High -- documented and corroborated by a real target-gene-induction record. |
| `=\|` | "Repression" | None found in this increment's research sample | `REPRESSION` | Medium -- promoted on KEGG's own documentation alone; disclosed as lacking a real-record example. |
| `--` | Metabolic: "Substrate-enzyme relation". Also used generically for physical binding. | N00888 (`Hypoxanthine -- XDH`) | `BINDING` | High -- documented and corroborated. |
| `+` (inside a group) | Complex member grouping -- members of a `(...)` group joined by `+` (rather than `,`) are components of one complex. | N00270 (`(STAT1+STAT2+IRF9)`), N00017 (`(SUFU+KIF7)`) | `COMPLEX_FORMATION`, plus new internal edges between adjacent group members (see below) | High -- documented and corroborated by multiple real records. |
| `==` | "Complex formation" | None found in this increment's research sample | `COMPLEX_FORMATION` | Medium -- promoted on documentation alone; KEGG documents it as meaning the same thing as in-group `+`. |
| `//` | "Missing interaction/reaction" | N00075 (`RB1* // E2F`) | `UNKNOWN` | Real and documented, but has no matching `NetworkEdgeInteractionType` member -- left `UNKNOWN` rather than force-fit. |
| `-^` | "Gain of function" / "enhanced reaction" | None found in this increment's research sample | `UNKNOWN` | Documented, but same no-matching-member reasoning as `//`. |
| `>>` | "Enzyme-enzyme relation of successive reactions" | N00890 (`MOCS2 >> GPHN`) | `UNKNOWN` | Real and documented, but same no-matching-member reasoning as `//`. |

**Target categories evaluated, per the task's own minimum list:**

- `ACTIVATION` -- not promoted. The only documented operator that could mean this (`->`) is the
  same context-dependent operator described above; promoting it for `ACTIVATION` would require
  guessing the network type.
- `INHIBITION` -- promoted (`-\|`).
- `EXPRESSION` -- promoted (`=>`).
- `REPRESSION` -- promoted (`=\|`).
- `BINDING` -- promoted (`--`).
- `COMPLEX_FORMATION` -- promoted (`+` in-group, `==`).
- `REACTION` -- not promoted. No KEGG-documented operator maps distinctly to "reaction" as
  opposed to "enzyme-product relation" (itself folded into the ambiguous `->`).
- `TRANSPORT` -- not promoted. No KEGG-documented NETWORK operator notation for transport was
  found anywhere in this increment's research.

**`+`-group internal edges.** Real KEGG usage of `+` is always *inside* a parenthesized group
(`(A+B+C)`), never as a bare top-level chain operator between two full chain steps. A `+`-group
contributes its full flattened leaf set to the *outer* edge exactly like a `,`-group (unchanged
behavior), and additionally produces new internal `COMPLEX_FORMATION` edges between each pair of
adjacent group members (recursively, at any nesting depth), since without this the `+` ->
`COMPLEX_FORMATION` mapping would never actually fire on real data. See
`_collect_internal_edges` in `kegg_network_topology.py`.

**Tokenizer fixes required to recognize these operators on real data** (structural
grammar/tokenization only -- no new topology-structure handling for chains, branches, or groups):

- `*` (trailing variant-marker suffix, e.g. `SMO*`) is a term character, not an operator.
  Previously caused `SMO* -> X` to fail with a spurious one-character operator. Real record:
  N00017.
- A single `/` glued between identifier characters (KEGG's compact gene-family shorthand, e.g.
  `TRAF2/5`, `BMP2/4`) stays part of one term; a doubled `//` still tokenizes as the real `//`
  operator. Previously, the pre-existing `/`-in-term rule silently swallowed `//` into a bogus
  term token. Fixed via one-character lookahead (`_is_term_char`/`_slash_is_inside_term`). Real
  records: N00151 (`TRAF2/5`), N00075 (`RB1* // E2F`).
- A group may use `,` or `+` as its member separator, but not both in the same group (raises a
  non-speculative `incomplete=True` on mixing). Previously, only `,` was accepted, so any
  `+`-grouped real record (N00017, N00062, N00151, N00270) failed to parse at all.

**`raw_operator` preservation.** Every parsed edge preserves the exact KEGG operator text on
`raw_operator` regardless of its normalized `interaction_type` -- including `UNKNOWN`-classified
operators (`->`, `//`, `-^`, `>>`) and the internal `+`-group edges. No operator's literal text is
ever discarded or normalized away.

**Known limitation, disclosed rather than fixed.** Some real metabolic-network `DEFINITION` text
uses parentheses as part of a compound's own display name rather than as topological grouping
(e.g. `Moco(sulfide)`, from N00890). This parser's structural grammar cannot distinguish that case
from genuine grouping without compound-name-aware lookahead, which is a separate, harder
structural problem out of scope for this operator-semantics increment. The parser correctly
reports `incomplete=True` in that case rather than guessing; see
`test_malformed_parenthesized_label_is_incomplete_not_guessed` in
`tests/connectors/test_kegg_network_topology.py`.

## 7. KEGG connector

`app.connectors.kegg.KeggNetworkRecord`/`normalize_network` (connector-level, KEGG-scoped --
distinct from, and upstream of, `app.normalization.network.normalize_network`, the entity-
identity-level function of almost the same name; callers chain
`parse_flat_file` -> `app.connectors.kegg.normalize_network` -> topology parsing ->
`app.normalization.network.normalize_network`/`map_network_node` ->
`app.persistence.network.persist_network`). `KeggConnector.normalize()` already existing
`fetch()`/`parse_flat_file()` work unmodified for NETWORK entries (the generic flat-file parser
is entry-type-agnostic); dispatch to `normalize_network` is by the entry id's own `"N"` prefix,
not by `entry_type` (which, for a NETWORK entry carrying a sub-classification such as
`"N00002  Network  Reference"`, reports the sub-classification itself, not the literal word
`"Network"`).

## 8. Persistence

`app.persistence.network.persist_network` -- follows this package's own established
`persist_*` shape exactly (`MATCHED` -> `REUSED_EXISTING`, `NEW` -> `CREATED`/`FAILED`,
`AMBIGUOUS`/`CONFLICTED` -> `REQUIRES_REVIEW`, `UNRESOLVED` -> `NO_ACTION`). A new network's own
topology (nodes, edges, perturbations, pathway/disease/publication associations) is created as
one atomic unit inside a `SAVEPOINT`, mirroring `persist_reaction`'s own reaction-plus-
participants atomicity. On `MATCHED` (reuse), children are never touched at all -- see
`docs/07_normalization_design.md`'s own "Idempotency" subsection for the full rationale.

## 9. Idempotency

Re-importing the identical KEGG NETWORK record creates no duplicates: one `Network` row (unique
`(source_type, external_id)`), the same logical set of `NetworkNode` rows (unique
`(network_id, raw_token)` when `raw_token` is not null), and the same logical set of
`NetworkEdge` rows (two partial unique indexes together cover both the "`raw_operator` present"
and "`raw_operator` null" cases, since SQL treats every `NULL` as distinct from every other
`NULL` and a single composite unique index cannot do this alone).

## 10. Provenance

Uses the existing `app.persistence.provenance` framework (`attach_source_cross_reference`,
`record_external_record`, `ExternalRecordProvenance`) exactly as every other `persist_*` module
does -- no second provenance framework. `entity_type="network"`.

## 11. Export

`Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView` (`app.agent1.types`) gain
`networks: tuple[CuratedNetwork, ...]` (plus `CuratedNetworkNode`/`CuratedNetworkEdge`/
`CuratedNetworkPerturbation`), reshaped field-for-field by `app.agent1.export`, mirroring the
same "exists = curated" policy already applied to `reactions`/`compounds`/`compartments`: a
network carries no `Claim`/`CurationState`-based human-review gate separate from its own
`curation_state` column (which, like `Reaction.curation_state`, is informational on the curated
view, never a filter). `AGENT1_CONTRACT_VERSION` bumped `1.5` -> `1.6` (additive field).

## 12. Testing

`tests/connectors/test_kegg_network_topology.py` (topology parsing), `tests/connectors/
test_kegg.py` (NETWORK flat-file fetch/normalize/dispatch), `tests/normalization/
test_network.py` (identity + node mapping), `tests/persistence/test_network.py` (creation,
reuse, idempotency, provenance), `tests/database/test_group_j_models.py` (schema/integrity:
cascade delete, duplicate-edge rejection, cross-network-edge prevention, `RESTRICT` on a
referenced `Reaction`, read-only node mapping).

## 13. Known limitations

- Node mapping only resolves `GENE`/`COMPOUND`/`REACTION` nodes (via an existing, global,
  exact-match KEGG-id lookup). `PROTEIN`/`COMPLEX` nodes are always `UNRESOLVED` in this
  increment: `Protein` has no `kegg_gene_id`-equivalent column, and `EnzymeComplex` has no KEGG
  identifier column at all, so there is no deterministic lookup to perform without fabricating
  one.
- Six topology operators (`-\|`, `=>`, `=\|`, `--`, `+` in-group, `==`) are now confidently
  classified into a specific `NetworkEdgeInteractionType` (section 6, "Operator semantics"). `->`
  remains deliberately `UNKNOWN` because KEGG itself documents it with two different meanings
  depending on network type (signaling "Activation" vs. metabolic "Enzyme-product relation"), and
  the parser has no signal for which applies. `//`, `-^`, and `>>` are real, KEGG-documented
  operators that remain `UNKNOWN` because none of them has a matching
  `NetworkEdgeInteractionType` member to map onto. `ACTIVATION`, `REACTION`, and `TRANSPORT` have
  no defensible KEGG NETWORK operator mapping found in this research and remain unreachable via
  this parser. Parenthesized text used as part of a compound's own display name (e.g.
  `Moco(sulfide)`) is indistinguishable from topological grouping without compound-name-aware
  lookahead and is reported `incomplete=True` rather than guessed.
- Re-importing a network whose own upstream KEGG record has genuinely changed since it was first
  imported (a new node/edge added, one removed) is not reconciled -- `MATCHED` never touches
  existing children. Re-running the full import pipeline for a changed record today requires a
  separate, explicit update path this increment does not add.
- `network_pathway`/`network_disease` store KEGG's own identifiers directly (no Agent 1
  `Pathway`/`Disease` entity exists to link to). `network_publication` resolves to an existing
  `Publication` row only when the reference's own PMID is already present; it never invents a
  publication-resolution strategy beyond what `app.persistence.publication` already provides.

## 14. Final architectural rule

Network import adds a topology layer. It never edits, deletes, or reinterprets an existing
`Gene`/`Protein`/`Compound`/`Reaction`/`EnzymeComplex`/`Publication` row, and it never becomes a
second source of truth for biochemical transformations that the reaction-centric schema already
owns.
