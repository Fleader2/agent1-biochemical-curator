"""Shared provenance helpers: ``SourceCrossReference``/``ExternalRecord`` attachment.

Used by every entity-specific ``app.persistence.*`` module so cross-reference
and external-record behavior is identical everywhere, per
``docs/07_normalization_design.md``-adjacent conventions of not duplicating
shared logic across entities.

Neither helper commits or rolls back -- see each module's own docstring:
commit/rollback ownership belongs to the caller (``app/db/session.py``:
"Committing is the responsibility of the service performing the write").
Both call ``session.flush()`` only, so the assigned primary key is available
to return without ending the caller's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.external_record import ExternalRecord
from app.models.source_cross_reference import SourceCrossReference


def attach_source_cross_reference(
    session: Session, *, entity_type: str, entity_id: UUID, source: SourceType, external_id: str
) -> UUID:
    """Idempotently attach a ``SourceCrossReference`` to an entity.

    Reuses an existing row with the identical ``(entity_type, entity_id,
    source, external_id)`` tuple rather than creating a duplicate --
    ``source_cross_reference`` has exactly one unique constraint on exactly
    those four columns (verified in
    ``tests/database/test_group_g_models.py``), so this is both an
    application-level idempotency check and backed by a real database
    constraint as a concurrency backstop (a genuine race between the
    ``SELECT`` and the ``INSERT`` below still raises ``IntegrityError`` on
    ``flush()``, which this function does not swallow -- the caller's own
    transaction handling is responsible for that, consistent with this
    module never committing/rolling back itself).

    Every ``app.normalization.*`` ``SourceType`` is a plain member of the
    same enum this column uses -- there is no "LLM"/"CLAUDE"/"OPENAI"
    member in ``app.models.enums.SourceType`` at all, so a cross-reference
    literally cannot be created from an LLM-generated source; this is a
    structural guarantee of the closed enum, not a runtime check this
    function performs.
    """
    existing_id = session.execute(
        select(SourceCrossReference.id).where(
            SourceCrossReference.entity_type == entity_type,
            SourceCrossReference.entity_id == entity_id,
            SourceCrossReference.source == source,
            SourceCrossReference.external_id == external_id,
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        return existing_id

    row = SourceCrossReference(
        entity_type=entity_type, entity_id=entity_id, source=source, external_id=external_id
    )
    session.add(row)
    session.flush()
    return row.id


@dataclass(frozen=True, slots=True)
class ExternalRecordProvenance:
    """Raw retrieval metadata supplied explicitly by the caller.

    ``app.normalization.*``'s ``*Identity``/``NormalizationResult`` types
    deliberately carry no raw retrieval metadata at all (retrieval
    timestamp, response hash, raw payload) -- that is a connector-layer
    concern (``app.connectors.http.ConnectorHttpClient``), not an identity
    concern, and normalization never threads it through. A caller that
    still has the original connector response available may supply one of
    these explicitly; if it does not, no ``ExternalRecord`` is created --
    this module never fabricates retrieval metadata that was not actually
    supplied (``docs/03_agent_behavior.md``: "Provenance Behavior" requires
    every external retrieval to preserve source/external identifier/
    retrieval timestamp/raw response hash -- fields this module refuses to
    invent on the caller's behalf).
    """

    retrieval_date: datetime
    raw_response_hash: str
    external_id: str | None = None
    request_url: str | None = None
    raw_response_json: list[Any] | dict[str, Any] | None = None
    raw_response_text: str | None = None


def record_external_record(
    session: Session, *, source: SourceType, provenance: ExternalRecordProvenance
) -> UUID:
    """Append one new ``ExternalRecord`` row. Always inserts -- never updates or reuses.

    ``external_record`` is append-only by design
    (``docs/02_database_schema.md``: "External records should be
    append-only where practical"; verified directly in
    ``tests/database/test_group_g_models.py``'s
    ``test_multiple_external_records_for_same_source_and_external_id_coexist``):
    retrieving the same external ID again creates a second historical row,
    it never overwrites or is deduplicated against the first.
    """
    row = ExternalRecord(
        source=source,
        external_id=provenance.external_id,
        retrieval_date=provenance.retrieval_date,
        request_url=provenance.request_url,
        raw_response_hash=provenance.raw_response_hash,
        raw_response_json=provenance.raw_response_json,
        raw_response_text=provenance.raw_response_text,
    )
    session.add(row)
    session.flush()
    return row.id


__all__ = ["ExternalRecordProvenance", "attach_source_cross_reference", "record_external_record"]
