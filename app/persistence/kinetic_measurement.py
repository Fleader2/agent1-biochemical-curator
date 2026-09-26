"""KineticMeasurement persistence (Agent 1.x Increment A).

Persists ``app.normalization.kinetic_measurement.KineticMeasurementIdentity``
faithfully -- never reinterpreting a reported value, unit, or parameter
type. This module performs no connector retrieval, no Decimal parsing (that
already happened in ``app.normalization.kinetic_measurement``), and no
cross-source entity resolution.

**Idempotency, never numeric-equality-based deduplication** (Increment A
Step 24). ``kinetic_measurement`` carries a partial unique index on
``(source, source_id)`` (migration ``0013_kinetic_measurement_sources``).
Persisting the identical ``(source, source_id)`` twice reuses the existing
row (``REUSED_EXISTING``); two *different* ``source_id`` values that happen
to report the identical numeric value are never merged -- each becomes its
own row, exactly as ``app/models/kinetic_measurement.py``'s own docstring
requires.

**Conservative, provenance-driven derivative detection, never value-based**
(Increment A Step 25). When ``identity.original_source``/
``.original_source_identifier`` are set (a source explicitly reports that
its record mirrors another source's record -- SABIO-RK/BRENDA today never
set these; a future source might), this module looks for an *existing* row
whose own ``(source, source_id)`` exactly matches that claimed origin. If
found, the new source is recorded as an additional ``SourceCrossReference``
on the existing row (never a new, duplicate ``KineticMeasurement`` row),
and this call returns ``REUSED_EXISTING`` referencing that row. No raw row
is ever deleted, and no confidence/replication count is adjusted here --
Increment A's explicit policy ("never increase confidence or replication
counts merely because the same underlying measurement appears through
multiple databases") is upheld structurally, by this module never touching
``confidence_score``/``confidence_class`` at all. If the claimed origin
does not (yet) exist as a row, this is not an error -- lineage cannot be
resolved against a row that has not been ingested yet, so this module falls
through to ordinary creation, preserving ``identity.original_source``/
``.original_source_identifier`` nowhere else (this table has no column for
them) -- a disclosed limitation, see
``docs/24_kinetic_data_curation_and_handoff.md`` §7.

**No unit conversion, no value reinterpretation.** ``parameter_value``/
``unit`` and ``original_value``/``original_unit`` are both set from
``identity.value``/``identity.unit`` -- identical values, because this
increment performs no separate conversion step (see
``app.normalization.kinetic_measurement``'s module docstring). This is not
duplication-by-mistake: the schema's own docstring describes
``original_value``/``original_unit`` as the never-overwritten as-reported
figures and ``parameter_value``/``unit`` as the working figures a later
increment's normalization *may* someday update -- so both start out
identical and this module never populates
``normalized_value``/``normalized_unit`` (left ``NULL``).

**BRENDA range maxima have no dedicated column.** ``KineticMeasurement`` has
no ``parameter_value_maximum`` column (verified directly against
``app/models/kinetic_measurement.py``; adding one is out of this
increment's narrow schema-extension scope, Step 3). When
``identity.value_maximum`` is present, it is appended to ``notes`` as a
plain, clearly-labeled sentence -- never silently dropped, and never
blended into ``parameter_value`` (see
``docs/24_kinetic_data_curation_and_handoff.md`` §12 for this disclosed
limitation and its follow-up).

**Transaction handling.** Mirrors ``app.persistence.knowledge_gap``: one
``SAVEPOINT`` (``session.begin_nested()``) around each insert attempt, an
``IntegrityError`` caught and resolved via a post-failure recheck query
(the partial unique index is the real concurrency authority), every other
exception left to propagate. Never commits or rolls back the session it is
given -- the caller owns that (``app/db/session.py``).

**Protein-context preservation, never a "winning" protein** (Agent 1.x
Increment C.6). ``identity.protein_id`` is the protein whose own resolution
*motivated this specific persist call* -- on a first-ever ``(source,
source_id)`` insert, it becomes ``KineticMeasurement.protein_id`` (kept
exactly as before, for backward compatibility with the ordinary,
single-protein-context case); on a *reuse* (exact replay, derivative
lineage, or a race), it is a **different, independently valid** protein
context for a record that already exists under a different protein.
Before this increment, that second protein's own successful discovery left
no trace at all once ``_reuse`` returned pointing at the first protein's
row -- confirmed live, Real Integration Pilot 1 Run 7 (yeast's real
FAS1/FAS2 heterodimer, sharing one EC number, each independently
discovering the identical 7 real SABIO-RK records; only the
alphabetically-first protein UUID ever appeared in the database).
``_attach_protein_context`` is now called on every persist outcome (create
*and* reuse alike) whenever ``identity.protein_id`` is set, recording that
protein's own applicability to the measurement in
``kinetic_measurement_protein_context`` -- idempotently (the same protein
attached twice is a no-op, never a duplicate row) and independent of
processing order (attaching protein A then protein B produces the exact
same two rows as attaching B then A). This never changes
``KineticMeasurement.protein_id`` itself once set -- no "correction," no
re-attribution, no arbitrarily preferred protein; the join table is
strictly additive.

**``KineticMeasurement.protein_id`` is a legacy convenience field after
this increment, not the authoritative answer to "which protein(s) is this
measurement applicable to."** It remains populated (whichever protein
happened to persist first) solely so pre-C.6 single-protein-context
callers/queries keep working unmodified. Any new code -- and any downstream
consumer of the Agent 1 handoff -- must treat
``kinetic_measurement_protein_context`` (and its reshapings,
``Agent1KnowledgePackage.kinetic_measurement_protein_contexts`` /
``CuratedKineticMeasurement.protein_ids``) as authoritative instead.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.kinetic_measurement import (
    KINETIC_MEASUREMENT_PROTEIN_CONTEXT_BASIS_QUERY,
    KineticMeasurement,
    KineticMeasurementProteinContext,
)
from app.normalization.kinetic_measurement import KineticMeasurementIdentity
from app.persistence.kinetic_measurement_types import KineticMeasurementPersistenceResult
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction

_ENTITY_TYPE = "kinetic_measurement"


def _existing_by_source(
    session: Session, source: SourceType, source_id: str
) -> KineticMeasurement | None:
    return session.execute(
        select(KineticMeasurement).where(
            KineticMeasurement.source == source, KineticMeasurement.source_id == source_id
        )
    ).scalar_one_or_none()


def attach_kinetic_measurement_protein_context(
    session: Session, *, kinetic_measurement_id: UUID, protein_id: UUID
) -> UUID:
    """Idempotently record that ``protein_id`` is biologically applicable to
    ``kinetic_measurement_id`` (Increment C.6). See module docstring.

    Mirrors ``app.persistence.provenance.attach_source_cross_reference``'s
    own idempotent shape exactly: reuse an existing row with the identical
    ``(kinetic_measurement_id, protein_id)`` pair rather than creating a
    duplicate -- both an application-level check and backed by a real
    database unique constraint as a concurrency backstop. Never commits or
    rolls back -- the caller owns that.
    """
    existing_id = session.execute(
        select(KineticMeasurementProteinContext.id).where(
            KineticMeasurementProteinContext.kinetic_measurement_id == kinetic_measurement_id,
            KineticMeasurementProteinContext.protein_id == protein_id,
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        return existing_id

    row = KineticMeasurementProteinContext(
        kinetic_measurement_id=kinetic_measurement_id,
        protein_id=protein_id,
        basis=KINETIC_MEASUREMENT_PROTEIN_CONTEXT_BASIS_QUERY,
    )
    session.add(row)
    session.flush()
    return row.id


def _build_notes(identity: KineticMeasurementIdentity) -> str | None:
    """Fold ``identity.notes`` and any BRENDA-style range maximum into one string.

    See module docstring: ``KineticMeasurement`` has no dedicated column for
    a reported range maximum, so it is preserved here as plain text rather
    than dropped or averaged into ``parameter_value``.
    """
    parts: list[str] = []
    if identity.notes:
        parts.append(identity.notes)
    if identity.value_maximum is not None:
        parts.append(f"reported range maximum: {identity.value_maximum} {identity.unit}")
    return " | ".join(parts) if parts else None


def _reuse(
    existing: KineticMeasurement,
    identity: KineticMeasurementIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
    reason: str,
) -> KineticMeasurementPersistenceResult:
    cross_reference_id = attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=existing.id,
        source=identity.source,
        external_id=identity.source_id,
    )
    if identity.protein_id is not None:
        attach_kinetic_measurement_protein_context(
            session, kinetic_measurement_id=existing.id, protein_id=identity.protein_id
        )
    external_record_id = (
        record_external_record(session, source=identity.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return KineticMeasurementPersistenceResult(
        action=PersistenceAction.REUSED_EXISTING,
        kinetic_measurement_id=existing.id,
        created=False,
        reused_existing=True,
        source=identity.source,
        source_id=identity.source_id,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason=reason,
    )


def persist_kinetic_measurement(
    identity: KineticMeasurementIdentity,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> KineticMeasurementPersistenceResult:
    """Persist one ``KineticMeasurementIdentity``. See module docstring."""
    if not isinstance(identity, KineticMeasurementIdentity):
        raise TypeError(
            f"persist_kinetic_measurement requires a KineticMeasurementIdentity, got {identity!r}"
        )

    existing = _existing_by_source(session, identity.source, identity.source_id)
    if existing is not None:
        return _reuse(
            existing,
            identity,
            session=session,
            provenance=provenance,
            reason=(
                f"reused existing kinetic measurement {existing.id}: identical "
                f"(source={identity.source}, source_id={identity.source_id!r}) already ingested"
            ),
        )

    if identity.original_source is not None:
        assert identity.original_source_identifier is not None  # enforced by __post_init__
        derivative_of = _existing_by_source(
            session, identity.original_source, identity.original_source_identifier
        )
        if derivative_of is not None:
            return _reuse(
                derivative_of,
                identity,
                session=session,
                provenance=provenance,
                reason=(
                    f"reused existing kinetic measurement {derivative_of.id}: {identity.source} "
                    f"record {identity.source_id!r} reports itself as derived from "
                    f"{identity.original_source} record "
                    f"{identity.original_source_identifier!r}, which already exists"
                ),
            )
        # Claimed origin not (yet) ingested -- not an error (Increment A Step
        # 25: this is not a confirmed derivative until the origin exists),
        # falls through to ordinary creation below.

    try:
        with session.begin_nested():
            row = KineticMeasurement(
                reaction_id=identity.reaction_id,
                protein_id=identity.protein_id,
                complex_id=identity.complex_id,
                parameter_type=identity.parameter_type.value,
                parameter_value=identity.value,
                unit=identity.unit,
                original_value=identity.value,
                original_unit=identity.unit,
                substrate_id=identity.substrate_id,
                organism_id=identity.organism_id,
                strain=identity.strain,
                temperature_c=identity.temperature_c,
                ph=identity.ph,
                publication_id=identity.publication_id,
                source=identity.source,
                source_id=identity.source_id,
                reported_rate_law=identity.reported_rate_law,
                reported_parameter_type=identity.reported_parameter_type,
                notes=_build_notes(identity),
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_by_source(session, identity.source, identity.source_id)
        if raced_existing is not None:
            return _reuse(
                raced_existing,
                identity,
                session=session,
                provenance=provenance,
                reason=(
                    f"reused existing kinetic measurement {raced_existing.id}: a concurrent "
                    f"insert of the identical (source={identity.source}, "
                    f"source_id={identity.source_id!r}) won the race"
                ),
            )
        return KineticMeasurementPersistenceResult(
            action=PersistenceAction.FAILED,
            kinetic_measurement_id=None,
            created=False,
            reused_existing=False,
            source=identity.source,
            source_id=identity.source_id,
            reason=(
                "kinetic measurement creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    cross_reference_id = attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=row.id,
        source=identity.source,
        external_id=identity.source_id,
    )
    if identity.protein_id is not None:
        attach_kinetic_measurement_protein_context(
            session, kinetic_measurement_id=row.id, protein_id=identity.protein_id
        )
    external_record_id = (
        record_external_record(session, source=identity.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return KineticMeasurementPersistenceResult(
        action=PersistenceAction.CREATED,
        kinetic_measurement_id=row.id,
        created=True,
        reused_existing=False,
        source=identity.source,
        source_id=identity.source_id,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="created new kinetic measurement",
    )


def get_kinetic_measurement(
    session: Session, kinetic_measurement_id: UUID
) -> KineticMeasurement | None:
    """One ``KineticMeasurement`` row by id, or ``None``. Read-only."""
    return session.get(KineticMeasurement, kinetic_measurement_id)


def list_kinetic_measurements_by_reaction(
    session: Session, reaction_id: UUID
) -> tuple[KineticMeasurement, ...]:
    """Every ``KineticMeasurement`` row for one reaction, oldest first. Read-only.

    Ordered by ``created_at`` ascending with ``id`` as a stable tiebreaker,
    the same convention ``app.persistence.knowledge_gap.list_open_knowledge_gaps``
    uses.
    """
    statement = (
        select(KineticMeasurement)
        .where(KineticMeasurement.reaction_id == reaction_id)
        .order_by(KineticMeasurement.created_at.asc(), KineticMeasurement.id.asc())
    )
    return tuple(session.execute(statement).scalars().all())


__all__ = [
    "attach_kinetic_measurement_protein_context",
    "get_kinetic_measurement",
    "list_kinetic_measurements_by_reaction",
    "persist_kinetic_measurement",
]
