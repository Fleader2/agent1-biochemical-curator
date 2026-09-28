# Experimental Context and Quantitative Observation Framework

Agent 1.x Increment "Experimental Context and Quantitative Observation
Framework." A foundational, connector-agnostic schema for reference,
experiment-specific, and time-series quantitative biological data --
protein abundance, protein/metabolite concentration, reaction flux, cell
volume, growth rate, and future observation types -- intended as the
common ingestion model for SGD reference protein abundance now, and any
future custom database later. **No connector, no ingestion, and no Agent
2/4 logic is implemented by this increment** -- schema, normalization
policy, and Agent 1 handoff only.

## 1. Objective

Agent 1 should be able to answer: *what quantitative biological
information is known, from what source, under what experimental
condition, perturbation, replicate, and time point?* -- for both static/
reference observations (e.g. SGD's reference protein abundance) and
dynamic, time-resolved ones, in one framework, without redesigning the
schema for each new observation type a future connector might add.

## 2. Core entities

Four new tables (migration `0017_exp_context_qobs`; see
`docs/02_database_schema.md` for the exact column listing):

* **`ExperimentalContext`** (`app.models.experimental_context`) --
  organism, strain, genotype, medium, carbon source, temperature, pH,
  growth phase, growth condition, a reference-vs-experiment-specific
  classification, and its own provenance. **Distinct from the
  pre-existing `ExperimentalCondition`** (`app.models.experimental_condition`,
  used only by the Claims/Evidence pipeline via `EvidenceCondition`): that
  table has no organism/strain/genotype/classification fields and is
  scoped narrowly to one `Claim`'s own supporting evidence. Neither table
  is deprecated by the other's existence; they serve different, non-
  overlapping consumers.
* **`Perturbation`** (`app.models.perturbation`) -- an open
  `perturbation_type` (genetic, chemical, nutrient/environmental,
  induction/repression, temperature/pH, or any future kind, without a
  migration), target, magnitude/unit, and onset/duration timing (see §4).
* **Replicates** -- deliberately *not* their own table. `biological_replicate_id`/
  `technical_replicate_id` are plain, optional string columns directly on
  `QuantitativeObservation`: a replicate has no independent attributes or
  identity beyond identifying which repeat an observation belongs to, so
  a separate table would be pure overhead. Never required -- a source
  that reports no replicate information leaves both `NULL`.
* **`QuantitativeObservation`** (`app.models.quantitative_observation`) --
  the central table. `observation_type` is a deliberately open `VARCHAR`,
  never a closed enum -- mirroring `KineticMeasurement.parameter_type`'s
  own established "never restrict the database so tightly that future
  types cannot be added" policy exactly (`app/models/enums.py`'s own
  module docstring). `app.normalization.quantitative_observation
  .QuantitativeObservationType` is the typo-proof, normalization-layer
  vocabulary for populating it (mirrors `KineticParameterType`'s own
  relationship to `parameter_type`), naming at minimum:
  `PROTEIN_ABUNDANCE`, `PROTEIN_CONCENTRATION`, `METABOLITE_CONCENTRATION`,
  `REACTION_FLUX`, `CELL_VOLUME`, `GROWTH_RATE`, and `OTHER` as the
  catch-all that makes "future observation types can be added without
  major schema redesign" literally true.

## 3. Time-series support

Time is a first-class field set on `QuantitativeObservation`, never
encoded only in free-text `notes`:

* `time_reference_basis` (`app.models.enums.TimeReferenceBasis`:
  `EXPERIMENT_START` | `PERTURBATION_ONSET`) -- `NULL` itself (never a
  member of this enum) represents "no time / a reference value," the
  first of the task's own four listed cases. There is deliberately no
  `NONE`/`REFERENCE` member, since the absence of a value already means
  that.
* `time_value`/`time_unit` -- the source's own as-reported figure,
  preserved verbatim.
* `time_canonical_s` -- the canonical-seconds conversion
  (`app.normalization.quantitative_observation.convert_time_to_seconds`),
  a closed, explicit vocabulary (`s`/`sec`/`min`/`h`/`day`, plural/
  abbreviated variants) -- `None` when the unit is not recognized, never
  guessed.
* A database `CHECK` (`ck_quantitative_observation_time_reference_requires_time`)
  requires that a non-null `time_reference_basis` always be accompanied
  by an actual time value -- a stated basis with no time value would be
  meaningless. Enforced a second time, identically, in
  `QuantitativeObservationIdentity.__post_init__` (so a caller gets a
  clear `ValueError` before ever reaching the database), and a third
  constraint -- `time_reference_basis == PERTURBATION_ONSET` requires
  `perturbation_id` to be set -- is enforced only at that Python layer
  (mirrors `KineticMeasurementIdentity`'s own paired-field validation
  convention for `original_source`/`original_source_identifier`; a
  cross-table conditional check was judged not worth a database `CHECK`
  for this one case).

`Perturbation`'s own `start_time_*`/`duration_*` columns apply the
identical reported-value/canonical-seconds discipline. An explicit end
time is deliberately never its own column: it is always exactly
`start_time_canonical_s + duration_canonical_s` once both are known, so a
third, independently-stored value could only ever drift out of sync with
the other two. A source that reports an end time instead of a duration
has that end time converted to a duration relative to the same context's
own start time at ingestion time -- ordinary arithmetic, not a scientific
derivation requiring `QuantitativeObservationDependency` tracking (§7).

## 4. Identity links

`QuantitativeObservation.protein_id`/`compound_id`/`reaction_id`/
`organism_id` link deterministically to existing Agent 1 entities --
accepted only as already-resolved UUIDs the caller supplies, exactly like
`KineticMeasurementIdentity`'s own established policy. **Never inferred
from fuzzy text.** When a source names a specific biological identity
that could not be deterministically resolved, `unresolved_identity_kind`
(open text naming *what kind* of entity, e.g. `"protein"`/`"compound"`)
and `unresolved_identity_text` (the raw source text) preserve that fact
explicitly, rather than fabricating a link or silently dropping the
record. These two fields are left `NULL` when an identity link is simply
irrelevant for that observation type (e.g. a growth-rate observation has
no compound identity to resolve at all) -- "irrelevant" and "unresolved"
are never conflated.

## 5. Value and units

Every observation preserves `value`/`unit` (as-reported),
`normalized_value`/`normalized_unit` (canonical, or both `None` when
unresolved -- never fabricated), `uncertainty`/`lower_bound`/`upper_bound`
(assumed to share `unit`, kept deliberately simple per this increment's
own "smallest extensible schema" mandate), and `measurement_method`.

**Canonical-unit policy** (`app.normalization.quantitative_observation`),
reusing the project's existing canonical-unit infrastructure
(`app.normalization.kinetic_units`) wherever the dimension already
exists, and introducing exactly two new canonical units where it does
not:

| Observation type | Canonical unit | Recognized-unit family |
|---|---|---|
| `PROTEIN_CONCENTRATION`, `METABOLITE_CONCENTRATION` | `nM` | Same concentration family as `KM`/`KI` |
| `REACTION_FLUX` | `nM_per_s` | Same flux family as `VMAX` |
| `GROWTH_RATE` | `per_sec` | Same dimension as `KCAT`, but its own recognized-unit table (hours are real-world-common for growth rate, never observed for `kcat`) |
| `PROTEIN_ABUNDANCE` | `molecules_per_cell` (new) | `molecules/cell`, `copies/cell`, and spelled-out variants |
| `CELL_VOLUME` | `pL` (new) | `fL`/`pL`/`nL`/`uL`/`mL`/`L` |

`convert_quantitative_unit` mirrors `convert_to_canonical_unit` exactly in
policy and return shape (reusing that module's own
`UnitConversionResult`/`UnitConversionStatus`): a closed, explicit,
mechanically-normalized (NFKC, micro-sign, minus-sign, caret-exponent,
whitespace/case) recognized-unit vocabulary only, never a general unit
parser. An unrecognized unit is left unresolved
(`UnitConversionStatus.UNRECOGNIZED_UNIT`); a unit recognized for a
*different* family is flagged `INCOMPATIBLE_DIMENSION`, never silently
converted across dimensions; an observation type with no canonical target
at all (`OTHER`) is `NOT_APPLICABLE_PARAMETER_TYPE`. Every case is
disclosed via a note on the identity's own `notes` field, exactly like
`KineticMeasurementIdentity`.

`QuantitativeObservationIdentity` (`app.normalization.quantitative_observation`)
is this table's own identity/normalization type, mirroring
`KineticMeasurementIdentity` field-for-field in shape and philosophy: a
pure, source-native-record -> source-neutral-identity reshaping, never
entity resolution, never a scientific derivation.

## 6. Provenance and evidence class

Every observation preserves `source`/`source_id` (a partial unique index
on `(source, source_id)`, both non-null, makes re-ingestion idempotent --
mirroring `kinetic_measurement`'s own identical index), `publication_id`,
`dataset_id` (an open free-text dataset/experiment identifier -- no new
dataset table), and `measurement_method`.

`evidence_class` (`app.models.enums.QuantitativeEvidenceClass`, NOT
NULL) is a deliberately small, closed vocabulary -- unlike
`observation_type`/`perturbation_type`, which are open-ended by design,
an observation's evidence class is always exactly one of:

* `EXPERIMENT_SPECIFIC` -- measured under one specific, non-baseline
  condition.
* `REFERENCE_BASELINE` -- the standard/untreated/baseline condition for
  its organism (e.g. SGD's reference protein abundance).
* `MODEL_PREDICTED` -- produced by a predictive model, never a direct
  measurement.
* `DERIVED` -- computed from other observations plus stated assumptions
  (§7), never itself a direct measurement or a model prediction.

A `DERIVED`/`MODEL_PREDICTED` value must never be represented as
`EXPERIMENT_SPECIFIC`/`REFERENCE_BASELINE` -- this axis exists precisely
so a downstream consumer (a future Agent 2 reconstruction increment, or
Agent 4 calibration) can distinguish a real measurement from a value this
repository, or a predictive model, produced.

`ExperimentalContext.classification`
(`app.models.enums.ExperimentalContextClassification`: `REFERENCE` |
`EXPERIMENT_SPECIFIC`) is a **distinct axis** from `evidence_class`: it
classifies the *context* itself (a growth condition can be "the"
standard reference condition for an organism regardless of which
specific observation later reuses it), never one observation's own
evidence class.

## 7. Derived-value dependency (never a derivation)

`QuantitativeObservationDependency` records that one `DERIVED`
observation depends on one or more other, already-persisted observations
-- e.g. the task's own example, an enzyme-concentration value derived
from a protein-abundance value *and* a cell-volume value. Each row names
one input, an open `role` label (e.g. `"protein_abundance_input"`), and
free-text `assumption_notes` for any extra, non-observational assumption
the derivation required (e.g. "assumed reference cell volume of 0.1 pL in
the absence of strain-specific data").

**This increment represents dependency/provenance only.** No code
anywhere in this repository computes a `DERIVED` observation's own
`value`/`normalized_value` from its declared inputs -- the derived row's
value is always independently supplied by whatever produced it, never
back-filled from its dependencies. A future increment that performs real
derivations would read this table to know which inputs were used and
would still write its own, separately-computed `QuantitativeObservation`
row; this table only ever records which rows *were* used, never performs
the arithmetic itself. `derived_observation_id` cascades on delete (no
independent meaning apart from the derived observation it describes);
`input_observation_id` restricts (an independent scientific observation
must never be deleted out from under a dependency that still names it) --
mirrors `KineticMeasurementProteinContext`'s own identical `ON DELETE`
split. A `CHECK` forbids a row naming itself as its own input.

## 8. Context compatibility

`app.models.enums.ContextCompatibility` (`EXACT_CONTEXT` |
`COMPATIBLE_REFERENCE` | `CONTEXT_MISMATCH` | `CONTEXT_UNKNOWN`) is a
minimal, wholly deterministic vocabulary for comparing two
`ExperimentalContext` records, computed on demand by
`app.normalization.quantitative_observation.classify_context_compatibility`
-- **never persisted as a column on either `ExperimentalContext` or
`QuantitativeObservation`**, since compatibility is a property of a
*pair* of contexts, not of one record alone, and never a numeric or
weighted score (task's own explicit "do not implement sophisticated
scoring yet"): either side missing or organism unknown -> `CONTEXT_UNKNOWN`;
organisms known and different -> `CONTEXT_MISMATCH`; organisms match and
any shared detail field (`strain`/`medium`/`carbon_source`/
`temperature_c`/`ph`/`growth_phase`/`growth_condition`) disagrees ->
`CONTEXT_MISMATCH`; organisms match, nothing disagrees, and every detail
field one side reports is also reported (and equal) on the other ->
`EXACT_CONTEXT`; organisms match, nothing disagrees, but at least one
side reports a field the other leaves unset -> `COMPATIBLE_REFERENCE`;
organisms match but nothing at all was actually comparable ->
`CONTEXT_UNKNOWN`. Never fuzzy-matches strings (e.g. `"YPD"` vs. "yeast
peptone dextrose" is a plain, disagreeing mismatch). This is the
structured condition metadata a later Agent 2/Agent 4 comparison would
need -- this increment preserves it and offers one deterministic
comparison primitive, nothing more.

## 9. Agent 1 handoff

`AGENT1_CONTRACT_VERSION` bumped `"1.3"` -> `"1.4"` (additive fields
only, no existing field removed or repurposed -- see `app/agent1/version`
reasoning inline in `app/agent1/types.py`'s own module docstring).
`Agent1KnowledgePackage` gained `experimental_contexts`/`perturbations`/
`quantitative_observations`/`quantitative_observation_dependencies` (raw
ORM rows); `Agent1CuratedKnowledgeView` gained `experimental_contexts`/
`perturbations`/`quantitative_observations` (the `Curated*` reshapings).
None of the four new tables carries a `Claim`/`CurationState` column, so
this contract's own established "exists = curated" policy applies to all
of them unfiltered -- identical to kinetic measurements and enzyme
regulatory states before them.

`CuratedQuantitativeObservation.dependencies` is computed in
`app.agent1.export` from `package.quantitative_observation_dependencies`
(never copied from a single column), exactly mirroring how
`CuratedKineticMeasurement.protein_ids` is computed from
`package.kinetic_measurement_protein_contexts` -- this, too, never
performs a derivation, only exposes which rows were used.

**Scoping** (`app.agent1.service`): `QuantitativeObservation` is scoped
exactly like `KineticMeasurement` -- by its own `organism_id` when set,
otherwise by whether its `protein_id`/`compound_id`/`reaction_id` names
an already-scoped entity. `Perturbation` has no `organism_id` (or any
other direct scope) of its own at all -- it is included only when an
already-scoped observation references it. `ExperimentalContext` is scoped
by its own `organism_id` *and* by whether an already-scoped observation
references it (a context with no `organism_id` of its own -- legitimate
-- would otherwise be silently dropped even though a scoped observation
names it). `QuantitativeObservationDependency` is scoped transitively
through already-scoped observation ids, exactly like
`KineticMeasurementProteinContext` through `kinetic_measurement_ids`.

## 10. Future custom-database integration path

A future custom connector supplying protein/metabolite concentrations,
fluxes, cell volumes, perturbations, longitudinal/time-series
measurements, or replicate data needs **no new Agent 2 contract change**
to be consumed, because it targets exactly the same four tables and the
same `Agent1CuratedKnowledgeView` shape this increment already
introduces:

1. **New observation types** need only a new
   `QuantitativeObservationType` value (or, if the connector's own
   vocabulary does not map cleanly, `OTHER` with `reported_observation_type`
   preserving its own label) -- `observation_type` is an open column, no
   migration required.
2. **New perturbation kinds** need only a new free-text
   `perturbation_type` value (or a new `PerturbationCategory` member for
   typo-proofing, itself just a normalization-layer convenience, not a
   schema change) -- same open-column policy.
3. **New units** need a new entry in the relevant recognized-unit table
   in `app.normalization.quantitative_observation` (or, for a genuinely
   new dimension, a new canonical unit constant, exactly as
   `CANONICAL_UNIT_MOLECULES_PER_CELL`/`CANONICAL_UNIT_PL` were added by
   this increment) -- an ordinary, additive normalization-layer change,
   never a schema migration.
4. **Time-series/longitudinal data** is already fully supported per
   observation row (§3) -- a connector reporting many time points for one
   biological quantity simply persists one `QuantitativeObservation` row
   per time point, all sharing the same `experimental_context_id`/
   `perturbation_id`/replicate ids.
5. **Replicate data** is already supported per observation row (§2) --
   a connector reporting multiple biological/technical replicates simply
   sets `biological_replicate_id`/`technical_replicate_id` distinctly per
   row.
6. **New identity-linking needs** (a custom database naming a protein/
   compound/reaction by an identifier Agent 1 does not yet resolve)
   follow the existing, unmodified entity-resolution pattern every other
   connector already uses (`app.entity_resolution`/`app.normalization.*`)
   -- resolved UUIDs are supplied to `QuantitativeObservationIdentity`
   exactly like every other identity field; an unresolved identity
   degrades gracefully to `unresolved_identity_kind`/
   `unresolved_identity_text` (§4), never a blocked ingestion.

None of this requires touching `app.agent1.types`/`.service`/`.export`
again unless a genuinely new *category* of information is introduced (the
same bar every prior Agent 1.x increment has already applied to
`AGENT1_CONTRACT_VERSION` bumps) -- a new *source* of the same four
tables' existing shape is, by design, a connector-only change.

## 11. Explicit non-goals (this increment)

Never implements a connector (SGD abundance or otherwise) beyond the
minimal fixture wiring proven by this increment's own tests; never
implements Agent 2 reconstruction logic (see
`docs/15_macroscopic_to_microscopic_kinetic_reconstruction_design.md` in
`agent2-antimony-builder` for that separate design increment) or Agent 4
calibration; never derives microscopic rate constants; never ingests an
FBA model; never adds a heuristic parameter value; never performs
statistical analysis of a time series; never computes a `DERIVED`
observation's own value (§7).

## 12. Testing

`tests/database/test_group_i_models.py` covers schema-level behavior:
static reference observations, time-series observations (including the
`CHECK` requiring an actual time value), perturbation onset/duration,
biological/technical replicates (including "never required"), protein/
compound/reaction-linked observations, cell-volume observations,
unresolved-identity preservation, uncertainty/bounds preservation,
provenance preservation, the `(source, source_id)` idempotency index, and
derived-observation dependency representation (including the
self-reference `CHECK` and the "never computed here" guarantee).
`tests/normalization/test_quantitative_observation.py` covers
`convert_quantitative_unit`/`convert_time_to_seconds`/
`QuantitativeObservationIdentity` (including every validation branch) and
`classify_context_compatibility` (every branch, plus determinism).
`tests/agent1/test_quantitative_observation_handoff.py` covers the full
package -> curated-view round trip, including scoping and dependency
reshaping, and confirms the contract-version bump.

## 13. Versioning

`AGENT1_CONTRACT_VERSION` bumped `"1.3"` -> `"1.4"` (additive fields on
both container types -- a real shape change per this repository's own
established bump criterion). Four new `app.models.enums` members
(`QuantitativeEvidenceClass`, `ExperimentalContextClassification`,
`TimeReferenceBasis`, `ContextCompatibility`) and one migration
(`0017_exp_context_qobs`, chosen short deliberately -- see that file's
own docstring for why: `alembic_version.version_num` is `VARCHAR(32)`).
No existing table, column, or contract field was altered.

## 14. Final architectural statement

> This framework represents what quantitative biological information is
> known, from what source, under what condition, and when -- it never
> decides what a downstream model should do with that information.
> Reconstruction, calibration, and derivation are the explicit
> responsibility of later, separately-scoped increments.
