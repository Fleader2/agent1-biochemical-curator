"""The Single-Evidence Assessment data contract.

One type: ``SingleEvidenceAssessment`` -- the final, immutable output of
this package. A deterministic, auditable, purely **categorical**
assessment of one ``CandidateClaim``'s evidence strength and identity-
resolution state, scored against exactly one supporting
``EvidenceExtraction``.

**This is not a final Claim confidence.** It carries no 0-100 final score
and no ``ConfidenceClass`` -- both are aggregate-claim concepts that
require information from more than one piece of evidence (a replication
bonus, a conflict penalty) which this package deliberately never computes.
See ``docs/13_single_evidence_confidence_contract.md`` for the full
rationale and the relationship to a future Increment 18 aggregator.

Never stored in the database (Increment 17 performs no persistence of any
kind) and never mutated after construction. Self-validating in
``__post_init__`` via ``app.confidence.validation``, the same pattern
every other data-contract module in this repository already uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.confidence.policy import EntityResolutionQuality, OrganismAssessment, ScoringStatus
from app.confidence.validation import (
    require_non_blank_string_tuple,
    validate_evidence_score_range,
    validate_scoring_status_consistency,
)
from app.extraction.types import Directness
from app.models.enums import EvidenceType


@dataclass(frozen=True, slots=True)
class SingleEvidenceAssessment:
    """The complete, auditable, single-evidence assessment for one ``CandidateClaim``.

    ``evidence_type``/``evidence_base_score``/``scoring_status`` describe
    the evidence-strength dimension: ``evidence_base_score`` is the
    authoritative ``docs/03_agent_behavior.md`` base score for
    ``evidence_type`` when one exists, else ``None`` (never a fabricated
    fallback) -- ``scoring_status`` reports which case applies.

    ``directness`` is carried through categorically, unchanged -- no
    numeric multiplier is computed from it in this package (no
    authoritative source specifies one; see
    ``docs/13_single_evidence_confidence_contract.md`` §7).

    ``subject_resolution``/``object_resolution``/``organism_resolution``/
    ``compartment_resolution`` are each a categorical identity-resolution
    state, never a numeric cap or penalty. ``subject_resolution`` is
    always populated (``CandidateClaim.subject`` is never ``None``).
    ``object_resolution``/``compartment_resolution`` are ``None`` when the
    claim has no object/compartment reference at all (a literal-value
    claim's missing object, for instance) -- distinct from
    ``EntityResolutionQuality.NOT_APPLICABLE``, which means a reference
    *exists* but was never typed as an entity. ``organism_resolution`` uses
    the separate, coarser ``OrganismAssessment`` vocabulary and is never
    ``None`` (``OrganismAssessment.NOT_STATED`` represents "no organism at
    all").

    ``reason_codes`` is the complete, deterministic, controlled audit
    trail (never prose-only); ``explanation`` is a human-readable
    rendering of the same information, generated deterministically, never
    by an LLM, and never claiming to state a "final confidence".
    """

    evidence_type: EvidenceType
    evidence_base_score: int | None
    scoring_status: ScoringStatus

    directness: Directness

    subject_resolution: EntityResolutionQuality
    object_resolution: EntityResolutionQuality | None
    organism_resolution: OrganismAssessment
    compartment_resolution: EntityResolutionQuality | None

    reason_codes: tuple[str, ...] = ()
    explanation: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"SingleEvidenceAssessment.evidence_type must be an EvidenceType, "
                f"got {self.evidence_type!r}"
            )
        if not isinstance(self.scoring_status, ScoringStatus):
            raise TypeError(
                "SingleEvidenceAssessment.scoring_status must be a ScoringStatus, "
                f"got {self.scoring_status!r}"
            )
        if self.evidence_base_score is not None:
            validate_evidence_score_range(
                self.evidence_base_score, field_name="evidence_base_score"
            )
        validate_scoring_status_consistency(
            evidence_base_score=self.evidence_base_score, scoring_status=self.scoring_status
        )

        if not isinstance(self.directness, Directness):
            raise TypeError(
                f"SingleEvidenceAssessment.directness must be a Directness, got {self.directness!r}"
            )

        if not isinstance(self.subject_resolution, EntityResolutionQuality):
            raise TypeError(
                "SingleEvidenceAssessment.subject_resolution must be an EntityResolutionQuality, "
                f"got {self.subject_resolution!r}"
            )
        for field_name in ("object_resolution", "compartment_resolution"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, EntityResolutionQuality):
                raise TypeError(
                    f"SingleEvidenceAssessment.{field_name} must be an EntityResolutionQuality "
                    f"or None, got {value!r}"
                )
        if not isinstance(self.organism_resolution, OrganismAssessment):
            raise TypeError(
                "SingleEvidenceAssessment.organism_resolution must be an OrganismAssessment, "
                f"got {self.organism_resolution!r}"
            )

        object.__setattr__(
            self,
            "reason_codes",
            require_non_blank_string_tuple(self.reason_codes, field_name="reason_codes"),
        )


__all__ = ["SingleEvidenceAssessment"]
