# Agent 1 v1 — Scope, Outputs, and Completion Contract

## 1. Purpose

This document formalizes Agent 1's final scope, verifies that every
intended Agent 1 output is represented in the current architecture, and
freezes Agent 1 v1's boundaries against modeling, simulation, and
scientific-critique capabilities that belong to later agents. It is the
final Agent 1 v1 increment. Agent 2 is explicitly not started here.

**This is the canonical, current-state scope document for Agent 1 v1.**
Where an earlier, historical increment contract (`docs/01`-`docs/22`)
appears to disagree with this document on Agent 1's present
responsibilities, outputs, or limitations, this document controls. Earlier
documents remain valid historical records of the design decisions made at
the time each increment was implemented; they are not rewritten to match
this one except for a short forward pointer where useful. Agent 1 v1 being
"complete" per this document means the v1 scope defined here is fully
implemented and validated -- it does not mean Agent 1 work is finished
forever: Agent 1.x may still add connectors, regulation curation, or
richer cofactor semantics (§21, §27) without those enhancements being
prerequisites for v1 completion.

**Forward pointer (Agent 1.x Increment A).** SABIO-RK and Open Enzyme
Database connectors, kinetic-measurement normalization/persistence, and a
`kinetic_measurements` extension to both `Agent1KnowledgePackage` and
`Agent1CuratedKnowledgeView` (`AGENT1_CONTRACT_VERSION` "1.0" -> "1.1")
were added after this document was frozen. See
`docs/24_kinetic_data_curation_and_handoff.md` for the full contract; this
document's own v1 baseline below is not rewritten.

## 2. Original multi-agent architecture

```text
Agent 1 -- Literature Curator      (this repository)
Agent 2 -- Antimony Builder        (future repository/component)
Agent 3 -- Validator               (future repository/component)
Agent 4 -- Simulator               (future repository/component)
Agent 5 -- Model Critic            (future repository/component)
```

## 3. Agent 1 responsibility

Agent 1 answers exactly one question:

> What biochemical knowledge is supported by the available evidence, how
> well is it supported, what is missing, and what experiments might
> address those gaps?

Agent 1 does not build models.

## 4. Agent 1 inputs

* KEGG (`app.connectors.kegg`)
* BRENDA (`app.connectors.brenda`)
* PubMed (`app.connectors.pubmed`)
* SGD (`app.connectors.sgd`)
* UniProt (`app.connectors.uniprot`)
* scientific papers, via the Evidence Extraction pipeline
  (`app.extraction`)

MetaCyc/BioCyc are named in `docs/01_overview.md`/`docs/03_agent_behavior.md`
as intended sources but have **no implemented connector** in this
repository today (§20).

## 5. Agent 1 outputs

1. Curated entities (organisms, genes, proteins, compounds, compartments)
2. Curated reactions
3. Curated enzymes / reaction-enzyme associations
4. Cofactors, represented through curated compounds/reaction participants
   (§10)
5. Curated regulation (§11 -- schema-ready, not pipeline-complete)
6. References and provenance
7. Confidence assessments
8. Knowledge gaps
9. Experiment recommendations
10. Experiment recommendation lifecycle
11. Experiment execution records
12. Experiment result records

## 6. Final pipeline

```text
Scientific Source
    -> Connector / Retrieval                (app.connectors)
    -> Evidence Extraction                   (app.extraction)
    -> Entity Resolution                     (app.entity_resolution)
    -> Normalization                         (app.normalization)
    -> Claim Generation                      (app.claim_generation)
    -> Single-Evidence Assessment            (app.confidence.scoring)
    -> Multi-Evidence Confidence             (app.confidence.aggregation)
    -> Claim / Evidence Persistence          (app.persistence.claim)
    -> Review Workflow                       (app.review)
    -> Curated Knowledge Base                (app.models / Postgres)
    -> Knowledge Gap Detection                (app.knowledge_gaps)
    -> Experiment Recommendation             (app.experiment_recommendation)
    -> Recommendation Lifecycle              (app.review.experiment_recommendation_workflow)
    -> Experiment Execution / Result Capture (app.persistence.experiment_execution)
    -> Experiment Result Interpretation      (app.result_interpretation)
    -> Agent 1 Read/Export Contract          (app.agent1)  <- this increment
```

**Experiment Result capture (and its deterministic
`EvidenceCandidate` interpretation) is an endpoint for Agent 1 v1.**
`ExperimentResult`/`EvidenceCandidate` are never automatically fed back
into Claim generation, confidence recomputation, or `KnowledgeGap`
resolution. That feedback loop is explicitly deferred (§27).

## 7. Curated entities

| Entity | Status |
|---|---|
| Organism | ORM (`app.models.organism`), normalized (`app.normalization.organism`), persisted, read via `app.agent1.service` |
| Gene | ORM, normalized (`app.normalization.gene`), persisted, read via `app.agent1.service` |
| Protein | ORM, normalized (`app.normalization.protein`), persisted, read via `app.agent1.service` |
| Compound | ORM, normalized (`app.normalization.compound`), persisted, read via `app.agent1.service` |
| Compartment | ORM, normalized (`app.normalization.compartment`), persisted, read via `app.agent1.service` |

All five have a complete normalization + persistence path, verified by the
existing test suites in `tests/normalization/`/`tests/persistence/`. This
increment adds no new logic for any of them -- only a read/export layer.

## 8. Curated reactions

`Reaction`/`ReactionParticipant` (`app.models.reaction`) are normalized
end-to-end (`app.normalization.reaction`), persisted
(`app.persistence.reaction`), and exposed by `app.agent1.service`.
Stoichiometry is preserved exactly (`ReactionParticipant.stoichiometry`, a
`CHECK`-constrained positive `Numeric`); direction/reversibility come from
`Reaction.reversible`.

## 9. Enzymes and reaction-enzyme associations

`ReactionEnzyme` (`app.models.reaction`) associates a reaction with exactly
one of a `Protein`/`EnzymeComplex` (database `CHECK`-enforced XOR),
normalized end-to-end (`app.normalization.reaction_enzyme`), persisted,
and exposed by `app.agent1.service`.

## 10. Cofactor representation

**No separate `Cofactor` entity or `ReactionParticipantRole` value exists.**
`ReactionParticipantRole` has exactly three members
(`REACTANT`/`PRODUCT`/`MODIFIER`, `app.models.enums`) -- verified directly
against the schema before writing this document. A cofactor (ATP, NAD+,
CoA, ...) is preserved as an ordinary `Compound` participating in a
reaction via `ReactionParticipant`, indistinguishable at the schema level
from a non-cofactor substrate except by which specific `Compound` row it
is. Agent 1 v1 **preserves cofactors as compounds/participants but does
not separately classify them** -- no cofactor inference is implemented or
planned for v1 (`tests/agent1/test_end_to_end_contract
.py::test_cofactor_represented_as_ordinary_reaction_participant` verifies
this directly).

## 11. Regulation representation

`RegulatoryInteraction` (`app.models.regulatory_interaction`) is a
complete, well-formed ORM table with its own real `curation_state` column
-- but **no extraction, normalization, or claim-generation pipeline in
this repository writes it today**, verified by direct inspection: no file
under `app/normalization/`, `app/persistence/`, `app/extraction/`, or
`app/claim_generation/` references `RegulatoryInteraction` at all, and no
`app.review` code path ever transitions its `curation_state` (that column
defaults to `PROPOSED` and stays there). The only tests exercising this
table (`tests/database/test_group_f_models.py`) construct rows directly
against the ORM, not through any pipeline.

Per this increment's own instructions (Step 3: "If regulation lacks a
complete normalization or persistence path: do not build a large new
subsystem... expose what exists, document the exact limitation"):
`app.agent1.service` reads and exposes whatever `RegulatoryInteraction`
rows exist (`Agent1KnowledgePackage.regulatory_interactions`), and this
limitation is included verbatim in every package's own `limitations`
field. **Regulation is schema-ready, not curated end-to-end, in Agent 1
v1.**

## 12. References/provenance

`Publication` (`app.models.publication`), `Evidence.publication_id`/
`Evidence.quoted_support`/`Evidence.source_type`/`Evidence.source_id`, and
`SourceCrossReference`/`ExternalRecord` (`app.persistence.provenance`)
together carry a claim's provenance from source to curated row. Exposed
via `Agent1KnowledgePackage.publications`/`.evidence` and summarized,
without fabrication, in `Agent1KnowledgePackage.provenance_summary` (a
plain count of claims with/without evidence, evidence with/without a
publication link or quoted support).

## 13. Confidence

`Claim.confidence_score`/`Claim.confidence_class` (written once, by
`app.persistence.claim.persist_claim_with_evidence`, from
`app.confidence.aggregation.aggregate_claim_confidence`'s own output) are
read verbatim into `ClaimConfidenceSummary` -- `app.agent1` never calls
`app.confidence` itself and never recomputes either value (Increment 27
instructions, Step 12).

## 14. Review

`Claim` carries **no `curation_state` column** (verified directly,
`app.review.workflow`'s own module docstring); its current curation state
is derived entirely from `ReviewEvent` history via
`app.review.workflow.get_current_curation_state` -- made public by this
increment specifically so `app.agent1` could read it without
reimplementing the derivation (previously a private helper,
`_current_curation_state`). `Agent1KnowledgePackage.review_states` carries
one `ClaimReviewState` (current state + full history) per claim.

## 15. Knowledge gaps

`KnowledgeGap` (`app.models.knowledge_gap`), detected deterministically by
`app.knowledge_gaps.analysis.analyze_knowledge_gaps` and persisted by
`app.persistence.knowledge_gap`, is exposed verbatim --
`gap_type`/`severity`/`missing_information`/`status`/`reason_codes_json`/
`supporting_claim_ids_json`/`supporting_evidence_ids_json` all read
directly from the row. No new gap-detection logic exists in this
increment.

## 16. Experiment recommendations

`ExperimentRecommendationRecord` (`app.models.experiment_recommendation`),
generated deterministically by `app.experiment_recommendation.recommender`
and persisted by `app.persistence.experiment_recommendation`, is exposed
verbatim -- `recommendation_status`/`experiment_class`/`objective`/
`required_measurement`/`success_criterion`/`rationale`/`template_id`/
`template_version` all read directly from the row. This is the "suggested
experiments" capability Agent 1 v1 provides. No additional experiment-
design reasoning occurs anywhere in `app.agent1`.

## 17. Experiment recommendation lifecycle

`ExperimentRecommendationRecord.lifecycle_status`
(`PROPOSED`/`ACCEPTED`/`REJECTED`/`DEFERRED`/`SUPERSEDED`) and its full
`ExperimentRecommendationEvent` audit trail are exposed as part of the same
row/read path (§16) -- `app.agent1` never transitions a recommendation's
lifecycle itself.

## 18. Experiment execution/results

`ExperimentExecution`/`ExperimentResult` (`app.models.experiment_execution`),
persisted by `app.persistence.experiment_execution` and transitioned by
`app.review.experiment_execution_workflow`, are exposed verbatim via
`Agent1KnowledgePackage.experiment_executions`/`.experiment_results`.
These demonstrate that Agent 1 can track experiments undertaken to fill
curated knowledge gaps. **`app.agent1` never interprets a result, never
closes a gap automatically, and never modifies a claim** -- verified
directly (`tests/agent1/test_end_to_end_contract.py`'s "no automatic
result interpretation" tests) and inherited unchanged from
`app.result_interpretation`'s own boundary
(`docs/22_experiment_result_interpretation_contract.md`).

## 19. Agent 1 high-level API

```python
from app.agent1 import get_agent1_knowledge_package, get_agent1_curated_knowledge_view

package = get_agent1_knowledge_package(session, organism_id=organism_id)
view = get_agent1_curated_knowledge_view(package)
```

Both are read-only: no `session.add`/`.flush`/`.commit`/`.rollback`, and no
connector call, occurs in `app.agent1.service`/`app.agent1.export`
(verified by source-level tests). `get_agent1_knowledge_package` assembles
`Agent1KnowledgePackage` (§20, full knowledge product);
`get_agent1_curated_knowledge_view` narrows it to
`Agent1CuratedKnowledgeView`, the Agent 2 handoff (§23).

`app.agent1.service` also exposes `curated_claims`/`machine_reviewed_claims`/
`non_curated_claims`/`rejected_claims` -- pure filters over an already-built
package's `review_states`, implementing the eligibility policy in §14/§22
without reimplementing curation-state derivation.

No high-level ingestion orchestration entry point (`curate_source_record`/
`run_agent1_curation`) is introduced in this increment: building one
cleanly would require deciding how `app.entity_resolution`'s
connector-bundle argument, `app.claim_generation`'s `EntityTypingHint`s,
and `app.confidence`'s aggregation window are supplied for an arbitrary
source record -- a design decision, not a "thin orchestration" wrapper.
§6's pipeline diagram documents the intended stage sequence instead
(Increment 27 instructions, Step 8).

## 20. Capability matrix

| Row | ORM | Normalization | Persistence | Review | Provenance | Public API | Limitations |
|---|---|---|---|---|---|---|---|
| Organism | `Organism` | `app.normalization.organism` | `app.persistence.organism` | n/a | n/a | `app.agent1` | none |
| Gene | `Gene` | `app.normalization.gene` | `app.persistence.gene` | n/a | n/a | `app.agent1` | none |
| Protein | `Protein` | `app.normalization.protein` | `app.persistence.protein` | n/a | n/a | `app.agent1` | none |
| Compound | `Compound` | `app.normalization.compound` | `app.persistence.compound` | n/a | n/a | `app.agent1` | none |
| Compartment | `Compartment` | `app.normalization.compartment` | `app.persistence.compartment` | n/a | n/a | `app.agent1` | none |
| Reaction | `Reaction` | `app.normalization.reaction` | `app.persistence.reaction` | n/a | n/a | `app.agent1` | none |
| Reaction participant | `ReactionParticipant` | (part of reaction normalization) | `app.persistence.reaction` | n/a | n/a | `app.agent1` | no cofactor classification (§10) |
| Reaction-enzyme association | `ReactionEnzyme` | `app.normalization.reaction_enzyme` | `app.persistence.reaction_enzyme` | n/a | n/a | `app.agent1` | none |
| Regulation | `RegulatoryInteraction` | **none** | **none** | own `curation_state` column, never transitioned | n/a | `app.agent1` (read of whatever exists) | schema-ready only (§11) |
| Publication/reference | `Publication` | `app.normalization.publication` | `app.persistence.publication` | n/a | is provenance | `app.agent1` | none |
| Evidence/provenance | `Evidence` | n/a (extraction-grounded, §12) | `app.persistence.claim` (write-only, §19) | n/a | is the record | `app.agent1` (direct `SELECT`, §19) | no dedicated read API existed before this increment |
| Confidence | `Claim.confidence_score`/`.confidence_class` | n/a | written once at claim persistence | n/a | n/a | `app.agent1` (read-only) | never recomputed (§13) |
| Review state | derived, no column (§14) | n/a | `ReviewEvent` | `app.review` | is the audit trail | `app.agent1` via `get_current_curation_state` (newly public) | none |
| Knowledge gap | `KnowledgeGap` | n/a | `app.persistence.knowledge_gap` | n/a | supporting ids | `app.agent1` | none |
| Experiment recommendation | `ExperimentRecommendationRecord` | n/a | `app.persistence.experiment_recommendation` | n/a | linked gap | `app.agent1` | none |
| Recommendation lifecycle | `ExperimentRecommendationRecord.lifecycle_status` + `ExperimentRecommendationEvent` | n/a | same | `app.review.experiment_recommendation_workflow` | audit trail | `app.agent1` | none |
| Experiment execution | `ExperimentExecution` + `ExperimentExecutionEvent` | n/a | `app.persistence.experiment_execution` | `app.review.experiment_execution_workflow` | audit trail | `app.agent1` | none |
| Experiment result | `ExperimentResult` | n/a | `app.persistence.experiment_execution` | n/a | linked execution | `app.agent1` | never auto-interpreted (§18, §27) |

## 21. Known Agent 1 v1 limitations

1. Regulation is schema-ready but not curated end-to-end (§11).
2. Cofactors are not deterministically distinguishable from other reaction
   participants (§10).
3. `candidate_subject_text`/`candidate_predicate_text`/
   `candidate_object_text` on an `EvidenceCandidate`
   (`app.result_interpretation`) are populated only when a caller
   explicitly supplies them -- nothing upstream produces this text
   automatically (`docs/22_experiment_result_interpretation_contract.md`
   §10).
4. MetaCyc/BioCyc connectors are not implemented (§4, §33).
5. `app.persistence.claim` exposes no read/list API of its own --
   `app.agent1.service` reads `Claim`/`Evidence` directly via `SELECT`
   rather than duplicating a read function that does not exist.
6. `Agent1KnowledgePackage`'s organism-scoped traversal (§19's `organism_id`
   parameter) is a best-effort convenience, not a guaranteed-complete
   closure over every indirectly-related row; `organism_id=None` always
   sees everything and is the authoritative "give me all data" mode.

## 22. Explicit Agent 1 non-goals

Agent 1 must never implement: Antimony generation, SBML generation, model
assembly, ODE generation, kinetic parameter estimation, parameter fitting,
simulation, Tellurium execution, COPASI execution, sensitivity analysis,
steady-state analysis, mass-balance validation, conservation-law analysis,
thermodynamic model critique, mechanistic hypothesis generation, or
autonomous experiment optimization. Structurally verified
(`tests/agent1/test_scope.py`): no forbidden library is imported anywhere
in `app/`, no forbidden library appears in `pyproject.toml`'s dependency
list, and no function/class definition anywhere in `app/` matches a
forbidden modeling/simulation name pattern.

## 23. Agent 2 boundary

Agent 2 (the Antimony Builder) consumes `Agent1CuratedKnowledgeView`
(`app.agent1.export.get_agent1_curated_knowledge_view`): curated (
`HUMAN_ACCEPTED`-only) reactions, participants, compounds, compartments,
enzyme associations, regulation, provenance, and confidence -- data only.
Agent 1 must not, and does not, transform this into Antimony syntax,
select a kinetic law, choose parameters, or build a reaction-network
graph. That transformation is Agent 2's sole responsibility.

## 24. Agent 3 boundary

Agent 3 (the Validator) checks mass balance, missing species, duplicate
reactions, disconnected subnetworks, unit consistency, and conservation
laws -- against whatever model Agent 2 builds from Agent 1's curated
knowledge. Agent 1 implements none of this (§22).

## 25. Agent 4 boundary

Agent 4 (the Simulator) runs Tellurium/COPASI, parameter scans,
sensitivity analyses, and steady-state calculations against a validated
model. Agent 1 implements none of this (§22).

## 26. Agent 5 boundary

Agent 5 (the Model Critic) asks whether a model violates thermodynamics,
is missing cofactors in its kinetic equations, includes ATP where it
should not, or encodes regulation unsupported by evidence. Agent 1 answers
a related but distinct question -- whether the underlying *biochemical
claim* is evidence-supported (§3) -- never whether a *mathematical model*
built from it is internally consistent. Agent 1 implements none of Agent
5's model-critique logic (§22).

## 27. Future experimental-feedback expansion

Deferred, not part of Agent 1 v1:

* feeding `EvidenceCandidate`/`ExperimentResult` back into Claim
  generation, confidence recomputation, or automatic `KnowledgeGap`
  resolution (§6, §18);
* additional literature/database connectors (MetaCyc, BioCyc);
* a complete regulation curation pipeline (extraction, normalization,
  claim-linkage) (§11);
* richer cofactor semantics (a dedicated classification, role, or
  metadata field) (§10);
* autonomous experiment design beyond the deterministic, template-based
  recommendations already produced by `app.experiment_recommendation`.

## 28. Completion criteria

Agent 1 v1 is declared complete because:

1. Scientific sources enter through the existing connector/extraction
   architecture (§4, §6).
2. Entities and claims are normalized/curated (§7-§9).
3. Evidence and provenance are retained (§12).
4. Confidence is available (§13).
5. Human review exists (§14).
6. Curated biochemical knowledge can be retrieved
   (`get_agent1_knowledge_package`, §19).
7. Knowledge gaps can be detected/persisted (§15).
8. Deterministic experiment recommendations can be generated/persisted/
   reviewed (§16-§17).
9. Experiment executions/results can be recorded (§18).
10. All of the above are exposed through a coherent read-only Agent 1
    output contract (`Agent1KnowledgePackage`/`Agent1CuratedKnowledgeView`,
    §19-§20).
11. No model-building/simulation behavior exists inside Agent 1 (§22,
    verified structurally).

## 29. Final scope-freeze statement

> Agent 1 is the biochemical knowledge curator.
>
> Its responsibility is to determine what biochemical knowledge is
> supported, how strongly it is supported, where the knowledge is
> incomplete, and what experiments could potentially fill those gaps.
>
> Agent 1 does not build, validate, simulate, or critique mathematical
> models.
>
> Those responsibilities belong to Agents 2 through 5.
