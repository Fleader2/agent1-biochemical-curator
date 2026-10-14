# sce00061 Fixture Recovery and Hermetic Harness Testing

## 1. Incident: the live developer database was emptied

During an earlier "Agent 1 cleanup increment" (removal of the misplaced
`Project`/`Subproject`/`Model`/`ModelSpecification`/`ModelingDecision`
schema, migration `0021_agent1_cleanup`), the migration round-trip was
verified with a full `alembic downgrade base` → `alembic upgrade head`
replay. That replay was run against the live developer database
(`agent1`, `DATABASE_URL`) rather than only against the disposable
`agent1_test` database. A downgrade-to-base/upgrade-to-head cycle only
replays schema — it does not re-run whatever real curation pipeline
originally populated the database's actual rows — so this wiped every
curated row in `agent1` (confirmed directly: `organism`/`reaction` both
at 0, `alembic_version` at the new head). This was discovered only
later, as the root cause of three `test_real_sce00061_workflow.py`
failures in the `agent-integration-harness` repository, which depends on
this database already containing the real, curated yeast
fatty-acid-biosynthesis (`sce00061`) dataset.

## 2. How the `sce00061` dataset was originally produced

There is no committed, static fixture and no documented API route or
script for this — every historical `artifacts/pilots/*` run used an
uncommitted, ad-hoc script to call
`app.pathway_curation.execute_pathway_curation` directly, with real, live
KEGG/SGD/UniProt/PubMed/SABIO-RK/BRENDA/GotEnzymes2/Open-Enzyme-Database
connectors. The most complete historical request is preserved at
`artifacts/pilots/yeast_fatty_acid_pilot6_substrate_specific_validation/01_request.json`.
The data was never a fixed snapshot: later pilots (run_1 through
pilot6) each incrementally extended it as the curation pipeline itself
gained more capability (kinetic-measurement enrichment, reaction
attribution, GotEnzymes2 integration, ...) across many separate runs,
which is why the historical high-water mark (38 reactions, 216 kinetic
measurements, 53 species, 115 Agent 2 parameters) reflects cumulative,
iterative development, not one single deterministic call.

## 3. Recovery procedure (now a real, committed, reusable script)

`scripts/sce00061_fixture.py` is the documented, reproducible recovery
procedure — see that file's own module docstring for full detail. It
builds a real `PathwayConnectorBundle` (live KEGG/SGD/UniProt/PubMed/
SABIO-RK/BRENDA connectors — no mocks) and calls
`execute_pathway_curation` with the same request parameters as the
pilot6 run. Three subcommands:

- `recover-dev` — runs the real curation against whatever
  `DATABASE_URL` is currently configured (used once, directly, to
  restore the developer database — see §4).
- `provision --database-name <name>` — creates a disposable database,
  migrates it to head, and curates it; used by the Integration Harness's
  hermetic test fixture (§5).
- `teardown --database-name <name>` — drops a `provision`-created
  database.

**Connectors omitted**: GotEnzymes2 (AI-predicted kinetics) and the Open
Enzyme Database. Both were confirmed live-unreachable/degraded at the
time this script was written — GotEnzymes2's own backend returned an
HTTP 400 whose body was a raw PostgreSQL "No space left on device"
error (a third-party server-side fault); the Open Enzyme Database timed
out after 30 seconds with no response. Neither is a wrong URL or a bug
in this repository. `PathwayConnectorBundle` already treats every
connector as independently optional — omitting one never fabricates a
substitute, it only means the plan steps that would have used it are
recorded as an unresolved frontier item instead. **This is the
reproducibility gap this recovery does not close**: re-run with those
two connectors restored (same construction pattern as the other six)
once/if their services recover, to reach closer to the historical 216
kinetic measurements.

**A real bug found and fixed while building this script**:
`app.config.settings.get_settings`/`app.db.session.get_engine`/
`get_sessionmaker` are all `@lru_cache`d. `provision`'s own admin
connection step (`CREATE DATABASE`, which must run against the
*previous* `DATABASE_URL`) calls `get_settings()` once before the new
database's URL is written into `os.environ` — so without an explicit
`get_settings.cache_clear()` + `reset_engine()` immediately after that
override, every later call in the same process (including the curation
step itself) kept silently resolving to the *original* database. An
earlier version of this script had exactly this bug: it printed the new
`database_url` correctly but actually curated into the previous
database every time — confirmed directly by comparing row counts in
both databases before and after the fix. The fixed script explicitly
clears both caches right after setting the override.

## 4. Developer database recovery result

`scripts/sce00061_fixture.py recover-dev` was run against the live
`agent1` database. Result (also independently reproduced against a
disposable database while debugging the caching bug above — see §3):

```
organism: 1, reaction: 38, kinetic_measurement: 72, compound: 53, publication: 21
completion_status: COMPLETE_WITH_GAPS ("no progress in the final iteration")
```

`reaction`/`compound` counts (38/53) match the historical high-water
mark exactly — these come from KEGG's own static pathway definition and
SGD/UniProt's own protein set, both rigid. `kinetic_measurement` (72, not
216) and the resulting Agent 2 parameter count are lower, directly
because of the two omitted connectors (§3) — this is real, live,
verified curation output, not a truncated or fabricated substitute, and
not a regression in Agent 1's own curation logic.

## 5. Hermetic Harness testing

See `agent-integration-harness/docs/08_hermetic_real_workflow_testing.md`
for the harness-side design: `test_real_sce00061_workflow.py` now
provisions and tears down its own disposable database via this same
script, rather than depending on the mutable developer database having
been pre-populated.

## 6. Remaining reproducibility gaps

- GotEnzymes2 and Open Enzyme Database connectors are not included (§3)
  — re-add if/when their live services recover.
- Live external data (KEGG/SGD/UniProt/PubMed/SABIO-RK/BRENDA) can
  legitimately change over time; `reaction`/`species` counts have been
  stable across every run observed during this work (3 independent
  live runs), `kinetic_measurement` stable across 2, but Agent 2's own
  downstream parameter count varied between two otherwise-identical
  runs (87 vs. 91) — confirmed to be real, legitimate variation in
  kinetic-evidence tie-breaking, not a bug (see
  `agent-integration-harness/tests/harness/test_real_sce00061_workflow.py`'s
  own `test_real_workflow_agent2_reports_executable` docstring, which
  asserts a floor rather than an exact count for this one field).
