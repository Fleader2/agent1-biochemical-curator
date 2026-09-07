# Agent 1.x Increment A — Kinetic Data Curation and Handoff

## 1. Purpose

This document defines what Agent 1.x Increment A added: two new external
kinetic-data connectors (SABIO-RK, Open Enzyme Database), a normalization
layer that reshapes their output into a source-neutral identity without
resolving entity identity itself, a persistence layer that ingests kinetic
measurements idempotently, and an extension of Agent 1's own output
contract (and, in the sibling `agent2-antimony-builder` repository, of the
Agent 1 → Agent 2 handoff contract) so curated kinetic measurements are
now available as input data. Agent 1 v1 itself (`docs/23_agent1_v1_scope_and_completion.md`)
remains the frozen baseline this increment extends, never rewrites.

## 2. Architectural boundary

```
SABIO-RK / Open Enzyme Database / BRENDA / papers
        -> Agent 1 curates reported kinetic facts
        -> Agent1CuratedKnowledgeView (kinetic_measurements)
        -> Agent 2 decides model usage / kinetic-law mapping / parameter declaration
        -> Agent 4 parameter fitting / calibration
```

Agent 1's role stops at *curating what was reported*. It never selects a
kinetic law, never decides which measurement satisfies which model
parameter, and never fits or calibrates anything. Agent 2's role (a future
increment in the sibling repository) is the mapping decision; Agent 4's
role is fitting/calibration against a real model. Nothing in this
increment blurs any of these boundaries.

## 3. Why new connectors were needed

Agent 1 already had a BRENDA kinetic connector (`app/connectors/brenda.py`)
covering Km/Ki/kcat/kcat-Km/pH-optimum/temperature-optimum/specific-activity.
SABIO-RK and Open Enzyme Database (OED) are two additional, independently
curated kinetic-parameter sources; OED itself aggregates data originating
from BRENDA/SABIO-RK, which is exactly why source-lineage handling (§15-§16)
matters for this increment specifically, not a hypothetical future one.

## 4. Existing kinetic schema: inspection findings

Before any code was written, the existing `app/models/kinetic_measurement.py`
schema was inspected directly (not assumed from memory or from this
increment's own prompt). It already modeled a single, richly-attributed
kinetic measurement (parameter type/value/unit, original vs. normalized
value/unit, substrate/organism/strain/temperature/pH/buffer/enzyme
concentration, publication/evidence linkage, confidence). Its own
pre-existing docstring already states the governing policy this increment
relies on throughout: "This table must never contain averaged values
derived from multiple papers ... two measurements that appear identical
... must both be able to persist as independent rows." No parallel kinetic
schema was created — the existing model was sufficient, extended only with
the four columns in §17.

## 5. SourceType extension

`app/models/enums.SourceType` gained two new members, `SABIORK` and `OED`,
added at the end of the enum, after every pre-existing value and before
`OTHER` — no existing value was renamed, reordered, or removed. This
required extending a native PostgreSQL `ENUM` (§17's migration), which is
one-way in PostgreSQL: `ALTER TYPE ... ADD VALUE` has no reverse. This is
documented at both the enum's own docstring and the migration's docstring,
not left implicit.

## 6. SABIO-RK: live API findings

SABIO-RK's long-documented legacy SOAP/REST endpoint
(`sabioRestWebServices`) was confirmed retired. The current, live public
interface, confirmed via direct HTTP calls this increment, is a plain
Apache Solr `/select` endpoint at
`https://sabiork.h-its.org/api/ft/proxy-select`, using standard Solr query
syntax (`q=ECNumber:<ec>`, `q=EntryID:<id>`), requiring no authentication.
Each Solr document additionally carries a field literally named `Json`
holding the complete structured per-entry record (organism, enzyme,
reaction, experimental conditions, publication, and every kinetic-law
parameter) — this is the field this connector actually parses; the flat
Solr fields alone were observed to be an incomplete, denormalized
projection of the same data.

## 7. SABIO-RK connector implementation

`app/connectors/sabiork.py` implements `SabiorkConnector` (`search()`,
`fetch()`, `normalize()`) following this repository's established
connector conventions exactly: it reuses `ConnectorHttpClient` for all
retrieval (no second HTTP stack, no retry/backoff/cache logic of its own),
raises the shared `ConnectorError` hierarchy, and separates raw retrieval,
source-native parsing (`parse_solr_response`, `parse_kinetic_law_json`),
and connector-level normalization into distinct functions. Only
`kineticlaw.parameter[]` entries whose `role` is `"Constant"` become
`SabioKineticParameter` records — `"Variable"`-role entries are assay
conditions (e.g. a tested substrate concentration), never reported
constants. One SABIO-RK entry commonly reports more than one parameter
type (e.g. both Km and kcat); each becomes its own independent record,
never merged.

## 8. Open Enzyme Database: live API findings

OED's real, live public API root, confirmed via direct HTTP calls this
increment, is `http://openenzymedb-api.platform.moleculemaker.org/api/v1`
— distinct from its marketing/browse site
(`https://openenzymedb.platform.moleculemaker.org`), which this connector
never scrapes. Exactly two endpoints exist: `/data` (paginated
kinetic-parameter records, filterable by EC number/organism/UniProt ID)
and `/metadata` (summary counts, not used by this connector). **No
prediction endpoint exists at all** — OED's own site describes a
separate, notebook/client-side ML prediction tool that has no
corresponding data-API endpoint. **No source-lineage field is exposed** —
despite OED's own description of itself as aggregating from
BRENDA/SABIO-RK, no field in a live `/data` row identifies which
underlying database, or which original record within it, a given row came
from. **No native per-record ID is exposed** — a single row commonly
reports up to three parameter values (`kcat`, `km`, `kcat_km`) as sibling
fields on one JSON object.

## 9. Open Enzyme Database connector implementation

`app/connectors/open_enzyme_database.py` implements
`OpenEnzymeDatabaseConnector`. `fetch()` always raises `NotImplementedError`
(OED has no fetch-by-id endpoint) — callers must use `search()`.
`normalize()` splits one `OedDataRow` into zero to three independent
`OedKineticParameter` records (one per non-null parameter value),
computing a deterministic identity for each via SHA-256 of a canonical,
sorted-keys JSON encoding of the row's own identifying fields plus the
specific parameter type — never Python's built-in `hash()`, and never a
value-based digest that would collide two independent measurements
sharing a coincidental numeric value. `original_source`/
`original_source_identifier` are always `None` on this connector's own
output, per §8's finding.

## 10. Experimental-data-only policy

Both new connectors are structurally incapable of returning AI-predicted
data: SABIO-RK's Solr endpoint has no prediction concept at all, and
OED's live API exposes no prediction endpoint to call (§8) —
`tests/connectors/test_open_enzyme_database.py::test_oed_search_calls_only_data_endpoint`
asserts this connector never calls any path other than `/data`. No record
whose experimental status could not be established is silently treated as
experimental anywhere in this increment; there is no such ambiguous case
in either connector's actual output.

## 11. No unit conversion policy

Agent 1 has no trusted unit-normalization framework yet. Every adapter in
`app/normalization/kinetic_measurement.py` copies a source's own reported
unit string verbatim into `KineticMeasurementIdentity.unit`, and
`normalized_value`/`normalized_unit` are left `None` for every source in
this increment — including for SABIO-RK, whose own API additionally
reports an SI-normalized pair (`n_start_value`/`unit.n_name`) that this
connector deliberately does not surface as Agent 1's own normalized
value, since SABIO-RK performed that conversion, not Agent 1. This is
carried all the way through: `app.persistence.kinetic_measurement` also
never populates `KineticMeasurement.normalized_value`/`.normalized_unit`,
and Agent 2's `CuratedKineticMeasurement.normalized_value`/
`.normalized_unit` are `None` for the same reason.

## 12. No rounding, no averaging, no range-combining

`app.normalization.kinetic_measurement.parse_decimal` parses every
reported numeric string directly into a `Decimal` — never through
`float` first, which would introduce binary floating-point representation
error before the value ever reached `Decimal`. BRENDA's occasional
reported range (`parameter_value`/`parameter_value_maximum`) is kept as
two entirely separate fields (`KineticMeasurementIdentity.value`/
`.value_maximum`), never averaged into one point value.
`KineticMeasurement` has no dedicated column for a range maximum (a
pre-existing schema limitation, not introduced by this increment); when
present, it is preserved as a clearly-labeled sentence appended to
`notes` by `app.persistence.kinetic_measurement._build_notes`, never
dropped and never blended into `parameter_value`.

## 13. Already-resolved-identity-only policy

Every entity reference on `KineticMeasurementIdentity`
(`reaction_id`/`protein_id`/`complex_id`/`substrate_id`/`organism_id`/
`publication_id`) is accepted only as an already-normalized UUID supplied
by the caller. No adapter in `app.normalization.kinetic_measurement`, no
function in `app.connectors.sabiork`/`app.connectors.open_enzyme_database`,
and no function in `app.persistence.kinetic_measurement` ever resolves an
entity from an EC number, organism name, substrate name, or publication
title itself — resolving those identities (were Agent 1 ever wired up to
call these connectors end-to-end) remains the job of the existing
`app.normalization.reaction`/`.protein`/`.compound`/`.organism`/
`.publication` modules, exactly as established by every prior increment.

## 14. Deterministic source-record identity, never numeric-equality dedup

`kinetic_measurement` carries a partial unique index,
`uq_kinetic_measurement_source_source_id`, on `(source, source_id)` (both
non-null) — a *source-record* identity, never a *value* identity. Two
independent experiments that report the identical numeric value for the
identical parameter type still have two different `(source, source_id)`
pairs (when the sources themselves distinguish the records) and both
persist as independent rows, per §4's pre-existing schema policy.
SABIO-RK supplies a native `EntryID`; this connector's normalization layer
combines it with a parameter's own type/name, since one entry's several
parameters are still independent measurements. BRENDA and OED supply no
native per-record ID, so their adapters compute one deterministically via
SHA-256 of a canonical, sorted-keys JSON encoding — never `hash()`.

## 15. Source lineage preservation, never invention

`KineticMeasurementIdentity.original_source`/`.original_source_identifier`
describe a *reported* upstream origin — populated only when a source
itself states one. Neither BRENDA's nor SABIO-RK's connector in this
repository ever sets them. OED's connector also never sets them, because
OED's live API exposes no such field at all (§8) — this is a disclosed,
genuine gap, not an oversight, and not silently worked around: an OED
record that in reality mirrors an underlying BRENDA/SABIO-RK measurement
cannot be identified as such from OED's API alone today. Should OED's API
ever add a lineage field, `kinetic_identity_from_oed` is the one place
that would need to change to start populating it.

## 16. Cross-source derivative detection, provenance-driven only

`app.persistence.kinetic_measurement.persist_kinetic_measurement`
implements Step 25's conservative derivative-detection policy: when
`identity.original_source`/`.original_source_identifier` are set, it looks
for an *existing* row whose own `(source, source_id)` exactly matches that
claimed origin. If found, the new source is recorded as an additional
`SourceCrossReference` on the existing row (never a duplicate
`KineticMeasurement` row), and the call returns `REUSED_EXISTING`
referencing that row — never adjusting `confidence_score`/
`confidence_class`, upholding the increment's explicit policy: "never
increase confidence or replication counts merely because the same
underlying measurement appears through multiple databases." If the claimed
origin does not yet exist as a row, this is not an error — it falls
through to ordinary creation, since lineage cannot be resolved against a
row that was never ingested. No raw row is ever deleted by this or any
other path in this increment.

## 17. KineticMeasurement schema extension

Migration `0013_kinetic_measurement_sources` (revises
`0012_experiment_execution`) adds the two `SourceType` enum values (§5)
and four new nullable columns to `kinetic_measurement`: `source`/
`source_id` (which connector-ingested source created this row — the same
shape `Evidence.source_type`/`.source_id` already use, deliberately not
routed through `SourceCrossReference`, which has no concept of "primary"
among several cross-references) and `reported_rate_law`/
`reported_parameter_type` (a source's own free-text framing, preserved
independently of the table's own controlled `parameter_type` column). The
partial unique index from §14 is created in the same migration. Verified:
`alembic upgrade head` applies cleanly; `alembic downgrade -1` then
`alembic upgrade head` both succeed (the two new enum values are not
reversed on downgrade — PostgreSQL cannot do this without recreating the
type — only the four columns and the index are, exactly as the
migration's own docstring discloses).

## 18. Normalization layer

`app/normalization/kinetic_measurement.py` defines `KineticParameterType`
(a normalization-layer-only controlled vocabulary; the database column
itself remains an open `VARCHAR`, unchanged), `KineticMeasurementIdentity`,
and three pure adapter functions
(`kinetic_identity_from_brenda`/`_sabiork`/`_oed`). Deliberately absent:
any `NormalizationResult`/`NormalizationStatus`/`*Lookup` protocol, unlike
every other module in this package. This is not an omission — `§4`'s
"every measurement is independent" policy means there is no
identity-dedup *decision* to make (no MATCHED/AMBIGUOUS/CONFLICTED
question exists for this table), only a pure reshaping. The one thing
that resembles deduplication (exact source-record replay) is a
replay-safety guarantee, and it lives entirely in the persistence layer
(§14, §19), backed by the database's own unique index — never here.

## 19. Persistence layer

`app/persistence/kinetic_measurement.py` implements
`persist_kinetic_measurement`, following this repository's established
`SAVEPOINT`/`IntegrityError`-recheck convention exactly
(`app.persistence.knowledge_gap` is its closest precedent): one
`session.begin_nested()` around each insert attempt, an `IntegrityError`
caught and resolved via a post-failure recheck query, every other
exception left to propagate. The caller owns commit/rollback — this
module calls neither `session.commit()` nor `session.rollback()`
anywhere (verified by `tests/persistence/test_kinetic_measurement.py::test_module_never_commits_or_rolls_back`).
`get_kinetic_measurement`/`list_kinetic_measurements_by_reaction` are the
module's read APIs.

## 20. Curated-eligibility policy

`KineticMeasurement` carries no `Claim`/`CurationState` column of its own
(verified directly against the model) — there is no `HUMAN_ACCEPTED`-style
review gate for this table, unlike `Claim`/`Evidence`. The narrowest
consistent policy is therefore "exists = curated," the identical policy
Agent 1's own output layer already applies to `Reaction`/`Compound`/
`Compartment` (structural/schema records with no claim-review gate of
their own) — never the `HUMAN_ACCEPTED`-only gate applied to
`claims`/`evidence`. `Agent1CuratedKnowledgeView.kinetic_measurements`
therefore includes every ingested `KineticMeasurement` in scope,
unfiltered.

## 21. Agent 1 output-contract extension

`app.agent1.types` gained `CuratedKineticMeasurement` and a
`kinetic_measurements` field on both `Agent1KnowledgePackage` (raw
`KineticMeasurement` rows) and `Agent1CuratedKnowledgeView` (the curated,
faithfully-reshaped view, per §20's policy). `app.agent1.service` scopes
`kinetic_measurements` by `organism_id` when set, and otherwise by
whether the row's `reaction_id` names an already-scoped reaction — a row
with neither is only included in a whole-database export. `app.agent1.export`
performs a purely mechanical field-for-field reshaping (no I/O, no
inference) with no kinetic-law selection, parameter mapping, or model
usage decision. `AGENT1_CONTRACT_VERSION` was bumped `"1.0"` -> `"1.1"` —
an additive field, not a breaking reshape of any existing field, hence a
minor bump only, enough to signal the new capability.

## 22. Testing strategy

Every new connector test (`tests/connectors/test_sabiork.py`,
`tests/connectors/test_open_enzyme_database.py`) uses an
`httpx.MockTransport` — no test in this increment makes a real network
call. Coverage includes: search/fetch, malformed/missing-field responses,
the SABIO-RK "Constant"-vs-"Variable" role filter, OED's row-splitting and
deterministic identity, the structural "OED calls only `/data`" guard, and
shared-foundation retry/backoff reuse. `tests/normalization/test_kinetic_measurement.py`
covers `Decimal`-exactness (never `float`), parameter-type mapping,
identity validation (non-empty `source_id`/`unit`, lineage-field pairing),
and all three adapters, including the BRENDA range-maximum and
pH-optimum-dimensionless-unit cases. `tests/persistence/test_kinetic_measurement.py`
covers creation, exact-replay idempotency, independent-measurement
non-deduplication, non-destructive reuse, derivative-lineage merging (both
the successful-match and unresolved-origin cases), confidence
non-adjustment on merge, concurrency (two threads racing to persist the
identical source record yield exactly one row), and the
`SAVEPOINT`/no-commit/no-rollback transaction-handling guarantees.
`tests/agent1/test_outputs.py` covers `kinetic_measurements` exposure on
both the package and the curated view. All of the above ran green
alongside the complete pre-existing suite, including with
`pytest -W error::sqlalchemy.exc.SAWarning`.

## 23. Agent 2 handoff extension (cross-repository)

In `agent2-antimony-builder`, only `app/agent2/types.py` (added
`CuratedKineticMeasurement` and a `kinetic_measurements` field on
`Agent1CuratedKnowledgeViewContract`), `app/agent2/version.py` (bumped
`AGENT1_HANDOFF_VERSION`), `app/agent2/__init__.py` (export), two docs
files, and `tests/agent2/test_contracts.py` changed. No Agent 1 Python
import was added anywhere in that repository (verified by its own
pre-existing AST-based import scan, `test_types_module_never_imports_agent1_package`,
which still passes). `CuratedKineticMeasurement` mirrors Agent 1's own
type field-for-field but independently, using Agent 2's own `str`-id/
validation conventions — never a shared class, never an Agent-1-side
import. The "known handoff gap" paragraph `Agent1CuratedKnowledgeViewContract`
previously carried (recorded during that repository's own Increment 1) is
updated to record that this gap is now closed, and both
`docs/02_agent1_handoff_contract.md` and `docs/04_core_domain_contracts.md`
carry concise notes only — Increment 2's own scope definition in that
repository is unmodified. Agent 2 still performs no kinetic-law selection,
parameter mapping, or model usage decision — the availability of curated
kinetic data does not implement that decision.

## 24. Version bump summary

| Constant | Repository | Before | After | Why |
|---|---|---|---|---|
| `SourceType` enum | Agent 1 | 12 members | 14 members | `SABIORK`/`OED` added (§5) |
| `AGENT1_CONTRACT_VERSION` | Agent 1 | `"1.0"` | `"1.1"` | `kinetic_measurements` added to both output types (§21) |
| `AGENT1_HANDOFF_VERSION` | Agent 2 | `"1.0"` | `"1.1"` | Kept in exact sync with Agent 1's own contract version (§23) |
| `AGENT2_CONTRACT_VERSION` | Agent 2 | `"0.2"` | `"0.2"` (unchanged) | No output-contract shape changed; only an input field was added |
| `BOUNDARY_POLICY_VERSION` | Agent 2 | unchanged | unchanged | No boundary heuristic exists yet |

## 25. Structural verification

No modeling, simulation, fitting, Antimony, boundary, or module behavior
was introduced in either repository by this increment. In Agent 1: no new
code path calls a kinetic-law selection, network assembly, or parameter
initialization routine (none exist in this repository at all). In Agent
2: `tests/agent2/test_contracts.py`'s pre-existing AST-based scans
(`test_no_forbidden_library_imported_anywhere_in_app`,
`test_no_forbidden_definitions_anywhere_in_app`,
`test_no_agent1_connector_or_runtime_import_anywhere_in_app`,
`test_no_antimony_generation_service_module_exists_yet`,
`test_no_simulation_or_validation_module_exists_yet`) all still pass
unmodified against the extended `app/agent2/types.py`.

## 26. Known limitations and disclosed gaps

* No unit-conversion framework exists in Agent 1 — `normalized_value`/
  `normalized_unit` are always `None` this increment, on both repositories'
  contracts (§11).
* `KineticMeasurement` has no dedicated column for a reported range
  maximum — preserved as text in `notes`, not a structured field (§12).
* Open Enzyme Database's live API exposes no source-lineage field —
  cross-source derivative detection (§16) cannot fire for its records
  today, even though OED is known to aggregate from BRENDA/SABIO-RK in
  general (§8, §15).
* SABIO-RK's `Organism:`/`UniProtID:`/`Substrate:` field-scoped search
  filters are implemented per Solr's documented query syntax but were not
  independently, conclusively live-verified this increment beyond
  `EntryID:`/`ECNumber:` — see `app/connectors/sabiork.py`'s own module
  docstring.
* Neither connector, nor the normalization/persistence layers, is wired
  into any Agent 1 ingestion pipeline or CLI in this increment — they
  exist as importable, fully tested modules, exactly like every
  connector/normalization/persistence module added in a prior increment
  before end-to-end orchestration existed for it.

## 27. Explicit non-goals of this increment

This increment does not implement: kinetic-law selection, parameter
mapping from a `CuratedKineticMeasurement` to a `ParameterSpecification`,
network assembly, module boundary heuristics, module decomposition,
Antimony generation, unit conversion/normalization, or any Agent 3/4/5
behavior. Agent 2 Increment 2 ("Whole-Network Assembly") is explicitly not
begun by this increment.

## 28. Completion criteria

Agent 1.x Increment A is complete because:

1. The existing kinetic schema was inspected before any decision was made
   (§4), and no parallel schema was created.
2. `SourceType` was extended following established migration conventions,
   without renaming any existing value (§5).
3. Both new connectors were built against their live, independently
   verified APIs, not assumed from training memory (§6-§9).
4. The mandatory experimental-data-only, no-unit-conversion,
   no-rounding/averaging, already-resolved-identity-only, deterministic-
   identity, and provenance-only-lineage policies are all upheld
   structurally, not just documented (§10-§16).
5. Persistence is idempotent on deterministic source-record identity,
   never on numeric equality, and never silently overwrites (§14, §19).
6. Agent 1's own output contract and the cross-repository Agent 2 handoff
   contract both expose curated kinetic measurements, kept in exact
   version lockstep (§21, §23-§24).
7. No modeling/simulation/Antimony/boundary/module behavior was introduced
   in either repository, verified structurally (§25).
8. Comprehensive, offline-fixture-based tests cover every connector,
   normalization, persistence, and Agent 1 output-layer behavior above,
   and the complete pre-existing test suite in both repositories remains
   green (§22).

## 29. Final scope-freeze statement

> Agent 1 curates what was reported: a measurement, its unit, and where it
> came from — nothing more.
>
> It does not decide which measurement belongs to which model, which
> kinetic law applies, or what value a parameter should ultimately take.
>
> Those decisions belong to Agent 2 and Agent 4.
