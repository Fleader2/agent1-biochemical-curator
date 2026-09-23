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

`PATHWAY_CURATION_POLICY_VERSION = "pathway-curation-v1"`
(`app.pathway_curation.types`) is this package's own policy version, a
single string constant, bumped only when the planning/execution
*behavior* changes materially -- mirroring the sibling Agent 2
repository's identical versioning convention, never a semantic-versioning
subsystem of its own. It is carried on every `PathwayCurationPlan
.policy_version`.

**This revision keeps `"pathway-curation-v1"` unchanged.** The
acceptance criterion for bumping it is a materially different planning/
execution *behavior* as observed from this package's own public contract
-- and while this revision adds real capability (participant resolution,
catalyst association, OED wiring, the readiness assessment), it does so
entirely by *filling in* previously-declared-but-unimplemented behavior
(the plan's own fixed backbone, the `FrontierReason`/`PlannerAction`
vocabularies, and the request/result shapes were already declared to
support exactly this) rather than changing what the contract itself
promises. This is still the unreleased Increment C implementation, being
completed before its first commit -- not a second, released increment;
`"pathway-curation-v2"` would misrepresent that.

**`AGENT1_CONTRACT_VERSION` is unchanged (`"1.2"`) for a different,
independent reason: `Agent1CuratedKnowledgeView`'s own field shape did not
change.** This revision populates fields that already existed on it
(`reaction_participants`, `reaction_enzyme_associations`) -- via the
existing `get_agent1_knowledge_package`/`get_agent1_curated_knowledge_view`
export chain, entirely unmodified -- rather than adding a new field to
either container type. `AGENT1_CONTRACT_VERSION` bumps only when the
actual Agent 1 -> Agent 2 handoff *schema* changes (`app.agent1.types`'s
own docstring); populating an existing, previously-empty field with real
data is not a schema change. `Agent2ReadinessAssessment` itself is a new,
`app.pathway_curation`-only type, attached only to `PathwayCurationResult`
(this package's own result type) -- it is not part of, and does not
require bumping, the `Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView`
contract at all.

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
`participant_count`, `compound_count`, `compartment_count`,
`reaction_enzyme_count`, `kinetic_measurement_count`,
`enzyme_state_count`).

**Blocking issue codes** (any one of these makes `is_ready` `False`):

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
  `Claim`/`Evidence` on its own.
* Enzyme complexes are never discovered or associated (§22) -- no
  connector in this repository can currently establish complex
  composition, and `Agent2ReadinessAssessment` cannot even verify a
  `complex_id` reference for the same reason (§36).
* Regulation and enzyme-state discovery are accepted request fields with
  no executor implementation (§25) -- explicitly disclosed via the
  `REGULATION_REQUESTED_NOT_SUPPORTED` frontier item, never silent.
* Reaction participants are resolved from KEGG's own `EQUATION` field
  only (§18-19); no Rhea/MetaCyc equation source is parsed (neither
  connector exists in this repository).
* The compartment evidence hierarchy (§21) has exactly one rung today
  (the request's own `default_compartment_text`) -- no connector exposes
  per-participant or per-reaction compartment localization, so there is
  no more specific evidence to prefer over it, and none to fall back to
  beneath it.
* The catalyst evidence hierarchy (§22) is deliberately narrower than a
  fully general one: it requires an explicit request-level seed, not
  merely "a real connector found this protein for this organism" --
  broader organism-specific reaction<->gene mapping connectors (were one
  ever added) could relax this in a future increment without changing
  this executor's own conservative default.
* This increment has not yet been run against the real, live KEGG/SGD/
  UniProt/PubMed/SABIO-RK/OED connectors for the yeast
  fatty-acid-biosynthesis pilot described in §1 -- every test here (§38)
  uses deterministic fake connectors; running the real pilot is an
  explicitly deferred next step, after this revision is reviewed and
  committed.

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

## 39. Final architectural rule

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
