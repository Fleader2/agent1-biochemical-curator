"""Protein persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**Organism scope**: required, non-``None`` (mirrors
``app.normalization.protein.normalize_protein``'s own ``organism_id``
parameter -- ``Protein.organism_id`` is ``NOT NULL``).

**Creation-required field**: ``name`` (``Protein.name`` is ``NOT NULL`` --
the only NOT NULL, non-identity column, per
``app.normalization.protein``'s own creation-completeness rule).

**Freshness recheck field** (``NEW`` only, global): ``uniprot_id``, if
supplied. ``Protein.uniprot_id`` carries **no database uniqueness
constraint at all** (``docs/07_normalization_design.md``, Open Question A)
-- this application-level recheck is Protein's *only* protection against a
stale ``NEW``.

``gene_id`` is persisted exactly as supplied on ``identity`` and never
derived, inferred, or defaulted -- ``ProteinIdentity.gene_id`` is already
"explicitly supplied and already resolved" by construction
(``app.normalization.protein`` never populates it from UniProt or
gene-side metadata itself), so passing it straight through introduces no
new inference here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.protein import Protein
from app.normalization.protein import ProteinIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "protein"


def persist_protein(
    identity: ProteinIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Protein normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_protein received a result for entity_type={result.entity_type!r}, "
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
            reason=result.reason or f"{result.status} protein identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved protein identity: no action taken",
    )


def _reuse(
    identity: ProteinIdentity,
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
        reason="reused existing matched protein",
    )


def _create(
    identity: ProteinIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.name:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason="cannot create Protein: name is required and was not supplied",
        )

    conditions = []
    if identity.uniprot_id is not None:
        conditions.append(Protein.uniprot_id == identity.uniprot_id)
    if any_row_matches(session, Protein.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a protein matching the supplied uniprot_id now exists -- "
                "re-normalize before retrying"
            ),
        )

    row = Protein(
        organism_id=organism_id,
        gene_id=identity.gene_id,
        name=identity.name,
        uniprot_id=identity.uniprot_id,
        ec_number=identity.ec_number,
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
        reason="created new protein",
    )


__all__ = ["persist_protein"]
