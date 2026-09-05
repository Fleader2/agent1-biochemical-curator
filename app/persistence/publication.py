"""Publication persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**Creation-required field**: ``title`` (``Publication.title`` is
``NOT NULL``).

**Freshness recheck fields** (``NEW`` only): ``pmid``, ``pmcid``, ``doi``
(each independently, if supplied) -- mirroring
``app.normalization.publication``'s own Level 1 anchors. All three are
also genuinely database-unique (partial unique indexes where non-null),
making Publication the one entity in this package where the recheck is
backed by a real constraint for every one of its strong identifiers, not
just some of them. ``Publication`` is organism-agnostic, so no organism
scoping is relevant here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.publication import Publication
from app.normalization.publication import PublicationIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "publication"


def persist_publication(
    identity: PublicationIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Publication normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_publication received a result for entity_type={result.entity_type!r}, "
            f"expected {_ENTITY_TYPE!r}"
        )

    if result.status is NormalizationStatus.MATCHED:
        assert result.matched_entity_id is not None
        return _reuse(identity, result, session=session, provenance=provenance)

    if result.status is NormalizationStatus.NEW:
        return _create(identity, result, session=session, provenance=provenance)

    if result.status in (NormalizationStatus.AMBIGUOUS, NormalizationStatus.CONFLICTED):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.REQUIRES_REVIEW,
            entity_type=_ENTITY_TYPE,
            review_required=True,
            reason=result.reason or f"{result.status} publication identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved publication identity: no action taken",
    )


def _reuse(
    identity: PublicationIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    entity_id = result.matched_entity_id
    assert entity_id is not None
    cross_reference_id = attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=entity_id,
        source=identity.source,
        external_id=identity.source_identifier,
    )
    external_record_id = (
        record_external_record(session, source=identity.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.REUSED_EXISTING,
        entity_type=_ENTITY_TYPE,
        entity_id=entity_id,
        reused=True,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="reused existing matched publication",
    )


def _create(
    identity: PublicationIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.title:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason="cannot create Publication: title is required and was not supplied",
        )

    conditions = []
    if identity.pmid is not None:
        conditions.append(Publication.pmid == identity.pmid)
    if identity.pmcid is not None:
        conditions.append(Publication.pmcid == identity.pmcid)
    if identity.doi is not None:
        conditions.append(Publication.doi == identity.doi)
    if any_row_matches(session, Publication.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a publication matching one of the supplied identifiers now "
                "exists -- re-normalize before retrying"
            ),
        )

    row = Publication(
        pmid=identity.pmid,
        pmcid=identity.pmcid,
        doi=identity.doi,
        title=identity.title,
        journal=identity.journal,
        year=identity.year,
    )
    session.add(row)
    session.flush()

    cross_reference_id = attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=row.id,
        source=identity.source,
        external_id=identity.source_identifier,
    )
    external_record_id = (
        record_external_record(session, source=identity.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.CREATED,
        entity_type=_ENTITY_TYPE,
        entity_id=row.id,
        created=True,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="created new publication",
    )


__all__ = ["persist_publication"]
