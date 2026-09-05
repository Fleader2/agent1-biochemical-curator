"""Tests for the shared ``app.persistence.provenance`` helpers.

Covers ``SourceCrossReference`` idempotency and ``ExternalRecord``
append-only behavior directly, since every entity-specific persistence
module delegates to these two functions rather than reimplementing either.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models.enums import SourceType
from app.models.external_record import ExternalRecord
from app.models.source_cross_reference import SourceCrossReference
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)


def test_attach_source_cross_reference_creates_a_row(db_session):
    entity_id = uuid4()
    cross_reference_id = attach_source_cross_reference(
        db_session,
        entity_type="organism",
        entity_id=entity_id,
        source=SourceType.NCBI,
        external_id="9606",
    )
    row = db_session.get(SourceCrossReference, cross_reference_id)
    assert row is not None
    assert row.entity_type == "organism"
    assert row.entity_id == entity_id
    assert row.source is SourceType.NCBI
    assert row.external_id == "9606"


def test_attach_source_cross_reference_is_idempotent(db_session):
    entity_id = uuid4()
    first_id = attach_source_cross_reference(
        db_session,
        entity_type="organism",
        entity_id=entity_id,
        source=SourceType.NCBI,
        external_id="9606",
    )
    second_id = attach_source_cross_reference(
        db_session,
        entity_type="organism",
        entity_id=entity_id,
        source=SourceType.NCBI,
        external_id="9606",
    )
    assert first_id == second_id

    count = db_session.execute(
        select(SourceCrossReference).where(SourceCrossReference.entity_id == entity_id)
    ).scalars().all()
    assert len(count) == 1


def test_attach_source_cross_reference_distinct_external_id_creates_new_row(db_session):
    entity_id = uuid4()
    first_id = attach_source_cross_reference(
        db_session,
        entity_type="organism",
        entity_id=entity_id,
        source=SourceType.NCBI,
        external_id="9606",
    )
    second_id = attach_source_cross_reference(
        db_session,
        entity_type="organism",
        entity_id=entity_id,
        source=SourceType.NCBI,
        external_id="10090",
    )
    assert first_id != second_id


def test_record_external_record_creates_a_row(db_session):
    provenance = ExternalRecordProvenance(
        retrieval_date=datetime(2024, 1, 1, tzinfo=UTC),
        raw_response_hash="deadbeef",
        external_id="9606",
        request_url="https://example.invalid/9606",
        raw_response_text="<xml></xml>",
    )
    record_id = record_external_record(db_session, source=SourceType.NCBI, provenance=provenance)
    row = db_session.get(ExternalRecord, record_id)
    assert row is not None
    assert row.source is SourceType.NCBI
    assert row.raw_response_hash == "deadbeef"
    assert row.raw_response_text == "<xml></xml>"


def test_record_external_record_is_append_only(db_session):
    """Two calls with identical provenance produce two distinct rows.

    ``ExternalRecord`` is a retrieval log, not deduplicated storage
    (``app/models/external_record.py``) -- this mirrors the existing
    verified test
    ``test_multiple_external_records_for_same_source_and_external_id_coexist``
    in ``tests/database``, exercised here through the persistence helper
    itself rather than the raw ORM.
    """
    provenance = ExternalRecordProvenance(
        retrieval_date=datetime(2024, 1, 1, tzinfo=UTC),
        raw_response_hash="deadbeef",
        external_id="9606",
    )
    first_id = record_external_record(db_session, source=SourceType.NCBI, provenance=provenance)
    second_id = record_external_record(db_session, source=SourceType.NCBI, provenance=provenance)
    assert first_id != second_id

    rows = db_session.execute(
        select(ExternalRecord).where(ExternalRecord.external_id == "9606")
    ).scalars().all()
    assert len(rows) == 2
