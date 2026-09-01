"""Database tests for Group G external_record/source_cross_reference/
review_event models.

See ``docs/02_database_schema.md``: "Table: external_record",
"Table: source_cross_reference", "Table: review_event".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import CurationState, SourceType
from app.models.external_record import ExternalRecord
from app.models.review_event import ReviewEvent
from app.models.source_cross_reference import SourceCrossReference

pytestmark = pytest.mark.database


def _minimal_external_record_kwargs() -> dict:
    return {
        "source": SourceType.PUBMED,
        "retrieval_date": datetime.now(UTC),
        "raw_response_hash": "test-only-hash-0001",
    }


def _minimal_source_cross_reference_kwargs() -> dict:
    return {
        "entity_type": "gene",
        "entity_id": uuid.uuid4(),
        "source": SourceType.SGD,
        "external_id": "test-only-ext-0001",
    }


def _minimal_review_event_kwargs() -> dict:
    return {
        "entity_type": "reaction",
        "entity_id": uuid.uuid4(),
        "new_state": CurationState.PROPOSED,
        "reviewer_type": "DETERMINISTIC_VALIDATOR",
    }


# --- external_record -------------------------------------------------------


def test_create_external_record(db_session):
    er = ExternalRecord(**_minimal_external_record_kwargs())
    db_session.add(er)
    db_session.flush()

    fetched = db_session.get(ExternalRecord, er.id)
    assert fetched is not None
    assert fetched.source == SourceType.PUBMED
    assert fetched.raw_response_hash == "test-only-hash-0001"
    assert fetched.created_at is not None


def test_external_record_source_is_required(db_session):
    db_session.add(
        ExternalRecord(
            source=None,
            retrieval_date=datetime.now(UTC),
            raw_response_hash="x",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_external_record_retrieval_date_is_required(db_session):
    db_session.add(
        ExternalRecord(source=SourceType.PUBMED, retrieval_date=None, raw_response_hash="x")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_external_record_raw_response_hash_is_required(db_session):
    db_session.add(
        ExternalRecord(
            source=SourceType.PUBMED, retrieval_date=datetime.now(UTC), raw_response_hash=None
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_external_record_nullable_fields_accept_null(db_session):
    er = ExternalRecord(
        external_id=None,
        request_url=None,
        raw_response_json=None,
        raw_response_text=None,
        **_minimal_external_record_kwargs(),
    )
    db_session.add(er)
    db_session.flush()  # must not raise

    fetched = db_session.get(ExternalRecord, er.id)
    assert fetched.external_id is None
    assert fetched.request_url is None
    assert fetched.raw_response_json is None
    assert fetched.raw_response_text is None


@pytest.mark.parametrize("source", list(SourceType))
def test_every_source_type_persists_on_external_record(db_session, source):
    er = ExternalRecord(
        source=source, retrieval_date=datetime.now(UTC), raw_response_hash="x"
    )
    db_session.add(er)
    db_session.flush()
    assert db_session.get(ExternalRecord, er.id).source == source


def test_external_record_invalid_source_type_is_rejected_by_postgres(db_session):
    er = ExternalRecord(**_minimal_external_record_kwargs())
    db_session.add(er)
    db_session.flush()
    with pytest.raises(DataError):
        db_session.execute(
            text("UPDATE external_record SET source = 'NOT_A_REAL_SOURCE' WHERE id = :id"),
            {"id": er.id},
        )


def test_external_record_raw_response_json_round_trips_exactly(db_session):
    payload = {"pmid": "12345", "nested": {"a": [1, 2, 3], "b": None}}
    er = ExternalRecord(raw_response_json=payload, **_minimal_external_record_kwargs())
    db_session.add(er)
    db_session.flush()

    fetched = db_session.get(ExternalRecord, er.id)
    assert fetched.raw_response_json == payload


def test_external_record_raw_response_text_round_trips_exactly(db_session):
    text_value = "line one\nline two\nunicode: éè"
    er = ExternalRecord(raw_response_text=text_value, **_minimal_external_record_kwargs())
    db_session.add(er)
    db_session.flush()

    fetched = db_session.get(ExternalRecord, er.id)
    assert fetched.raw_response_text == text_value


def test_external_record_has_no_updated_at_column():
    assert not hasattr(ExternalRecord, "updated_at")


def test_external_record_has_no_index_beyond_primary_key(db_session):
    """docs/02_database_schema.md's "Required Indexes" section does not list
    any field of external_record — none should be invented."""
    inspector = inspect(db_session.get_bind())
    assert inspector.get_indexes("external_record") == []


def test_external_record_has_no_record_type_column():
    """The specification defines no record_type column on external_record —
    none is added here."""
    assert not hasattr(ExternalRecord, "record_type")


def test_external_record_has_no_invented_uniqueness(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_unique_constraints("external_record") == []


def test_multiple_external_records_for_same_source_and_external_id_coexist(db_session):
    """External records are append-only: retrieving the same external_id
    twice must create two rows, not silently replace the first."""
    kwargs = {
        "source": SourceType.PUBMED,
        "retrieval_date": datetime.now(UTC),
        "raw_response_hash": "test-only-hash-a",
        "external_id": "test-only-same-external-id",
    }
    db_session.add(ExternalRecord(**kwargs))
    kwargs2 = dict(kwargs)
    kwargs2["raw_response_hash"] = "test-only-hash-b"
    db_session.add(ExternalRecord(**kwargs2))
    db_session.flush()  # must not raise


# --- source_cross_reference --------------------------------------------


def test_create_source_cross_reference(db_session):
    scr = SourceCrossReference(**_minimal_source_cross_reference_kwargs())
    db_session.add(scr)
    db_session.flush()

    fetched = db_session.get(SourceCrossReference, scr.id)
    assert fetched is not None
    assert fetched.entity_type == "gene"
    assert fetched.source == SourceType.SGD
    assert fetched.external_id == "test-only-ext-0001"
    assert fetched.created_at is not None


def test_source_cross_reference_entity_type_is_required(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    kwargs["entity_type"] = None
    db_session.add(SourceCrossReference(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_source_cross_reference_entity_id_is_required(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    kwargs["entity_id"] = None
    db_session.add(SourceCrossReference(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_source_cross_reference_source_is_required(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    kwargs["source"] = None
    db_session.add(SourceCrossReference(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_source_cross_reference_external_id_is_required(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    kwargs["external_id"] = None
    db_session.add(SourceCrossReference(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_source_cross_reference_entity_type_and_id_persist_literally(db_session):
    entity_id = uuid.uuid4()
    scr = SourceCrossReference(
        entity_type="protein",
        entity_id=entity_id,
        source=SourceType.UNIPROT,
        external_id="test-only-P12345",
    )
    db_session.add(scr)
    db_session.flush()

    fetched = db_session.get(SourceCrossReference, scr.id)
    assert fetched.entity_type == "protein"
    assert fetched.entity_id == entity_id


def test_source_cross_reference_dangling_entity_id_is_allowed(db_session):
    dangling = uuid.uuid4()
    kwargs = _minimal_source_cross_reference_kwargs()
    kwargs["entity_id"] = dangling
    scr = SourceCrossReference(**kwargs)
    db_session.add(scr)
    db_session.flush()  # must not raise
    assert db_session.get(SourceCrossReference, scr.id).entity_id == dangling


def test_source_cross_reference_no_foreign_key_on_entity_id(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_foreign_keys("source_cross_reference") == []


@pytest.mark.parametrize("source", list(SourceType))
def test_every_source_type_persists_on_source_cross_reference(db_session, source):
    scr = SourceCrossReference(
        entity_type="gene", entity_id=uuid.uuid4(), source=source, external_id="x"
    )
    db_session.add(scr)
    db_session.flush()
    assert db_session.get(SourceCrossReference, scr.id).source == source


def test_source_cross_reference_exact_duplicate_fails(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    db_session.add(SourceCrossReference(**kwargs))
    db_session.flush()

    db_session.add(SourceCrossReference(**dict(kwargs)))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_source_cross_reference_different_entity_type_is_allowed(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    db_session.add(SourceCrossReference(**kwargs))
    db_session.flush()

    other = dict(kwargs)
    other["entity_type"] = "protein"
    db_session.add(SourceCrossReference(**other))
    db_session.flush()  # must not raise


def test_source_cross_reference_different_entity_id_is_allowed(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    db_session.add(SourceCrossReference(**kwargs))
    db_session.flush()

    other = dict(kwargs)
    other["entity_id"] = uuid.uuid4()
    db_session.add(SourceCrossReference(**other))
    db_session.flush()  # must not raise


def test_source_cross_reference_different_source_is_allowed(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    db_session.add(SourceCrossReference(**kwargs))
    db_session.flush()

    other = dict(kwargs)
    other["source"] = SourceType.NCBI
    db_session.add(SourceCrossReference(**other))
    db_session.flush()  # must not raise


def test_source_cross_reference_different_external_id_is_allowed(db_session):
    kwargs = _minimal_source_cross_reference_kwargs()
    db_session.add(SourceCrossReference(**kwargs))
    db_session.flush()

    other = dict(kwargs)
    other["external_id"] = "test-only-different-ext-id"
    db_session.add(SourceCrossReference(**other))
    db_session.flush()  # must not raise


def test_source_cross_reference_has_exactly_one_unique_constraint(db_session):
    inspector = inspect(db_session.get_bind())
    unique_constraints = inspector.get_unique_constraints("source_cross_reference")
    assert len(unique_constraints) == 1
    assert set(unique_constraints[0]["column_names"]) == {
        "entity_type",
        "entity_id",
        "source",
        "external_id",
    }


def test_source_cross_reference_has_no_entity_type_enum(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {col["name"]: col for col in inspector.get_columns("source_cross_reference")}
    assert "ENUM" not in type(columns["entity_type"]["type"]).__name__.upper()


def test_source_cross_reference_has_no_other_unrelated_relationship(db_session):
    """No FK links source_cross_reference to external_record: similar
    source/external_id values do not justify inventing a relationship."""
    inspector = inspect(db_session.get_bind())
    assert inspector.get_foreign_keys("source_cross_reference") == []


def test_source_cross_reference_has_no_updated_at_column():
    assert not hasattr(SourceCrossReference, "updated_at")


# --- review_event -----------------------------------------------------------


def test_create_review_event(db_session):
    re_ = ReviewEvent(**_minimal_review_event_kwargs())
    db_session.add(re_)
    db_session.flush()

    fetched = db_session.get(ReviewEvent, re_.id)
    assert fetched is not None
    assert fetched.entity_type == "reaction"
    assert fetched.new_state == CurationState.PROPOSED
    assert fetched.reviewer_type == "DETERMINISTIC_VALIDATOR"
    assert fetched.created_at is not None


def test_review_event_entity_type_is_required(db_session):
    kwargs = _minimal_review_event_kwargs()
    kwargs["entity_type"] = None
    db_session.add(ReviewEvent(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_review_event_entity_id_is_required(db_session):
    kwargs = _minimal_review_event_kwargs()
    kwargs["entity_id"] = None
    db_session.add(ReviewEvent(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_review_event_new_state_is_required(db_session):
    kwargs = _minimal_review_event_kwargs()
    kwargs["new_state"] = None
    db_session.add(ReviewEvent(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_review_event_reviewer_type_is_required(db_session):
    kwargs = _minimal_review_event_kwargs()
    kwargs["reviewer_type"] = None
    db_session.add(ReviewEvent(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_review_event_nullable_fields_accept_null(db_session):
    re_ = ReviewEvent(
        previous_state=None, reviewer_id=None, comment=None, **_minimal_review_event_kwargs()
    )
    db_session.add(re_)
    db_session.flush()  # must not raise

    fetched = db_session.get(ReviewEvent, re_.id)
    assert fetched.previous_state is None
    assert fetched.reviewer_id is None
    assert fetched.comment is None


def test_review_event_entity_type_and_id_persist_literally(db_session):
    entity_id = uuid.uuid4()
    re_ = ReviewEvent(
        entity_type="claim",
        entity_id=entity_id,
        new_state=CurationState.MACHINE_REVIEWED,
        reviewer_type="AI_CRITIC",
    )
    db_session.add(re_)
    db_session.flush()

    fetched = db_session.get(ReviewEvent, re_.id)
    assert fetched.entity_type == "claim"
    assert fetched.entity_id == entity_id


def test_review_event_dangling_entity_id_is_allowed(db_session):
    dangling = uuid.uuid4()
    kwargs = _minimal_review_event_kwargs()
    kwargs["entity_id"] = dangling
    re_ = ReviewEvent(**kwargs)
    db_session.add(re_)
    db_session.flush()  # must not raise
    assert db_session.get(ReviewEvent, re_.id).entity_id == dangling


def test_review_event_no_foreign_key_on_entity_id(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_foreign_keys("review_event") == []


def test_review_event_reviewer_type_is_plain_varchar(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {col["name"]: col for col in inspector.get_columns("review_event")}
    assert "ENUM" not in type(columns["reviewer_type"]["type"]).__name__.upper()


@pytest.mark.parametrize(
    "reviewer_type", ["AI_CRITIC", "HUMAN", "DETERMINISTIC_VALIDATOR", "test-only-custom-reviewer"]
)
def test_review_event_arbitrary_reviewer_types_coexist(db_session, reviewer_type):
    kwargs = _minimal_review_event_kwargs()
    kwargs["reviewer_type"] = reviewer_type
    re_ = ReviewEvent(**kwargs)
    db_session.add(re_)
    db_session.flush()  # must not raise
    assert db_session.get(ReviewEvent, re_.id).reviewer_type == reviewer_type


def test_review_event_reviewer_type_has_no_check_constraint(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_check_constraints("review_event") == []


@pytest.mark.parametrize("new_state", list(CurationState))
def test_review_event_every_curation_state_persists_as_new_state(db_session, new_state):
    kwargs = _minimal_review_event_kwargs()
    kwargs["new_state"] = new_state
    re_ = ReviewEvent(**kwargs)
    db_session.add(re_)
    db_session.flush()
    assert db_session.get(ReviewEvent, re_.id).new_state == new_state


def test_review_event_previous_state_and_new_state_can_differ(db_session):
    re_ = ReviewEvent(
        entity_type="reaction",
        entity_id=uuid.uuid4(),
        previous_state=CurationState.PROPOSED,
        new_state=CurationState.MACHINE_REVIEWED,
        reviewer_type="DETERMINISTIC_VALIDATOR",
    )
    db_session.add(re_)
    db_session.flush()

    fetched = db_session.get(ReviewEvent, re_.id)
    assert fetched.previous_state == CurationState.PROPOSED
    assert fetched.new_state == CurationState.MACHINE_REVIEWED


def test_review_event_has_no_updated_at_column():
    assert not hasattr(ReviewEvent, "updated_at")


def test_review_event_has_no_index_beyond_primary_key(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_indexes("review_event") == []


def test_review_event_has_no_uniqueness_constraint(db_session):
    inspector = inspect(db_session.get_bind())
    assert inspector.get_unique_constraints("review_event") == []


# --- cross-table scientific-integrity checks --------------------------------


def test_no_reviewer_type_enum_exists_in_database(db_session):
    enum_names = {
        name.lower()
        for name in db_session.execute(
            text("SELECT typname FROM pg_type WHERE typtype = 'e'")
        ).scalars().all()
    }
    assert "reviewertype" not in enum_names
    assert "entitytype" not in enum_names
    assert "entity_type" not in enum_names


def test_no_automatic_cross_table_records_are_created(db_session):
    """Creating an external_record must not create any
    source_cross_reference, claim, evidence, gene, protein, or other entity
    row — this substep is schema only, no connector/normalization logic."""
    from app.models.claim import Claim, Evidence
    from app.models.gene import Gene
    from app.models.protein import Protein

    er = ExternalRecord(**_minimal_external_record_kwargs())
    db_session.add(er)
    db_session.flush()

    assert db_session.query(SourceCrossReference).count() == 0
    assert db_session.query(Claim).count() == 0
    assert db_session.query(Evidence).count() == 0
    assert db_session.query(Gene).count() == 0
    assert db_session.query(Protein).count() == 0
