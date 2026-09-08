# Agent 1.x Enzyme Regulatory States, Allostery, Modification, and State-Specific Kinetics Contract

## 1. Purpose

This document defines Agent 1.x Increment B: a structured, evidence-backed
representation of enzyme regulatory states (post-translational
modification and allostery) and their state-specific kinetic properties,
so Agent 2 can later represent modified or ligand-bound enzyme forms as
distinct model species with distinct parameter sets. Agent 1 v1
(`docs/23_agent1_v1_scope_and_completion.md`) remains the frozen baseline
this increment extends, never rewrites.

## 2. Scope boundary

```
Protein / EnzymeComplex
        -> EnzymeState
        -> EnzymeModification / AllostericInteraction (defining facts)
        -> EnzymeStateTransition (state-to-state, optionally reaction-linked)
        -> state-specific KineticMeasurement
        -> Agent1CuratedKnowledgeView
        -> Agent 2 (future): distinct model species + state-specific parameters
```

Agent 1 curates the biological facts: that a state exists, what defines
it, and what was measured for it. Agent 2 decides how those curated
states become model species, reactions, kinetic laws, and parameters.
Agent 4 later calibrates parameters where experimental fitting is
required. This increment implements none of the Agent 2/4 side --
contracts and documentation only for those (§§24-26).

## 3. Regulatory mechanisms

Two mechanisms are given first-class representation: covalent/post-
translational modification (phosphorylation, acetylation, cysteinylation,
ubiquitination, methylation) and allostery (ligand binding with a
qualitative regulatory effect). Both were previously only representable,
at best, as a `RegulatoryInteraction.effect` value (`PHOSPHORYLATION`/
`DEPHOSPHORYLATION`) describing a *regulatory relationship* between two
entities -- never a distinguishable *state* of the modified protein
itself, and with no residue, site, ligand, or state-specific kinetic
information at all (§5's confirmed gap).

## 4. Protein/complex identity versus enzyme state

`Protein`/`EnzymeComplex` identity is completely unchanged by this
increment: no new row is ever created on either table to represent a
modified or bound form. `EnzymeState` is a new, first-class layer:
exactly one of `protein_id`/`complex_id` is set (a database `CHECK`
constraint, `ck_enzyme_state_exactly_one_target`, mirroring
`reaction_enzyme`'s identical precedent), naming the underlying
macromolecule a state describes. A protein can have arbitrarily many
`EnzymeState` rows; the protein row itself never multiplies.

## 5. EnzymeState

`app.models.enzyme_state.EnzymeState`. Fields: `id`, `protein_id`,
`complex_id`, `state_type`, `state_label` (free text, non-identity),
`compartment_id`, `active_state`, `identity_key` (see §16),
`source`/`source_id` (connector-record idempotency, mirroring
`kinetic_measurement`), `publication_id`/`evidence_id` (provenance,
mirroring `kinetic_measurement`'s identical pair), `notes`,
`created_at`/`updated_at`. **Confirmed gap this table closes**: before
this increment, nothing in the schema could distinguish "Protein E" from
"phosphorylated Protein E," and `ReactionEnzyme`/`KineticMeasurement`
could only ever reference `protein_id`/`complex_id` generally -- never a
specific regulatory state (verified directly by inspection of
`app/models/reaction.py`/`app/models/kinetic_measurement.py` before any
change was made).

## 6. EnzymeStateType

```text
BASE
MODIFIED
ALLOSTERICALLY_BOUND
OTHER
```

A native PostgreSQL enum (`app/models/enums.py`), matching this
repository's established convention for closed, small vocabularies
(`RegulatoryEffect`, `ReactionParticipantRole`, ...) rather than
`VARCHAR` + `CHECK`. Describes the curator's judgment of a state's
predominant nature -- never a computed union of "has modifications" and
"has ligands" (a state can carry both regardless of its `state_type`).
`COMPLEX_STATE` was deliberately not added: `EnzymeState.complex_id`
already supports a complex parent without redundant semantics.

## 7. EnzymeModification

`app.models.enzyme_state.EnzymeModification`. Fields: `id`,
`enzyme_state_id` (`ON DELETE CASCADE` -- no independent scientific
meaning apart from its state, mirroring `enzyme_complex_member`),
`modification_type`, `residue`, `residue_position`, `site_label`,
`modifying_compound_id`, `stoichiometry`, `identity_key`,
`source`/`source_id`, `publication_id`/`evidence_id`, `notes`,
timestamps. A state may have multiple modifications (doubly
phosphorylated, or phosphorylated + acetylated) -- each is its own row;
`EnzymeState` embeds the complete set only for identity computation
(§16), never by flattening them into `EnzymeState`'s own columns.

## 8. ModificationType

```text
PHOSPHORYLATION
ACETYLATION
CYSTEINYLATION
UBIQUITINATION
METHYLATION
OTHER
```

`CYSTEINYLATION` means specifically *S-cysteinylation*: the covalent,
disulfide-linked addition of a free cysteine to a protein cysteine
residue -- one specific, well-defined modification. No prior use of the
term existed anywhere in this project before this increment (verified by
a full-repository search), so this definition is not a reinterpretation
of an existing convention. Deliberately **not** a catch-all: S-
nitrosylation, S-glutathionylation, sulfenylation, and inter/intra-protein
disulfide-bond formation are each chemically distinct events and would
each need their own future `ModificationType` member if curated -- never
folded into `CYSTEINYLATION` without the source explicitly stating that
specific modification.

## 9. AllostericInteraction

`app.models.enzyme_state.AllostericInteraction`. Fields: `id`,
`enzyme_state_id` (`ON DELETE CASCADE`), `ligand_compound_id` (**required,
never nullable** -- a ligand that cannot be resolved to a canonical
`Compound` is not representable here at all, never identified by
free-text name alone), `effect`, `site_label`, `mechanism` (free text),
`identity_key`, `source`/`source_id`, `publication_id`/`evidence_id`,
`notes`, timestamps.

## 10. AllostericEffect

```text
ACTIVATOR
INHIBITOR
MODULATOR
UNKNOWN
```

Never inferred from a kinetic value (a lower `Km` does not by itself
imply `ACTIVATOR`) -- set only when a source explicitly states the
effect, or `UNKNOWN` when a binding relationship is curated but its
effect is not stated. Never reduced to a binary on/off: `MODULATOR`
exists for a stated-but-not-purely-activating-or-inhibiting effect.

## 11. Modification reactions

A modification transition (e.g. `E + ATP -> E-P + ADP`) may correspond to
a real biochemical reaction. `EnzymeStateTransition.reaction_id`
references an already-curated `Reaction` when the source knowledge
resolves it through the existing reaction-curation pipeline -- this
increment never creates a `Reaction` row on a transition's behalf, and
never invents ATP/ADP/Pi (or any other) participant. A transition with no
linked reaction is still fully valid; the biochemical reaction, if it
exists, simply is not yet curated.

## 12. ReactionEnzyme state-specific catalysis

`app.models.reaction.ReactionEnzyme` gained `enzyme_state_id`
(migration `0014_enzyme_regulatory_states`).
`ck_reaction_enzyme_exactly_one_target` was widened from a 2-way
(`protein_id`/`complex_id`) to a 3-way exactly-one-of
(`protein_id`/`complex_id`/`enzyme_state_id`) `CHECK` -- the 2-way rule is
a strict special case, so no pre-existing row can violate the new
constraint. A third partial unique index,
`uq_reaction_enzyme_reaction_id_enzyme_state_id`, mirrors the existing two.
`app.normalization.reaction_enzyme.ReactionEnzymeIdentity` and
`app.persistence.reaction_enzyme` were both extended identically (three
disjoint identity spaces, never bridged -- see that module's own
docstring).

## 13. State-specific kinetic measurements

`app.models.kinetic_measurement.KineticMeasurement` gained
`enzyme_state_id` (nullable, never required). This is the central
deliverable: a measurement can now say "phosphorylated E, kcat = 32/s"
distinctly from "Protein E, kcat = 4.5/s," or "AMP-bound E, Km = 0.4 mM."
Every existing kinetic field, and full source provenance (§20), is
preserved unchanged.

## 14. Kinetic applicability

A measurement attached to an `EnzymeState` applies **only** to that
state -- never treated as applicable to the parent protein/complex
generally, nor to any other state of it, unless a later, explicit Agent 2
modeling policy states otherwise. This distinction is preserved verbatim
into the Agent 1 handoff (`CuratedKineticMeasurement.enzyme_state_id`,
§23) -- Agent 1 never collapses or generalizes it.

## 15. Binding fact versus kinetic consequence

Mandatory distinction, upheld structurally: `AllostericInteraction`
records the *qualitative regulatory fact* ("ligand X binds/modulates
state E, effect = ACTIVATOR") and carries no numeric kinetic column at
all (verified: no `value`/`unit`/`km`/`kcat` field exists on that table).
The *quantitative kinetic consequence* ("E bound to X has Km = ...") is a
separate, state-specific `KineticMeasurement` row, linked only by sharing
the same `enzyme_state_id` -- never merged into one row, never inferred
from the other.

## 16. State identity

`EnzymeState.identity_key` is a deterministic SHA-256 digest (prefix
`"es-v1:"`, mirroring `knowledge_gap.identity_key`'s own versioned-digest
precedent -- never Python's built-in `hash()`) over: `parent_type`,
`parent_id`, `state_type`, `compartment_id`, and the complete
canonicalized modification/allosteric-ligand set the state was created
with (`app.normalization.enzyme_state.compute_enzyme_state_identity_key`).
`state_label`/`active_state`/`notes`/`source`/`source_id`/
`publication_id`/`evidence_id` never participate -- metadata, not
identity. **Fixed at creation, never recomputed automatically**: a later
`EnzymeModification`/`AllostericInteraction` attached to an
already-existing state (via `persist_enzyme_modification`/
`persist_allosteric_interaction`) does not retroactively change that
state's own `identity_key` -- see §23's caller-responsibility note.
Phosphorylation at Ser15 and at Ser42 never collapse (different
`residue_position`); one phosphate and two never collapse when
multiplicity (`stoichiometry`) is known and differs.

## 17. Modification-set canonicalization

Each `ModificationIdentity`/`AllostericLigandIdentity` is rendered to its
own canonical, sorted-keys JSON dict, and the resulting list is sorted by
that dict's own canonical string representation before hashing -- a
total, deterministic order requiring no custom comparator and no
`hash()`. Phosphorylation-then-acetylation and acetylation-then-
phosphorylation therefore always produce the identical `identity_key`.

## 18. Allosteric-state identity

The canonical design (Increment B instructions, Step 22):

```
EnzymeState:
    parent = E
    state_type = ALLOSTERICALLY_BOUND

AllostericInteraction:
    ligand = AMP
    effect = ACTIVATOR
```

`EnzymeStateIdentity.allosteric_ligands` folds the intended ligand set
into the state's own `identity_key` (§16) exactly as modifications do, so
an AMP-bound state and an ATP-bound state of the identical protein are
always distinct states. There is exactly one representation of a
ligand-bound state -- never two incompatible ones.

## 19. Experimental-condition dependence

State-specific kinetic measurements follow the same "never collapse"
policy `KineticMeasurement` already established for non-state-specific
ones: multiple independent measurements for the same state, reaction, and
parameter type, under different pH/temperature/organism/strain/assay
conditions/publications, all persist independently (§13's own row-level
pH/temperature/strain columns are unchanged). Agent 2 chooses
applicability among them later -- Agent 1 never averages or picks a
"canonical" value.

## 20. Source provenance

All four new tables carry the identical two-part provenance shape
`kinetic_measurement` already established:
`source`/`source_id` (a partial unique index, `(source, source_id)` where
both are non-null, for connector-record idempotency) and
`publication_id`/`evidence_id` (linking into the existing
`Publication`/`Evidence` -> `Claim` chain when a row was curated from
literature). No parallel scientific-truth system was created --
`RegulatoryInteraction`'s own `claim_id`-only pattern was considered and
rejected in favor of the strictly more general `kinetic_measurement`
pattern (a superset: it also supports connector-native provenance, which
`RegulatoryInteraction` does not).

## 21. Persistence/idempotency

`app.persistence.enzyme_state` provides `persist_enzyme_state`,
`persist_enzyme_modification`, `persist_allosteric_interaction`,
`persist_enzyme_state_transition` -- one function per table, each
following `app.persistence.knowledge_gap`'s SAVEPOINT/IntegrityError-
recheck convention exactly: identity-key lookup first, `session.begin_nested()`
around the insert, a raced `IntegrityError` resolved by re-querying by
`identity_key`, never a raw exception escaping as though it were a
scientific decision. No function commits or rolls back the session it is
given. Persisting the identical curated content twice always reuses the
existing row and never rewrites its columns; two rows differing in any
identity-relevant field always persist independently.

## 22. Agent1KnowledgePackage

Gained `enzyme_states: tuple[EnzymeState, ...]`,
`enzyme_modifications: tuple[EnzymeModification, ...]`,
`allosteric_interactions: tuple[AllostericInteraction, ...]`,
`enzyme_state_transitions: tuple[EnzymeStateTransition, ...]` -- raw ORM
rows, mirroring every other category on this type. Scoped by `organism_id`
via the already-scoped protein set (and, for a complex-targeted state, a
direct `EnzymeComplex.organism_id` query -- see `app.agent1.service`'s
own module docstring for the disclosed `EnzymeComplex`-exposure gap this
does not close, §30). `reaction_enzyme_associations`/`kinetic_measurements`
need no field changes: both are raw ORM rows that already carry
`enzyme_state_id` for free.

## 23. Agent1CuratedKnowledgeView

Gained the identically-named four fields, holding the new
`CuratedEnzymeState`/`CuratedEnzymeModification`/
`CuratedAllostericInteraction`/`CuratedEnzymeStateTransition` types (pure,
field-for-field reshapings, `app.agent1.export`), plus
`CuratedKineticMeasurement.enzyme_state_id`. None of the four new tables
carries a `Claim`/`CurationState` column, so the same "exists = curated"
policy already applied to `reactions`/`compounds`/`kinetic_measurements`
applies here too -- never gated on `HUMAN_ACCEPTED`. **Caller
responsibility, not enforced by this module**: the modification/ligand set
passed to `EnzymeStateIdentity` at creation time and the individual
`EnzymeModification`/`AllostericInteraction` rows persisted afterward must
describe the same content -- neither the normalization nor the
persistence layer can verify this after the fact (§16, §21).

## 24. Agent 2 handoff

`AGENT1_CONTRACT_VERSION` bumped `"1.1"` -> `"1.2"` (additive fields only,
no existing field reshaped). In `agent2-antimony-builder`: local,
decoupled `CuratedEnzymeState`/`CuratedEnzymeModification`/
`CuratedAllostericInteraction`/`CuratedEnzymeStateTransition` mirrors, an
`enzyme_state_id` field on the local `CuratedKineticMeasurement`, and
`AGENT1_HANDOFF_VERSION` updated to `"1.2"` to match exactly -- no Agent 1
Python import added. See that repository's own
`docs/02_agent1_handoff_contract.md`/`docs/04_core_domain_contracts.md`
for the concise notes this increment added there.

## 25. Agent 2 future species semantics (documented, not implemented)

Agent 2 will eventually map one `CuratedEnzymeState` to a distinct model
species (`E`, `E_P`, `E_Ac`, `E_Cys`, `E_AMP`, ...), each with its own
catalytic associations, kinetic-law applicability, and
`ParameterSpecification`s -- their distinct identity must be preserved,
never collapsed into one generic enzyme species. Agent 2 may represent a
state transition (`EnzymeStateTransition`) as an Antimony reaction (e.g.
`E -> E_P`) or a ligand-binding/release step. **None of this is
implemented by this increment or by `agent2-antimony-builder` today** --
Agent 1 never generates Antimony, and Agent 2's Whole-Network Assembly
(Increment 2) is unmodified except for the minimal additive
attachment described in that repository's own report.

## 26. Agent 4 boundary

A state-specific Agent 1 kinetic measurement may become a state-specific
Agent 2 parameter with `source=CURATED` (or, under an explicit future
modeling adaptation, `LITERATURE_DERIVED`) -- it must never automatically
apply to other enzyme states. `CALIBRATED` remains exclusively reserved
for a value returned by Agent 4's future feedback; nothing in this
increment, in Agent 1 or Agent 2, ever assigns it.

## 27. Existing regulation limitations

`RegulatoryInteraction`'s own pipeline remains exactly as incomplete as
`docs/23_agent1_v1_scope_and_completion.md` §11 already discloses: schema-
ready, not curated end-to-end, no normalization/extraction/claim-
generation pipeline writes it today. This increment does not attempt to
finish that subsystem. What is now first-class, structured regulatory
knowledge (not dependent on `RegulatoryInteraction` at all): enzyme
regulatory states, their modifications, and their allosteric interactions
-- curated and queried entirely through the four new tables.

## 28. Connector support

Investigated: UniProt, BRENDA, KEGG, SABIO-RK, OED, and the existing
literature evidence-extraction pipeline. **Findings, not assumptions**:

* UniProt's connector (`app/connectors/uniprot.py`) parses no PTM/sequence-
  feature data at all today -- `UniProtEntryRecord` has no typed field for
  it (only `raw` retains the complete, unparsed source JSON, which UniProt's
  real API is known to include a `features` array in, but this was not
  independently re-verified live this increment and no adapter reads it).
* BRENDA's connector (`app/connectors/brenda.py`) exposes an `inhibitor`
  qualifier on Ki measurements as a **free-text name**, never resolved to a
  `Compound` id -- `AllostericInteraction.ligand_compound_id` requires a
  resolved compound (§9), so this text cannot safely populate it without a
  compound-normalization step this connector does not perform.
* KEGG, SABIO-RK, and OED connectors expose no PTM, allosteric-regulator,
  or state-specific-kinetics field of any kind (verified against each
  connector's own typed record shapes, §35 of the governing task).
* `app.extraction`/`app.claim_generation` already support free-text
  statements like "phosphorylation of E increases activity" or "AMP
  inhibits E" via `Claim.predicate` (a plain, unconstrained `VARCHAR`) --
  but have no structured mapping from such a claim onto a specific
  `EnzymeState`/`EnzymeModification`/`AllostericInteraction`.

## 29. Minimal connector adapters: none implemented

No adapter was implemented for any connector this increment (Increment B
instructions, Step 36: "Implement adapters only where current connector
data clearly supports them. Do not invent connector data."). Every
connector investigated in §28 either exposes no structured data for these
new tables at all, or exposes it only as unresolved free text that this
increment's own policies (§9, "never identify a ligand solely by free-text
name") forbid using directly. This is a disclosed scope decision, not an
oversight.

## 30. Testing

`tests/database/test_group_h_models.py` (schema: tables, XOR/`CHECK`
constraints, enum vocabularies, indexes, cascade/restrict behavior),
`tests/normalization/test_enzyme_state.py` (identity policy: base vs.
modified vs. ligand-bound, site/multiplicity distinction, ordering
independence, protein-vs-complex, modification/allostery/transition
construction validation), `tests/persistence/test_enzyme_state.py`
(create/reuse/idempotency/concurrency/no-commit/SAVEPOINT, state-specific
kinetics), `tests/agent1/test_outputs.py` (package/curated-view exposure
of all four new categories plus state-specific `ReactionEnzyme`/
`KineticMeasurement`), and extensions to
`tests/normalization/test_reaction_enzyme.py`/
`tests/persistence/test_reaction_enzyme.py` (the widened 3-way target).
Full repository suite green, including `pytest -W error::sqlalchemy.exc.SAWarning`
and a full migration upgrade/downgrade/upgrade round trip.

## 31. Known limitations

* `EnzymeComplex` is not exposed as a top-level `Agent1KnowledgePackage`
  field (pre-existing, not introduced by this increment) -- a
  complex-targeted `EnzymeState` is still scoped correctly internally, but
  a consumer of the package cannot otherwise read complex details from it.
* No connector currently supplies structured PTM/allostery/state-specific-
  kinetics data (§28-29) -- every row in these four tables today must come
  from manual curation or a future literature-extraction mapping step.
* `EnzymeStateIdentity`'s embedded modification/ligand set and the
  individually-persisted `EnzymeModification`/`AllostericInteraction` rows
  are not cross-verified for consistency by this module (§23) -- a caller
  responsibility.
* A BRENDA-reported inhibitor name cannot yet become a curated
  `AllostericInteraction` without a compound-normalization step this
  increment does not add.

## 32. Final architectural rule

> Protein and enzyme-complex identity define the underlying macromolecule.
>
> EnzymeState defines a distinct regulated biochemical form of that
> macromolecule.
>
> Modification and allostery are curated biological facts, while their
> quantitative kinetic consequences are preserved as state-specific
> kinetic measurements.
>
> Agent 2 may represent distinct enzyme states as distinct model species,
> but Agent 1 never invents those model representations.
