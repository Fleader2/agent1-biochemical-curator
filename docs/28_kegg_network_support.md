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
directed edges. Handles linear chains, branches, grouped-upstream elements, and nested grouping;
preserves any unrecognized KEGG operator verbatim; reports a structurally invalid expression as
`incomplete=True` rather than ever inventing a speculative edge. See that module's own docstring
for the full grammar and the deliberately minimal operator-to-interaction-type mapping (only
`->` -> `UNKNOWN` and `+` -> `COMPLEX_FORMATION` are asserted; every other operator is `UNKNOWN`
with its own literal text preserved on `raw_operator`).

## 6. KEGG connector

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

## 7. Persistence

`app.persistence.network.persist_network` -- follows this package's own established
`persist_*` shape exactly (`MATCHED` -> `REUSED_EXISTING`, `NEW` -> `CREATED`/`FAILED`,
`AMBIGUOUS`/`CONFLICTED` -> `REQUIRES_REVIEW`, `UNRESOLVED` -> `NO_ACTION`). A new network's own
topology (nodes, edges, perturbations, pathway/disease/publication associations) is created as
one atomic unit inside a `SAVEPOINT`, mirroring `persist_reaction`'s own reaction-plus-
participants atomicity. On `MATCHED` (reuse), children are never touched at all -- see
`docs/07_normalization_design.md`'s own "Idempotency" subsection for the full rationale.

## 8. Idempotency

Re-importing the identical KEGG NETWORK record creates no duplicates: one `Network` row (unique
`(source_type, external_id)`), the same logical set of `NetworkNode` rows (unique
`(network_id, raw_token)` when `raw_token` is not null), and the same logical set of
`NetworkEdge` rows (two partial unique indexes together cover both the "`raw_operator` present"
and "`raw_operator` null" cases, since SQL treats every `NULL` as distinct from every other
`NULL` and a single composite unique index cannot do this alone).

## 9. Provenance

Uses the existing `app.persistence.provenance` framework (`attach_source_cross_reference`,
`record_external_record`, `ExternalRecordProvenance`) exactly as every other `persist_*` module
does -- no second provenance framework. `entity_type="network"`.

## 10. Export

`Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView` (`app.agent1.types`) gain
`networks: tuple[CuratedNetwork, ...]` (plus `CuratedNetworkNode`/`CuratedNetworkEdge`/
`CuratedNetworkPerturbation`), reshaped field-for-field by `app.agent1.export`, mirroring the
same "exists = curated" policy already applied to `reactions`/`compounds`/`compartments`: a
network carries no `Claim`/`CurationState`-based human-review gate separate from its own
`curation_state` column (which, like `Reaction.curation_state`, is informational on the curated
view, never a filter). `AGENT1_CONTRACT_VERSION` bumped `1.5` -> `1.6` (additive field).

## 11. Testing

`tests/connectors/test_kegg_network_topology.py` (topology parsing), `tests/connectors/
test_kegg.py` (NETWORK flat-file fetch/normalize/dispatch), `tests/normalization/
test_network.py` (identity + node mapping), `tests/persistence/test_network.py` (creation,
reuse, idempotency, provenance), `tests/database/test_group_j_models.py` (schema/integrity:
cascade delete, duplicate-edge rejection, cross-network-edge prevention, `RESTRICT` on a
referenced `Reaction`, read-only node mapping).

## 12. Known limitations

- Node mapping only resolves `GENE`/`COMPOUND`/`REACTION` nodes (via an existing, global,
  exact-match KEGG-id lookup). `PROTEIN`/`COMPLEX` nodes are always `UNRESOLVED` in this
  increment: `Protein` has no `kegg_gene_id`-equivalent column, and `EnzymeComplex` has no KEGG
  identifier column at all, so there is no deterministic lookup to perform without fabricating
  one.
- Only two topology operators (`->`, `+`) are confidently classified into a specific
  `NetworkEdgeInteractionType`. Every other KEGG operator variant (including ones that may mean
  activation/inhibition/expression specifically) is preserved verbatim but classified `UNKNOWN`
  until a future increment can confirm real operator-to-semantics evidence.
- Re-importing a network whose own upstream KEGG record has genuinely changed since it was first
  imported (a new node/edge added, one removed) is not reconciled -- `MATCHED` never touches
  existing children. Re-running the full import pipeline for a changed record today requires a
  separate, explicit update path this increment does not add.
- `network_pathway`/`network_disease` store KEGG's own identifiers directly (no Agent 1
  `Pathway`/`Disease` entity exists to link to). `network_publication` resolves to an existing
  `Publication` row only when the reference's own PMID is already present; it never invents a
  publication-resolution strategy beyond what `app.persistence.publication` already provides.

## 13. Final architectural rule

Network import adds a topology layer. It never edits, deletes, or reinterprets an existing
`Gene`/`Protein`/`Compound`/`Reaction`/`EnzymeComplex`/`Publication` row, and it never becomes a
second source of truth for biochemical transformations that the reaction-centric schema already
owns.
