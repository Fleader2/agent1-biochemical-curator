"""Organism persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach this module implements.

**Creation-required field**: ``scientific_name`` (``Organism.scientific_name``
is ``NOT NULL``).

**Freshness recheck fields** (``NEW`` only, exactly mirroring
``app.normalization.organism``'s own strong anchors): ``ncbi_taxonomy_id``,
``kegg_code``, ``biocyc_id`` (each independently, if supplied), and
``(scientific_name, strain)`` together if ``strain`` was supplied. Only
``(scientific_name, strain)`` -- and only when ``strain`` is not ``None`` --
is backed by a real database constraint (a partial unique index); the
three external identifiers are not database-unique at all
(``docs/07_normalization_design.md`` records this as still open), so this
recheck is their only protection against a stale ``NEW``.
"""

from __future__ import annotations

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.organism import Organism
from app.normalization.organism import OrganismIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "organism"


def persist_organism(
    identity: OrganismIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Organism normalization decision to the database.

    Never mutates a matched row's identity fields even when ``identity``'s
    metadata differs from what is already stored (see package docstring).
    """
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_organism received a result for entity_type={result.entity_type!r}, "
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
            reason=result.reason or f"{result.status} organism identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved organism identity: no action taken",
    )


def _reuse(
    identity: OrganismIdentity,
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
        reason="reused existing matched organism",
    )


def _create(
    identity: OrganismIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.scientific_name:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason="cannot create Organism: scientific_name is required and was not supplied",
        )

    conditions = []
    if identity.ncbi_taxonomy_id is not None:
        conditions.append(Organism.ncbi_taxonomy_id == identity.ncbi_taxonomy_id)
    if identity.kegg_code is not None:
        conditions.append(Organism.kegg_code == identity.kegg_code)
    if identity.biocyc_id is not None:
        conditions.append(Organism.biocyc_id == identity.biocyc_id)
    if identity.strain is not None:
        conditions.append(
            and_(
                Organism.scientific_name == identity.scientific_name,
                Organism.strain == identity.strain,
            )
        )
    if any_row_matches(session, Organism.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: an organism matching one of the supplied identifiers now "
                "exists -- re-normalize before retrying"
            ),
        )

    row = Organism(
        scientific_name=identity.scientific_name,
        strain=identity.strain,
        ncbi_taxonomy_id=identity.ncbi_taxonomy_id,
        kegg_code=identity.kegg_code,
        biocyc_id=identity.biocyc_id,
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
        reason="created new organism",
    )


__all__ = ["persist_organism"]
