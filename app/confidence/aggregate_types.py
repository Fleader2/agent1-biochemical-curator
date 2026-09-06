"""The Multi-Evidence Aggregation data contract.

Three types, in the order data flows through this package:

1. ``EvidenceContribution`` -- one evidence item's contribution to one
   logical canonical claim: a ``CandidateClaim`` + its own
   ``SingleEvidenceAssessment`` (Increment 17), plus the provenance/
   independence/conflict metadata aggregation needs that a single
   evidence item's own assessment does not carry (publication identity,
   independence grouping, explicit conflict severity). Produced by a
   caller (a coordinator, or ``app.confidence.aggregation
   .build_evidence_contribution``), never invented from nothing.
2. ``ContributionBreakdown`` -- the immutable, auditable record of what
   ``app.confidence.aggregation.aggregate_claim_confidence`` actually did
   with one ``EvidenceContribution`` -- never deleted, even when the
   contribution was numerically ineligible or an exact duplicate.
3. ``AggregateClaimConfidence`` -- the final, immutable output: this
   package's answer for one logical claim's evidence set, including the
   final 0-100 score/``ConfidenceClass`` and an ``algorithm_version``
   identifying which canonical aggregation-mathematics formula produced
   it (Increment 18A resolved Increment 18's own disclosed gap in how
   multiple *differing* evidence base values combine -- see
   ``app.confidence.aggregation``'s module docstring for the full
   diminishing-return-weighting algorithm).

All three are frozen dataclasses that validate themselves in
``__post_init__``, the same self-validating-at-construction pattern every
other data-contract module in this repository already uses. Never stored
in the database (this package performs no persistence of any kind).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.claim_generation.types import CandidateClaim
from app.confidence.aggregate_policy import (
    ALGORITHM_VERSION,
    ConflictSeverity,
    ExperimentalRelevance,
    OrganismRelevance,
)
from app.confidence.aggregate_validation import (
    require_non_blank_string_tuple,
    require_non_empty_str,
    validate_aggregate_score_and_class_consistency,
    validate_non_negative_decimal,
    validate_non_negative_int,
    validate_score_range,
    validate_weight_range,
)
from app.confidence.types import SingleEvidenceAssessment
from app.models.enums import ConfidenceClass, EvidenceType


@dataclass(frozen=True, slots=True)
class EvidenceContribution:
    """One evidence item's contribution to one logical canonical claim.

    ``single_evidence_assessment`` must describe the exact same
    ``candidate_claim`` (``evidence_type``/``directness`` are cross-checked
    for agreement in ``__post_init__``) -- this type never lets a caller
    attach an assessment computed for a different claim.

    ``publication_identifier``/``source_identifier`` are the caller's best
    available stable identifiers (a resolved ``Publication`` UUID string,
    a PMID/PMCID/DOI, or -- absent anything stronger -- the originating
    ``EvidenceExtraction.source_identifier``) -- never a bare title
    (Increment 18 instructions, Step 11).

    ``experiment_identifier`` is ``None`` unless the caller has genuine,
    deterministic knowledge that this contribution represents a distinct
    experiment within its own publication (Step 12) -- absent that, same-
    publication contributions default to one shared independence group
    (see ``app.confidence.aggregation``'s effective-independence-group
    derivation).

    ``organism_relevance``/``experimental_relevance`` default to
    ``UNKNOWN`` -- a caller may supply a better-informed value, but
    ``app.confidence.aggregation.build_evidence_contribution`` is the
    recommended way to construct one, since it derives these
    deterministically from ``candidate_claim``/``single_evidence_assessment``
    rather than requiring the caller to know the exact policy.

    ``conflict_status`` defaults to ``ConflictSeverity.NONE`` -- this type
    never discovers a conflict itself; it only carries whatever an
    external conflict-detection layer (not built in this increment)
    already determined (Step 15).
    """

    candidate_claim: CandidateClaim
    single_evidence_assessment: SingleEvidenceAssessment
    publication_identifier: str
    source_identifier: str

    experiment_identifier: str | None = None
    organism_relevance: OrganismRelevance = OrganismRelevance.UNKNOWN
    experimental_relevance: ExperimentalRelevance = ExperimentalRelevance.UNKNOWN
    independence_group: str | None = None
    conflict_status: ConflictSeverity = ConflictSeverity.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_claim, CandidateClaim):
            raise TypeError(
                "EvidenceContribution.candidate_claim must be a CandidateClaim, "
                f"got {self.candidate_claim!r}"
            )
        if not isinstance(self.single_evidence_assessment, SingleEvidenceAssessment):
            raise TypeError(
                "EvidenceContribution.single_evidence_assessment must be a "
                f"SingleEvidenceAssessment, got {self.single_evidence_assessment!r}"
            )
        if self.single_evidence_assessment.evidence_type is not self.candidate_claim.evidence_type:
            raise ValueError(
                "EvidenceContribution.single_evidence_assessment.evidence_type must equal "
                "candidate_claim.evidence_type -- the assessment must describe this same claim"
            )
        if self.single_evidence_assessment.directness is not self.candidate_claim.directness:
            raise ValueError(
                "EvidenceContribution.single_evidence_assessment.directness must equal "
                "candidate_claim.directness -- the assessment must describe this same claim"
            )

        object.__setattr__(
            self,
            "publication_identifier",
            require_non_empty_str(self.publication_identifier, field_name="publication_identifier"),
        )
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty_str(self.source_identifier, field_name="source_identifier"),
        )
        if self.experiment_identifier is not None:
            object.__setattr__(
                self,
                "experiment_identifier",
                require_non_empty_str(
                    self.experiment_identifier, field_name="experiment_identifier"
                ),
            )
        if self.independence_group is not None:
            object.__setattr__(
                self,
                "independence_group",
                require_non_empty_str(self.independence_group, field_name="independence_group"),
            )

        if not isinstance(self.organism_relevance, OrganismRelevance):
            raise TypeError(
                "EvidenceContribution.organism_relevance must be an OrganismRelevance, "
                f"got {self.organism_relevance!r}"
            )
        if not isinstance(self.experimental_relevance, ExperimentalRelevance):
            raise TypeError(
                "EvidenceContribution.experimental_relevance must be an ExperimentalRelevance, "
                f"got {self.experimental_relevance!r}"
            )
        if not isinstance(self.conflict_status, ConflictSeverity):
            raise TypeError(
                f"EvidenceContribution.conflict_status must be a ConflictSeverity, "
                f"got {self.conflict_status!r}"
            )


@dataclass(frozen=True, slots=True)
class ContributionBreakdown:
    """The immutable, auditable record of what aggregation did with one ``EvidenceContribution``.

    Never omitted from ``AggregateClaimConfidence.contribution_breakdown``,
    including when ``is_duplicate`` or ``numerically_eligible`` is
    ``False`` -- ineligible/duplicate evidence is never deleted, only
    excluded from numeric aggregation (Increment 18 instructions, Step 18).

    **Increment 18A addition -- the diminishing-return weighting steps,
    exposed per contribution so every arithmetic step is inspectable**
    (see ``app.confidence.aggregation``'s module docstring for the full
    algorithm):

    * ``adjusted_contribution`` -- ``evidence_base_score x organism_relevance
      x experimental_relevance``, as an exact ``Decimal`` (changed from a
      floor-divided ``int`` in Increment 18 -- the diminishing-return sum
      needs the unrounded value to avoid compounding rounding error across
      several contributions).
    * ``weight`` -- this contribution's diminishing-return weight
      (``0.5 ** (rank - 1)``, ``rank`` being this contribution's 1-based
      position when every numerically eligible, non-duplicate contribution
      is sorted by ``adjusted_contribution`` descending).
    * ``weighted_contribution`` -- ``adjusted_contribution x weight``.
    * ``cumulative_score`` -- the running sum of ``weighted_contribution``
      up to and including this contribution, in descending-rank order; the
      last (weakest-ranked) eligible contribution's ``cumulative_score``
      equals the aggregate evidence value before replication/conflict.

    All four are ``None`` together, exactly when ``numerically_eligible``
    is ``False`` -- an ineligible or duplicate contribution has no numeric
    contribution to report.
    """

    source_identifier: str
    publication_identifier: str
    evidence_type: EvidenceType
    evidence_base_score: int | None

    organism_relevance: OrganismRelevance
    experimental_relevance: ExperimentalRelevance

    is_duplicate: bool
    numerically_eligible: bool
    ineligibility_reasons: tuple[str, ...]

    independence_group: str
    counted_for_replication: bool

    conflict_status: ConflictSeverity

    adjusted_contribution: Decimal | None
    weight: Decimal | None = None
    weighted_contribution: Decimal | None = None
    cumulative_score: Decimal | None = None

    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty_str(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(
            self,
            "publication_identifier",
            require_non_empty_str(self.publication_identifier, field_name="publication_identifier"),
        )
        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"ContributionBreakdown.evidence_type must be an EvidenceType, "
                f"got {self.evidence_type!r}"
            )
        if self.evidence_base_score is not None:
            validate_score_range(self.evidence_base_score, field_name="evidence_base_score")
        if not isinstance(self.organism_relevance, OrganismRelevance):
            raise TypeError(
                f"ContributionBreakdown.organism_relevance must be an OrganismRelevance, "
                f"got {self.organism_relevance!r}"
            )
        if not isinstance(self.experimental_relevance, ExperimentalRelevance):
            raise TypeError(
                "ContributionBreakdown.experimental_relevance must be an ExperimentalRelevance, "
                f"got {self.experimental_relevance!r}"
            )
        if (not self.numerically_eligible) != bool(self.ineligibility_reasons):
            raise ValueError(
                "ContributionBreakdown.ineligibility_reasons must be non-empty if and only if "
                "numerically_eligible is False"
            )
        object.__setattr__(
            self,
            "ineligibility_reasons",
            require_non_blank_string_tuple(
                self.ineligibility_reasons, field_name="ineligibility_reasons"
            ),
        )
        object.__setattr__(
            self,
            "independence_group",
            require_non_empty_str(self.independence_group, field_name="independence_group"),
        )
        if not isinstance(self.conflict_status, ConflictSeverity):
            raise TypeError(
                f"ContributionBreakdown.conflict_status must be a ConflictSeverity, "
                f"got {self.conflict_status!r}"
            )

        weighting_fields = (
            "adjusted_contribution",
            "weight",
            "weighted_contribution",
            "cumulative_score",
        )
        populated = {name: getattr(self, name) is not None for name in weighting_fields}
        if any(populated.values()) != all(populated.values()):
            raise ValueError(
                "ContributionBreakdown's adjusted_contribution/weight/weighted_contribution/"
                "cumulative_score must be either all None or all populated together"
            )
        if populated["adjusted_contribution"] and not self.numerically_eligible:
            raise ValueError(
                "ContributionBreakdown's weighting fields must be None when "
                "numerically_eligible is False -- an ineligible contribution has no numeric "
                "contribution to report"
            )
        if self.adjusted_contribution is not None:
            validate_non_negative_decimal(
                self.adjusted_contribution, field_name="adjusted_contribution"
            )
        if self.weight is not None:
            validate_weight_range(self.weight, field_name="weight")
        if self.weighted_contribution is not None:
            validate_non_negative_decimal(
                self.weighted_contribution, field_name="weighted_contribution"
            )
        if self.cumulative_score is not None:
            validate_non_negative_decimal(self.cumulative_score, field_name="cumulative_score")

        object.__setattr__(
            self,
            "reason_codes",
            require_non_blank_string_tuple(self.reason_codes, field_name="reason_codes"),
        )


@dataclass(frozen=True, slots=True)
class AggregateClaimConfidence:
    """The final, auditable multi-evidence confidence result for one logical claim.

    **Increment 18A**: ``score``/``confidence_class`` are now ``None``/
    ``UNKNOWN`` in exactly one case -- no numerically eligible evidence
    exists at all. Increment 18's own blocking case (numerically eligible
    contributions with genuinely differing adjusted base values) is
    resolved by the canonical diminishing-return weighting algorithm (see
    ``app.confidence.aggregation``'s module docstring) -- heterogeneous
    evidence no longer produces ``UNKNOWN`` merely for being heterogeneous.

    ``base_evidence_total`` is the rounded (round-half-up) diminishing-
    return-weighted sum of every numerically eligible, non-duplicate
    contribution's own adjusted score, *before* replication bonus/conflict
    penalty (``None`` only when no eligible evidence exists).
    ``replication_bonus``/``conflict_penalty`` are always populated
    integers (0 when not applicable) -- both are independently well-defined
    regardless of whether ``score`` itself could be computed.

    ``algorithm_version`` identifies which aggregation-mathematics formula
    produced this result (``app.confidence.aggregate_policy
    .ALGORITHM_VERSION``, currently always ``"confidence-v1"``) -- a future
    revision of the weighting formula would use a new version string
    rather than silently redefining this one.

    ``evidence_count`` is the total number of contributions supplied
    (including duplicates). ``independent_evidence_count`` is the number
    of distinct, non-derivative independence groups among numerically
    eligible, non-duplicate contributions (the count that actually drove
    ``replication_bonus``). ``dependent_evidence_count`` is every other
    eligible, non-duplicate contribution (same independence group as
    another, or derivative-directness and therefore excluded from the
    replication count).
    """

    score: int | None
    confidence_class: ConfidenceClass

    base_evidence_total: int | None
    replication_bonus: int
    conflict_penalty: int

    organism_modifier_summary: tuple[str, ...]
    experimental_modifier_summary: tuple[str, ...]

    evidence_count: int
    independent_evidence_count: int
    dependent_evidence_count: int

    reason_codes: tuple[str, ...]
    contribution_breakdown: tuple[ContributionBreakdown, ...]
    explanation: str = ""
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.confidence_class, ConfidenceClass):
            raise TypeError(
                f"AggregateClaimConfidence.confidence_class must be a ConfidenceClass, "
                f"got {self.confidence_class!r}"
            )
        validate_aggregate_score_and_class_consistency(
            score=self.score, confidence_class=self.confidence_class
        )
        if self.base_evidence_total is not None:
            validate_score_range(self.base_evidence_total, field_name="base_evidence_total")
        if (self.base_evidence_total is None) != (self.score is None):
            raise ValueError(
                "AggregateClaimConfidence.score and base_evidence_total must both be None or "
                "both be populated -- a base value always yields a computable score, and a "
                "score is never computed without one"
            )

        validate_non_negative_int(self.replication_bonus, field_name="replication_bonus")
        if self.replication_bonus > 10:
            raise ValueError(
                f"AggregateClaimConfidence.replication_bonus must not exceed the authoritative "
                f"cap of 10, got {self.replication_bonus!r}"
            )
        validate_non_negative_int(self.conflict_penalty, field_name="conflict_penalty")

        validate_non_negative_int(self.evidence_count, field_name="evidence_count")
        validate_non_negative_int(
            self.independent_evidence_count, field_name="independent_evidence_count"
        )
        validate_non_negative_int(
            self.dependent_evidence_count, field_name="dependent_evidence_count"
        )
        if self.independent_evidence_count + self.dependent_evidence_count > self.evidence_count:
            raise ValueError(
                "AggregateClaimConfidence: independent_evidence_count + dependent_evidence_count "
                "must not exceed evidence_count"
            )

        object.__setattr__(
            self,
            "organism_modifier_summary",
            require_non_blank_string_tuple(
                self.organism_modifier_summary, field_name="organism_modifier_summary"
            ),
        )
        object.__setattr__(
            self,
            "experimental_modifier_summary",
            require_non_blank_string_tuple(
                self.experimental_modifier_summary, field_name="experimental_modifier_summary"
            ),
        )
        object.__setattr__(
            self,
            "reason_codes",
            require_non_blank_string_tuple(self.reason_codes, field_name="reason_codes"),
        )
        if not isinstance(self.contribution_breakdown, tuple) or not all(
            isinstance(item, ContributionBreakdown) for item in self.contribution_breakdown
        ):
            raise TypeError(
                "AggregateClaimConfidence.contribution_breakdown must be a tuple of "
                "ContributionBreakdown"
            )
        if len(self.contribution_breakdown) != self.evidence_count:
            raise ValueError(
                "AggregateClaimConfidence.contribution_breakdown must contain exactly "
                "evidence_count entries -- every supplied contribution must be represented"
            )
        object.__setattr__(
            self,
            "algorithm_version",
            require_non_empty_str(self.algorithm_version, field_name="algorithm_version"),
        )


__all__ = ["AggregateClaimConfidence", "ContributionBreakdown", "EvidenceContribution"]
