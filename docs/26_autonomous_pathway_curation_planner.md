# Agent 1.x Autonomous Pathway Curation Planner Contract

## 1. Purpose

This document defines Agent 1.x Increment C: `app.pathway_curation`, an
orchestration layer that lets Agent 1 accept a single high-level request
-- for example, "Curate the biochemical knowledge required to model fatty
acid biosynthesis in *Saccharomyces cerevisiae*" -- and plan and execute a
bounded, auditable curation run using Agent 1's *existing* connectors,
normalization, persistence, evidence, review, confidence, knowledge-gap,
and export infrastructure. Agent 1 v1
(`docs/23_agent1_v1_scope_and_completion.md`) and Increments A/B
(`docs/24_kinetic_data_curation_and_handoff.md`,
`docs/25_enzyme_regulatory_states_contract.md`) remain the frozen baseline
this increment orchestrates, never rewrites.

This revision (the **pre-commit completion revision**, still part of the
same, still-unreleased Increment C) closes the structural gaps an initial
completion pass left open: reaction participants are now resolved,
compounds and compartments are wired end to end, catalyst associations are
conservatively created, Open Enzyme Database is wired alongside SABIO-RK,
and a new, explicit `Agent2ReadinessAssessment` answers the question the
rest of this document exists to make precise: **is this export actually
usable by Agent 2, or does it merely look plausible?**

## 2. Scope boundary

```
High-level request (organism + biological process)
        -> PathwayCurationPlan (read-only, deterministic backbone)
        -> execute_pathway_curation (bounded, iterative)
              -> KEGG (pathway/reaction discovery, equation parsing)
              -> KEGG (participant compound expansion)
              -> SGD / UniProt (gene/protein resolution)
              -> conservative catalyst association (ReactionEnzyme)
              -> PubMed (literature)
              -> SABIO-RK + Open Enzyme Database (kinetics)
              -> existing normalize_X / persist_X / analyze_knowledge_gaps
        -> Agent1KnowledgePackage / Agent1CuratedKnowledgeView
        -> Agent2ReadinessAssessment (read-only, over that same export)
```

This package never invents a biological fact, model structure, or kinetic
assumption -- every entity it curates was already reachable through an
existing connector/normalizer/persister, only now reachable *without* a
human writing each individual call by hand. It never generates Antimony,
SBML, an ODE, a simulation, or a model critique (Agents 2-5's exclusive
responsibilities; see §38 and `tests/pathway_curation/test_scope.py`), it
never constructs an Agent 2 object of any kind (the readiness assessment
in §36 is read-only over Agent 1's own export, not a step toward building
one), and it never moves a claim to `HUMAN_ACCEPTED` on a human's behalf
(§35).

## 3. The confirmed orchestration gap this increment closes

Verified directly against the repository before writing any code: every
`app.normalization.*normalize_X` function requires a caller-supplied
`*Lookup` implementation, and every `app.persistence.*persist_X` function
requires the `NormalizationResult` only calling `normalize_X` first can
produce -- but the *only* implementations of any `*Lookup` protocol
anywhere in this repository were test fakes
(`tests/normalization/test_*.py`, `tests/claim_generation/fakes.py`). No
real, database-backed adapter existed, and no high-level entry point could
wire discovery, normalization, and persistence together against a real
database end to end
(`docs/23_agent1_v1_scope_and_completion.md` §19: "No high-level ingestion
orchestration entry point ... is introduced in this increment"). This
increment supplies both missing pieces: `app.pathway_curation.lookups`
(one real `SqlAlchemy*Lookup` class per entity type -- Organism, Reaction,
Gene, Protein, Publication, Compound, Compartment, ReactionEnzyme, all
present from the first completion pass -- each a thin, read-only
`SELECT`-only adapter reimplementing no identity policy) and
`app.pathway_curation.executor` (the orchestration entry point itself).

A second, distinct gap was found and closed in *this* revision: the first
completion pass discovered reactions but never their participants, so an
`Agent1CuratedKnowledgeView` it produced could name a reaction without
naming anything that reaction consumed or produced -- structurally
present, but not usable by Agent 2 without a human filling in the
chemistry by hand. §18-22 describe how that gap is closed.

## 4. `CompletionPolicy`

```text
STRUCTURAL_COVERAGE
STRUCTURAL_AND_EVIDENCE
STRUCTURAL_EVIDENCE_AND_KINETICS
```

Each successive member is strictly additive over the one before it, never
an opaque numeric threshold. `STRUCTURAL_COVERAGE` requires only that
pathway reactions are identified and their participants/catalysts are
resolved where available. `STRUCTURAL_AND_EVIDENCE` adds: supporting
publications attempted. `STRUCTURAL_EVIDENCE_AND_KINETICS` adds: kinetic
measurements and regulation attempted. None of the three ever requires
*zero* gaps -- `COMPLETE_WITH_GAPS` is a fully legitimate terminal status
under every policy (§31). `required_frontier_reasons()` (§26) is the
single function translating a policy into which frontier-reason
categories actually count against it.

## 5. `CurationMode`

```text
PILOT
STANDARD
```

`PILOT` (the request default) applies conservative budget overrides via
`policy.pilot_defaults()` -- `max_iterations=3`, `max_connector_calls=25`,
`max_publications=5` -- but only to a budget field the caller left at
`PathwayCurationRequest`'s own non-pilot baseline default; an explicit,
deliberately-chosen budget is never overridden (`policy
.apply_mode_defaults`). `STANDARD` applies no override at all.

## 6. `PlannerAction` vocabulary

```text
RESOLVE_ORGANISM, DISCOVER_PATHWAY, DISCOVER_REACTIONS, RESOLVE_REACTION,
DISCOVER_ENZYMES, RESOLVE_GENE, RESOLVE_PROTEIN, DISCOVER_PUBLICATIONS,
RETRIEVE_PUBLICATION, DISCOVER_KINETICS, DISCOVER_REGULATION,
DISCOVER_ENZYME_STATES, ANALYZE_GAPS, ASSESS_COMPLETION
```

Every value maps to a capability this repository's existing
connectors/normalization/persistence/knowledge-gap machinery can already
execute deterministically. This package never encodes arbitrary
executable Python or shell commands, and never adds an action with no
real, already-implemented backing capability. `DISCOVER_REGULATION` and
`DISCOVER_ENZYME_STATES` are declared in this vocabulary because a future
increment's executor may implement them against Increment B's existing
persistence layer, but neither is planned or executed by this executor
(§25). Reaction-participant resolution and catalyst association (§18-22)
are deliberately **not** separate `PlannerAction` values -- they are
sub-steps of `RESOLVE_REACTION`/`DISCOVER_ENZYMES` respectively, recorded
in each iteration's own audit trail (§34), not new planner-level actions.

## 7. `FrontierReason` vocabulary and priority order

```text
UNRESOLVED_REACTION_IDENTITY          \
REACTION_MISSING_PARTICIPANTS          |
UNRESOLVED_REACTION_PARTICIPANT        | structural / modeling-readiness-blocking
MISSING_COMPARTMENT_CONTEXT            | (§4's bar, always required for COMPLETE;
UNRESOLVED_CATALYST                    | see "Structural frontier priority" below)
REACTION_ENZYME_PERSISTENCE_FAILED    /
MISSING_ORGANISM_CONTEXT
MISSING_PUBLICATION                    -- counted from STRUCTURAL_AND_EVIDENCE up
MISSING_KINETICS                      \
KINETICS_REQUESTED_NOT_ATTEMPTED       |
MISSING_REGULATION                     | counted only under
REGULATION_REQUESTED_NOT_SUPPORTED     |  STRUCTURAL_EVIDENCE_AND_KINETICS
MISSING_ENZYME_STATE                  /
AMBIGUOUS_IDENTITY
CONFLICTED_IDENTITY
SOURCE_FAILURE
NO_CONNECTOR_AVAILABLE
```

This is a controlled, deterministic vocabulary, never free-text-only.
`policy.FRONTIER_PRIORITY_ORDER` is the single ranking over it (lower
rank is more urgent); `policy.sort_frontier` applies that rank plus a
stable, intrinsic tiebreak (`entity_kind`, `entity_text`, `entity_id`,
`frontier_id`) so a run's reported frontier order never depends on
connector response order or dict/set iteration.

**Structural frontier priority** (revised in the pre-commit revision):
the order above is a *modeling-readiness* priority, not a claim about
biological importance -- a reaction with no participants sorts ahead of
a merely-missing publication because the former blocks Agent 2 network
construction and the latter never does (§4/§31). `policy
._STRUCTURAL_FRONTIER_REASONS` is the exact set that can prevent
`COMPLETE` (never `COMPLETE_WITH_GAPS`, which no structural item ever
blocks): `UNRESOLVED_REACTION_IDENTITY`, `REACTION_MISSING_PARTICIPANTS`,
`UNRESOLVED_REACTION_PARTICIPANT`, `UNRESOLVED_CATALYST`, and
`REACTION_ENZYME_PERSISTENCE_FAILED`.

Six reasons were added in the pre-commit revision, each mapping to a
concrete new code path introduced alongside it: `REACTION_MISSING_
PARTICIPANTS` (§19), `REACTION_ENZYME_PERSISTENCE_FAILED` (§22),
`KINETICS_REQUESTED_NOT_ATTEMPTED` (§23), and `REGULATION_REQUESTED_
NOT_SUPPORTED` (§25).

## 8. `PathwayCurationRequest`

The bounded input contract: `request_id`, `organism_text`,
`biological_process` (both required, non-empty), an optional
`organism_ncbi_taxonomy_id` (§11), an optional `default_compartment_text`
(§21, added in the pre-commit revision), `seed_entity_texts` (genes/
proteins to explicitly resolve), `include_entity_kinds` (an allow-list;
empty means no restriction), `include_kinetics`/`include_regulation`/
`include_enzyme_states`/`include_publications` (booleans, all
conservative-default `False` except `include_publications`), `exclusions`
(disjoint from `seed_entity_texts`), `max_iterations`/
`max_connector_calls`/`max_publications` (all positive), a
`completion_policy`, a `mode`, and free-text `notes`. Frozen and
self-validating (`__post_init__` raises `ValueError`/`TypeError` on any
invalid combination) -- never partially constructed.

## 9. `CurationPlanStep` / `PathwayCurationPlan`

`CurationPlanStep` is one deterministic unit of planned work: a
`step_id`, a `PlannerAction`, an optional `target_entity_kind`,
`dependencies` (other step ids), a positive `priority`, human-readable
`rationale`/`expected_output_kind`, optional `query_text`/
`structured_input` (plain data, never executable code), an optional
`source`, and a `status` (`PENDING`/`COMPLETED`/`SKIPPED`/`BLOCKED`,
distinct from the whole run's `CompletionStatus`). `PathwayCurationPlan`
validates step-id uniqueness and that every `dependencies` entry names a
real step in the same plan.

## 10. Planning is read-only (Step 42)

`planner.plan_pathway_curation` never opens a database session and never
calls a connector -- calling it twice with an identical request always
returns an identical plan
(`tests/pathway_curation/test_planner.py::test_plan_is_deterministic`).
It plans only the *fixed backbone* every run follows: resolve organism,
discover pathway, discover reactions, optionally discover enzymes/
publications/kinetics (depending on the request), always analyze gaps and
assess completion. The concrete reactions/genes/proteins/publications/
participants/catalyst associations a real run discovers are inherently
data-dependent and are never guessed at plan time -- they are recorded
per iteration in `PathwayCurationResult.iterations` instead (§28, §34). A
human can therefore call `plan_pathway_curation` and inspect the plan
before anything executes.

## 11. Organism resolution

Organism resolution always happens first (`strategies.resolve_organism`),
directly from the request's own `organism_text`/`organism_ncbi_taxonomy_id`
-- never from a connector, and never inferring a strain the caller did not
state. **A genuine capability gap discovered during implementation**:
`app.normalization.organism.normalize_organism` requires at least one
strong identifier (NCBI taxonomy id / KEGG code / BioCyc id) or an
existing matching row to ever return `NEW`; a bare `scientific_name` alone
is always `UNRESOLVED`, by that module's own long-standing, deliberate
design (no deterministic rule exists anywhere in this repository for
"is this scientific name specific enough to safely create a new organism
row"). `PathwayCurationRequest.organism_ncbi_taxonomy_id` closes this gap:
a caller may supply a real, externally-verifiable identifier it already
has (never fabricated by this package) so a first-ever run against an
empty database is not permanently blocked. Leaving it `None` remains fully
legitimate -- resolution then only succeeds by matching an
already-persisted organism, and otherwise the run terminates `BLOCKED`
(§31), never with an unhandled exception.

## 12. Pathway/reaction discovery (KEGG)

`strategies.discover_pathway` searches KEGG's `pathway` database for
`request.biological_process` and returns every hit's KEGG pathway id,
sorted -- never itself deciding which pathway "is" the requested process;
the executor deterministically takes the first, sorted, hit.
`strategies.discover_reactions_in_pathway` fetches that one pathway's flat
file and mechanically parses its own `REACTION` field: each line's first
whitespace-delimited token is a KEGG reaction id, deduplicated,
order-preserving -- never inferred, never a reaction not literally named
in the fetched record.

## 13. Reaction resolution: discovery versus expansion

Two retrieval shapes are deliberately kept separate throughout
`strategies.py`. **Discovery** (a free-text query, possibly several hits,
e.g. `resolve_reaction_by_text`) reuses
`app.entity_resolution.adapters.resolve_reaction_via_kegg` directly, then
`_classify_candidates` applies the existing, already-tested
`classify_outcome`/`sort_candidates` to the verdict -- more than one
distinct `NEW` candidate from one free-text search becomes an
`AMBIGUOUS_IDENTITY` frontier item, never chosen arbitrarily. **Expansion**
(an exact external id already known from a previously fetched structured
record -- every reaction id KEGG's own pathway `REACTION` field names,
via `resolve_reaction_by_kegg_id`) fetches that one id directly, never
searches, and is structurally incapable of that same multi-candidate
ambiguity. A pathway member id KEGG's own record names but whose reaction
record cannot itself be fetched (a stale/renamed id) becomes an
`UNRESOLVED_REACTION_IDENTITY` frontier item, never silently dropped.

`strategies.fetch_kegg_reaction_record` (added in the pre-commit revision)
factors the fetch/normalize/type-check step out on its own: the executor
calls it exactly once per reaction, using the same fetched
`KeggReactionRecord` both to resolve the reaction's own identity *and* to
parse its `equation` into participants (§19) -- never two connector calls
for one reaction. `resolve_reaction_by_kegg_id` accepts that same record
via its `prefetched_record` parameter to guarantee this.

## 14. Source-neutral connector routing (`strategies.py`)

Every function in `strategies.py` answers one source-neutral question
("find reactions," "find a catalyst," "find kinetics") by calling into
already-existing capability -- a real connector's `search`/`fetch`/
`normalize` methods, an already-existing `app.normalization.*
identity_from_*` adapter or `app.entity_resolution.adapters
.resolve_*_via_*` combined adapter, an already-existing `normalize_X`, and
an already-existing `persist_X` -- never a new duplicate-resolution or
persistence algorithm. Connector I/O failures
(`app.connectors.exceptions.ConnectorError`) are deliberately not caught
in `strategies.py`; `executor.py` is what converts them into a
`SOURCE_FAILURE` frontier item, mirroring
`app.entity_resolution.resolver`'s own "how do we call this connector" vs.
"how do we report that it failed" separation.

## 15. Reused, never duplicated: entity resolution / normalization / persistence

Nothing in this increment reimplements identity or persistence policy.
`app.pathway_curation.lookups` supplies the missing real `*Lookup`
adapters (§3) for every entity type this executor touches, including
`SqlAlchemyCompoundLookup`/`SqlAlchemyCompartmentLookup`/
`SqlAlchemyReactionEnzymeLookup` (present from the first completion pass,
confirmed sufficient and unmodified by this revision -- no new lookup
adapter was needed); everything downstream of that -- `normalize_organism`,
`normalize_reaction`, `normalize_compound`, `normalize_compartment`,
`normalize_gene`, `normalize_protein`, `normalize_publication`,
`normalize_reaction_enzyme`, `persist_organism`, `persist_reaction`,
`persist_compound`, `persist_gene`, `persist_protein`,
`persist_publication`, `persist_reaction_enzyme`,
`persist_kinetic_measurement`, `analyze_knowledge_gaps`,
`persist_knowledge_gap_analysis`, `get_agent1_knowledge_package`,
`get_agent1_curated_knowledge_view` -- is called exactly as already
implemented, unmodified. `NormalizationStatus`
(`MATCHED`/`NEW`/`AMBIGUOUS`/`CONFLICTED`/`UNRESOLVED`) and
`PersistenceAction` (`CREATED`/`REUSED_EXISTING`/`NO_ACTION`/
`REQUIRES_REVIEW`/`FAILED`) remain deliberately separate vocabularies,
never conflated (`strategies._outcome_for_result`).

Critically, `persist_reaction`'s own `_create` path already persisted
whatever `ReactionIdentity.participants` it was given, inside the same
per-reaction `SAVEPOINT` as the reaction row itself -- this was true
*before* this revision, simply unused, because nothing ever supplied a
non-empty `participants` tuple. §19 describes how this revision starts
supplying one, entirely at the orchestration layer, without touching
`app.persistence.reaction` at all.

## 16. Gene/protein resolution (SGD / UniProt)

`_resolve_seeded_genes_and_proteins` (executor.py) resolves every text in
`request.seed_entity_texts` via `strategies.resolve_gene_by_text` (SGD)
and, when a gene resolves or regardless, `strategies.resolve_protein_by_text`
(UniProt) -- both discovery-path free-text searches (§13), so an
unmatched seed text becomes an `UNRESOLVED_CATALYST` frontier item rather
than a `SOURCE_FAILURE`. A resolved protein's own already-persisted
`ec_number` (read via `_ec_numbers_for_protein`, a plain `session.get` --
every `persist_X` in this codebase flushes, so the row is queryable
before the caller's own transaction commits) is what seeds both kinetics
discovery (§23) and catalyst association (§22); no EC number is ever
guessed from a reaction or gene symbol alone.

## 17. Publication enrichment (PubMed)

`_discover_publications` searches PubMed for
`f"{organism_text} {biological_process}"`, capped at
`request.max_publications`, and resolves each hit's PMID via the
expansion path (`strategies.resolve_publication_by_pmid`). A query with no
hits becomes a `MISSING_PUBLICATION` frontier item, never treated as a
connector failure.

## 18. Deterministic KEGG equation parsing

**The highest-priority gap this revision closes.** `app.normalization
.reaction.reaction_identity_from_kegg` deliberately never parses
`KeggReactionRecord.equation` -- that module's own docstring, unchanged by
this revision, calls the raw text "unstructured" and states parsing it is
out of that increment's scope. It is not, in fact, unstructured: KEGG's
own reaction-equation grammar is a small, long-stable, documented format
(`https://www.kegg.jp/kegg/rest/keggapi.html`) -- a fixed arrow token
(`<=>`/`=>`/`->`/`<-`) separating two `"+"`-joined sides, each term an
optional plain positive-integer coefficient (default 1) followed by a
bare KEGG compound id (`C\d{5}`).

`app.pathway_curation.equation_parser.parse_kegg_equation` implements
exactly that grammar, at the orchestration layer, without touching
`app.normalization.reaction` at all. It **never guesses**: a term whose
leading token is not a plain positive integer (KEGG's own polymer/
variable-coefficient notation, e.g. `"2n C00023"`) is reported verbatim in
`unparseable_tokens`, never coerced; a term whose compound token is not
exactly `C` + five digits (a glycan id like `G00123`, or any other
non-compound token) is likewise reported verbatim; an equation with no
recognizable arrow at all reports its entire text as the one unparseable
token. It never raises for any input -- both a fully unparseable equation
and a partially-parseable one are legitimate, inspectable outcomes the
executor turns into frontier items (§19), never an exception. It performs
no mass/charge balancing (a separate, deterministic validation concern
this increment does not implement -- `Reaction.balanced_mass`/
`.balanced_charge` exist precisely so a *later* pass can compute that) and
no polymer expansion.

## 19. Reaction-participant resolution

`executor._resolve_reaction_participants` runs once per resolved
reaction, on the same `KeggReactionRecord` already fetched to resolve the
reaction's own identity (§13). It parses `record.equation` (§18), then
resolves every parsed term's KEGG compound id via
`strategies.resolve_participant_compound` -- an **expansion** call
(§13's distinction): each term already names an exact, already-known KEGG
compound id (from the equation itself), so this is never a free-text
search. A compound id that cannot be fetched, or that fetches to
something other than a compound record, becomes its own
`UNRESOLVED_REACTION_PARTICIPANT` frontier item; an unparseable equation
term (§18) becomes one too. **Partial success is preserved**: a reaction
with 3 of 4 participants resolved still gets those 3 attached -- a
reaction is never left with zero participants merely because one term
could not be resolved. A reaction whose equation was blank, fully
unparseable, or whose every term failed to resolve gets a
`REACTION_MISSING_PARTICIPANTS` frontier item of its own (§7) -- distinct
from, and in addition to, any per-term `UNRESOLVED_REACTION_PARTICIPANT`
items.

Resolved participants are attached to the reaction's own `ReactionIdentity`
via `dataclasses.replace(identity, participants=participants)` inside
`strategies.resolve_reaction_by_kegg_id`, immediately before
`normalize_reaction`/`persist_reaction` are called (§15) -- the executor
never persists a `ReactionParticipant` row itself.

A per-run `compound_cache: dict[str, UUID | None]` (keyed by KEGG compound
id) avoids re-fetching a compound already resolved (or already known to
have failed) earlier in the same run -- a fatty-acid-biosynthesis-shaped
pathway reuses a handful of compounds (acetyl-CoA, NADPH, ...) across many
reactions, and this cache is what makes that reuse a single connector
call, not one per reaction that mentions the compound.

## 20. Compound resolution

Every participant compound is resolved via `app.normalization.compound
.normalize_compound`/`app.persistence.compound.persist_compound`,
unmodified, through the real `SqlAlchemyCompoundLookup` (§15) -- reused
directly, never a second compound-identity algorithm. `compound_identity_
from_kegg` (also unmodified) preserves whatever KEGG's compound record
actually supplies: `kegg_compound_id`, `canonical_name` (KEGG's first
`NAME` entry), remaining names as `synonyms`, and `formula`. KEGG's own
compound record exposes no charge, no ChEBI/PubChem id, and no InChI/
InChIKey/SMILES (verified directly against `KeggCompoundRecord`,
unchanged since Increment 15) -- none of those fields is fabricated here;
they are simply absent on the resulting `Compound` row when KEGG alone is
the source, exactly as `compound_identity_from_kegg`'s own docstring
already discloses. An existing `Compound` row sharing the same
`kegg_compound_id` is reused, never duplicated (§15's `NormalizationStatus
.MATCHED` path).

## 21. Compartment policy

`ReactionParticipant.compartment_id` is **nullable** (verified directly
against `app/models/reaction.py`) -- the existing schema/handoff contract
does not itself require a non-null compartment on every participant. This
increment therefore never invents one to satisfy a requirement that does
not exist: no participant is ever defaulted to `"cytosol"` or any other
compartment merely because none was resolved.

`PathwayCurationRequest.default_compartment_text` (added in this
revision) is the one compartment-evidence path this executor has: an
explicit, conservative **scope assumption the caller asserts**, never an
inference this package makes on its own -- "model otherwise-unlocalized
pathway participants in this compartment for this curation request."
`strategies.resolve_reference_compartment_by_name` resolves it only
against an existing **reference** compartment
(`Compartment.organism_id IS NULL`, one of the 13 standard rows seeded by
migration `0002_reference_data`) by exact name -- deliberately **not**
`normalize_compartment` (§15): that module's own identity policy treats a
bare `name` as weak, candidate-generation-only evidence that can never by
itself reach `MATCHED` or `NEW` (appropriate for a *connector's* identity
claim, which this is not). A name matching zero or more than one
reference compartment resolves to `None` -- never fuzzy, never guessed,
never used to create a new compartment row; the caller is warned, and
every participant this run persists simply keeps `compartment_id=None`.
`default_compartment_text` is applied uniformly to every participant this
run resolves; no connector in this repository exposes per-participant
compartment localization today, so there is no competing, more specific
evidence source it could ever override (§21's evidence hierarchy has
exactly one rung in this increment).

## 22. ReactionEnzyme association and the catalyst evidence hierarchy

**The second-highest-priority gap this revision closes.** No connector in
this repository exposes a structured, organism-specific reaction<->gene
mapping (`app.normalization.reaction_enzyme`'s own module docstring,
unchanged: "No connector ... exposes a structured Reaction<->enzyme
association ... EC numbers are never used for this purpose [alone]" --
inferring one from a bare EC-number match would be exactly the kind of
biological inference that module's own instructions forbid). Given that
real constraint, this executor implements the *strongest evidence
hierarchy actually available*: a `ReactionEnzyme` association
(`relationship="CATALYZES"`) is created only when **both**:

1. the catalyst was **explicitly named** in the request's own
   `seed_entity_texts` -- a request-scoped human assertion, the strongest
   evidence this executor has access to (mirroring `organism_ncbi_
   taxonomy_id`'s identical role for organism resolution, §11); **and**
2. that protein's own already-persisted `ec_number` **exactly matches**
   this specific resolved reaction's own `ec_number` (from KEGG's
   `ENZYME` field, via `reaction_identity_from_kegg`, unmodified).

EC-number equality *alone*, for a protein the caller never seeded, is
**never** sufficient and is never used to create an association here --
two proteins sharing one EC number are never both associated with a
reaction merely because their EC numbers match; only the one the caller
actually named is ever considered
(`tests/pathway_curation/test_executor.py
::test_catalyst_association_is_conservative_about_shared_ec_numbers`
constructs exactly this case and asserts the unseeded sibling is never
touched). This is structurally guaranteed, not merely tested: `executor
._associate_catalysts` only ever iterates over `resolved_protein_ec_
numbers`, a list populated exclusively by proteins this run actually
seeded and resolved (§16) -- there is no code path in this executor that
queries "every protein with EC number X."

`strategies.associate_catalyst` builds a `ReactionEnzymeIdentity` (always
`protein_id`, never `complex_id`/`enzyme_state_id` -- this executor
discovers neither a complex composition nor a state-specific association;
see §25 and §37), normalizes it via the existing, unmodified
`normalize_reaction_enzyme`, and persists it via the existing, unmodified
`persist_reaction_enzyme` through the already-present
`SqlAlchemyReactionEnzymeLookup` (§15). A conservatively-supported pairing
whose persistence itself reports `FAILED`/`REQUIRES_REVIEW` becomes a
`REACTION_ENZYME_PERSISTENCE_FAILED` frontier item (§7) -- never silently
dropped.

**Enzyme complexes** (e.g. yeast fatty-acid synthase, a real multi-subunit
complex): no connector in this repository can currently discover complex
composition, so this executor never attempts to construct one, and never
collapses a would-be complex into a single protein to simplify the
handoff -- this is a disclosed limitation (§37), not an invented
composition.

## 23. Kinetics enrichment (SABIO-RK and Open Enzyme Database)

**Wired in this revision**: the first completion pass built `strategies
.discover_kinetics_oed` but never called it from the executor. `_discover_
kinetics` now tries **both** SABIO-RK and Open Enzyme Database,
independently, for every `(protein_id, ec_number)` pair resolved this run
(§16) -- never a bare EC number guessed from a reaction or gene symbol
alone. Either connector's absence, or either connector's own
`ConnectorError`, is recorded as a warning and never blocks the other
source or destroys structural pathway curation; a `MISSING_KINETICS`
frontier item is added only when at least one source was actually
attempted and neither produced a measurement. When `include_kinetics=True`
but no `(protein_id, ec_number)` pair was ever resolved (typically: no
`seed_entity_texts` at all), kinetics enrichment is never even attempted
-- this becomes its own, distinct `KINETICS_REQUESTED_NOT_ATTEMPTED`
frontier item (§7), never conflated with "attempted, found nothing."

## 24. Kinetic measurement linkage

`protein_id`/`organism_id` are always threaded through to the persisted
`KineticMeasurement` (`kinetic_identity_from_sabiork`/`_from_oed`, both
unmodified), so a measurement is actually linked, never an orphaned row
invisible to `app.agent1.service.get_agent1_knowledge_package`'s own
organism-scoping rule -- a genuine gap the first completion pass
discovered and fixed (confirmed via a manual end-to-end smoke test against
the real test database). No unit conversion, no "best" value selection,
and no averaging of independent measurements is ever performed --
`persist_kinetic_measurement`'s own idempotent-on-source-identity policy
(§15) is untouched by this executor.

## 25. Regulation and enzyme-state enrichment: accepted, not executed

`PathwayCurationRequest.include_regulation`/`.include_enzyme_states` are
valid, non-rejected request fields, and `planner.py` does not add a
`DISCOVER_REGULATION`/`DISCOVER_ENZYME_STATES` plan step for either today
(the planner's own fixed backbone, §10, only ever emits steps this
increment's executor can actually perform). Setting either flag never
errors and never changes what a run discovers. **Revised in this
increment**: setting either flag now always adds an explicit
`REGULATION_REQUESTED_NOT_SUPPORTED` frontier item (§7) -- the flag must
never misleadingly imply comprehensive regulatory curation was attempted
when this executor has no discovery route for it at all. A later
increment implementing `_discover_regulation` against Increment B's
existing `EnzymeState`/`AllostericInteraction`/`RegulatoryInteraction`
persistence layer is the natural continuation.

## 26. `CurationFrontierItem`

One deterministic, unresolved concern: `frontier_id` (content-derived,
never a random UUID -- `policy.build_frontier_id`), `entity_kind`,
`reason` (§7), a positive `priority`, at least one of `entity_text`/
`entity_id`, optional `parent_entity_id`, `attempted_sources`, `resolved`
(default `False`), `blocked_reason`, `notes`. This package never mutates a
frontier item in place to mark it resolved -- a resolved item is simply
omitted from the next iteration's frontier (`CurationRunState
.resolve_frontier`), and the audit trail (§28, §34) is what shows the
transition.

## 27. `CurationRunState`

Internal-only mutable bookkeeping (`app.pathway_curation.state`, not
re-exported from the package's own `__init__.py`): the current frontier
(keyed by `frontier_id`, idempotent `add_frontier`), executed query
identities (§29), the connector-call counter, discovered entity/reaction/
publication id lists (insertion-ordered, deduplicated), and warnings. One
instance per `execute_pathway_curation` call; never shared across runs.
The per-run `compound_cache` (§19) is separate, local state in
`execute_pathway_curation` itself, not part of `CurationRunState`.

## 28. The bounded iteration loop

`execute_pathway_curation` resolves the organism once (§11), resolves the
compartment scope assumption once if supplied (§21), then loops for up to
`request.max_iterations` iterations, stopping early once
`state.connector_calls_made >= request.max_connector_calls`. Iteration 1
always performs structural discovery (pathway, reactions and their
participants, seeded genes/proteins) plus, when enabled, publication and
kinetics enrichment; every iteration resolves any reaction ids still
pending and re-attempts catalyst association over whatever reactions/
proteins have been discovered so far (cheaply, via query dedup, §29).
Every iteration, regardless of whether it made progress, produces exactly
one `CurationIterationRecord` (§34) -- never silently omitted from the
audit trail.

## 29. Query deduplication

`policy.query_identity` builds one deterministic identity string per
logical connector query (connector, action, sorted keyword arguments) --
independent of, and never a substitute for, each connector's own
HTTP-level caching (`app.connectors.cache`). `CurationRunState
.has_run_query`/`.record_query` ensure the same logical query is never
re-issued within one run, even across iterations -- this is also what
makes re-attempting catalyst association every iteration (§28) cheap: a
`(reaction_id, protein_id)` pair already attempted is skipped immediately.

## 30. No-progress stopping rule

An iteration after the first that added no new entity/reaction/publication
id and left the frontier's own content unchanged sets `no_progress=True`
on its `CurationIterationRecord` and ends the loop -- a run never spins
through its full `max_iterations` budget making no further progress.

## 31. Completion assessment

`policy.assess_completion` classifies one run's terminal `CompletionStatus`
in a fixed precedence: a hard blocker (organism resolution failure) always
wins (`BLOCKED`); then budget exhaustion with unresolved high-priority
frontier remaining (`BUDGET_EXHAUSTED`); then whether any frontier reason
`policy.required_frontier_reasons(request.completion_policy)` counts
remains unresolved (`COMPLETE_WITH_GAPS`); otherwise `COMPLETE`.
`COMPLETE_WITH_GAPS` is always a fully legitimate terminal state, never
treated as a failure -- no completion policy ever requires zero gaps, and
`COMPLETE` itself is never claimed under `STRUCTURAL_COVERAGE` while any
`REACTION_MISSING_PARTICIPANTS`/`UNRESOLVED_REACTION_PARTICIPANT`/
`UNRESOLVED_CATALYST`/`REACTION_ENZYME_PERSISTENCE_FAILED` item remains
(§7's structural set, extended in this revision).

**`completion_status` and `agent2_readiness` (§36) answer different
questions, deliberately never conflated**: completion asks "did Agent 1
finish the requested curation policy?"; readiness asks "is the resulting
structural handoff internally complete enough for Agent 2 to consume it?"
A run can be `COMPLETE_WITH_GAPS` (missing kinetics/regulation, both
nonblocking, §36) and still be fully Agent-2-ready.

## 32. Knowledge-gap analysis reuse

Every run calls the existing, whole-database
`app.knowledge_gaps.analysis.analyze_knowledge_gaps` and
`app.persistence.knowledge_gap.persist_knowledge_gap_analysis` exactly
once, at the end of the iteration loop, regardless of how far the run
progressed -- never a second, pathway-curation-specific gap detector.

## 33. Versioning

`PATHWAY_CURATION_POLICY_VERSION` (`app.pathway_curation.types`) is this
package's own policy version, a single string constant, bumped only when
the planning/execution *behavior* changes materially -- mirroring the
sibling Agent 2 repository's identical versioning convention, never a
semantic-versioning subsystem of its own. It is carried on every
`PathwayCurationPlan.policy_version`.

**The Increment C pre-commit revision kept it at `"pathway-curation-v1"`**
(filling in previously-declared-but-unimplemented behavior -- participant
resolution, catalyst association, OED wiring, the readiness assessment --
without changing what the contract itself promised).

**Increment C.1 bumps it to `"pathway-curation-v1.1"`.** F1 changes what
reaction-membership discovery actually returns for a real pathway (from
"whatever a `REACTION` field happened to contain, often nothing" to "every
reaction KEGG's own link operation reports"); F2 changes what catalyst
discovery does when no seed is supplied (from "nothing" to "an
organism-scoped, EC-driven search"); F4 adds a new, precedence-bearing
request field; F6 changes what `is_ready` can report for the same export
shape (an empty export was `True`, is now always `False`). Each is a
materially different, released behavior change observable from this
package's own public contract, not a fix invisible to a caller -- exactly
the bumping criterion §33 itself states. Still a dotted, pre-1.0-style
increment, not a new `v2`: no request/result field was removed or
repurposed, only added to or corrected, and this remains the same
unreleased-until-committed Increment C lineage.

**`AGENT1_CONTRACT_VERSION` is unchanged (`"1.2"`) for a different,
independent reason: `Agent1CuratedKnowledgeView`'s own field shape did not
change, in either the pre-commit revision or C.1.** Both revisions
populate fields that already existed on it (`reaction_participants`,
`reaction_enzyme_associations`) -- via the existing `get_agent1_
knowledge_package`/`get_agent1_curated_knowledge_view` export chain,
entirely unmodified -- rather than adding a new field to either container
type. `AGENT1_CONTRACT_VERSION` bumps only when the actual Agent 1 ->
Agent 2 handoff *schema* changes (`app.agent1.types`'s own docstring);
populating an existing, previously-empty field with real data is not a
schema change, and neither is adding `modelable_reaction_count`/
`NO_MODELABLE_REACTIONS` to `Agent2ReadinessAssessment` (§36/§44) --
that type is `app.pathway_curation`-only, attached only to
`PathwayCurationResult` (this package's own result type), never part of
the `Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView` contract at all.

**Increment C.2 bumps it to `"pathway-curation-v1.2"`.** Catalyst-discovery
precedence materially changes: direct organism-specific KGML reaction->gene
evidence is now tried first and, when present, entirely preempts the
EC-based search (§47) -- a released, observable behavior change from this
package's own public contract, exactly like C.1's own F1/F2/F4/F6 bumps
above. `AGENT1_CONTRACT_VERSION` is unchanged for the same reason C.1's own
bump-analysis gives: `ReactionEnzyme` rows flow through the existing,
unmodified `Agent1KnowledgePackage.reaction_enzyme_associations` field --
more real rows populating an already-existing field, never a new one.

## 34. Auditability

`PathwayCurationResult` is the complete, auditable outcome of one run:
the original `request`, the `final_plan`, every `CurationIterationRecord`
(`iterations`), aggregate counters (`iterations_completed`,
`connector_calls_made`), every logical query executed
(`queries_executed`), the resolved `organism_id` (`None` if organism
resolution itself never succeeded), every discovered entity/reaction/
publication id, the final `unresolved_frontier`, persisted
`knowledge_gap_ids`, the `completion_status` and its `completion_reasons`
(never empty -- every status is explained), accumulated `warnings`, and --
always populated, regardless of how far the run progressed --
`agent1_knowledge_package`/`curated_knowledge_view`/`agent2_readiness`, so
neither Agent 1's own output contract nor its readiness assessment ever
narrows for a partial or blocked run. For participant/catalyst resolution
specifically, the audit trail (frontier notes, `warnings`, `queries_
executed`) always answers: which source reaction generated this
participant, which compound identifier was resolved (or why not), which
compartment evidence was used (or why none was), which evidence
established the catalyst, which source supplied a kinetic measurement,
what failed, and what remained unresolved -- no opaque autonomous
decision is ever made without a corresponding, inspectable record.

## 35. Human-review safety

This package never calls `app.review.workflow.human_review_claim` and
never sets a claim's curation state to `HUMAN_ACCEPTED` -- verified
structurally, not just by convention
(`tests/pathway_curation/test_scope.py
::test_pathway_curation_never_imports_or_calls_human_review_claim`). Every
entity this package persists (organisms, reactions, compounds,
compartments, genes, proteins, publications, reaction/enzyme
associations, kinetic measurements) follows exactly the same
`PROPOSED`/`REQUIRES_REVIEW` lifecycle the existing persistence layer
already applies when a human is the one calling it by hand; autonomy in
*planning and connector orchestration* never becomes autonomy in
*accepting curated knowledge*.

## 36. Agent 2 structural-readiness assessment

`app.pathway_curation.readiness.validate_agent2_readiness` is a pure,
read-only function over the two container types Agent 1's existing export
layer already produces (`Agent1CuratedKnowledgeView`, `Agent1
KnowledgePackage`) -- it constructs no Agent 2 object of any kind, and
never mutates either container. It is always called, once, at the end of
`execute_pathway_curation`, and its result is always attached to
`PathwayCurationResult.agent2_readiness` -- a caller never has to
reconstruct it by hand from the raw export tuples.

**A pathway is not structurally ready for Agent 2 merely because its
reactions have been discovered. Modelable reactions must contain resolved
biochemical participants and valid structural references.**

`Agent2ReadinessAssessment` reports `is_ready` (`True` iff
`blocking_issues` is empty), `blocking_issues`/`nonblocking_issues`
(tuples of `Agent2ReadinessIssue`: a controlled `Agent2ReadinessIssueCode`,
a `blocking: bool`, a human-readable `message`, and the specific
`entity_id` involved), and plain counts (`reaction_count`,
`modelable_reaction_count` -- added in Increment C.1, §44 --
`participant_count`, `compound_count`, `compartment_count`,
`reaction_enzyme_count`, `kinetic_measurement_count`,
`enzyme_state_count`).

**Blocking issue codes** (any one of these makes `is_ready` `False`):

* `NO_MODELABLE_REACTIONS` (Increment C.1, §44) -- zero exported reactions
  satisfy the modelable-reaction definition below; always checked in
  addition to, never instead of, every other check here.
* `REACTION_WITHOUT_PARTICIPANTS` -- a reaction with zero exported
  `ReactionParticipant` rows.
* `PARTICIPANT_COMPOUND_MISSING` -- a participant's `compound_id` does not
  resolve to an exported compound (a dangling reference).
* `PARTICIPANT_COMPARTMENT_MISSING` -- **only** when a participant's
  `compartment_id` is *set but dangling* (does not resolve to an exported
  compartment); a **`None`** compartment is a separate, nonblocking case
  (below) -- the schema permits it.
* `PARTICIPANT_ROLE_INVALID` / `PARTICIPANT_STOICHIOMETRY_INVALID` --
  defensive integrity checks; unreachable in practice (the Python enum
  and the database's own `CHECK (stoichiometry > 0)` constraint already
  guarantee both), kept so a direct database mutation outside this
  package's own write paths is still caught.
* `REACTION_ENZYME_REACTION_MISSING` / `REACTION_ENZYME_CATALYST_MISSING`
  -- a `ReactionEnzyme` association whose `reaction_id`/`protein_id`/
  `enzyme_state_id` does not resolve. `complex_id` is **never** checked:
  neither `Agent1CuratedKnowledgeView` nor `Agent1KnowledgePackage`
  exposes an `EnzymeComplex` list at all (a pre-existing gap, not
  introduced by this package) -- a complex-targeted association is
  disclosed as structurally unverifiable here, never assumed valid *or*
  flagged invalid.
* `ENZYME_STATE_REFERENCE_MISSING` -- a persisted `EnzymeModification`/
  `AllostericInteraction`/`EnzymeStateTransition` whose own
  `enzyme_state_id` (or `from_state_id`/`to_state_id`) does not resolve.
  This is the one **exception** to "missing regulation is nonblocking"
  (below): an object that *exists* in the export but cannot be resolved
  is a contract-integrity defect, not a disclosed scientific gap.

**Nonblocking issue codes** (disclosed, but `is_ready` can still be
`True`):

* `PARTICIPANT_COMPARTMENT_MISSING` (the `None`-compartment case) --
  `ReactionParticipant.compartment_id` is nullable by schema; Agent 2 may
  apply its own default or placeholder.
* `KINETIC_REFERENCE_MISSING` -- a dangling kinetic-measurement reference.
  **Missing kinetic measurements and missing regulatory knowledge are
  scientific gaps, but they do not by themselves make an otherwise valid
  structural pathway unusable by Agent 2** -- Agent 2 may declare a
  placeholder/tentative kinetic law regardless.

Readiness validation runs after the export is fully assembled and never
mutates it (§10's read-only discipline extended to this final step, too).

## 37. Known limitations

* No LLM adapter exists anywhere in this repository, and this increment
  does not add one -- automatic literature evidence-extraction/claim
  generation from free text (as opposed to structural discovery via KEGG/
  SGD/UniProt/PubMed/SABIO-RK/OED) is out of scope. A discovered
  publication is persisted as a `Publication` row; it produces no
  `Claim`/`Evidence` on its own. **This is F5, and it remains explicitly
  deferred by Increment C.1** -- see §45.
* Enzyme complexes are never discovered or associated (§22) -- no
  connector in this repository can currently establish complex
  composition, and `Agent2ReadinessAssessment` cannot even verify a
  `complex_id` reference for the same reason (§36). Increment C.1 does not
  change this: it is one of this increment's explicit non-goals (see §45).
* Regulation and enzyme-state discovery are accepted request fields with
  no executor implementation (§25) -- explicitly disclosed via the
  `REGULATION_REQUESTED_NOT_SUPPORTED` frontier item, never silent.
  Unchanged by Increment C.1.
* Reaction participants are resolved from KEGG's own `EQUATION` field
  only (§18-19); no Rhea/MetaCyc equation source is parsed (neither
  connector exists in this repository).
* The compartment evidence hierarchy (§21) has exactly one rung today
  (the request's own `default_compartment_text`) -- no connector exposes
  per-participant or per-reaction compartment localization, so there is
  no more specific evidence to prefer over it, and none to fall back to
  beneath it.
* The catalyst evidence hierarchy (§22, extended by §42) is still
  deliberately narrower than a fully general one: a `ReactionEnzyme`
  association still requires either an explicit request-level seed or a
  clean, single, organism-scoped EC-number-driven UniProt candidate --
  never bare EC equality across every organism sharing it. Increment C.1's
  own EC-based discovery route (§42) reuses UniProt's existing
  `organism_name`-text-filter query shape unchanged (this package may not
  modify `app.entity_resolution.adapters`) -- confirmed, live, to admit
  more than one same-named-species UniProt entry (distinct sequenced
  strains/isolates) than a strict `organism_id`-based filter would; this
  makes EC-based discovery occasionally more connector-call-expensive and
  occasionally more ambiguity-prone than a hypothetical stricter query
  would be, never less conservative about what it actually persists.
* This increment (through its pre-commit revision) had not yet been run
  against the real, live KEGG/SGD/UniProt/PubMed/SABIO-RK/OED connectors
  before Pilot 1 Run 1 -- that pilot is what discovered F1/F2/F4/F6/F7,
  all addressed by Increment C.1 (§39-§45). Pilot 1 Run 2, against this
  revision, is the explicitly deferred next step after review and commit.
* **(Superseded by §46's completion below, kept here for history.)**
  Organism-specific reaction-membership resolution (F1) was, until §46,
  blocked rather than solved: `sce00061` legitimately linked to zero
  reactions, and this was correctly disclosed (`PATHWAY_REACTION_
  MEMBERSHIP_EMPTY`) rather than silently treated as `map00061`'s full 68
  reactions. §46 replaces this bullet with a resolved mechanism; its own
  residual limitation (KGML pathway-diagram nodes are anchored on a
  representative KO per diagram position, not always the organism's own
  most-specific KO from its `GENE` field) is documented there instead of
  here.

## 38. Testing

`tests/pathway_curation/` covers every layer of this contract against the
real, migrated PostgreSQL test database (never a mocked session):
`test_types.py`/`test_policy.py` (pure contract/policy unit tests, no
database), `test_planner.py` (read-only planning, no database or
connector), `test_equation_parser.py` (the KEGG equation grammar: single/
multiple substrates and products, omitted vs. explicit coefficients,
reversible/irreversible arrows, a malformed equation, an unknown/glycan
compound token, a variable-polymer coefficient, deterministic output),
`test_readiness.py` (pure, in-memory `Agent2ReadinessAssessment` checks:
a fully-resolved reaction, a resolved compartment, every blocking issue
code including the complex-reference non-check and the enzyme-state
contract-integrity exception, every nonblocking issue code), `test_
executor.py` (the full bounded loop, through deterministic fake
connectors satisfying the exact structural shape the real connectors
satisfy: one-reaction/small-linear/branched pathway discovery, an
unresolved reaction id, an unmatched seed gene, literature enrichment,
kinetics enrichment with protein/organism linkage, participant resolution
with correct roles/stoichiometry, an unparseable equation, an unknown
compound token alongside successfully-resolved ones, a compound-fetch
failure alongside successfully-resolved ones, both compartment-evidence
outcomes plus the no-evidence-at-all case, catalyst-association
conservatism against a shared EC number, repeated-execution idempotency
across organisms/compounds/reactions/participants/genes/proteins/
associations/measurements, OED kinetics wiring, a partial kinetics-source
failure that does not block the other source, the regulation-requested-
not-supported and kinetics-requested-not-attempted disclosure signals, a
KEGG connector failure, a partial source failure that does not block an
unrelated source, no connectors configured at all, organism-resolution
failure, request-validation rejection, and one full fake end-to-end pilot
exercising every enrichment path together and asserting `agent2_
readiness.is_ready is True`), and `test_scope.py` (structural, AST-based:
no forbidden modeling/simulation library or definition anywhere in this
package, no call to `human_review_claim`, no import of a nonexistent
Agent 2-5 package, no model-shaped field on any contract type).

**Increment C.1** added: `tests/connectors/test_kegg.py` gained 12 tests
for `link()`/`parse_link_response` (a valid response, multiple entries,
literal-duplicate-row preservation, response-order preservation, an empty
response, a malformed line, a blank identifier, a mismatched relationship
type returned verbatim, a connector failure, and both empty-argument
validations). `test_types.py` gained coverage for `strain_text` and
`source_pathway_id` (valid KEGG-shaped ids, rejection of malformed ones,
and blank-is-`None` consistent with every other optional string field).
`test_readiness.py` gained the F6 suite (§44): a completely empty export,
an organism-only export, an export whose only reactions all lack
participants, and a case where one modelable reaction among several is
already sufficient. `test_executor.py` gained a dedicated F1/F2/F4
regression section: a pathway record with no `REACTION` field whose
membership still resolves via `link()` (the central F1 test), empty
reaction membership producing `PATHWAY_REACTION_MEMBERSHIP_EMPTY`, a
`link()` connector failure producing `SOURCE_FAILURE`, a structured
pathway id used directly with no free-text search ever issued, a
structured id taking precedence over a simultaneously-supplied
`biological_process`, a structured id that does not resolve, the
free-text fallback when no structured id is supplied, autonomous catalyst
discovery from a reaction's own EC number with `seed_entity_texts=()`,
the required negative case (a real isozyme pair sharing one EC number
never fabricates an association), no discovery attempted when a reaction
carries no EC number at all, and seed-based and autonomous discovery
coexisting without duplication. The full fake pilot test was updated to
use `seed_entity_texts=()`, relying entirely on autonomous discovery, and
still ends `agent2_readiness.is_ready is True`.

**Increment C.1's organism-specific pathway-resolution completion** (§46)
added: `tests/connectors/test_kegg.py` gained 8 tests for `get_kgml()`/
`parse_kgml_reaction_ids` (raw-XML retrieval, 404-is-`None`, a non-404
failure still raising, empty-`pathway_id` rejection, unique-and-ordered
extraction, extraction of every id from a multi-id `name` attribute, a
no-`<reaction>`-elements document parsing to an empty tuple, and malformed
XML raising `ConnectorParseError`). `test_executor.py` gained an F10
section: an organism-specific pathway's KGML subset is used and the
reference-only reactions a naive `map`-equivalence approach would have
imported are never fetched/expanded; a generic pathway with no KGML
document keeps the original `link()`-only behavior byte-for-byte; a KGML
document that exists but is empty still falls back to `link()`; a pathway
with zero reactions from either mechanism still produces the F9
`PATHWAY_REACTION_MEMBERSHIP_EMPTY` block; and a synthetic, non-yeast
organism-prefixed id (`xyz00061`) exercises the identical code path,
proving no organism code is special-cased.

**Increment C.2's organism-specific catalyst resolution** (§47) added:
`tests/connectors/test_kegg.py` gained 13 tests for `parse_kgml_entries`/
`KeggKgmlEntry` (one gene/one reaction, multiple genes at one entry,
multiple reactions on one gene entry, an ortholog entry, gene and ortholog
entries together, a reaction with no catalyst-associated entry at all, a
`type="group"` entry with `<component>` children, duplicate-identifier
deduplication, stable ordering, malformed XML, missing attributes,
unrelated entry types preserved unfiltered, and an empty document).
`test_executor.py` gained a 19-test C.2 section: one direct gene resolving
to a `ReactionEnzyme`; the ACC1/HFA1 regression (two explicitly-named genes
both persisted independently, never collapsed and never expanded into a
13-candidate EC ambiguity); a `type="group"` entry never fabricating
independent catalysts or a complex; an ortholog-only entry never fabricating
a Gene/Protein; the pre-existing EC-only ambiguity regression (still
conservative); direct evidence never diluted by what a broad EC search
would otherwise surface; six multi-EC scenarios (independent per-EC
queries, duplicate-EC dedup, one-resolves-one-fails, both-ECs-same-protein
dedup, conflicting candidate sets staying ambiguous, wildcard ECs never
queried, a wildcard alongside a real EC still querying the real one); a
seedless autonomous resolution test; an idempotency test (repeated
execution, zero duplicate `ReactionEnzyme`/`Protein` rows); a provenance
test (`queries_executed` traces context->gene->protein->association);
catalyst-context retrieval failure never blocking structural reaction
processing; an unresolvable direct gene disclosed rather than silently
replaced by an EC-matched substitute; and an EC contradiction disclosed via
a warning while the direct-evidence association still persists.

## 39. Increment C.1 — Live Pathway Discovery Repair

Pilot 1 Run 1 (`artifacts/pilots/yeast_fatty_acid_001/13_pilot_report.md`)
ran this increment's own pre-commit revision against real KEGG/SGD/
UniProt/PubMed/SABIO-RK/OED services for the first time and found that
structural pathway curation never actually began: KEGG pathway discovery
itself succeeded, but reaction-membership discovery silently returned
zero reactions, with no frontier item disclosing it, and the run still
reported `agent2_readiness.is_ready = True` for a completely empty
export. Four mandatory defects (F1, F2, F4, F6) and one opportunistic
documentation fix (F7) are corrected below; one (F3) is added as a small,
clean extension; one (F5) is explicitly and deliberately left deferred.
Nothing here redesigns Increment C, adds Agent 2 behavior, adds Antimony
generation, or adds LLM literature extraction.

## 40. F1: KEGG pathway<->reaction link operation

See §13/§18's already-updated text and `app.connectors.kegg`'s own module
docstring for the full verification detail (a live `GET /get/{pathway}`
response, for both a generic and an organism-specific pathway, plus a
third, unrelated pathway to rule out a lipid-pathway-specific quirk, all
confirmed to carry no `REACTION` field). The fix: `KeggConnector.link()`
(`GET /link/{target_db}/{dbentries}`) is a new, generic, `search()`-shaped
connector primitive -- retrieval and parsing only, no curation policy --
and `strategies.discover_reactions_in_pathway` now calls
`connector.link("reaction", pathway_id)` exclusively; it no longer reads
the pathway's own fetched record at all. A pathway that resolves but
links to zero reactions now always produces a `PATHWAY_REACTION_
MEMBERSHIP_EMPTY` frontier item (§7), which is also a structural,
`COMPLETE`-blocking reason (`policy._STRUCTURAL_FRONTIER_REASONS`) and
therefore also drives `Agent2ReadinessAssessment.is_ready = False` via
§44's modelable-reaction rule. A `link()` failure is reported as
`SOURCE_FAILURE`, never confused with a legitimate empty result. The
pathway's own `/get/` record may still be fetched for metadata
(`strategies.fetch_kegg_pathway_metadata`) -- used only to confirm an
explicitly supplied structured pathway id actually resolves (§41), never
as a reaction-membership source.

## 41. F4: structured pathway ids and precedence

`PathwayCurationRequest.source_pathway_id` is an optional, validated,
KEGG-shaped pathway id (`^[a-z]{2,5}[0-9]{5}$`, e.g. `sce00061`/
`map00061`/`hsa00061`/`ko00061` -- deliberately not overfit to `sce`
alone). When supplied, `executor._resolve_pathway_identity` uses it
directly: `strategies.fetch_kegg_pathway_metadata` confirms it resolves,
and `strategies.discover_pathway` (the free-text search) is never called
at all -- confirmed by dedicated tests asserting no `("search", (...,
"pathway"))` call appears in the connector's own call log, even when a
`biological_process` that would otherwise match a *different* pathway is
also supplied. A structured id that does not resolve on KEGG becomes an
`UNRESOLVED_REACTION_IDENTITY` frontier item, exactly like an
unsuccessful free-text search. Leaving `source_pathway_id` unset
preserves the pre-C.1 free-text behavior exactly (§12) -- `biological_
process` remains required regardless, and is still used for literature
queries (§17) and as the plan's own human-readable scope statement
(§10) even when a structured id drives structural discovery.

## 42. F2: autonomous catalyst discovery from reaction evidence

Before C.1, `app.pathway_curation.executor` had exactly one path to SGD/
UniProt: `_resolve_seeded_genes_and_proteins`, gated entirely on
`request.seed_entity_texts`. An empty seed list meant catalyst discovery
never started at all, regardless of what the resolved reactions
themselves disclosed. `executor._discover_catalysts_from_reactions` (new)
removes that bottleneck: for every discovered reaction's own already-
persisted `ec_number` (KEGG's `ENZYME` annotation, via `reaction_
identity_from_kegg`, unmodified), it calls `strategies.discover_
catalyst_candidates_by_ec_number` -- an organism-scoped UniProt search
keyed by the EC number itself (a query string like `"ec:6.4.1.2"`) rather
than a caller-supplied gene symbol. This runs whenever UniProt is
configured and at least one reaction has been discovered, **unconditionally
on `seed_entity_texts`**.

**Discovery, never invented evidence** (§16 of the Increment C.1
instructions): `discover_catalyst_candidates_by_ec_number` is a thin reuse
of the existing `resolve_protein_by_text`/`resolve_protein_via_uniprot`
-- an EC-number query is simply a different query string, not a new
resolution algorithm, so a candidate this discovers reaches
`NormalizationStatus`/persistence through the exact same, unmodified
code path a seeded gene symbol already does. Multiple distinct candidates
sharing the queried EC number are never resolved arbitrarily: they reach
`AMBIGUOUS_IDENTITY` through the same, unmodified `classify_outcome`
machinery every other discovery path in this package already uses.
**Confirmed live, while implementing this increment, that this is a real
case, not a hypothetical one**: EC 6.4.1.2 in *Saccharomyces cerevisiae*
alone resolves to two distinct, real gene products on UniProt --
cytosolic `ACC1` (Q00955) and mitochondrial `HFA1` (P32874), a genuine
isozyme pair. An ambiguous or fully-unresolved EC-based discovery attempt
becomes a `REACTION_CATALYST_UNRESOLVED` frontier item (§7) -- the
autonomous-discovery analogue of `UNRESOLVED_CATALYST` (reserved for an
explicitly seeded text that failed), both in the same structural,
`COMPLETE`-blocking tier.

A resolved candidate is appended to the same `resolved_protein_ec_
numbers` list seed-based resolution already populates -- `executor
._associate_catalysts` (creates `ReactionEnzyme` only for an exact
EC-to-reaction match, §22, unchanged) and `_discover_kinetics` (§23,
unchanged) require **no code changes at all** to pick up an
autonomously-discovered protein; both already iterate that one list
regardless of which path populated it. `seed_entity_texts`'s semantics
are therefore now exactly what §20 of the Increment C.1 instructions
requires: *optional caller-supplied hints that may augment discovery*,
never *the only proteins Agent 1 is allowed to investigate* -- a seeded
gene and an autonomously-discovered one may coexist for the same run, and
when they resolve to the same real protein, persistence reuses it
(`MATCHED`), never duplicating.

Organism scoping for this discovery route is exactly as precise as the
pre-existing, unmodified `resolve_protein_via_uniprot` adapter already
makes it for every other UniProt discovery call in this package (an
`organism_name` text filter, not a numeric `organism_id` filter) -- see
§37's disclosed limitation for the precision/cost trade-off this implies,
never resolved by modifying `app.entity_resolution.adapters` (out of
this package's own scope boundary).

## 43. F3: optional strain context

`PathwayCurationRequest.strain_text` is a small, additive field: `app.
normalization.organism.normalize_organism`/`OrganismIdentity`/`strategies
.resolve_organism` already accepted a `strain` parameter before C.1 -- the
executor simply always called it with a hard-coded `None`. C.1 threads
`request.strain_text` through unchanged (`strategies.resolve_organism(...,
strain=effective_request.strain_text, ...)`); no other code changed, no
migration was needed (`Organism.strain` already exists), and `"S288C"` is
never concatenated into `organism_text` or any other field -- organism
resolution's own existing `(scientific_name, strain)` identity matching
(§11, unmodified) does the rest.

## 44. F6: readiness is never vacuously true

See §36 (updated in place, not renumbered, since it already existed) for
the complete `Agent2ReadinessAssessment`/`NO_MODELABLE_REACTIONS`/
`modelable_reaction_count` contract. In summary: a reaction counts as
*modelable* only if it exists, has at least one participant, every
participant's compound resolves, every non-`None` compartment resolves,
and every role/stoichiometry is valid -- deliberately never requiring a
resolved catalyst, kinetics, or regulation (§24 of the Increment C.1
instructions). Zero modelable reactions is always its own additional
blocking issue, regardless of what else the export does or does not
contain -- an empty export, an organism-only export, and an export whose
only reactions all lack usable participants are all now `is_ready =
False` for this reason alone, closing the exact gap Pilot 1 Run 1 found.

## 45. F5 deferred; F7 corrected; enzyme complexes remain a non-goal

**F5 (publications not in the Agent 1 handoff) is explicitly deferred, not
solved, by this revision.** `app.agent1.service._select_publications`
derives the exported publication set exclusively from `Evidence
.publication_id` values (an Increment 27 design decision, not introduced
by Increment C or C.1); this package's own `_discover_publications` only
ever persists a bare `Publication` row and creates no `Claim`/`Evidence`
(no LLM adapter exists in this repository, and this revision adds none).
**Increment C/C.1 may therefore discover and persist publications that
are not exported in the Agent 1 handoff unless connected through the
existing Claims/Evidence architecture** -- a known, disclosed enrichment/
handoff limitation, tracked separately, and it does not block structural
pathway discovery (F1/F2/F4/F6 are all independent of it).

**F7 (a stale `Settings.sabiork_base_url` docstring claim) is corrected.**
That docstring previously claimed `SabiorkConnector.from_settings` "falls
back to a live-verified default endpoint when this is unset"; the actual
code raises `ValueError` when unset, exactly like `kegg_base_url`/
`sgd_base_url`/`uniprot_base_url`. The docstring is corrected to say so;
no runtime behavior changed (`app.connectors.sabiork.SabiorkConnector`
itself was not modified).

**Enzyme complexes remain an explicit non-goal of this revision.** The
real yeast FAS1/FAS2 fatty-acid-synthase system is exactly the kind of
multi-subunit complex this package cannot represent as a catalyst today
(§22/§37) -- discovering FAS1/FAS2 as genes/proteins, and disclosing that
their catalytic *complex* cannot be represented, is acceptable and
expected; inventing a single-protein stand-in catalyst to make readiness
report `True` is never acceptable, and this revision does not do so.

## 46. Organism-specific KEGG pathway resolution (Increment C.1 final completion)

§40 (F1) fixed pathway-reaction discovery in general, but left one gap
undiscovered until a dedicated, live-KEGG-only investigation was run
specifically against it: `link("reaction", pathway_id)` -- F1's own
mechanism -- returns real data for a generic reference pathway
(`map00061`: 68 reactions) but returns **zero** rows for the corresponding
organism-specific pathway (`sce00061`). Before this completion, that
organism-specific case correctly, honestly produced `PATHWAY_REACTION_
MEMBERSHIP_EMPTY` (F9, §40) -- a truthful "cannot resolve this" outcome,
never a silent failure, but also never useful for the biological scope a
caller actually asked for (`sce00061`, not `map00061`).

**The investigation's central question**: does KEGG expose, anywhere in
its structured (non-HTML, non-OCR) API, an organism-specific pathway's own
reaction subset -- and can it be obtained deterministically, without
either (a) treating every one of `map00061`'s 68 reference reactions as
yeast-specific (confirmed live to be biologically wrong: several are
discrete-enzyme bacterial/plant fatty-acid-synthase-II steps that yeast's
own FAS-I megasynthase gene set does not separately encode) or (b)
inventing a heuristic KEGG does not actually publish.

**What was tried and rejected first.** A gene-level chain --
`link("sce", "sce00061")` (13 genes, confirmed to exactly match the
flat-file `GENE` field) → each gene's own `link("enzyme", gene)`/
`link("ko", gene)` → `link("reaction", "ec:...")`/`link("reaction",
"ko:...")`, intersected against `map00061`'s own 68 reactions -- was
built and evaluated live before any code was changed. It recovered only
41 of 68 (60%) with the remaining 27 genuinely ambiguous (wildcard/
incomplete EC classifications with no resolvable target; one gene's own
EC numbers pointing to reactions absent from the reference pathway
entirely, which would have introduced false positives without the
reference-intersection step). This chain was **not implemented**: its own
27-reaction gap sampled as core FAS-cycle chemistry, not clearly-excluded
bacterial/plant-only steps, meaning a chunk of the "unresolved" set was
plausibly a false negative of the method, not a correctly-excluded
non-yeast reaction -- an uncomfortable ambiguity this package's own
scientific-conservatism principle (§47's final rule) does not accept
papering over with a disclosed-but-uncertain partial mapping when a
strictly better mechanism turned out to exist.

**What was found instead: KGML.** Every organism-specific (and KO-level)
KEGG pathway id has its own KGML pathway-diagram document (`GET /get/
{pathway_id}/kgml`, KEGG's long-published pathway markup format, retrieved
via the same unauthenticated REST API as every other operation this
connector uses). That document's own `<reaction name="rn:...">` elements
are KEGG's own curated, per-organism reaction nodes for that diagram --
confirmed live across three independent, unrelated organisms
(`sce00061` -> 41 reactions, `hsa00061` -> 45, `eco00061` -> 50), every
single one of which is a strict subset of `map00061`'s 68 reference
reactions, with zero exceptions in any of the three. A generic
"map"-prefixed reference pathway has no per-organism diagram of its own
and consistently 404s at this same endpoint (confirmed live) -- which is
exactly the signal `get_kgml()` uses to mean "not applicable here," never
an invented empty result.

**The implementation** (`app.connectors.kegg.KeggConnector.get_kgml`/
`parse_kgml_reaction_ids`, `app.pathway_curation.strategies
.discover_reactions_in_pathway`): for any `pathway_id`, try its own KGML
document first; if one exists and declares at least one reaction, that
set *is* the answer, used as-is, with no further intersection or
provenance-tracking step needed -- it is already KEGG's own organism-
scoped answer, not a candidate this package derived and must justify.
Only when no KGML document exists (or one exists but is empty) does this
fall through to the pre-existing `link("reaction", pathway_id)` mechanism
-- preserving §40's original F1 behavior byte-for-byte for every
"map"-prefixed pathway id this function already handled correctly. There
is no organism-code-specific branch anywhere in this implementation: the
same two calls run for every `pathway_id` regardless of prefix (confirmed
by a dedicated test using a synthetic, non-yeast organism prefix,
`xyz00061`, with no live network access). The requested pathway id itself
is never replaced: `sce00061` is what is fetched, discovered, and audited
throughout -- `map00061` is never consulted at all in this mechanism, so
there is no separate "reference pathway id" to track in provenance,
unlike the rejected gene/EC/KO chain above, which would have needed one.

**Diagnostic cross-check (evaluation only, never hardcoded into
production logic -- ACC1/HFA1/FAS1/FAS2 appear nowhere in
`app/connectors/kegg.py` or `app/pathway_curation/`)**: live-verified that
`sce00061`'s own `GENE` field lists all four (`YNR016C`/`ACC1`,
`YMR207C`/`HFA1`, both `EC:6.4.1.2 6.3.4.14 2.1.3.15`/`KO:K11262`;
`YKL182W`/`FAS1`, `YPL231W`/`FAS2`, both `EC:2.3.1.86`/`KO:K00668`/
`K00667` respectively). In the KGML diagram itself, ACC1/HFA1's own
reaction (`R00742`) is backed by a `type="gene"` entry directly naming
`sce:YMR207C sce:YNR016C` -- the most specific evidence KGML offers.
FAS1/FAS2's iterative elongation cycle is backed by 33 `type="ortholog"`
entries naming `ko:K00665` ("fatty acid synthase, animal type") rather
than yeast's own more specific `K00667`/`K00668` -- KEGG's pathway-diagram
pipeline draws one representative ortholog per diagram position across an
orthology group, which need not be the exact KO an organism's own `GENE`
field lists for the analogous activity. This is a genuine, disclosed
residual limitation of KGML-based membership (§37): the *reaction* is
still correctly attributed to the organism (both entry types are only
drawn on an organism-specific diagram because that organism's genome
mapping includes them), but the *specific KO cited on the diagram node*
is not always the organism's own most-precise one. Nothing in this
package reads or depends on which KO a KGML node names -- only the
reaction id -- so this limitation does not affect correctness of the
implemented mechanism, only a possible future one that tried to use
KGML's KO annotations for finer-grained catalyst attribution.

**F9 is unweakened**: a pathway for which neither KGML nor `link()`
yields any reaction still produces `PATHWAY_REACTION_MEMBERSHIP_EMPTY`,
still blocks `COMPLETE`, and still drives `Agent2ReadinessAssessment
.is_ready = False` via §44's modelable-reaction rule -- confirmed by a
dedicated test. F2 (catalyst discovery) and F5 (Claims/Evidence) are
untouched; same-EC-number is still never sufficient evidence for a
`ReactionEnzyme` association (§42), regardless of this section's own,
separate use of EC/KO numbers during investigation.

**Versioning**: `PATHWAY_CURATION_POLICY_VERSION` remains
`"pathway-curation-v1.1"` -- this is the completion of the same
already-uncommitted C.1 increment §33 introduced, not a new behavioral
generation. `AGENT1_CONTRACT_VERSION` is unchanged: the Agent 1 -> Agent 2
handoff schema itself gained no new field.

## 47. Increment C.2 — Organism-specific catalyst resolution

Real Integration Pilot 1 Run 3 (full-budget structural completion, §46's own
KGML mechanism at scale) reached 38 structurally curated reactions and
**zero** `ReactionEnzyme` associations. The cause: catalyst discovery's only
route (§42, F2) was a broad EC-number search against UniProt, and every
EC-annotation group either surfaced too many organism-scoped candidates to
choose among safely (5-18 for `6.4.1.2`/`2.3.1.86`-shaped annotations) or,
for a genuine, separate reason, surfaced none at all (six annotation groups
carrying **two or more** whitespace-separated EC numbers -- confirmed live
to reliably return zero UniProt candidates, because the entire multi-EC
string was sent as one invalid query clause). Increment C.2 addresses both:
a stronger evidence path that avoids needing the broad EC search at all for
most reactions, and a fix to that search's own query construction for when
it must still run.

**The stronger evidence path**: an organism-specific KEGG pathway's KGML
diagram (already the source of §46's own reaction membership) also draws
`type="gene"` diagram nodes that directly, explicitly associate one or more
organism-specific KEGG gene ids with a specific reaction -- confirmed live
on `sce00061` to exist for **every one of its 41 reactions**, tracing to
exactly its own 13 `GENE`-field genes. `app.connectors.kegg.parse_kgml_entries`
parses every `<entry>` element in a KGML document (a lower-level, more
general counterpart to §46's `parse_kgml_reaction_ids`, which reads a
different, top-level element); `app.pathway_curation.strategies
.discover_catalyst_context` turns that into `KgmlCatalystContext` --
`direct_gene_evidence` (from `type="gene"` entries only) and
`complex_flagged_reaction_ids` (from `type="group"` entries, see below).

**Gene and Protein resolution reuse existing machinery unchanged.** A KEGG
organism-specific gene id (e.g. `YER061C`) is, for *S. cerevisiae*, already
the same systematic ORF/locus-tag text SGD's own search accepts --
`strategies.resolve_gene_by_kegg_gene_id` is a thin, documented reuse of
`resolve_gene_by_text` with that id as the query, and once a Gene resolves,
its own `symbol` (falling back to `systematic_name`) becomes the UniProt
query text for `resolve_protein_by_text`, unmodified. Nothing about Gene/
Protein normalization, lookup, or persistence changed at all.

**Precedence, enforced in `executor._resolve_direct_catalysts_from_kgml`,
called before the pre-existing EC fallback every iteration**: a reaction
with direct KGML gene evidence is handled from that evidence alone -- the
broad EC search never even runs for it, successful or not (a failed direct
gene/protein resolution is disclosed as `REACTION_CATALYST_UNRESOLVED`,
never silently replaced by an EC-matched substitute). `executor
._discover_catalysts_from_reactions` (the EC fallback) now takes a
`skip_kegg_reaction_ids` set populated by exactly that function, and remains
otherwise unchanged in spirit: still tried only for reactions with no direct
evidence, still conservative about ambiguity (§26 below).

**Multiple genes at one entry are not automatically ambiguity.** KGML's own
`type="gene"` entry may list more than one gene in its `name` attribute at
one diagram position (confirmed live: `sce00061`'s own ACC1/HFA1 entry for
`R00742`) -- this is KEGG's documented syntax for alternative/isozyme gene
products at that position, structurally distinct from its separate
`type="group"`/`<component>` mechanism for representing an explicit,
multi-node visual complex. Both genes are resolved independently, and both
may receive their own `ReactionEnzyme` row -- `app.models.reaction
.ReactionEnzyme`'s own uniqueness constraint is per `(reaction_id,
protein_id)` pair, not per reaction, so this required no model change. A
`type="group"` entry's own reaction id(s), by contrast, are never treated as
gene evidence at all: KGML's group/component structure does not by itself
establish whether the grouped entities are independent isozymes or obligate
complex subunits, this package still never creates an `EnzymeComplex`
(§37/§45, unchanged), and the reaction is disclosed as unresolved catalyst
context rather than guessing either interpretation. *Diagnostic-only, never
hardcoded*: `sce00061` has zero `type="group"` entries -- FAS1/FAS2's own
entry (`R05190`) uses the plain multi-name syntax, same as ACC1/HFA1, so
this package's own general rule (not any FAS-specific one) would associate
both as independent catalysts if that reaction's own participants had
resolved fully (they do not -- §46's already-disclosed `R05190`
polymer-notation gap is unrelated and untouched by C.2).

**Orthologs are never organism-specific genes.** A `type="ortholog"` entry
(a cross-organism orthology-group id, KO) never populates
`direct_gene_evidence` -- Step 14's own principle, reflected directly in
`discover_catalyst_context`'s type check. A reaction whose only
KGML-associated entry is an ortholog produces no direct evidence at all;
the EC fallback remains available for it, unchanged.

**The multi-EC query fix**: `Reaction.ec_number` may itself be a single,
whitespace-separated multi-value string (KEGG's own `ENZYME` field
convention, verified against `app.normalization.reaction
.reaction_identity_from_kegg`'s own docstring). `strategies.split_ec_numbers`
splits it into distinct tokens; `strategies.is_fully_classified_ec` excludes
wildcard/partial ones (`"1.3.1.-"`) from ever being queried at all (UniProt's
`ec:` clause has no documented wildcard syntax, and this package never
broadens a wildcard into a guessed full EC number); each remaining token is
queried independently
(`strategies.discover_catalyst_candidates_for_one_ec`, with its own
EC-token-level cache so two reactions sharing one EC number never re-query
it); the resulting raw candidate pools are merged and deduplicated by
UniProt accession before being classified exactly once
(`strategies.classify_and_persist_protein_candidates`) -- never once per EC,
which would treat two ECs on the same reaction as unrelated searches. EC
equality alone still never establishes a `ReactionEnzyme` by itself (§26 of
the Increment C.2 instructions; unchanged, and covered by a dedicated
regression test).

**Contradictory evidence is disclosed, never silently resolved either way.**
When direct KGML gene evidence resolves to a protein whose own curated EC
number(s) share nothing at all with the reaction's own EC annotation,
`executor._check_direct_catalyst_ec_contradiction` records a warning -- the
association is still persisted (direct reaction->gene evidence outranks a
generic EC-agreement check), and the disclosure exists so a human reviewer
can investigate, not to block either source.

**Non-goals, unchanged by this increment**: `R07762`/`R07763`'s missing
`NAME`, `R02768`'s name collision, `R05190`'s polymer stoichiometry,
enzyme-complex construction, F5's publication-export gap, regulation/
enzyme-state discovery, and Pilot 1 Run 4 are all explicitly out of scope
and untouched.

**Versioning**: `PATHWAY_CURATION_POLICY_VERSION` bumps to
`"pathway-curation-v1.2"` -- catalyst-discovery precedence materially
changed (direct KGML gene evidence now runs before, and can entirely
preempt, the EC-based search), exactly the bumping criterion §33 states.
`AGENT1_CONTRACT_VERSION` is unchanged: `ReactionEnzyme` associations flow
through the existing, unmodified `Agent1KnowledgePackage`/
`Agent1CuratedKnowledgeView` export chain -- more rows populate an existing
field, not a new one.

## 48. Final architectural rule

> A high-level curation request is planned deterministically and executed
> within an explicit, auditable budget -- never an open-ended agent loop.
>
> Every entity this package creates was already reachable through an
> existing connector, normalizer, and persister; this package only
> orchestrates calling them, and invents nothing they would not have
> produced on their own -- including the participants and catalysts a
> reaction actually has, never a guess standing in for either.
>
> Incomplete biological knowledge is `COMPLETE_WITH_GAPS`, not a failure --
> a documented frontier item, never a fabricated fact filling the gap.
> Missing kinetics and missing regulation are disclosed scientific gaps
> that never by themselves make an otherwise structurally valid pathway
> unusable by Agent 2; a reaction with no participants, or a dangling
> structural reference, is a different kind of problem, and is never
> reported as ready.
>
> Autonomy here means autonomous planning, connector orchestration, and
> self-assessment of the result; it never means autonomous acceptance of
> curated knowledge, and it never means Agent 1 building, simulating, or
> critiquing a model.
>
> A structural claim this package makes about a real biological source --
> "this pathway has these reactions," "this export is ready" -- must be
> verified against how that source actually behaves today, not against how
> it was assumed to behave when this package was first written; Pilot 1
> Run 1 exists precisely because that verification was still owed, and
> Increment C.1 is what paying it looks like.
