"""PostgreSQL concurrency tests for Increment 11 schema hardening.

Uses independent connections from ``migrated_engine`` (a real connection
pool bound to the test PostgreSQL database) rather than the single-
transaction ``db_session`` fixture -- genuine concurrency requires
genuinely independent sessions/transactions, not two ORM objects sharing
one connection. Each test opens its own connections directly and is
responsible for its own cleanup, since it does not go through
``db_session``'s automatic-rollback machinery.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme
from app.normalization.reaction_enzyme import ReactionEnzymeIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.reaction_enzyme import persist_reaction_enzyme
from app.persistence.reaction_id_allocator import allocate_reaction_internal_id
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database

_THREADS = 8
_PER_THREAD = 25


def test_reaction_internal_id_allocation_is_unique_under_concurrent_sessions(
    migrated_engine: Engine,
) -> None:
    """Many independent connections allocating ids at once must never collide.

    A PostgreSQL sequence is safe by construction (see
    ``app.persistence.reaction_id_allocator``'s module docstring) -- this
    test exercises that guarantee directly, against real concurrent
    connections, rather than trusting the claim untested.
    """

    def allocate_many() -> list[str]:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                return [allocate_reaction_internal_id(session) for _ in range(_PER_THREAD)]
            finally:
                session.close()

    with ThreadPoolExecutor(max_workers=_THREADS) as executor:
        batches = list(executor.map(lambda _: allocate_many(), range(_THREADS)))

    all_ids = [value for batch in batches for value in batch]
    assert len(all_ids) == _THREADS * _PER_THREAD
    assert len(set(all_ids)) == len(all_ids), "sequence-backed allocator produced a duplicate id"


def test_reaction_enzyme_concurrent_new_for_same_pair_yields_one_success(
    migrated_engine: Engine,
) -> None:
    """Two independent sessions racing to create the same (reaction, protein)

    pair must produce exactly one success and one safe, structured failure --
    never two rows, and never a raw unhandled exception escaping
    ``persist_reaction_enzyme``.

    This test commits real rows from independent connections (true
    concurrency requires it), unlike every other test in this suite which
    relies on ``db_session``'s automatic rollback -- so setup uses a
    globally-unique ``internal_id`` (not ``tests/persistence/conftest.py``'s
    ``make_reaction``, whose test-only counter resets every process and is
    only safe under a rolled-back transaction) and teardown explicitly
    deletes everything it committed.
    """
    with migrated_engine.connect() as setup_connection:
        setup_session = Session(bind=setup_connection)
        try:
            organism = Organism(scientific_name=f"test-only concurrency organism {uuid4().hex}")
            setup_session.add(organism)
            setup_session.flush()
            reaction = Reaction(
                internal_id=f"TEST_CONCURRENCY_{uuid4().hex}",
                name="test-only concurrency reaction",
                organism_id=organism.id,
            )
            protein = Protein(organism_id=organism.id, name="test-only concurrency protein")
            setup_session.add_all([reaction, protein])
            setup_session.flush()
            setup_session.commit()
            organism_id, reaction_id, protein_id = organism.id, reaction.id, protein.id
        finally:
            setup_session.close()

    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def attempt(label: str) -> None:
        with migrated_engine.connect() as connection:
            session = Session(bind=connection)
            try:
                identity = ReactionEnzymeIdentity(
                    source=SourceType.OTHER,
                    source_identifier=f"concurrency-{label}-{uuid4()}",
                    reaction_id=reaction_id,
                    protein_id=protein_id,
                    relationship="CATALYZES",
                )
                result = NormalizationResult(
                    status=NormalizationStatus.NEW,
                    source=identity.source,
                    source_identifier=identity.source_identifier,
                    entity_type="reaction_enzyme",
                    match_method=MatchMethod.NONE,
                )
                ready.wait(timeout=5)
                outcomes[label] = persist_reaction_enzyme(identity, result, session=session)
                session.commit()
            except BaseException as exc:
                errors[label] = exc
            finally:
                session.close()

    threads = [threading.Thread(target=attempt, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == {}, f"persist_reaction_enzyme leaked an unhandled exception: {errors}"
        actions = [outcome.action for outcome in outcomes.values()]
        assert actions.count(PersistenceAction.CREATED) == 1
        assert actions.count(PersistenceAction.FAILED) == 1

        with migrated_engine.connect() as verify_connection:
            count = verify_connection.execute(
                select(func.count())
                .select_from(ReactionEnzyme)
                .where(
                    ReactionEnzyme.reaction_id == reaction_id,
                    ReactionEnzyme.protein_id == protein_id,
                )
            ).scalar_one()
            assert count == 1
    finally:
        with migrated_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                delete(ReactionEnzyme).where(ReactionEnzyme.reaction_id == reaction_id)
            )
            cleanup_connection.execute(delete(Reaction).where(Reaction.id == reaction_id))
            cleanup_connection.execute(delete(Protein).where(Protein.id == protein_id))
            cleanup_connection.execute(delete(Organism).where(Organism.id == organism_id))
            cleanup_connection.commit()
