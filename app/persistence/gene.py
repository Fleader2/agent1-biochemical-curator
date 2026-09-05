"""Gene persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**Organism scope**: required, non-``None`` (mirrors
``app.normalization.gene.normalize_gene``'s own ``organism_id`` parameter
-- ``Gene.organism_id`` is ``NOT NULL``).

**Creation-required fields**: at least one of ``symbol``/``systematic_name``/
``ncbi_gene_id``/``sgd_id`` (the documented ``docs/02_database_schema.md``
Gene "Constraints" rule, the same rule ``app.normalization.gene`` already
enforces before returning ``NEW``).

**Freshness recheck fields** (``NEW`` only, global -- not organism-scoped,
mirroring ``app.normalization.gene``'s own global strong anchors):
``sgd_id``, ``ncbi_gene_id``, ``kegg_gene_id`` (each independently, if
supplied). All three are genuinely database-unique (global partial unique
indexes), so this recheck is backed by a real constraint.

``Gene.aliases_json``/``Gene.description`` are persisted exactly as
supplied on ``identity`` -- nothing is inferred beyond them. ``uniprot_id``
is never set: ``app.normalization.gene`` deliberately excludes it from Gene
identity entirely (see that module's own "Schema-policy mismatch" note),
and this persistence layer does not reintroduce it. ``name``/``chromosome``
are left ``None`` -- ``GeneIdentity`` carries neither.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.gene import Gene
from app.normalization.gene import GeneIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "gene"


def persist_gene(
    identity: GeneIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Gene normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_gene received a result for entity_type={result.entity_type!r}, "
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
            reason=result.reason or f"{result.status} gene identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved gene identity: no action taken",
    )


def _reuse(
    identity: GeneIdentity,
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
        reason="reused existing matched gene",
    )


def _create(
    identity: GeneIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not any((identity.symbol, identity.systematic_name, identity.ncbi_gene_id, identity.sgd_id)):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "cannot create Gene: at least one of symbol/systematic_name/ncbi_gene_id/"
                "sgd_id is required and none was supplied"
            ),
        )

    conditions = []
    if identity.sgd_id is not None:
        conditions.append(Gene.sgd_id == identity.sgd_id)
    if identity.ncbi_gene_id is not None:
        conditions.append(Gene.ncbi_gene_id == identity.ncbi_gene_id)
    if identity.kegg_gene_id is not None:
        conditions.append(Gene.kegg_gene_id == identity.kegg_gene_id)
    if any_row_matches(session, Gene.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a gene matching one of the supplied identifiers now exists -- "
                "re-normalize before retrying"
            ),
        )

    row = Gene(
        organism_id=organism_id,
        symbol=identity.symbol,
        systematic_name=identity.systematic_name,
        sgd_id=identity.sgd_id,
        ncbi_gene_id=identity.ncbi_gene_id,
        kegg_gene_id=identity.kegg_gene_id,
        description=identity.description,
        aliases_json=list(identity.aliases) if identity.aliases else None,
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
        reason="created new gene",
    )


__all__ = ["persist_gene"]
