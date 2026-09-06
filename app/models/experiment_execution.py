"""Experiment execution and result records (Increment 25).

Three tables, forming the bridge from an accepted
``ExperimentRecommendationRecord`` (Increment 24) to concrete, auditable
laboratory work:

* ``experiment_execution`` -- one attempt to actually carry out an accepted
  recommendation. A single recommendation may have many executions
  (replication is scientifically meaningful, Increment 25 instructions
  Step 5), so ``recommendation_id`` carries no uniqueness of its own --
  only the derived ``execution_identity`` (``recommendation_id`` +
  caller-supplied ``execution_identifier``) does.
* ``experiment_execution_event`` -- an append-only lifecycle audit trail,
  structurally independent of ``experiment_recommendation_event`` for the
  identical reason that table is independent of ``review_event``: a
  distinct status vocabulary (``PLANNED``/``IN_PROGRESS``/``COMPLETED``/
  ``FAILED``/``CANCELLED``) with no shared meaning.
* ``experiment_result`` -- one observation/measurement produced by one
  execution. Append-only: no ``updated_at`` at all (the same convention
  ``ExperimentalCondition``/``ExperimentRecommendationEvent`` already use
  for a record this schema never expects to be mutated after creation) --
  a correction is a new row with a new ``result_identity``, never an
  ``UPDATE`` of a previously recorded value (Increment 25 instructions,
  Step 20).

**No native enum types.** ``status``/``result_type`` are plain ``VARCHAR``
columns guarded by ``CHECK`` constraints, following the exact convention
``app.models.knowledge_gap.GAP_TYPE_VALUES``/``app.models
.experiment_recommendation.LIFECYCLE_STATUS_VALUES`` already established
(no models-layer import of an upper-layer package, no duplicated enum
definition).

**What this module never records.** No column here represents scientific
interpretation: no ``supports_claim``/``refutes_claim``/``confidence_delta``/
``gap_resolved``/``hypothesis`` field exists anywhere in this file
(Increment 25 instructions, Step 12). ``ExperimentExecution``/
``ExperimentResult`` describe what was planned/done and what was observed,
never what it means.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.naming import conv

from app.db.base import Base

#: Transcribed verbatim from ``app.review.experiment_execution_types
#: .ExecutionStatus`` at the time this model was written. Not imported from
#: there -- ``app/models/`` is the lowest layer in this repository's import
#: graph, the same reasoning ``app.models.experiment_recommendation
#: .LIFECYCLE_STATUS_VALUES`` already documents for itself.
EXECUTION_STATUS_VALUES = ("PLANNED", "IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED")

#: Transcribed verbatim from ``app.persistence.experiment_execution_types.ResultType``.
RESULT_TYPE_VALUES = (
    "QUANTITATIVE_MEASUREMENT",
    "QUALITATIVE_OBSERVATION",
    "DETECTION",
    "NON_DETECTION",
    "ASSAY_OUTCOME",
)


def _in_list_check(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({quoted})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


class ExperimentExecution(Base):
    """One attempt to carry out an accepted ``ExperimentRecommendationRecord``.

    ``recommendation_id`` is ``NOT NULL`` with ``ON DELETE RESTRICT`` --
    the same ownership-FK convention every other row in this schema follows
    (e.g. ``experiment_recommendation.knowledge_gap_id``). It is
    deliberately **not** unique: ``app.persistence.experiment_execution
    .persist_experiment_execution`` is the sole gate that requires the
    linked recommendation's ``lifecycle_status`` to be ``ACCEPTED`` at
    creation time; this table itself does not (and structurally cannot)
    enforce that, matching ``experiment_recommendation_event.actor_type``'s
    own documented trust-boundary limitation (application-layer
    enforcement, not a database constraint).

    ``execution_identity`` (``"experiment-exec-v1:<sha256>"``,
    ``app.persistence.experiment_execution.compute_execution_identity``) is
    a deterministic digest of ``recommendation_id`` +
    ``execution_identifier`` only -- never of ``planned_conditions_json``/
    ``performed_by``/``notes``/any other caller-supplied descriptive field.
    This is a deliberate difference from ``ExperimentRecommendationRecord
    .recommendation_identity``: a recommendation's content is a pure
    deterministic function of its identity ingredients (so an identity
    match with different content is always a bug), while an execution's
    descriptive fields are free-form caller input with no such guarantee --
    ``persist_experiment_execution`` therefore treats a repeat call with the
    identical ``execution_identifier`` as an ordinary idempotent replay
    (``REUSED_EXISTING``) without a content-equality check, rather than
    raising a conflict error.

    ``planned_conditions_json`` is populated only from explicitly structured
    data (for example ``{"required_fields": [...]}`` copied from the
    originating recommendation's own
    ``experimental_context_requirements_json``) -- never a fabricated value
    for a field the caller did not supply (Increment 25 instructions,
    Step 22). ``actual_conditions_json`` is nullable and caller-supplied at
    creation time only; no API in this increment updates it afterward (see
    ``docs/21_experiment_execution_result_contract.md``'s open architecture
    questions).
    """

    __tablename__ = "experiment_execution"
    __table_args__ = (
        CheckConstraint(
            _in_list_check("status", EXECUTION_STATUS_VALUES),
            name=conv("ck_experiment_execution_status_valid"),
        ),
        Index(
            "uq_experiment_execution_execution_identity",
            "execution_identity",
            unique=True,
        ),
        Index("ix_experiment_execution_recommendation_id", "recommendation_id"),
        Index("ix_experiment_execution_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    recommendation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the formulaic
        # fk_<table>_<column>_<referred_table> name here would be 67
        # characters, exceeding PostgreSQL's 63-byte identifier limit --
        # the same pitfall (and fix) as
        # experiment_recommendation_event.recommendation_id.
        ForeignKey(
            "experiment_recommendation.id",
            ondelete="RESTRICT",
            name="fk_experiment_execution_recommendation_id",
        ),
        nullable=False,
    )

    execution_identifier: Mapped[str] = mapped_column(String, nullable=False)
    execution_identity: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="PLANNED", server_default="PLANNED"
    )

    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    performed_by: Mapped[str | None] = mapped_column(String)
    laboratory: Mapped[str | None] = mapped_column(String)
    protocol_reference: Mapped[str | None] = mapped_column(String)

    planned_conditions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actual_conditions_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list[ExperimentExecutionEvent]] = relationship(back_populates="execution")
    results: Mapped[list[ExperimentResult]] = relationship(back_populates="execution")


class ExperimentExecutionEvent(Base):
    """One lifecycle transition in one execution's audit trail.

    ``actor_type``/``actor_id`` mirror ``review_event.reviewer_type``/
    ``reviewer_id`` exactly: plain, unconstrained ``VARCHAR`` (no ``CHECK``,
    no enum). Unlike ``experiment_recommendation_event`` (whose
    ``actor_type`` is only ever written as ``HUMAN`` by the code that
    constructs it), Increment 25 instructions explicitly decline to assume
    execution transitions are human-only -- see
    ``app.review.experiment_execution_workflow``'s own docstring for why
    both fields are always caller-supplied here, with no hardcoded value.
    """

    __tablename__ = "experiment_execution_event"
    __table_args__ = (
        CheckConstraint(
            _in_list_check("previous_status", EXECUTION_STATUS_VALUES),
            name=conv("ck_experiment_execution_event_previous_status_valid"),
        ),
        CheckConstraint(
            _in_list_check("new_status", EXECUTION_STATUS_VALUES),
            name=conv("ck_experiment_execution_event_new_status_valid"),
        ),
        Index("ix_experiment_execution_event_execution_id", "execution_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # Explicit, shortened name: the formulaic
        # fk_<table>_<column>_<referred_table> name would exceed
        # PostgreSQL's 63-byte identifier limit -- the same pitfall (and
        # fix) as experiment_recommendation_event.recommendation_id.
        ForeignKey(
            "experiment_execution.id",
            ondelete="RESTRICT",
            name="fk_experiment_execution_event_execution_id",
        ),
        nullable=False,
    )

    previous_status: Mapped[str] = mapped_column(String, nullable=False)
    new_status: Mapped[str] = mapped_column(String, nullable=False)

    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)

    comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped[ExperimentExecution] = relationship(back_populates="events")


class ExperimentResult(Base):
    """One observation/measurement produced by one ``ExperimentExecution``.

    Append-only: no ``updated_at`` column exists at all (the same
    intentional omission ``ExperimentalCondition``/
    ``ExperimentRecommendationEvent`` already document for a row this
    schema never expects to mutate after creation). A correction to a
    previously recorded value is always a *new* row with its own
    ``result_identity`` -- ``app.persistence.experiment_execution`` never
    issues an ``UPDATE`` against any column of this table.

    ``result_identity`` (``"experiment-result-v1:<sha256>"``) is derived
    from provenance/classification fields only (``execution_id``,
    ``result_type``, ``measurement_name``, ``sample_identifier``,
    ``replicate_identifier``, ``time_point``, and the caller-supplied
    ``result_identifier`` that distinguishes otherwise-identical repeats)
    -- **never** from ``value_text``/``value_numeric`` themselves
    (Increment 25 instructions, Step 19: "a correction to a result value
    must not silently collide with provenance identity"). A second call
    with the identical identity ingredients but a different recorded value
    is therefore an identity match with differing content, and
    ``app.persistence.errors.ExperimentResultIdentityConflictError`` is
    raised rather than silently overwriting the first observation.

    No column here represents interpretation: there is no
    ``supports_claim``/``refutes_claim``/``confidence_delta``/
    ``gap_resolved``/``hypothesis`` field, and none will ever be added to
    this table -- that is a different, later stage's responsibility (see
    ``docs/21_experiment_execution_result_contract.md`` §23).
    """

    __tablename__ = "experiment_result"
    __table_args__ = (
        CheckConstraint(
            _in_list_check("result_type", RESULT_TYPE_VALUES),
            name=conv("ck_experiment_result_result_type_valid"),
        ),
        Index("uq_experiment_result_result_identity", "result_identity", unique=True),
        Index("ix_experiment_result_execution_id", "execution_id"),
        Index("ix_experiment_result_measurement_name", "measurement_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experiment_execution.id", ondelete="RESTRICT"),
        nullable=False,
    )

    result_identity: Mapped[str] = mapped_column(String, nullable=False)
    result_type: Mapped[str] = mapped_column(String, nullable=False)
    measurement_name: Mapped[str] = mapped_column(String, nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String)

    uncertainty_text: Mapped[str | None] = mapped_column(Text)
    statistical_support: Mapped[str | None] = mapped_column(Text)

    sample_identifier: Mapped[str | None] = mapped_column(String)
    replicate_identifier: Mapped[str | None] = mapped_column(String)
    time_point: Mapped[str | None] = mapped_column(String)
    condition_label: Mapped[str | None] = mapped_column(String)

    instrument_reference: Mapped[str | None] = mapped_column(String)
    raw_data_reference: Mapped[str | None] = mapped_column(Text)

    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped[ExperimentExecution] = relationship(back_populates="results")


__all__ = [
    "EXECUTION_STATUS_VALUES",
    "RESULT_TYPE_VALUES",
    "ExperimentExecution",
    "ExperimentExecutionEvent",
    "ExperimentResult",
]
