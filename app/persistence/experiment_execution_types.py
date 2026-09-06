"""Data contract for ExperimentExecution/ExperimentResult persistence (Increment 25).

Four types:

1. ``ResultType`` -- the closed vocabulary
   ``ExperimentResult.result_type`` is written from. Transcribed verbatim
   as ``app.models.experiment_execution.RESULT_TYPE_VALUES`` for the models
   layer, the same "vocabulary exists in code, transcribed as plain strings
   in the schema" pattern ``app.knowledge_gaps.types.GapType`` already
   established. Deliberately minimal (Increment 25 instructions, Step 13):
   no member here requires scientific interpretation to assign.
2. ``ExperimentResultInput`` -- one caller-specified observation, the sole
   public input to ``app.persistence.experiment_execution
   .record_experiment_result``. Mirrors ``app.persistence.claim_types
   .ExperimentalConditionInput``'s role: every field is taken verbatim, no
   value is ever derived or inferred by this package.
3. ``ExperimentExecutionPersistenceResult`` -- the outcome of one
   ``persist_experiment_execution`` call.
4. ``ExperimentResultPersistenceResult`` -- the outcome of one
   ``record_experiment_result`` call.

Both persistence-result types reuse ``app.persistence.types
.PersistenceAction`` directly (``CREATED``/``REUSED_EXISTING``/
``REQUIRES_REVIEW``/``FAILED``), the same convention
``app.persistence.experiment_recommendation_types
.ExperimentRecommendationPersistenceResult`` already established, rather
than inventing a parallel action vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.persistence.types import PersistenceAction

# Small, local, non-shared validation helpers -- the same "no cross-layer
# import of another package's private validation module" discipline
# ``app.review.validation``'s own docstring documents for itself.
# ``app.persistence`` never imports from ``app.review`` (a lower-layer
# package must not depend on a sibling/upper one), so these are not reused
# from there even though they are functionally identical.


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected str or None, got {value!r}")
    stripped = value.strip()
    return stripped or None


def _require_timezone_aware(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {value!r}")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime: {value!r}")
    return value


class ResultType(StrEnum):
    """The closed vocabulary ``ExperimentResult.result_type`` is written from.

    Kept intentionally minimal (Increment 25 instructions, Step 13) --
    every member is a classification a caller can assign without any
    biological judgment call: whether a measurement is quantitative or
    qualitative, whether something was detected or not, or a generic assay
    outcome for anything else. No member here encodes "this supports/
    contradicts a claim" or any other interpretive category.
    """

    QUANTITATIVE_MEASUREMENT = "QUANTITATIVE_MEASUREMENT"
    QUALITATIVE_OBSERVATION = "QUALITATIVE_OBSERVATION"
    DETECTION = "DETECTION"
    NON_DETECTION = "NON_DETECTION"
    ASSAY_OUTCOME = "ASSAY_OUTCOME"


@dataclass(frozen=True, slots=True)
class ExperimentResultInput:
    """One caller-specified observation. See module docstring.

    ``result_identifier`` is required and non-blank -- together with the
    execution's own identity and this input's ``result_type``/
    ``measurement_name``/``sample_identifier``/``replicate_identifier``/
    ``time_point``, it is one of the ingredients
    ``app.persistence.experiment_execution.compute_result_identity`` hashes
    into ``result_identity``. It exists specifically so that two otherwise
    identical repeats (same measurement, same sample, same replicate, same
    time point) can still be recorded as distinct rows when the caller
    explicitly says they are distinct (Increment 25 instructions, Step 19).

    At least one of ``value_text``/``value_numeric`` must be supplied -- a
    result recording nothing would carry no information at all. A
    ``QUANTITATIVE_MEASUREMENT`` additionally requires ``value_numeric`` --
    a quantitative result without a number is a contradiction in terms.
    Neither requirement is scientific interpretation; both are structural
    completeness checks, the same kind ``app.experiment_recommendation
    .types.ExperimentRecommendation`` already applies to its own
    ``experiment_class``/``status`` pairing.

    ``value_numeric`` must be a ``Decimal`` when supplied, never a
    ``float`` -- the same precision discipline
    ``app.models.kinetic_measurement.KineticMeasurement.parameter_value``
    already enforces at the schema level for exactly the same reason
    (floats introduce silent representation error a scientific measurement
    must not carry).
    """

    result_identifier: str
    result_type: ResultType
    measurement_name: str
    value_text: str | None = None
    value_numeric: Decimal | None = None
    unit: str | None = None
    uncertainty_text: str | None = None
    statistical_support: str | None = None
    sample_identifier: str | None = None
    replicate_identifier: str | None = None
    time_point: str | None = None
    condition_label: str | None = None
    instrument_reference: str | None = None
    raw_data_reference: str | None = None
    observed_at: datetime | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "result_identifier",
            _require_non_empty_str(self.result_identifier, field_name="result_identifier"),
        )
        if not isinstance(self.result_type, ResultType):
            raise TypeError(
                f"ExperimentResultInput.result_type must be a ResultType, got {self.result_type!r}"
            )
        object.__setattr__(
            self,
            "measurement_name",
            _require_non_empty_str(self.measurement_name, field_name="measurement_name"),
        )
        object.__setattr__(self, "value_text", _clean_optional(self.value_text))
        if self.value_numeric is not None and not isinstance(self.value_numeric, Decimal):
            raise TypeError(
                "ExperimentResultInput.value_numeric must be a Decimal or None, "
                f"got {self.value_numeric!r}"
            )
        object.__setattr__(self, "unit", _clean_optional(self.unit))
        object.__setattr__(self, "uncertainty_text", _clean_optional(self.uncertainty_text))
        object.__setattr__(
            self, "statistical_support", _clean_optional(self.statistical_support)
        )
        object.__setattr__(
            self, "sample_identifier", _clean_optional(self.sample_identifier)
        )
        object.__setattr__(
            self, "replicate_identifier", _clean_optional(self.replicate_identifier)
        )
        object.__setattr__(self, "time_point", _clean_optional(self.time_point))
        object.__setattr__(self, "condition_label", _clean_optional(self.condition_label))
        object.__setattr__(
            self, "instrument_reference", _clean_optional(self.instrument_reference)
        )
        object.__setattr__(
            self, "raw_data_reference", _clean_optional(self.raw_data_reference)
        )
        if self.observed_at is not None:
            object.__setattr__(
                self,
                "observed_at",
                _require_timezone_aware(self.observed_at, field_name="observed_at"),
            )
        object.__setattr__(self, "notes", _clean_optional(self.notes))

        if self.value_text is None and self.value_numeric is None:
            raise ValueError(
                "ExperimentResultInput requires at least one of value_text/value_numeric"
            )
        if self.result_type is ResultType.QUANTITATIVE_MEASUREMENT and self.value_numeric is None:
            raise ValueError(
                "ExperimentResultInput requires value_numeric when result_type is "
                "QUANTITATIVE_MEASUREMENT"
            )


@dataclass(frozen=True, slots=True)
class ExperimentExecutionPersistenceResult:
    """The outcome of one ``persist_experiment_execution`` call.

    * ``CREATED`` -- a new ``ExperimentExecution`` row was inserted.
      ``execution_id`` is its id; ``created=True``.
    * ``REUSED_EXISTING`` -- a row with the identical ``execution_identity``
      already existed. ``execution_id`` is the existing row's id;
      ``reused_existing=True``. Its content is never overwritten, and --
      unlike ``ExperimentRecommendationPersistenceResult`` -- no
      content-equality check is performed (see
      ``app.models.experiment_execution.ExperimentExecution``'s own
      docstring for why: descriptive fields here are free-form caller
      input, not a deterministic function of the identity ingredients).
    * ``REQUIRES_REVIEW`` -- no row exists yet for this identity, and the
      linked ``ExperimentRecommendationRecord``'s ``lifecycle_status`` is
      not ``ACCEPTED`` -- no row is created. ``execution_id`` is ``None``;
      ``review_required=True``.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could
      be found on the post-failure recheck. ``execution_id`` is ``None``.
    """

    action: PersistenceAction
    execution_id: UUID | None
    execution_identity: str
    created: bool
    reused_existing: bool
    review_required: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.REQUIRES_REVIEW,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"ExperimentExecutionPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.execution_identity, str) or not self.execution_identity.strip():
            raise ValueError(
                "ExperimentExecutionPersistenceResult.execution_identity must be a non-empty "
                f"string, got {self.execution_identity!r}"
            )
        if self.action is PersistenceAction.CREATED:
            if self.execution_id is None:
                raise ValueError("CREATED requires execution_id")
            if not self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "CREATED requires created=True, reused_existing=False, review_required=False"
                )
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.execution_id is None:
                raise ValueError("REUSED_EXISTING requires execution_id")
            if self.created or not self.reused_existing or self.review_required:
                raise ValueError(
                    "REUSED_EXISTING requires created=False, reused_existing=True, "
                    "review_required=False"
                )
        elif self.action is PersistenceAction.REQUIRES_REVIEW:
            if self.execution_id is not None:
                raise ValueError("REQUIRES_REVIEW must not carry execution_id")
            if self.created or self.reused_existing or not self.review_required:
                raise ValueError(
                    "REQUIRES_REVIEW requires created=False, reused_existing=False, "
                    "review_required=True"
                )
        else:  # FAILED
            if self.execution_id is not None:
                raise ValueError("FAILED must not carry execution_id")
            if self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "FAILED requires created=False, reused_existing=False, review_required=False"
                )


@dataclass(frozen=True, slots=True)
class ExperimentResultPersistenceResult:
    """The outcome of one ``record_experiment_result`` call.

    * ``CREATED`` -- a new ``ExperimentResult`` row was inserted.
    * ``REUSED_EXISTING`` -- a row with the identical ``result_identity`` and
      matching content already existed. Content is never overwritten.
    * ``REQUIRES_REVIEW`` -- no row exists yet for this identity, and the
      linked ``ExperimentExecution``'s ``status`` does not permit recording
      results right now (``PLANNED``/``CANCELLED``). ``result_id`` is
      ``None``; ``review_required=True``.
    * ``FAILED`` -- an ``IntegrityError`` occurred and no matching row could
      be found on the post-failure recheck.
    """

    action: PersistenceAction
    result_id: UUID | None
    result_identity: str
    created: bool
    reused_existing: bool
    review_required: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = (
            PersistenceAction.CREATED,
            PersistenceAction.REUSED_EXISTING,
            PersistenceAction.REQUIRES_REVIEW,
            PersistenceAction.FAILED,
        )
        if self.action not in valid_actions:
            raise ValueError(
                f"ExperimentResultPersistenceResult.action must be one of {valid_actions}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.result_identity, str) or not self.result_identity.strip():
            raise ValueError(
                "ExperimentResultPersistenceResult.result_identity must be a non-empty "
                f"string, got {self.result_identity!r}"
            )
        if self.action is PersistenceAction.CREATED:
            if self.result_id is None:
                raise ValueError("CREATED requires result_id")
            if not self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "CREATED requires created=True, reused_existing=False, review_required=False"
                )
        elif self.action is PersistenceAction.REUSED_EXISTING:
            if self.result_id is None:
                raise ValueError("REUSED_EXISTING requires result_id")
            if self.created or not self.reused_existing or self.review_required:
                raise ValueError(
                    "REUSED_EXISTING requires created=False, reused_existing=True, "
                    "review_required=False"
                )
        elif self.action is PersistenceAction.REQUIRES_REVIEW:
            if self.result_id is not None:
                raise ValueError("REQUIRES_REVIEW must not carry result_id")
            if self.created or self.reused_existing or not self.review_required:
                raise ValueError(
                    "REQUIRES_REVIEW requires created=False, reused_existing=False, "
                    "review_required=True"
                )
        else:  # FAILED
            if self.result_id is not None:
                raise ValueError("FAILED must not carry result_id")
            if self.created or self.reused_existing or self.review_required:
                raise ValueError(
                    "FAILED requires created=False, reused_existing=False, review_required=False"
                )


__all__ = [
    "ExperimentExecutionPersistenceResult",
    "ExperimentResultInput",
    "ExperimentResultPersistenceResult",
    "ResultType",
]
