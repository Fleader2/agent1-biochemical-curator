"""Compound persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**Creation-required field**: ``canonical_name`` (``Compound.canonical_name``
is ``NOT NULL``).

**Freshness recheck fields** (``NEW`` only): ``chebi_id``,
``kegg_compound_id``, ``pubchem_cid``, ``metacyc_id``, ``inchikey`` (each
independently, if supplied) -- mirroring
``app.normalization.compound``'s own five Level 1 anchors. **None of them
carries any database uniqueness constraint at all**
(``docs/07_normalization_design.md``, Open Question E) -- this
application-level recheck is Compound's only protection against a stale
``NEW``.

**No chemistry is canonicalized here.** ``formula``/``charge``/``inchi``/
``is_generic`` are persisted exactly as supplied -- no protonation, charge,
stereochemistry, or generic/specific rewriting (the same conservatism
``app.normalization.compound`` already applies at the identity layer).
``is_generic`` is only set on the new row when ``identity.is_generic`` is
not ``None`` -- passing an explicit ``None`` to a ``NOT NULL`` column would
fail at flush, so an unspecified claim is left to the column's own
``DEFAULT FALSE`` rather than being forced to a value normalization never
asserted.

**Synonyms are persisted only at creation time**, as ``CompoundSynonym``
rows, and only then -- an existing ``MATCHED`` compound's synonym set is
never modified, consistent with this package's non-destructive-reuse policy
(``app.persistence``'s package docstring: "not an entity-merging or
metadata-reconciliation engine").
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.compound import Compound, CompoundSynonym
from app.normalization.compound import CompoundIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "compound"


def persist_compound(
    identity: CompoundIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Compound normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_compound received a result for entity_type={result.entity_type!r}, "
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
            reason=result.reason or f"{result.status} compound identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved compound identity: no action taken",
    )


def _reuse(
    identity: CompoundIdentity,
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
        reason="reused existing matched compound",
    )


def _create(
    identity: CompoundIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.canonical_name:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason="cannot create Compound: canonical_name is required and was not supplied",
        )

    conditions = []
    if identity.chebi_id is not None:
        conditions.append(Compound.chebi_id == identity.chebi_id)
    if identity.kegg_compound_id is not None:
        conditions.append(Compound.kegg_compound_id == identity.kegg_compound_id)
    if identity.pubchem_cid is not None:
        conditions.append(Compound.pubchem_cid == identity.pubchem_cid)
    if identity.metacyc_id is not None:
        conditions.append(Compound.metacyc_id == identity.metacyc_id)
    if identity.inchikey is not None:
        conditions.append(Compound.inchikey == identity.inchikey)
    if any_row_matches(session, Compound.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a compound matching one of the supplied identifiers now "
                "exists -- re-normalize before retrying"
            ),
        )

    row = Compound(
        canonical_name=identity.canonical_name,
        formula=identity.formula,
        charge=identity.charge,
        chebi_id=identity.chebi_id,
        kegg_compound_id=identity.kegg_compound_id,
        pubchem_cid=identity.pubchem_cid,
        metacyc_id=identity.metacyc_id,
        inchi=identity.inchi,
        inchikey=identity.inchikey,
    )
    if identity.is_generic is not None:
        row.is_generic = identity.is_generic
    session.add(row)
    session.flush()

    for synonym in identity.synonyms:
        session.add(
            CompoundSynonym(compound_id=row.id, synonym=synonym, source=identity.source.value)
        )
    if identity.synonyms:
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
        reason="created new compound",
    )


__all__ = ["persist_compound"]
