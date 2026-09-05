"""Compartment persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**Organism scope**: required keyword argument, but its *value* may
legitimately be ``None`` -- mirroring
``app.normalization.compartment.normalize_compartment``'s own
``organism_id: UUID | None`` parameter and the verified schema fact that
``organism_id IS NULL`` means a standard/reference compartment definition,
not an unknown organism (see ``app.normalization.compartment``'s module
docstring and migration ``0002_reference_data``). Persisting with
``organism_id=None`` creates a genuine reference-scope row; this module
never clones a reference compartment into an organism-specific copy on its
own -- the caller decides which scope to persist into by which
``organism_id`` it passes, exactly as normalization already decided which
scope to normalize against.

**Creation-required field**: ``name`` (``Compartment.name`` is
``NOT NULL``).

**Freshness recheck field** (``NEW`` only, global): ``ontology_id``, if
supplied. ``Compartment`` has **no index or uniqueness constraint of any
kind** beyond its primary key (``docs/07_normalization_design.md``, Open
Question H) -- this application-level recheck is Compartment's only
protection against a stale ``NEW``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.compartment import Compartment
from app.normalization.compartment import CompartmentIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "compartment"


def persist_compartment(
    identity: CompartmentIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID | None,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Compartment normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_compartment received a result for entity_type={result.entity_type!r}, "
            f"expected {_ENTITY_TYPE!r}"
        )

    if result.status is NormalizationStatus.MATCHED:
        assert result.matched_entity_id is not None
        return _reuse(identity, result, session=session, provenance=provenance)

    if result.status is NormalizationStatus.NEW:
        return _create(
            identity, result, organism_id=organism_id, session=session, provenance=provenance
        )

    if result.status in (NormalizationStatus.AMBIGUOUS, NormalizationStatus.CONFLICTED):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.REQUIRES_REVIEW,
            entity_type=_ENTITY_TYPE,
            review_required=True,
            reason=result.reason or f"{result.status} compartment identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved compartment identity: no action taken",
    )


def _reuse(
    identity: CompartmentIdentity,
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
        reason="reused existing matched compartment",
    )


def _create(
    identity: CompartmentIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID | None,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.name:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason="cannot create Compartment: name is required and was not supplied",
        )

    conditions = []
    if identity.ontology_id is not None:
        conditions.append(Compartment.ontology_id == identity.ontology_id)
    if any_row_matches(session, Compartment.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a compartment matching the supplied ontology_id now exists -- "
                "re-normalize before retrying"
            ),
        )

    row = Compartment(
        organism_id=organism_id,
        name=identity.name,
        abbreviation=identity.abbreviation,
        ontology_id=identity.ontology_id,
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
        reason="created new compartment",
    )


__all__ = ["persist_compartment"]
