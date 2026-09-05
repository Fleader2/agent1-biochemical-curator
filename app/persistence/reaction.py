"""Reaction persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**``NEW`` is explicitly unsupported and always returns ``FAILED``.**
``Reaction.internal_id`` is ``NOT NULL`` and the *only* genuinely
database-unique column on this table (``docs/02_database_schema.md``:
"Reaction IDs must remain stable after creation"), but no production-safe
allocator for it exists anywhere in this repository. The only precedent,
``tests/database/test_group_c_models.py``'s ``_internal_id()``, is an
``itertools.count()`` counter that is explicitly test-only and not
concurrency-safe across processes or even across two sessions in the same
process. Inventing a "``SELECT MAX(...) + 1``"-style allocator here would
introduce a real race condition (two concurrent ``NEW`` reactions could
compute the same next value and one ``INSERT`` would silently corrupt the
other's numbering, or -- absent a uniqueness violation surfaced cleanly --
worse, collide past it) for the sake of one increment; per this increment's
explicit instructions, that limitation is reported rather than papered
over with an unsafe allocator. A real fix needs one of: a database
sequence, an application-level reservation table, or a
``retry-on-unique-violation`` loop around a candidate id -- all schema/
architecture decisions out of scope here (see this increment's completion
report).

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

from sqlalchemy.orm import Session

from app.normalization.reaction import ReactionIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "reaction"

_NEW_UNSUPPORTED_REASON = (
    "cannot create Reaction: internal_id allocation has no production-safe, "
    "concurrency-safe implementation in this architecture -- Reaction NEW "
    "persistence is not supported (see app.persistence.reaction module docstring); "
    "re-normalize against an existing reaction or resolve the internal_id allocation "
    "architecture question before creating new reactions"
)


def persist_reaction(
    identity: ReactionIdentity,
    result: NormalizationResult,
    *,
    organism_id: UUID,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Reaction normalization decision to the database.

    ``NEW`` always returns ``FAILED`` -- see module docstring.
    """
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_reaction received a result for entity_type={result.entity_type!r}, "
            f"expected {_ENTITY_TYPE!r}"
        )

    if result.status is NormalizationStatus.MATCHED:
        assert result.matched_entity_id is not None
        return _reuse(identity, result, session=session, provenance=provenance)

    if result.status is NormalizationStatus.NEW:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=_NEW_UNSUPPORTED_REASON,
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


__all__ = ["persist_reaction"]
