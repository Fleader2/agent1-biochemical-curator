"""QuantitativeObservation persistence (Agent 1.x Increment "Experimental Context
and Quantitative Observation Framework" / "SGD Reference Protein Abundance
Integration").

Mirrors ``app.persistence.kinetic_measurement`` in shape and policy, narrowed
to this table's own, simpler invariants: ``QuantitativeObservation`` carries
no derivative-lineage concept (no ``original_source``/
``original_source_identifier`` fields on
``app.normalization.quantitative_observation.QuantitativeObservationIdentity``),
so this module has no analogue to that module's "claimed origin" fallthrough
-- every persist call is either an exact ``(source, source_id)`` replay
(``REUSED_EXISTING``) or a brand new row (``CREATED``).

**Idempotency via the partial unique index, never numeric-equality-based
deduplication** (mirrors ``kinetic_measurement``'s own identical policy,
migration ``0017_exp_context_qobs``'s ``uq_quantitative_observation_source_source_id``).
When ``identity.source``/``identity.source_id`` are both set, persisting the
identical pair twice reuses the existing row; two different ``source_id``
values that happen to report the identical numeric value are never merged.
When either is ``None`` (a caller with no deterministic source identity to
key on), idempotency cannot be checked at all -- this module always creates
a new row rather than guessing at equality, exactly like
``KineticMeasurement`` would if given a null ``source_id`` (a case that
cannot occur there today only because every existing caller always supplies
one; this module keeps the guard explicit for is own, single first caller,
SGD abundance, and any future one).

**Transaction handling.** Identical to ``persist_kinetic_measurement``: one
``SAVEPOINT`` around each insert attempt, ``IntegrityError`` resolved via a
post-failure recheck query, every other exception left to propagate. Never
commits or rolls back the session it is given.

**``get_or_create_sgd_abundance_reference_context``** exists because
``ExperimentalContext`` (unlike ``QuantitativeObservation``) has no
database-level unique constraint at all (see
``app/models/experimental_context.py``'s own docstring: two contexts
describing the same real-world condition are deliberately never
deduplicated by the schema, mirroring ``KineticMeasurement``'s "never merge"
policy). Repeated SGD abundance ingestion nonetheless needs *one* reusable
reference context (task's own Sec 6: "repeated ingestion must reuse the
same ExperimentalContext"), so this function keys reuse on an
application-level marker -- a fixed, deterministic ``source``/``source_id``
pair unique to "the SGD reference context for this organism" -- and accepts
that this is a narrower, weaker guarantee than a real unique index (a
genuine concurrent race could still create two rows; not a concern in this
repository's own single-writer ingestion model, and disclosed here rather
than silently assumed away).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import ExperimentalContextClassification, SourceType
from app.models.experimental_context import ExperimentalContext
from app.models.quantitative_observation import QuantitativeObservation
from app.normalization.quantitative_observation import QuantitativeObservationIdentity
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.quantitative_observation_types import (
    QuantitativeObservationPersistenceResult,
)
from app.persistence.types import PersistenceAction

_ENTITY_TYPE = "quantitative_observation"

#: Fixed marker source_id for the one reusable SGD abundance reference context, per
#: organism -- never a per-protein or per-abundance-value key, since the same context
#: applies to every SGD abundance observation for a given organism (task Sec 2: "create or
#: reuse ONE deterministic SGD reference context").
_SGD_ABUNDANCE_REFERENCE_CONTEXT_SOURCE_ID_PREFIX = "sgd-protein-abundance-reference-context"


def _existing_observation_by_source(
    session: Session, source: SourceType, source_id: str
) -> QuantitativeObservation | None:
    return session.execute(
        select(QuantitativeObservation).where(
            QuantitativeObservation.source == source,
            QuantitativeObservation.source_id == source_id,
        )
    ).scalar_one_or_none()


def _reuse(
    existing: QuantitativeObservation,
    identity: QuantitativeObservationIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
    reason: str,
) -> QuantitativeObservationPersistenceResult:
    assert identity.source is not None and identity.source_id is not None
    attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=existing.id,
        source=identity.source,
        external_id=identity.source_id,
    )
    if provenance is not None:
        record_external_record(session, source=identity.source, provenance=provenance)
    return QuantitativeObservationPersistenceResult(
        action=PersistenceAction.REUSED_EXISTING,
        quantitative_observation_id=existing.id,
        created=False,
        reused_existing=True,
        source=identity.source,
        source_id=identity.source_id,
        reason=reason,
    )


def persist_quantitative_observation(
    identity: QuantitativeObservationIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> QuantitativeObservationPersistenceResult:
    """Persist one ``QuantitativeObservationIdentity``. See module docstring."""
    if not isinstance(identity, QuantitativeObservationIdentity):
        raise TypeError(
            "persist_quantitative_observation requires a QuantitativeObservationIdentity, "
            f"got {identity!r}"
        )

    has_source_identity = identity.source is not None and identity.source_id is not None

    if has_source_identity:
        assert identity.source is not None and identity.source_id is not None
        existing = _existing_observation_by_source(session, identity.source, identity.source_id)
        if existing is not None:
            return _reuse(
                existing,
                identity,
                session=session,
                provenance=provenance,
                reason=(
                    f"reused existing quantitative observation {existing.id}: identical "
                    f"(source={identity.source}, source_id={identity.source_id!r}) already "
                    "ingested"
                ),
            )

    try:
        with session.begin_nested():
            row = QuantitativeObservation(
                observation_type=identity.observation_type,
                reported_observation_type=identity.reported_observation_type,
                value=identity.value,
                unit=identity.unit,
                normalized_value=identity.normalized_value,
                normalized_unit=identity.normalized_unit,
                uncertainty=identity.uncertainty,
                lower_bound=identity.lower_bound,
                upper_bound=identity.upper_bound,
                measurement_method=identity.measurement_method,
                evidence_class=identity.evidence_class,
                time_reference_basis=identity.time_reference_basis,
                time_value=identity.time_value,
                time_unit=identity.time_unit,
                time_canonical_s=identity.time_canonical_s,
                experimental_context_id=identity.experimental_context_id,
                perturbation_id=identity.perturbation_id,
                biological_replicate_id=identity.biological_replicate_id,
                technical_replicate_id=identity.technical_replicate_id,
                protein_id=identity.protein_id,
                compound_id=identity.compound_id,
                reaction_id=identity.reaction_id,
                organism_id=identity.organism_id,
                unresolved_identity_kind=identity.unresolved_identity_kind,
                unresolved_identity_text=identity.unresolved_identity_text,
                source=identity.source,
                source_id=identity.source_id,
                publication_id=identity.publication_id,
                dataset_id=identity.dataset_id,
                notes=identity.notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        if not has_source_identity:
            raise
        assert identity.source is not None and identity.source_id is not None
        raced_existing = _existing_observation_by_source(
            session, identity.source, identity.source_id
        )
        if raced_existing is not None:
            return _reuse(
                raced_existing,
                identity,
                session=session,
                provenance=provenance,
                reason=(
                    f"reused existing quantitative observation {raced_existing.id}: a "
                    f"concurrent insert of the identical (source={identity.source}, "
                    f"source_id={identity.source_id!r}) won the race"
                ),
            )
        return QuantitativeObservationPersistenceResult(
            action=PersistenceAction.FAILED,
            quantitative_observation_id=None,
            created=False,
            reused_existing=False,
            source=identity.source,
            source_id=identity.source_id,
            reason=(
                "quantitative observation creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    if identity.source is not None and identity.source_id is not None:
        attach_source_cross_reference(
            session,
            entity_type=_ENTITY_TYPE,
            entity_id=row.id,
            source=identity.source,
            external_id=identity.source_id,
        )
    if provenance is not None and identity.source is not None:
        record_external_record(session, source=identity.source, provenance=provenance)
    return QuantitativeObservationPersistenceResult(
        action=PersistenceAction.CREATED,
        quantitative_observation_id=row.id,
        created=True,
        reused_existing=False,
        source=identity.source,
        source_id=identity.source_id,
        reason="created new quantitative observation",
    )


def get_or_create_sgd_abundance_reference_context(
    session: Session, *, organism_id: UUID
) -> ExperimentalContext:
    """Get-or-create the one reusable SGD reference context for ``organism_id``.

    Never invents medium/growth phase/temperature/pH -- SGD's own median
    abundance figure documents none of those (task Sec 2's own explicit
    constraint), so every field on the created context beyond
    ``organism_id``/``classification``/``source`` stays ``NULL``. See module
    docstring for why reuse here is an application-level (not database-
    enforced) guarantee.
    """
    marker_source_id = f"{_SGD_ABUNDANCE_REFERENCE_CONTEXT_SOURCE_ID_PREFIX}:{organism_id}"
    existing = session.execute(
        select(ExperimentalContext).where(
            ExperimentalContext.source == SourceType.SGD,
            ExperimentalContext.source_id == marker_source_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    context = ExperimentalContext(
        organism_id=organism_id,
        classification=ExperimentalContextClassification.REFERENCE,
        source=SourceType.SGD,
        source_id=marker_source_id,
        notes=(
            "SGD reference protein abundance context: SGD's own cross-study median "
            "abundance figures report no single strain, medium, growth phase, "
            "temperature, or pH -- none is fabricated here."
        ),
    )
    session.add(context)
    session.flush()
    return context


__all__ = [
    "get_or_create_sgd_abundance_reference_context",
    "persist_quantitative_observation",
]
