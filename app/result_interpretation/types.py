"""The Result Interpretation data contract (Increment 26).

Three types:

1. ``InterpretationStatus`` -- the closed classification this package
   assigns to every ``EvidenceCandidate`` it produces, describing *what
   kind of observation this is*, never what it means biologically.
2. ``ExperimentInterpretationContext`` -- an optional, entirely
   caller-supplied bundle of already-known subject/predicate/object text.
   This package never infers or invents these three fields itself
   (Increment 26 instructions, Step 13) -- nothing upstream of this
   package (verified directly: ``ExperimentRecommendationRecord`` has no
   subject/predicate/object text fields, only a polymorphic
   ``target_entity_type``/``target_entity_id`` pair and free-text
   ``objective``/``rationale``) currently produces this text, so in
   practice every ``EvidenceCandidate`` this package builds today leaves
   ``candidate_subject_text``/``candidate_predicate_text``/
   ``candidate_object_text`` as ``None`` unless a caller explicitly
   constructs and supplies a context -- an architecture gap disclosed here
   and in this increment's completion report, the same "report the
   absence, do not approximate it" precedent
   ``app.persistence.claim_types.ExperimentalConditionInput``'s own
   docstring already established for an analogous gap.
3. ``EvidenceCandidate`` -- the final, immutable output of this package:
   one candidate evidence record, source-grounded back to the
   ``ExperimentExecution``/``ExperimentResult`` it was generated from,
   structurally compatible with (but never merged into, and never
   converted into) ``app.models.claim.Evidence``'s own column shape --
   ``quoted_support``/``curator_summary``/``evidence_type``/``directness``
   map name-for-name onto that table's columns (verified directly against
   ``app/models/claim.py`` before writing this module), so a later,
   separate increment can persist one without renaming or reinterpreting
   any field. This package never constructs an ``Evidence`` row itself.

All three are frozen dataclasses that validate themselves in
``__post_init__``, the same self-validating-at-construction pattern every
other data-contract module in this repository already uses. Nothing here
performs I/O, calls an LLM, reads a database, or writes one.

**Schema provenance, verified directly before writing this module, not
assumed:**

* ``EvidenceType`` is reused verbatim from ``app.models.enums`` (Increment
  26 instructions, Step 11: "Do not invent another evidence vocabulary.").
* ``Directness`` is reused verbatim from ``app.extraction.types`` -- the
  same local ``StrEnum`` ``Evidence.directness`` (a plain ``VARCHAR``
  column) is already written from elsewhere in this pipeline. This
  package only ever assigns ``Directness.AUTHORS_OBSERVED`` (Increment 26
  instructions, Step 12) -- these are laboratory observations, never an
  inferred or proposed interpretation.
* ``result_type`` is kept as a plain ``str`` (not
  ``app.persistence.experiment_execution_types.ResultType``) specifically
  so an unrecognized value can still be carried through verbatim for audit
  purposes when ``interpretation_status`` is ``UNSUPPORTED_RESULT_TYPE``
  (see that status's own docstring) -- forcing it to a closed enum would
  make that exact case impossible to represent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.extraction.types import Directness
from app.models.enums import EvidenceType
from app.result_interpretation.validation import (
    clean_optional,
    require_non_empty_str,
    require_timezone_aware,
)


class InterpretationStatus(StrEnum):
    """What kind of observation this candidate represents. Never a biological conclusion.

    * ``DIRECT_OBSERVATION`` -- a quantitative measurement or a positive
      detection was directly recorded.
    * ``NON_DETECTION`` -- the recorded result was an explicit negative/
      non-detection observation. Never rewritten into "absence," "negative
      biology," or "failure" (Increment 26 instructions, Step 9).
    * ``QUALITATIVE_OBSERVATION`` -- a qualitative, non-numeric observation
      (or a generic assay outcome) was recorded.
    * ``INSUFFICIENT_INFORMATION`` -- the recorded result carries neither a
      usable numeric value nor descriptive text for its category (should
      not occur for a result that itself satisfied
      ``ExperimentResultInput``'s own construction invariants, but this
      package does not assume that invariant holds for every
      ``ExperimentResult`` row it is ever handed).
    * ``UNSUPPORTED_RESULT_TYPE`` -- ``ExperimentResult.result_type`` is
      not one of the ``ResultType`` values this package currently knows
      how to interpret. A candidate is still produced (never silently
      dropped), flagged for human attention.
    """

    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    NON_DETECTION = "NON_DETECTION"
    QUALITATIVE_OBSERVATION = "QUALITATIVE_OBSERVATION"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    UNSUPPORTED_RESULT_TYPE = "UNSUPPORTED_RESULT_TYPE"


@dataclass(frozen=True, slots=True)
class ExperimentInterpretationContext:
    """Already-known subject/predicate/object text, supplied entirely by the caller.

    Every field defaults to ``None`` -- the conservative default is
    "populate nothing," never a guess. See module docstring for why
    nothing in this repository currently produces non-``None`` values for
    this type.
    """

    candidate_subject_text: str | None = None
    candidate_predicate_text: str | None = None
    candidate_object_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_subject_text", clean_optional(self.candidate_subject_text)
        )
        object.__setattr__(
            self, "candidate_predicate_text", clean_optional(self.candidate_predicate_text)
        )
        object.__setattr__(
            self, "candidate_object_text", clean_optional(self.candidate_object_text)
        )


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """One candidate evidence record, source-grounded to one experiment result.

    ``experiment_execution_id``/``experiment_result_id`` are provenance
    only -- database row identity, not a normalized entity reference of any
    kind. ``value_text``/``value_numeric``/``unit``/``statistical_support``/
    ``sample_identifier``/``replicate_identifier``/``time_point``/
    ``condition_label``/``raw_data_reference``/``observed_at``/``notes`` are
    all copied verbatim from the originating ``ExperimentResult`` --
    Increment 26 instructions, Step 8: "measurement preserved exactly,
    value preserved exactly." At least one of ``value_text``/
    ``value_numeric`` must be present -- a candidate recording nothing
    would carry no information at all, the same structural completeness
    check ``app.persistence.experiment_execution_types
    .ExperimentResultInput`` already applies to its own source data.
    """

    experiment_execution_id: UUID
    experiment_result_id: UUID

    result_type: str
    measurement_name: str

    value_text: str | None
    value_numeric: Decimal | None
    unit: str | None

    statistical_support: str | None

    sample_identifier: str | None
    replicate_identifier: str | None
    time_point: str | None

    condition_label: str | None

    raw_data_reference: str | None

    observed_at: datetime | None

    notes: str | None

    candidate_subject_text: str | None
    candidate_predicate_text: str | None
    candidate_object_text: str | None

    evidence_type: EvidenceType
    directness: Directness

    quoted_support: str
    curator_summary: str

    interpretation_status: InterpretationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_execution_id, UUID):
            raise TypeError(
                "EvidenceCandidate.experiment_execution_id must be a UUID, "
                f"got {self.experiment_execution_id!r}"
            )
        if not isinstance(self.experiment_result_id, UUID):
            raise TypeError(
                "EvidenceCandidate.experiment_result_id must be a UUID, "
                f"got {self.experiment_result_id!r}"
            )

        object.__setattr__(
            self, "result_type", require_non_empty_str(self.result_type, field_name="result_type")
        )
        object.__setattr__(
            self,
            "measurement_name",
            require_non_empty_str(self.measurement_name, field_name="measurement_name"),
        )

        object.__setattr__(self, "value_text", clean_optional(self.value_text))
        if self.value_numeric is not None and not isinstance(self.value_numeric, Decimal):
            raise TypeError(
                f"EvidenceCandidate.value_numeric must be a Decimal or None, "
                f"got {self.value_numeric!r}"
            )
        object.__setattr__(self, "unit", clean_optional(self.unit))
        object.__setattr__(
            self, "statistical_support", clean_optional(self.statistical_support)
        )
        object.__setattr__(
            self, "sample_identifier", clean_optional(self.sample_identifier)
        )
        object.__setattr__(
            self, "replicate_identifier", clean_optional(self.replicate_identifier)
        )
        object.__setattr__(self, "time_point", clean_optional(self.time_point))
        object.__setattr__(self, "condition_label", clean_optional(self.condition_label))
        object.__setattr__(
            self, "raw_data_reference", clean_optional(self.raw_data_reference)
        )
        object.__setattr__(self, "notes", clean_optional(self.notes))

        if self.observed_at is not None:
            object.__setattr__(
                self,
                "observed_at",
                require_timezone_aware(self.observed_at, field_name="observed_at"),
            )

        object.__setattr__(
            self, "candidate_subject_text", clean_optional(self.candidate_subject_text)
        )
        object.__setattr__(
            self, "candidate_predicate_text", clean_optional(self.candidate_predicate_text)
        )
        object.__setattr__(
            self, "candidate_object_text", clean_optional(self.candidate_object_text)
        )

        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"EvidenceCandidate.evidence_type must be an EvidenceType, "
                f"got {self.evidence_type!r}"
            )
        if not isinstance(self.directness, Directness):
            raise TypeError(
                f"EvidenceCandidate.directness must be a Directness, got {self.directness!r}"
            )
        if not isinstance(self.interpretation_status, InterpretationStatus):
            raise TypeError(
                "EvidenceCandidate.interpretation_status must be an InterpretationStatus, "
                f"got {self.interpretation_status!r}"
            )

        object.__setattr__(
            self,
            "quoted_support",
            require_non_empty_str(self.quoted_support, field_name="quoted_support"),
        )
        object.__setattr__(
            self,
            "curator_summary",
            require_non_empty_str(self.curator_summary, field_name="curator_summary"),
        )

        if self.value_text is None and self.value_numeric is None:
            raise ValueError(
                "EvidenceCandidate requires at least one of value_text/value_numeric"
            )


__all__ = [
    "EvidenceCandidate",
    "ExperimentInterpretationContext",
    "InterpretationStatus",
]
