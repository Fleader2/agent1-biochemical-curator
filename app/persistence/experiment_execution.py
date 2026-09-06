"""ExperimentExecution/ExperimentResult persistence (Increment 25).

Bridges an accepted ``ExperimentRecommendationRecord`` (Increment 24) to
concrete, auditable laboratory work: ``persist_experiment_execution``
records one attempt to carry it out, and ``record_experiment_result``
records one observation that attempt produced. Neither function performs
any scientific interpretation -- no result is ever turned into
``supports_claim``/``refutes_claim``/a confidence change/a resolved
``KnowledgeGap``/a hypothesis (Increment 25 instructions, Step 5). This
module never imports ``app.models.claim``, ``app.models.knowledge_gap``, or
``app.confidence`` for exactly this reason.

**Accepted-recommendation gate** (Step 7). Creating a new
``ExperimentExecution`` requires the linked
``ExperimentRecommendationRecord.lifecycle_status`` to be ``ACCEPTED`` at
persist time. Any other lifecycle status (``PROPOSED``/``DEFERRED``/
``REJECTED``/``SUPERSEDED``) yields ``PersistenceAction.REQUIRES_REVIEW``
with no row created -- the same conservative-result philosophy
``app.persistence.knowledge_gap``/``app.persistence.experiment_recommendation``
already use for their own terminal-state gates. This single check also
implements Step 8 (a *new* execution may never be created once an
``ACCEPTED`` recommendation later becomes ``SUPERSEDED``): the check reads
the recommendation's *current* status, not the status at any earlier point.
Existing execution rows created while the recommendation was still
``ACCEPTED`` are never touched, deleted, or invalidated by a later
supersession -- they remain historical records, exactly as
``app.review.experiment_recommendation_workflow`` already guarantees no
code path here reacts to a recommendation's lifecycle at all after an
execution exists.

**Execution identity** (Step 4). ``execution_identity`` is a deterministic
digest of ``recommendation_id`` + caller-supplied ``execution_identifier``
only -- never Python's built-in ``hash()``, a canonical sorted-keys JSON
serialization fed through SHA-256 instead, the same convention
``app.persistence.knowledge_gap.compute_identity_key``/
``app.experiment_recommendation.recommender.compute_recommendation_identity``
already establish. A single recommendation may have many executions
(replication is scientifically meaningful, Step 5): only
``execution_identity`` is unique at the database level, never
``recommendation_id`` alone.

**Execution idempotency, without a content-conflict check** (Step 32). A
repeat call with the identical ``execution_identity`` returns
``REUSED_EXISTING``, but -- unlike ``persist_experiment_recommendation`` --
this module never compares the repeat call's descriptive fields
(``planned_conditions``/``performed_by``/``laboratory``/...) against the
existing row's. See ``app.models.experiment_execution.ExperimentExecution``'s
own docstring for why: those fields are free-form caller input, not a
deterministic function of the identity ingredients, so a mismatch is not
necessarily a bug the way it would be for a recommendation.

**Result identity and conflict** (Steps 19-20, 32). ``result_identity`` is
a deterministic digest of provenance/classification fields only
(``execution_identity``, ``result_type``, ``measurement_name``,
``sample_identifier``, ``replicate_identifier``, ``time_point``, and the
caller-supplied ``result_identifier``) -- never of ``value_text``/
``value_numeric``. An identity match with matching full content is
``REUSED_EXISTING``; an identity match with differing content raises
``app.persistence.errors.ExperimentResultIdentityConflictError`` rather
than silently overwriting a previously recorded observation. There is no
revision/supersession mechanism in this increment (Step 20's "if not
implemented now" branch) -- a correction requires a new, distinct
``result_identifier``.

**Result-recording gate** (Step 29). Results may be recorded while the
linked execution's status is ``IN_PROGRESS``, ``COMPLETED``, or ``FAILED``
-- ``FAILED`` is deliberately included (Step 31: "do not discard
scientifically valid partial measurements merely because execution
ultimately failed"). Results are blocked for ``PLANNED``/``CANCELLED``
executions, represented as ``PersistenceAction.REQUIRES_REVIEW`` with no
row created. This gate is checked only when *creating* a new row -- an
identical-identity replay is always recognized as ``REUSED_EXISTING``
regardless of the execution's current status, the same "reuse is never
blocked by a status/terminal condition" precedent
``app.persistence.experiment_recommendation`` already establishes for
terminal ``KnowledgeGap`` rows.

**Transaction handling.** Exactly mirrors
``app.persistence.experiment_recommendation``: one ``SAVEPOINT``
(``session.begin_nested()``) around each insert attempt, an
``IntegrityError`` caught and resolved via a post-failure recheck query,
every other exception (including ``ExperimentResultIdentityConflictError``)
left to propagate. Never commits or rolls back the session it is given.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.persistence.errors import ExperimentResultIdentityConflictError
from app.persistence.experiment_execution_types import (
    ExperimentExecutionPersistenceResult,
    ExperimentResultInput,
    ExperimentResultPersistenceResult,
)
from app.persistence.types import PersistenceAction

_EXECUTION_IDENTITY_VERSION = "experiment-exec-v1"
_RESULT_IDENTITY_VERSION = "experiment-result-v1"

_ACCEPTED_LIFECYCLE_STATUS = "ACCEPTED"
_RESULT_ALLOWED_EXECUTION_STATUSES = frozenset({"IN_PROGRESS", "COMPLETED", "FAILED"})


def compute_execution_identity(recommendation_id: UUID, execution_identifier: str) -> str:
    """A deterministic, versioned digest of ``(recommendation_id, execution_identifier)``.

    Never Python's built-in ``hash()``. See module docstring.
    """
    canonical = {
        "recommendation_id": str(recommendation_id),
        "execution_identifier": execution_identifier,
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{_EXECUTION_IDENTITY_VERSION}:{digest}"


def compute_result_identity(execution_identity: str, result: ExperimentResultInput) -> str:
    """A deterministic, versioned digest of one result's identity ingredients.

    Deliberately excludes ``value_text``/``value_numeric`` -- see
    ``app.persistence.errors.ExperimentResultIdentityConflictError`` for why.
    """
    canonical = {
        "execution_identity": execution_identity,
        "result_type": result.result_type.value,
        "measurement_name": result.measurement_name,
        "sample_identifier": result.sample_identifier,
        "replicate_identifier": result.replicate_identifier,
        "time_point": result.time_point,
        "result_identifier": result.result_identifier,
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{_RESULT_IDENTITY_VERSION}:{digest}"


def persist_experiment_execution(
    *,
    recommendation_id: UUID,
    execution_identifier: str,
    planned_conditions: dict | None = None,
    actual_conditions: dict | None = None,
    planned_start_at=None,
    performed_by: str | None = None,
    laboratory: str | None = None,
    protocol_reference: str | None = None,
    notes: str | None = None,
    session: Session,
) -> ExperimentExecutionPersistenceResult:
    """Persist one execution attempt of an accepted recommendation. See module docstring.

    No recommendation recomputation and no experiment-recommendation
    generation logic occurs here -- ``recommendation_id`` is only ever used
    to look up the existing ``ExperimentRecommendationRecord`` row and check
    its current ``lifecycle_status``.
    """
    if not isinstance(recommendation_id, UUID):
        raise TypeError(
            f"persist_experiment_execution requires a UUID recommendation_id, "
            f"got {recommendation_id!r}"
        )
    if not isinstance(execution_identifier, str) or not execution_identifier.strip():
        raise ValueError(
            "persist_experiment_execution requires a non-empty execution_identifier, "
            f"got {execution_identifier!r}"
        )
    execution_identifier = execution_identifier.strip()

    identity = compute_execution_identity(recommendation_id, execution_identifier)

    existing = _existing_execution_by_identity(session, identity)
    if existing is not None:
        return ExperimentExecutionPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            execution_id=existing.id,
            execution_identity=identity,
            created=False,
            reused_existing=True,
            reason=(
                f"reused existing experiment execution {existing.id} with the identical "
                "execution_identity"
            ),
        )

    recommendation = session.get(ExperimentRecommendationRecord, recommendation_id)
    if recommendation is None:
        raise ValueError(f"no ExperimentRecommendationRecord exists with id {recommendation_id!r}")
    if recommendation.lifecycle_status != _ACCEPTED_LIFECYCLE_STATUS:
        return ExperimentExecutionPersistenceResult(
            action=PersistenceAction.REQUIRES_REVIEW,
            execution_id=None,
            execution_identity=identity,
            created=False,
            reused_existing=False,
            review_required=True,
            reason=(
                f"ExperimentRecommendationRecord {recommendation_id} has lifecycle_status "
                f"{recommendation.lifecycle_status!r}, not ACCEPTED; an execution is not "
                "created for a recommendation that has not been accepted"
            ),
        )

    try:
        with session.begin_nested():
            row = ExperimentExecution(
                recommendation_id=recommendation_id,
                execution_identifier=execution_identifier,
                execution_identity=identity,
                status="PLANNED",
                planned_start_at=planned_start_at,
                performed_by=performed_by,
                laboratory=laboratory,
                protocol_reference=protocol_reference,
                planned_conditions_json=(
                    planned_conditions if planned_conditions is not None else {}
                ),
                actual_conditions_json=actual_conditions,
                notes=notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = _existing_execution_by_identity(session, identity)
        if raced is not None:
            return ExperimentExecutionPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                execution_id=raced.id,
                execution_identity=identity,
                created=False,
                reused_existing=True,
                reason=(
                    f"reused existing experiment execution {raced.id} found after a "
                    "concurrent-insert race"
                ),
            )
        return ExperimentExecutionPersistenceResult(
            action=PersistenceAction.FAILED,
            execution_id=None,
            execution_identity=identity,
            created=False,
            reused_existing=False,
            reason=(
                "experiment execution creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return ExperimentExecutionPersistenceResult(
        action=PersistenceAction.CREATED,
        execution_id=row.id,
        execution_identity=identity,
        created=True,
        reused_existing=False,
        reason="created new experiment execution",
    )


def record_experiment_result(
    *,
    execution_id: UUID,
    result: ExperimentResultInput,
    session: Session,
) -> ExperimentResultPersistenceResult:
    """Persist one observation produced by one execution. See module docstring."""
    if not isinstance(execution_id, UUID):
        raise TypeError(
            f"record_experiment_result requires a UUID execution_id, got {execution_id!r}"
        )
    if not isinstance(result, ExperimentResultInput):
        raise TypeError(
            f"record_experiment_result requires an ExperimentResultInput, got {result!r}"
        )

    execution = session.get(ExperimentExecution, execution_id)
    if execution is None:
        raise ValueError(f"no ExperimentExecution exists with id {execution_id!r}")

    identity = compute_result_identity(execution.execution_identity, result)

    existing = _existing_result_by_identity(session, identity)
    if existing is not None:
        _require_result_content_matches(existing, result, execution_id, identity)
        return ExperimentResultPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            result_id=existing.id,
            result_identity=identity,
            created=False,
            reused_existing=True,
            reason=(
                f"reused existing experiment result {existing.id} with the identical "
                "result_identity"
            ),
        )

    if execution.status not in _RESULT_ALLOWED_EXECUTION_STATUSES:
        return ExperimentResultPersistenceResult(
            action=PersistenceAction.REQUIRES_REVIEW,
            result_id=None,
            result_identity=identity,
            created=False,
            reused_existing=False,
            review_required=True,
            reason=(
                f"ExperimentExecution {execution_id} has status {execution.status!r}; results "
                "are not recorded for a PLANNED or CANCELLED execution"
            ),
        )

    try:
        with session.begin_nested():
            row = _build_result_row(execution_id, identity, result)
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = _existing_result_by_identity(session, identity)
        if raced is not None:
            _require_result_content_matches(raced, result, execution_id, identity)
            return ExperimentResultPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                result_id=raced.id,
                result_identity=identity,
                created=False,
                reused_existing=True,
                reason=(
                    f"reused existing experiment result {raced.id} found after a "
                    "concurrent-insert race"
                ),
            )
        return ExperimentResultPersistenceResult(
            action=PersistenceAction.FAILED,
            result_id=None,
            result_identity=identity,
            created=False,
            reused_existing=False,
            reason=(
                "experiment result creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return ExperimentResultPersistenceResult(
        action=PersistenceAction.CREATED,
        result_id=row.id,
        result_identity=identity,
        created=True,
        reused_existing=False,
        reason="created new experiment result",
    )


def get_experiment_execution(session: Session, execution_id: UUID) -> ExperimentExecution | None:
    """One ``ExperimentExecution`` row by id, or ``None``. Read-only."""
    return session.get(ExperimentExecution, execution_id)


def list_executions_for_recommendation(
    session: Session, recommendation_id: UUID
) -> tuple[ExperimentExecution, ...]:
    """Every execution for one recommendation, oldest first. Read-only."""
    statement = (
        select(ExperimentExecution)
        .where(ExperimentExecution.recommendation_id == recommendation_id)
        .order_by(ExperimentExecution.created_at.asc(), ExperimentExecution.id.asc())
    )
    return tuple(session.execute(statement).scalars().all())


def get_experiment_result(session: Session, result_id: UUID) -> ExperimentResult | None:
    """One ``ExperimentResult`` row by id, or ``None``. Read-only."""
    return session.get(ExperimentResult, result_id)


def list_results_for_execution(
    session: Session, execution_id: UUID
) -> tuple[ExperimentResult, ...]:
    """Every result for one execution. Read-only.

    Ordered by ``observed_at`` ascending (nulls last, since not every
    result carries one), then ``created_at`` ascending, then ``id`` as a
    final stable tiebreak (Increment 25 instructions, Step 42).
    """
    statement = (
        select(ExperimentResult)
        .where(ExperimentResult.execution_id == execution_id)
        .order_by(
            ExperimentResult.observed_at.asc().nulls_last(),
            ExperimentResult.created_at.asc(),
            ExperimentResult.id.asc(),
        )
    )
    return tuple(session.execute(statement).scalars().all())


def diff_planned_vs_actual_conditions(execution: ExperimentExecution) -> dict[str, list[str]]:
    """Report exact key-level differences between planned and actual conditions. Read-only.

    A deterministic, structural comparison only (Increment 25 instructions,
    Step 24) -- it reports which keys are present in one side but not the
    other, and which shared keys hold different values. It never classifies
    a difference as a scientific failure, a deviation worth flagging, or
    anything else requiring judgment; the caller decides what a reported
    difference means, if anything.
    """
    planned = execution.planned_conditions_json or {}
    actual = execution.actual_conditions_json or {}
    if not isinstance(planned, dict) or not isinstance(actual, dict):
        return {"missing_in_actual": [], "extra_in_actual": [], "differing_keys": []}
    planned_keys = set(planned.keys())
    actual_keys = set(actual.keys())
    differing = sorted(
        key
        for key in planned_keys & actual_keys
        if planned[key] != actual[key]
    )
    return {
        "missing_in_actual": sorted(planned_keys - actual_keys),
        "extra_in_actual": sorted(actual_keys - planned_keys),
        "differing_keys": differing,
    }


def _existing_execution_by_identity(session: Session, identity: str) -> ExperimentExecution | None:
    return session.execute(
        select(ExperimentExecution).where(ExperimentExecution.execution_identity == identity)
    ).scalar_one_or_none()


def _existing_result_by_identity(session: Session, identity: str) -> ExperimentResult | None:
    return session.execute(
        select(ExperimentResult).where(ExperimentResult.result_identity == identity)
    ).scalar_one_or_none()


def _build_result_row(
    execution_id: UUID, identity: str, result: ExperimentResultInput
) -> ExperimentResult:
    return ExperimentResult(
        execution_id=execution_id,
        result_identity=identity,
        result_type=result.result_type.value,
        measurement_name=result.measurement_name,
        value_text=result.value_text,
        value_numeric=result.value_numeric,
        unit=result.unit,
        uncertainty_text=result.uncertainty_text,
        statistical_support=result.statistical_support,
        sample_identifier=result.sample_identifier,
        replicate_identifier=result.replicate_identifier,
        time_point=result.time_point,
        condition_label=result.condition_label,
        instrument_reference=result.instrument_reference,
        raw_data_reference=result.raw_data_reference,
        observed_at=result.observed_at,
        notes=result.notes,
    )


def _require_result_content_matches(
    existing: ExperimentResult, result: ExperimentResultInput, execution_id: UUID, identity: str
) -> None:
    if _result_content_matches(existing, result, execution_id):
        return
    raise ExperimentResultIdentityConflictError(
        f"an ExperimentResult with result_identity {identity!r} already exists "
        f"(id={existing.id}) but its persisted content differs from the supplied result -- "
        "a correction to a previously recorded observation requires a new, distinct "
        "result_identifier, never an overwrite of an existing row"
    )


def _result_content_matches(
    existing: ExperimentResult, result: ExperimentResultInput, execution_id: UUID
) -> bool:
    return (
        existing.execution_id == execution_id
        and existing.result_type == result.result_type.value
        and existing.measurement_name == result.measurement_name
        and existing.value_text == result.value_text
        and existing.value_numeric == result.value_numeric
        and existing.unit == result.unit
        and existing.uncertainty_text == result.uncertainty_text
        and existing.statistical_support == result.statistical_support
        and existing.sample_identifier == result.sample_identifier
        and existing.replicate_identifier == result.replicate_identifier
        and existing.time_point == result.time_point
        and existing.condition_label == result.condition_label
        and existing.instrument_reference == result.instrument_reference
        and existing.raw_data_reference == result.raw_data_reference
        and existing.observed_at == result.observed_at
        and existing.notes == result.notes
    )


__all__ = [
    "compute_execution_identity",
    "compute_result_identity",
    "diff_planned_vs_actual_conditions",
    "get_experiment_execution",
    "get_experiment_result",
    "list_executions_for_recommendation",
    "list_results_for_execution",
    "persist_experiment_execution",
    "record_experiment_result",
]
