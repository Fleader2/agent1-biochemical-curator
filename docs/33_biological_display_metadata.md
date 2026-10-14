# Biological Display Metadata (Agent 1 → Agent 2 Contract, `AGENT1_CONTRACT_VERSION` `"1.7"`)

## What changed

`Agent1CuratedKnowledgeView` gained two new, additive, raw-ORM-row fields:

- `genes: tuple[Gene, ...]`
- `proteins: tuple[Protein, ...]`

Both tables already existed and were already queried into `Agent1KnowledgePackage` by
`app.agent1.service.get_agent1_knowledge_package` — this increment only exposes that
already-curated data one layer further, in `app.agent1.export.get_agent1_curated_knowledge_view`,
passed through unfiltered exactly like `compartments`/`compounds`/`reactions` already are:
structural/schema records, not claims, with no `Claim`/`CurationState` column of their own to
filter by.

No new query logic. No gene/protein identity inferred from free text. No change to any
existing field.

## Why

Agent 2 (the Antimony Builder) generates Antimony symbols for reactions and kinetic
parameters. Without gene/protein context, a reaction with no curated name falls back to its
raw internal id — unreadable. Agent 1 already curates exactly the data needed to do better
(`Gene.symbol`/`.systematic_name`, `Protein.name`/`.ec_number`), reachable from a reaction via
the existing `ReactionEnzyme.protein_id`/`.complex_id` association — it was simply never
exposed in the handoff.

## Ownership boundary

Agent 1 owns curated biological identity and naming metadata (which gene, which protein,
which EC number a reaction/complex is associated with — all already-curated facts). Agent 2
owns Antimony symbol generation (the naming *preference order*, sanitization, collision
resolution, and the final rendered symbol). This increment moves no logic across that
boundary: Agent 1 exposes facts; Agent 2 decides how to render them.

## Fields exposed

`Gene` (as already defined in `app/models/gene.py`): `id`, `organism_id`, `symbol`,
`systematic_name`, `name`, `description`, `sgd_id`, `ncbi_gene_id`, `uniprot_id`,
`kegg_gene_id`, `chromosome`, `aliases_json`. `symbol`/`systematic_name` are nullable — not
every gene is guaranteed to carry one (Agent 2 must not assume either is always populated).

`Protein` (`app/models/protein.py`): `id`, `gene_id` (nullable), `organism_id`, `name`
(non-null), `uniprot_id`, `ec_number`, `subunit_state`, `localization_consensus`, `notes`.

`Reaction.ec_number` already existed on the raw `Reaction` row and was already passed
through unreshaped in the handoff — no change needed on this side for that field; see the
sibling `agent2-antimony-builder` repository's own contract doc for how Agent 2's local
mirror type now also carries it.

## Not in scope for this increment

- `EnzymeComplex`/`EnzymeComplexMember` (multi-subunit complex membership) are **not**
  queried into `Agent1KnowledgePackage` at all yet — adding that query is a separate,
  future increment, not exposed here. A reaction catalyzed by a complex (`ReactionEnzyme
  .complex_id` set) has no corresponding entry in the new `proteins`/`genes` tuples; Agent 2
  falls back to its existing lower-tier naming for that association.
- No new curation, no gene/protein identity resolved from a reaction/EC number that wasn't
  already linked via a curated `ReactionEnzyme`/`Protein.gene_id` relationship.
