"""Enzyme regulatory state persistence (Agent 1.x Increment B).

Four persist functions, one per table, following
``app.persistence.knowledge_gap``'s established convention exactly: one
``SAVEPOINT`` (``session.begin_nested()``) around each insert attempt, an
``IntegrityError`` caught and resolved via a post-failure recheck-by-
``identity_key`` query, every other exception left to propagate. Never
commits or rolls back the session it is given -- the caller owns that.

Consolidated into one module (rather than four) for the same reason
``app.normalization.enzyme_state`` is one module: Increment B's own
instructions explicitly permit "a cohesive smaller module" in place of one
file per table (Step 24).

**Idempotency, never numeric-equality-based deduplication.** Every table's
``identity_key`` (computed by the corresponding
``app.normalization.enzyme_state`` function) carries a real database
unique index -- persisting the identical curated content twice reuses the
existing row; two rows differing in any identity-relevant field always
persist independently. **No destructive overwrite, ever**: a reused row's
existing columns are never rewritten by a later call.

**No silent merging of distinct states.** ``persist_enzyme_state`` never
merges two ``EnzymeStateIdentity`` values whose ``identity_key`` differs,
regardless of how similar their ``state_label`` or ``notes`` might look --
identity is exactly and only what ``compute_enzyme_state_identity_key``
computes from the state's defining fields.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.normalization.enzyme_state import (
    AllostericInteractionIdentity,
    EnzymeModificationIdentity,
    EnzymeStateIdentity,
    EnzymeStateParentType,
    EnzymeStateTransitionIdentity,
    compute_allosteric_interaction_identity_key,
    compute_enzyme_modification_identity_key,
    compute_enzyme_state_identity_key,
    compute_enzyme_state_transition_identity_key,
)
from app.persistence.enzyme_state_types import EnzymeRegulatoryPersistenceResult
from app.persistence.provenance import ExternalRecordProvenance, record_external_record
from app.persistence.types import PersistenceAction

_ENZYME_STATE_ENTITY_TYPE = "enzyme_state"
_ENZYME_MODIFICATION_ENTITY_TYPE = "enzyme_modification"
_ALLOSTERIC_INTERACTION_ENTITY_TYPE = "allosteric_interaction"
_ENZYME_STATE_TRANSITION_ENTITY_TYPE = "enzyme_state_transition"


def _reused(
    *,
    entity_type: str,
    entity_id: UUID,
    reason: str,
    provenance: ExternalRecordProvenance | None,
    session: Session,
    source: SourceType | None,
) -> EnzymeRegulatoryPersistenceResult:
    external_record_id = (
        record_external_record(session, source=source, provenance=provenance)
        if provenance is not None
        else None
    )
    return EnzymeRegulatoryPersistenceResult(
        action=PersistenceAction.REUSED_EXISTING,
        entity_type=entity_type,
        entity_id=entity_id,
        created=False,
        reused=True,
        external_record_id=external_record_id,
        reason=reason,
    )


def _created(
    *,
    entity_type: str,
    entity_id: UUID,
    reason: str,
    provenance: ExternalRecordProvenance | None,
    session: Session,
    source: SourceType | None,
) -> EnzymeRegulatoryPersistenceResult:
    external_record_id = (
        record_external_record(session, source=source, provenance=provenance)
        if provenance is not None
        else None
    )
    return EnzymeRegulatoryPersistenceResult(
        action=PersistenceAction.CREATED,
        entity_type=entity_type,
        entity_id=entity_id,
        created=True,
        reused=False,
        external_record_id=external_record_id,
        reason=reason,
    )


def _failed(*, entity_type: str, reason: str) -> EnzymeRegulatoryPersistenceResult:
    return EnzymeRegulatoryPersistenceResult(
        action=PersistenceAction.FAILED,
        entity_type=entity_type,
        entity_id=None,
        created=False,
        reused=False,
        reason=reason,
    )


def persist_enzyme_state(
    identity: EnzymeStateIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> EnzymeRegulatoryPersistenceResult:
    """Persist one ``EnzymeState`` row (never its children -- see
    ``persist_enzyme_modification``/``persist_allosteric_interaction``).

    ``identity.modifications``/``.allosteric_ligands`` are used only to
    compute ``identity_key`` -- no ``EnzymeModification``/
    ``AllostericInteraction`` row is created by this function. The caller
    is responsible for persisting each one separately afterward, once this
    call's ``entity_id`` (the new or reused state's real id) is known.
    """
    if not isinstance(identity, EnzymeStateIdentity):
        raise TypeError(f"persist_enzyme_state requires an EnzymeStateIdentity, got {identity!r}")

    identity_key = compute_enzyme_state_identity_key(identity)
    existing = session.execute(
        select(EnzymeState.id).where(EnzymeState.identity_key == identity_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _reused(
            entity_type=_ENZYME_STATE_ENTITY_TYPE,
            entity_id=existing,
            reason=f"reused existing enzyme state {existing} with the identical identity_key",
            provenance=provenance,
            session=session,
            source=identity.source,
        )

    try:
        with session.begin_nested():
            row = EnzymeState(
                protein_id=identity.parent_id
                if identity.parent_type is EnzymeStateParentType.PROTEIN
                else None,
                complex_id=identity.parent_id
                if identity.parent_type is EnzymeStateParentType.COMPLEX
                else None,
                state_type=identity.state_type,
                state_label=identity.state_label,
                compartment_id=identity.compartment_id,
                active_state=identity.active_state,
                identity_key=identity_key,
                source=identity.source,
                source_id=identity.source_identifier,
                publication_id=identity.publication_id,
                evidence_id=identity.evidence_id,
                notes=identity.notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = session.execute(
            select(EnzymeState.id).where(EnzymeState.identity_key == identity_key)
        ).scalar_one_or_none()
        if raced is not None:
            return _reused(
                entity_type=_ENZYME_STATE_ENTITY_TYPE,
                entity_id=raced,
                reason=(
                    f"reused existing enzyme state {raced}: a concurrent insert of the "
                    "identical identity_key won the race"
                ),
                provenance=provenance,
                session=session,
                source=identity.source,
            )
        return _failed(
            entity_type=_ENZYME_STATE_ENTITY_TYPE,
            reason=(
                "enzyme state creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return _created(
        entity_type=_ENZYME_STATE_ENTITY_TYPE,
        entity_id=row.id,
        reason="created new enzyme state",
        provenance=provenance,
        session=session,
        source=identity.source,
    )


def persist_enzyme_modification(
    identity: EnzymeModificationIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> EnzymeRegulatoryPersistenceResult:
    """Persist one ``EnzymeModification`` row attached to an already-existing ``EnzymeState``."""
    if not isinstance(identity, EnzymeModificationIdentity):
        raise TypeError(
            f"persist_enzyme_modification requires an EnzymeModificationIdentity, got {identity!r}"
        )

    identity_key = compute_enzyme_modification_identity_key(identity)
    existing = session.execute(
        select(EnzymeModification.id).where(EnzymeModification.identity_key == identity_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _reused(
            entity_type=_ENZYME_MODIFICATION_ENTITY_TYPE,
            entity_id=existing,
            reason=(
                f"reused existing enzyme modification {existing} with the identical identity_key"
            ),
            provenance=provenance,
            session=session,
            source=identity.source,
        )

    try:
        with session.begin_nested():
            row = EnzymeModification(
                enzyme_state_id=identity.enzyme_state_id,
                modification_type=identity.modification_type,
                residue=identity.residue,
                residue_position=identity.residue_position,
                site_label=identity.site_label,
                modifying_compound_id=identity.modifying_compound_id,
                stoichiometry=identity.stoichiometry,
                identity_key=identity_key,
                source=identity.source,
                source_id=identity.source_identifier,
                publication_id=identity.publication_id,
                evidence_id=identity.evidence_id,
                notes=identity.notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = session.execute(
            select(EnzymeModification.id).where(EnzymeModification.identity_key == identity_key)
        ).scalar_one_or_none()
        if raced is not None:
            return _reused(
                entity_type=_ENZYME_MODIFICATION_ENTITY_TYPE,
                entity_id=raced,
                reason=(
                    f"reused existing enzyme modification {raced}: a concurrent insert of the "
                    "identical identity_key won the race"
                ),
                provenance=provenance,
                session=session,
                source=identity.source,
            )
        return _failed(
            entity_type=_ENZYME_MODIFICATION_ENTITY_TYPE,
            reason=(
                "enzyme modification creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return _created(
        entity_type=_ENZYME_MODIFICATION_ENTITY_TYPE,
        entity_id=row.id,
        reason="created new enzyme modification",
        provenance=provenance,
        session=session,
        source=identity.source,
    )


def persist_allosteric_interaction(
    identity: AllostericInteractionIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> EnzymeRegulatoryPersistenceResult:
    """Persist one ``AllostericInteraction`` row attached to an already-existing ``EnzymeState``."""
    if not isinstance(identity, AllostericInteractionIdentity):
        raise TypeError(
            f"persist_allosteric_interaction requires an AllostericInteractionIdentity, "
            f"got {identity!r}"
        )

    identity_key = compute_allosteric_interaction_identity_key(identity)
    existing = session.execute(
        select(AllostericInteraction.id).where(AllostericInteraction.identity_key == identity_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _reused(
            entity_type=_ALLOSTERIC_INTERACTION_ENTITY_TYPE,
            entity_id=existing,
            reason=(
                f"reused existing allosteric interaction {existing} with the identical "
                "identity_key"
            ),
            provenance=provenance,
            session=session,
            source=identity.source,
        )

    try:
        with session.begin_nested():
            row = AllostericInteraction(
                enzyme_state_id=identity.enzyme_state_id,
                ligand_compound_id=identity.ligand_compound_id,
                effect=identity.effect,
                site_label=identity.site_label,
                mechanism=identity.mechanism,
                identity_key=identity_key,
                source=identity.source,
                source_id=identity.source_identifier,
                publication_id=identity.publication_id,
                evidence_id=identity.evidence_id,
                notes=identity.notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = session.execute(
            select(AllostericInteraction.id).where(
                AllostericInteraction.identity_key == identity_key
            )
        ).scalar_one_or_none()
        if raced is not None:
            return _reused(
                entity_type=_ALLOSTERIC_INTERACTION_ENTITY_TYPE,
                entity_id=raced,
                reason=(
                    f"reused existing allosteric interaction {raced}: a concurrent insert of "
                    "the identical identity_key won the race"
                ),
                provenance=provenance,
                session=session,
                source=identity.source,
            )
        return _failed(
            entity_type=_ALLOSTERIC_INTERACTION_ENTITY_TYPE,
            reason=(
                "allosteric interaction creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return _created(
        entity_type=_ALLOSTERIC_INTERACTION_ENTITY_TYPE,
        entity_id=row.id,
        reason="created new allosteric interaction",
        provenance=provenance,
        session=session,
        source=identity.source,
    )


def persist_enzyme_state_transition(
    identity: EnzymeStateTransitionIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> EnzymeRegulatoryPersistenceResult:
    """Persist one ``EnzymeStateTransition`` row between two already-existing states."""
    if not isinstance(identity, EnzymeStateTransitionIdentity):
        raise TypeError(
            f"persist_enzyme_state_transition requires an EnzymeStateTransitionIdentity, "
            f"got {identity!r}"
        )

    identity_key = compute_enzyme_state_transition_identity_key(identity)
    existing = session.execute(
        select(EnzymeStateTransition.id).where(
            EnzymeStateTransition.identity_key == identity_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _reused(
            entity_type=_ENZYME_STATE_TRANSITION_ENTITY_TYPE,
            entity_id=existing,
            reason=(
                f"reused existing enzyme state transition {existing} with the identical "
                "identity_key"
            ),
            provenance=provenance,
            session=session,
            source=identity.source,
        )

    try:
        with session.begin_nested():
            row = EnzymeStateTransition(
                from_state_id=identity.from_state_id,
                to_state_id=identity.to_state_id,
                transition_type=identity.transition_type,
                reaction_id=identity.reaction_id,
                identity_key=identity_key,
                source=identity.source,
                source_id=identity.source_identifier,
                publication_id=identity.publication_id,
                evidence_id=identity.evidence_id,
                notes=identity.notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = session.execute(
            select(EnzymeStateTransition.id).where(
                EnzymeStateTransition.identity_key == identity_key
            )
        ).scalar_one_or_none()
        if raced is not None:
            return _reused(
                entity_type=_ENZYME_STATE_TRANSITION_ENTITY_TYPE,
                entity_id=raced,
                reason=(
                    f"reused existing enzyme state transition {raced}: a concurrent insert of "
                    "the identical identity_key won the race"
                ),
                provenance=provenance,
                session=session,
                source=identity.source,
            )
        return _failed(
            entity_type=_ENZYME_STATE_TRANSITION_ENTITY_TYPE,
            reason=(
                "enzyme state transition creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return _created(
        entity_type=_ENZYME_STATE_TRANSITION_ENTITY_TYPE,
        entity_id=row.id,
        reason="created new enzyme state transition",
        provenance=provenance,
        session=session,
        source=identity.source,
    )


def get_enzyme_state(session: Session, enzyme_state_id: UUID) -> EnzymeState | None:
    """One ``EnzymeState`` row by id, or ``None``. Read-only."""
    return session.get(EnzymeState, enzyme_state_id)


def list_enzyme_states_for_protein(session: Session, protein_id: UUID) -> tuple[EnzymeState, ...]:
    """Every ``EnzymeState`` row for one protein, oldest first. Read-only."""
    statement = (
        select(EnzymeState)
        .where(EnzymeState.protein_id == protein_id)
        .order_by(EnzymeState.created_at.asc(), EnzymeState.id.asc())
    )
    return tuple(session.execute(statement).scalars().all())


def list_enzyme_states_for_complex(session: Session, complex_id: UUID) -> tuple[EnzymeState, ...]:
    """Every ``EnzymeState`` row for one enzyme complex, oldest first. Read-only."""
    statement = (
        select(EnzymeState)
        .where(EnzymeState.complex_id == complex_id)
        .order_by(EnzymeState.created_at.asc(), EnzymeState.id.asc())
    )
    return tuple(session.execute(statement).scalars().all())


__all__ = [
    "get_enzyme_state",
    "list_enzyme_states_for_complex",
    "list_enzyme_states_for_protein",
    "persist_allosteric_interaction",
    "persist_enzyme_modification",
    "persist_enzyme_state",
    "persist_enzyme_state_transition",
]
