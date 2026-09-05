"""Reaction<->enzyme association persistence.

See ``app.persistence`` (package docstring) for the general status ->
action policy and stale-``NEW`` safety approach.

**No organism parameter.** ``reaction_enzyme`` has no ``organism_id``
column and no organism reference of any kind (see
``app.normalization.reaction_enzyme``'s module docstring) -- there is
nothing to pass through or scope by here.

**No ``SourceCrossReference`` is ever attached.** ``SourceCrossReference``
represents a genuine external identifier for an entity
(``app/models/source_cross_reference.py``: "An external identifier
associated with an internal entity"). No connector in this repository
exposes a structured external identifier *for a reaction/enzyme
association itself* -- ``identity.source_identifier`` here is only an
opaque request-tracking string, not something an external database would
recognize as this association's own id (see
``app.normalization.reaction_enzyme``'s module docstring: "Connector
adapters deliberately omitted"). Attaching a cross-reference from it would
misrepresent a synthetic tracking value as a genuine external identifier,
so this module never calls ``attach_source_cross_reference`` for either
``MATCHED`` reuse or ``NEW`` creation. ``ExternalRecord`` provenance may
still be recorded when a caller supplies it -- that table is a standalone,
entity-agnostic retrieval log, not itself a claim about this association's
external identity.

**Creation-required field**: ``relationship`` (the only ``NOT NULL``,
non-identity column -- ``app.normalization.reaction_enzyme``'s own
``_has_creation_complete_metadata`` rule).

**Freshness recheck** (``NEW`` only): re-query the exact
``(reaction_id, protein_id)`` or ``(reaction_id, complex_id)`` pair
``normalize_reaction_enzyme`` itself used. As of Increment 11
(``migrations/versions/0009_persistence_hardening.py``), this pair is
additionally protected by two real partial unique indexes
(``uq_reaction_enzyme_reaction_id_protein_id``/
``uq_reaction_enzyme_reaction_id_complex_id``) -- the recheck remains as a
fast, friendly first line of defense, but the database constraint is now
the actual concurrency authority: the ``INSERT`` below is wrapped to catch
the residual-race ``IntegrityError`` and convert it to a conservative
``FAILED`` result rather than letting a raw database exception escape as
if it were a scientific decision. A database-level ``CHECK`` constraint
(``ck_reaction_enzyme_exactly_one_target``, same migration) also now
enforces "exactly one of ``protein_id``/``complex_id``" independently of
``ReactionEnzymeIdentity``'s own application-level XOR check.

**No organism-consistency checking.** This module never queries
``Reaction``/``Protein``/``EnzymeComplex`` to confirm the referenced
protein or complex actually belongs to the same organism as the referenced
reaction -- doing so would require reads well outside the association
table itself, and no existing architecture performs that check anywhere
(open policy question, see this increment's completion report).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reaction import ReactionEnzyme
from app.normalization.reaction_enzyme import ReactionEnzymeIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import ExternalRecordProvenance, record_external_record
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "reaction_enzyme"


def persist_reaction_enzyme(
    identity: ReactionEnzymeIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Reaction<->enzyme association normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_reaction_enzyme received a result for entity_type={result.entity_type!r}, "
            f"expected {_ENTITY_TYPE!r}"
        )

    if result.status is NormalizationStatus.MATCHED:
        assert result.matched_entity_id is not None
        return _reuse(result, session=session, provenance=provenance)

    if result.status is NormalizationStatus.NEW:
        return _create(identity, result, session=session, provenance=provenance)

    if result.status in (NormalizationStatus.AMBIGUOUS, NormalizationStatus.CONFLICTED):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.REQUIRES_REVIEW,
            entity_type=_ENTITY_TYPE,
            review_required=True,
            reason=(
                result.reason or f"{result.status} reaction/enzyme association requires review"
            ),
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved reaction/enzyme association: no action taken",
    )


def _reuse(
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    entity_id = result.matched_entity_id
    assert entity_id is not None
    external_record_id = (
        record_external_record(session, source=result.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.REUSED_EXISTING,
        entity_type=_ENTITY_TYPE,
        entity_id=entity_id,
        reused=True,
        external_record_id=external_record_id,
        reason="reused existing matched reaction/enzyme association",
    )


def _create(
    identity: ReactionEnzymeIdentity,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    if not identity.relationship:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "cannot create ReactionEnzyme: relationship is required and was not supplied"
            ),
        )

    if identity.protein_id is not None:
        condition = (ReactionEnzyme.reaction_id == identity.reaction_id) & (
            ReactionEnzyme.protein_id == identity.protein_id
        )
    else:
        assert identity.complex_id is not None  # guaranteed by ReactionEnzymeIdentity's XOR check
        condition = (ReactionEnzyme.reaction_id == identity.reaction_id) & (
            ReactionEnzyme.complex_id == identity.complex_id
        )
    if session.execute(select(ReactionEnzyme.id).where(condition).limit(1)).first() is not None:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: an association matching this exact reaction/enzyme pair now "
                "exists -- re-normalize before retrying"
            ),
        )

    row = ReactionEnzyme(
        reaction_id=identity.reaction_id,
        protein_id=identity.protein_id,
        complex_id=identity.complex_id,
        relationship=identity.relationship,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "stale NEW: the database rejected this reaction/enzyme pair as a "
                f"duplicate despite passing the freshness recheck: {exc.orig}"
            ),
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
        external_record_id=external_record_id,
        reason="created new reaction/enzyme association",
    )


__all__ = ["persist_reaction_enzyme"]
