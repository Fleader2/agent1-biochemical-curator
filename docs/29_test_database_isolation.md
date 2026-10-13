# Test Database Isolation and the Persistent-Contamination Fix

## 1. Problem

`agent1_test` (the dedicated PostgreSQL database every test run uses, `docs/05_testing.md`)
was found holding substantial, real-looking leftover data: 216 `kinetic_measurement` rows, 38
`reaction` rows, 55 `compound` rows, 13 each of `gene`/`protein`/`compartment`, and 1 `organism`
-- a count signature matching the real yeast `sce00061` fatty-acid pathway dataset this project
curates from live KEGG/BRENDA/SABIO-RK data, but with its own distinct `organism.id`, confirming
it was never a copy of the development database -- some process had, at some point, committed a
genuine curation run's own output directly into the test database and never cleaned it up.

This silently broke 109 tests across `tests/pathway_curation/test_executor.py`,
`tests/persistence/test_kinetic_measurement.py`, `tests/persistence/test_knowledge_gap.py`, and
two others -- every one of them an unfiltered `SELECT` over an entire table, asserting an exact
row count (e.g. `assert len(rows) == 1`) that is only ever true when the table starts genuinely
empty.

## 2. Root cause

`db_session` (`tests/conftest.py`) wraps each test in a transaction on **one connection**,
rolled back unconditionally in its own `finally` block regardless of how the test ends -- this
isolation is airtight for any write made *through that one fixture*, including a test that
raises an exception.

It does nothing at all for a write committed on a **different** connection. A handful of
concurrency tests (`tests/persistence/test_kinetic_measurement.py
::test_concurrent_same_source_record_yields_one_row` and similarly-shaped tests in
`test_knowledge_gap.py`/`test_experiment_execution.py`/`test_experiment_result.py`/
`test_experiment_recommendation.py`/`test_enzyme_state.py`) must use two genuinely independent
connections to exercise a real database-level race, and call `session.commit()` directly -- by
design, and each already cleans up its own specific rows in a `try`/`finally`. But *any* other
out-of-band write on a separate connection -- a one-off script, a manual investigation, a test
process killed before its own cleanup runs -- is just as invisible to `db_session`'s rollback,
and nothing anywhere resets the database to a known baseline before a session starts.

`migrated_engine` (session-scoped) only ran `alembic upgrade head` -- a safe no-op once already
at head, with no opinion at all about what data already sits in the tables. This is the actual
gap: **nothing in the fixture chain ever guaranteed a clean starting state**, so contamination
committed by any out-of-band write, once present, persisted indefinitely across every subsequent
test, every subsequent session, and every subsequent pytest invocation.

## 3. Fix

`tests/conftest.py::reset_test_database(alembic_config)`, called once by `migrated_engine`
before yielding the engine to any test:

```python
def reset_test_database(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
```

Replaying the full migration history -- rather than issuing a blanket `TRUNCATE` across every
table -- is deliberate. A `TRUNCATE`-based first attempt did clear contamination, but also
discarded migration-seeded reference data no test created and none should be able to delete
(migration `0002_reference_data`'s 13 standard compartments), turning
`test_default_compartment_text_resolves_to_the_seeded_reference_compartment` red. A per-table
"truncate unless it's a seed table" exception list would need hand-maintenance every time a
future migration adds more seed data -- exactly the kind of fragile, test-specific workaround
this fix is supposed to avoid. `downgrade("base")` drops every table outright (a safe no-op
against a brand-new, never-migrated database -- confirmed directly: no `alembic_version` table
means there is no current revision to downgrade from); the following `upgrade("head")`
recreates the schema *and* re-runs every seeding migration's own `INSERT`, using the exact same
already-tested code path `tests/database/test_migrations.py` already exercises for "empty
database to head." No duplicated seeding logic, and nothing to keep in sync as new migrations
(including ones that add more seed data) are written.

This runs once per pytest session (`migrated_engine` is session-scoped), costs well under two
seconds for all 19 migrations, and requires zero changes to any application persistence code --
`app.persistence.*` never commits or rolls back on its own (confirmed unchanged); this is purely
test-fixture infrastructure.

## 4. What this does not change

- `db_session`'s own per-test rollback semantics: unchanged.
- The commit-based concurrency tests' own need for two real connections: unchanged, and still
  their own responsibility to clean up their own specific rows (which they already do) --
  `reset_test_database` is the backstop for everything else, not a replacement for that.
- Any application persistence/commit/rollback policy: nothing in `app/` was touched.
- No existing assertion was weakened and no existing test was deleted to make the suite pass --
  every one of the 109 originally-failing tests passes unchanged, because the database they
  observe is now actually empty, as they always assumed.

## 5. Regression coverage

`tests/database/test_fixture_isolation.py` (against disposable `scratch_database` instances,
never the shared `agent1_test`, to avoid any risk to the real suite while testing the reset
mechanism itself):

- a row committed on a connection entirely independent of `db_session` is removed by
  `reset_test_database`;
- the 13 migration-seeded reference compartments survive a reset;
- a realistic foreign-key chain (`organism` -> `network` -> `network_node` -> `network_edge`,
  exercising the KEGG NETWORK support tables specifically) committed out-of-band is fully
  cleared with no foreign-key violation;
- data inserted in one ordinary test (via `db_session`) is absent from the next;
- an uncommitted `db_session` write is invisible to an independent connection immediately, the
  mechanism that makes rollback-after-failure hold for every ordinary test;
- contamination left behind by a simulated "separate, already-finished process" does not
  survive a fresh reset -- the same guarantee a new pytest invocation gets at session start.
