#!/usr/bin/env python3
"""Real, live ``sce00061`` (*Saccharomyces cerevisiae* fatty-acid-biosynthesis) reference
dataset curation -- the documented, reproducible recovery procedure for Agent 1's own curated
data.

Written after discovering the developer database (``agent1``) had been emptied by an earlier
migration-round-trip verification run against it (rather than only against ``agent1_test``) --
see ``docs/32_sce00061_fixture_recovery.md`` for the full incident record. Used both for that
one-time developer-database recovery (``recover-dev``) and by the Integration Harness's own
hermetic real-workflow test fixture (``provision``/``teardown`` -- see
``agent-integration-harness/tests/harness/test_real_sce00061_workflow.py``), so neither
depends on hand-inserted rows: both call the identical real
``app.pathway_curation.execute_pathway_curation`` entrypoint, with real, live KEGG/SGD/
UniProt/PubMed/SABIO-RK/BRENDA connectors -- no mocks, nothing fabricated.

Subcommands
-----------

``recover-dev``
    Runs the real curation pipeline against whatever ``DATABASE_URL`` is currently configured
    (normally this repository's own developer database via ``.env``). Commits on success.
    Prints a one-line JSON summary (organism id, resulting row counts) to stdout.

``provision --database-name <name>``
    Creates a brand-new, empty PostgreSQL database named ``<name>`` (via an admin connection
    to the ``postgres`` database, exactly like ``tests/conftest.py``'s own ``scratch_database``
    fixture), migrates it to head with this repository's real Alembic chain, then runs the
    identical real curation step against it. Prints
    ``{"database_url": ..., "organism_id": ..., ...counts}`` as one JSON line to stdout -- a
    caller (the harness's test fixture) reads this to discover both the hermetic database to
    point Agent 1's own runner at, and the real organism id it should request.

``teardown --database-name <name>``
    Drops the database ``provision`` created. Idempotent (``DROP DATABASE IF EXISTS``) -- safe
    to call even if ``provision`` itself failed partway.

Reproducibility note
---------------------
This queries live external services as they exist today; it is **not** guaranteed to
reproduce byte-identical row counts to any specific historical pilot run (KEGG/BRENDA/
SABIO-RK/PubMed content can change over time, and this script omits two connectors -- see
below). It uses the same request parameters as the most complete historical pilot
(``artifacts/pilots/yeast_fatty_acid_pilot6_substrate_specific_validation/01_request.json``:
``source_pathway_id="sce00061"``, ``organism_ncbi_taxonomy_id=559292``, ``strain_text="S288C"``,
``completion_policy=STRUCTURAL_EVIDENCE_AND_KINETICS``, ``mode=STANDARD``).

**Connectors deliberately omitted**: GotEnzymes2 and the Open Enzyme Database. Both were
confirmed live-unreachable/degraded when this script was written -- GotEnzymes2's own backend
returned an HTTP 400 whose body was a raw PostgreSQL "No space left on device" error (a
third-party server-side fault, not a wrong URL or a bug here); the Open Enzyme Database timed
out after 30s with no response. ``PathwayConnectorBundle`` already treats every connector field
as independently optional -- omitting one never fabricates a substitute, it only means the
plan steps that would have used it are recorded as an unresolved frontier item
(``NO_CONNECTOR_AVAILABLE``) instead. Re-run with those two connectors added back (same
pattern as the other six below) once/if their services recover.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_PILOT6_REQUEST = {
    "organism_text": "Saccharomyces cerevisiae",
    "biological_process": "fatty acid biosynthesis",
    "organism_ncbi_taxonomy_id": 559292,
    "strain_text": "S288C",
    "default_compartment_text": "cytosol",
    "source_pathway_id": "sce00061",
    "gotenzymes_organism_code": "sce",
    "include_kinetics": True,
    "include_regulation": True,
    "include_enzyme_states": True,
    "include_publications": True,
    "include_protein_abundance": True,
    "max_iterations": 10,
    "max_connector_calls": 600,
    "max_publications": 5,
}


def _build_connector_bundle():
    import httpx

    from app.connectors.brenda import BrendaConnector, build_brenda_http_client
    from app.connectors.http import ConnectorHttpClient
    from app.connectors.kegg import KeggConnector
    from app.connectors.pubmed import PubMedConnector, build_pubmed_http_client
    from app.connectors.sabiork import SabiorkConnector
    from app.connectors.sgd import SgdConnector
    from app.connectors.uniprot import UniProtConnector
    from app.pathway_curation.executor import PathwayConnectorBundle

    def _plain_client() -> ConnectorHttpClient:
        return ConnectorHttpClient(httpx.Client(timeout=30.0), max_retries=3)

    return PathwayConnectorBundle(
        kegg=KeggConnector(_plain_client(), base_url="https://rest.kegg.jp"),
        sgd=SgdConnector(_plain_client(), base_url="https://www.yeastgenome.org/backend"),
        uniprot=UniProtConnector(_plain_client(), base_url="https://rest.uniprot.org"),
        pubmed=PubMedConnector.from_settings(build_pubmed_http_client(httpx.Client(timeout=30.0))),
        sabiork=SabiorkConnector(
            _plain_client(), base_url="https://sabiork.h-its.org/api/ft/proxy-select"
        ),
        brenda=BrendaConnector.from_settings(build_brenda_http_client(httpx.Client(timeout=30.0))),
    )


def _run_curation() -> dict[str, Any]:
    """Run the real curation step against the currently-configured ``DATABASE_URL``.

    Must be called only after any environment-variable override (``provision``) is already in
    place and this process has not yet called ``get_settings()``/``get_engine()`` -- both are
    process-cached (``app.config.settings.get_settings``, ``app.db.session.get_engine``), so a
    later override within the same process would not take effect.
    """
    from app.db.session import get_sessionmaker
    from app.pathway_curation import PathwayCurationRequest, execute_pathway_curation
    from app.pathway_curation.types import CompletionPolicy, CurationMode

    request = PathwayCurationRequest(
        request_id=f"sce00061-fixture-{uuid.uuid4().hex[:12]}",
        completion_policy=CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS,
        mode=CurationMode.STANDARD,
        **_PILOT6_REQUEST,
    )
    connectors = _build_connector_bundle()

    session = get_sessionmaker()()
    try:
        result = execute_pathway_curation(request, session=session, connectors=connectors)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return {
        "organism_id": str(result.organism_id) if result.organism_id else None,
        "completion_status": result.completion_status.value,
        "completion_reasons": list(result.completion_reasons),
        "warnings": list(result.warnings),
        "unresolved_frontier_count": len(result.unresolved_frontier),
        "connector_calls_made": result.connector_calls_made,
        "reaction_count": len(result.curated_knowledge_view.reactions),
        "kinetic_measurement_count": len(result.curated_knowledge_view.kinetic_measurements),
        "compound_count": len(result.curated_knowledge_view.compounds),
        "publication_count": len(result.curated_knowledge_view.publications),
    }


def _admin_url_and_target(database_name: str) -> tuple[str, str]:
    """``(admin_url, target_database_url)`` for ``database_name``, reusing this
    repository's own currently-configured connection host/credentials -- mirrors
    ``tests/conftest.py``'s ``scratch_database`` fixture exactly."""
    from app.config.settings import get_settings

    base_url = str(get_settings().database_url)
    admin_url, _, _ = base_url.rpartition("/")
    return f"{admin_url}/postgres", f"{admin_url}/{database_name}"


def cmd_recover_dev(_: argparse.Namespace) -> int:
    summary = _run_curation()
    print(json.dumps(summary))
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    from sqlalchemy import create_engine, text

    admin_url, target_url = _admin_url_and_target(args.database_name)

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{args.database_name}"'))
            connection.execute(text(f'CREATE DATABASE "{args.database_name}"'))
    finally:
        admin_engine.dispose()

    os.environ["DATABASE_URL"] = target_url

    # ``_admin_url_and_target`` (above) already called ``get_settings()`` once, against the
    # *previous* ``DATABASE_URL`` -- and ``get_settings``/``get_engine``/``get_sessionmaker``
    # are all ``@lru_cache``d, so without explicitly clearing them here, every later call in
    # this same process (including inside ``_run_curation``) would keep resolving to that
    # stale, pre-override database, silently. Confirmed directly: an earlier version of this
    # function without this clear curated real data into the *previous* database while still
    # printing this call's own ``target_url`` in its summary -- the hermetic hard-recovery
    # incident this script's own module docstring now points to.
    from app.config.settings import get_settings
    from app.db.session import reset_engine

    get_settings.cache_clear()
    reset_engine()

    from alembic import command
    from alembic.config import Config

    alembic_config = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", target_url)
    command.upgrade(alembic_config, "head")

    summary = _run_curation()
    summary["database_url"] = target_url
    print(json.dumps(summary))
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    from sqlalchemy import create_engine, text

    admin_url, _ = _admin_url_and_target(args.database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{args.database_name}"'))
    finally:
        admin_engine.dispose()
    print(json.dumps({"dropped": args.database_name}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("recover-dev").set_defaults(func=cmd_recover_dev)

    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--database-name", required=True)
    provision_parser.set_defaults(func=cmd_provision)

    teardown_parser = subparsers.add_parser("teardown")
    teardown_parser.add_argument("--database-name", required=True)
    teardown_parser.set_defaults(func=cmd_teardown)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
