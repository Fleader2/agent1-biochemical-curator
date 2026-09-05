"""Reaction persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**``NEW`` is now supported.** As of Increment 11
(``migrations/versions/0009_persistence_hardening.py``), ``internal_id`` is
allocated via ``app.persistence.reaction_id_allocator.
allocate_reaction_internal_id`` -- a PostgreSQL sequence, safe under
concurrent writers by construction (see that module's docstring for the
full mechanism and why the previously-rejected ``MAX + 1``/counter-based
approaches were never used). Prior to this increment, ``NEW`` always
returned ``FAILED`` because no such allocator existed; that limitation is
now resolved.

Reaction creation, its participant rows, its ``SourceCrossReference``, and
its ``ExternalRecord`` are all created inside one PostgreSQL SAVEPOINT
(``session.begin_nested()``) so that a failure partway through (for
example an invalid ``compound_id``/``compartment_id`` on a supplied
participant) rolls back *only* this reaction's own work, as a single unit
-- never the caller's own outer transaction, which this package never
commits or rolls back itself (see the package docstring's transaction
policy). An ``IntegrityError`` raised inside that block is caught and
converted to a conservative ``FAILED`` result; nothing else is caught, so
an unrelated bug does not get silently absorbed as if it were an expected
data condition.

Participants are persisted exactly as supplied on ``identity`` -- literal
multiplicity, literal stoichiometry (``Decimal``, never coerced), literal
``compartment_id`` (including ``None``) -- with no aggregation,
proportional-ratio reduction, orientation reversal, or compartment
inference of any kind, matching ``app.normalization.reaction``'s own
participant-canonicalization policy. Participants remain **not required**
for ``NEW``: this increment does not add that stricter rule (open question
J, ``docs/07_normalization_design.md``, remains open).

``MATCHED``/``AMBIGUOUS``/``CONFLICTED``/``UNRESOLVED`` are fully
supported, following the same shape as every other entity module in this
package. ``MATCHED`` reuse never touches ``reaction_participant`` rows --
this module does not attempt to reconcile, extend, or overwrite an
existing reaction's participant structure, consistent with the
non-destructive-reuse policy and with ``app.normalization.reaction``'s own
"structural agreement/disagreement" being a read-only check, not a write
instruction.

**Organism scope**: required, non-``None`` (mirrors
``app.normalization.reaction.normalize_reaction``'s own ``organism_id``
parameter, which requires a real organism -- see that module's own
"no null-organism global reaction fallback" note).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reaction import Reaction, ReactionParticipant
from app.normalization.reaction import ReactionIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence._freshness import any_row_matches
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.reaction_id_allocator import allocate_reaction_internal_id
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "reaction"


def persist_reaction(
    identity: ReactionIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Reaction normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_reaction received a result for entity_type={result.entity_type!r}, "
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
            reason=result.reason or f"{result.status} reaction identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved reaction identity: no action taken",
    )


def _reuse(
    identity: ReactionIdentity,
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
        reason="reused existing matched reaction",
    )


def _create(
    identity: ReactionIdentity,
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
            reason="cannot create Reaction: name is required and was not supplied",
        )

    conditions = []
    if identity.kegg_reaction_id is not None:
        conditions.append(Reaction.kegg_reaction_id == identity.kegg_reaction_id)
    if identity.metacyc_reaction_id is not None:
        conditions.append(Reaction.metacyc_reaction_id == identity.metacyc_reaction_id)
    if identity.rhea_id is not None:
        conditions.append(Reaction.rhea_id == identity.rhea_id)
    if any_row_matches(session, Reaction.id, conditions):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: a reaction matching one of the supplied identifiers now "
                "exists -- re-normalize before retrying"
            ),
        )

    try:
        with session.begin_nested():
            row = Reaction(
                internal_id=allocate_reaction_internal_id(session),
                name=identity.name,
                organism_id=organism_id,
                reversible=identity.reversible,
                reaction_type=identity.reaction_type,
                ec_number=identity.ec_number,
                kegg_reaction_id=identity.kegg_reaction_id,
                metacyc_reaction_id=identity.metacyc_reaction_id,
                rhea_id=identity.rhea_id,
            )
            session.add(row)
            session.flush()

            for participant in identity.participants:
                session.add(
                    ReactionParticipant(
                        reaction_id=row.id,
                        compound_id=participant.compound_id,
                        compartment_id=participant.compartment_id,
                        role=participant.role,
                        stoichiometry=participant.stoichiometry,
                    )
                )
            if identity.participants:
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
    except IntegrityError as exc:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "reaction creation rolled back as a unit due to a database integrity "
                f"violation: {exc.orig}"
            ),
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.CREATED,
        entity_type=_ENTITY_TYPE,
        entity_id=row.id,
        created=True,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="created new reaction",
    )


__all__ = ["persist_reaction"]
