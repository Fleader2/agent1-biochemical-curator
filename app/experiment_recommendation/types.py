"""The Deterministic Experiment Recommendation data contract (Increment 23).

Four types:

1. ``ExperimentClass`` -- a controlled, local enum of only the high-level
   experiment classes this package's templates actually assign (§
   ``app.experiment_recommendation.templates`` for exactly which). Several
   classes the increment's own request listed as "possible" (
   ``KINETIC_MEASUREMENT``, ``GENETIC_PERTURBATION``,
   ``PROTEIN_INTERACTION_ASSAY``, ``METABOLITE_MEASUREMENT``,
   ``FLUX_MEASUREMENT``, ``EXPRESSION_MEASUREMENT``,
   ``ENTITY_ANNOTATION_REVIEW``) are deliberately **not** members here --
   no currently-implemented ``GapType`` policy can select any of them
   without inventing a claim/entity "topic" distinction the schema does
   not structurally support (see
   ``docs/19_experiment_recommendation_contract.md`` §6/§9 for the full
   per-class disclosure).
2. ``RecommendationStatus`` -- the smallest vocabulary that lets this layer
   say "this gap is real, but a specific experiment class cannot be safely
   recommended" (``REQUIRES_HUMAN_DESIGN``), "the structured context
   supplied was not enough to evaluate this gap at all"
   (``INSUFFICIENT_INFORMATION``, distinct from the former: a context
   problem, not a scientific-judgment one), "this is not an experimental
   gap" (``NOT_APPLICABLE``), or an actual recommendation (``RECOMMENDED``).
3. ``ExperimentRecommendation`` -- one immutable, in-memory recommendation.
   Never persisted by this package (Increment 23 writes nothing to
   ``KnowledgeGap.suggested_experiment`` or anywhere else) and never
   contains free-form hypothesis text, protocol steps, reagents, vendors,
   cost, or schedule.
4. ``ExperimentRecommendationContext`` -- the minimal, immutable bundle of
   already-loaded structured upstream data (``Claim``/``Evidence`` ORM rows,
   keyed by id) a template may read. No ``Session``, no query capability --
   see ``app.experiment_recommendation.recommender`` for the one place a
   ``Session`` is ever touched (building a context, never inside template
   logic itself).

All four are frozen and validate themselves in ``__post_init__``, the same
pattern every other data-contract module in this repository already uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from app.experiment_recommendation.validation import (
    clean_optional,
    require_non_blank_string_tuple,
    require_non_empty_str,
    require_uuid_tuple,
)
from app.knowledge_gaps.types import GapSeverity, GapType
from app.models.claim import Claim, Evidence


class ExperimentClass(StrEnum):
    """Only the high-level experiment classes this package's templates actually assign.

    See ``app.experiment_recommendation.templates`` for the exact template
    behind each member, and ``docs/19_experiment_recommendation_contract.md``
    §6 for the full taxonomy discussion, including every class considered
    and rejected.
    """

    REPLICATION_EXPERIMENT = "REPLICATION_EXPERIMENT"
    PROTEIN_LOCALIZATION_ASSAY = "PROTEIN_LOCALIZATION_ASSAY"
    EXPERIMENTAL_CONTEXT_CHARACTERIZATION = "EXPERIMENTAL_CONTEXT_CHARACTERIZATION"
    REACTION_VALIDATION = "REACTION_VALIDATION"
    ENZYME_SUBSTRATE_ASSAY = "ENZYME_SUBSTRATE_ASSAY"


class RecommendationStatus(StrEnum):
    """What this layer was able to determine about one knowledge gap.

    ``RECOMMENDED`` -- a specific ``ExperimentClass`` is deterministically
    justified; ``NOT_APPLICABLE`` -- this gap is not an experimental
    opportunity at all (a provenance or connectivity observation);
    ``REQUIRES_HUMAN_DESIGN`` -- this is a real experimental gap, but the
    available structured information does not deterministically justify one
    specific experiment class; ``INSUFFICIENT_INFORMATION`` -- the supplied
    ``ExperimentRecommendationContext`` did not contain the claim/evidence
    data this gap references, so no evaluation could be performed at all
    (a context-completeness problem, distinct from a scientific-judgment
    one).
    """

    RECOMMENDED = "RECOMMENDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    REQUIRES_HUMAN_DESIGN = "REQUIRES_HUMAN_DESIGN"


@dataclass(frozen=True, slots=True)
class ExperimentRecommendation:
    """One immutable, in-memory experiment recommendation for one knowledge gap.

    ``experiment_class`` is populated if and only if ``status`` is
    ``RECOMMENDED`` -- a structural guarantee, not merely documentation:
    ``REQUIRES_HUMAN_DESIGN``/``NOT_APPLICABLE``/``INSUFFICIENT_INFORMATION``
    never carry one, since by definition no specific class was
    deterministically justified. ``success_criterion`` follows the same
    rule (Increment 23 instructions, Step 27: "Every RECOMMENDED experiment
    should include a structured success criterion").

    ``objective``/``rationale`` are always populated regardless of
    ``status`` -- even a ``REQUIRES_HUMAN_DESIGN`` result states what needs
    resolving, it just does not commit to how. ``rationale`` is always the
    deterministic trace ``"{gap_type} -> {template_id} -> {status}"``
    (Increment 23 instructions, Step 24) -- never generated prose beyond
    formatting known fields.

    ``required_measurement``/``required_comparison`` are ``None`` unless a
    template's own deterministic logic populates them from structured
    context already present (never invented -- Steps 28/29).
    ``experimental_context_requirements`` names context *fields* that
    should be explicitly controlled/reported (e.g. ``"temperature"``),
    never invented *values*.

    Deliberately excluded (Increment 23 instructions, Step 6): free-form
    hypothesis text, protocol steps, reagent catalog numbers, vendor names,
    cost, schedule, and any confidence score not already a direct field
    inherited from the gap itself (``gap_severity``).
    """

    knowledge_gap_identity: str
    gap_type: GapType
    gap_severity: GapSeverity
    status: RecommendationStatus
    objective: str
    rationale: str
    template_id: str
    template_version: str

    experiment_class: ExperimentClass | None = None
    target_entity_type: str | None = None
    target_entity_id: UUID | None = None
    required_measurement: str | None = None
    required_comparison: str | None = None
    experimental_context_requirements: tuple[str, ...] = ()
    success_criterion: str | None = None

    supporting_claim_ids: tuple[UUID, ...] = ()
    supporting_evidence_ids: tuple[UUID, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "knowledge_gap_identity",
            require_non_empty_str(self.knowledge_gap_identity, field_name="knowledge_gap_identity"),
        )
        if not isinstance(self.gap_type, GapType):
            raise TypeError(
                f"ExperimentRecommendation.gap_type must be a GapType, got {self.gap_type!r}"
            )
        if not isinstance(self.gap_severity, GapSeverity):
            raise TypeError(
                "ExperimentRecommendation.gap_severity must be a GapSeverity, "
                f"got {self.gap_severity!r}"
            )
        if not isinstance(self.status, RecommendationStatus):
            raise TypeError(
                f"ExperimentRecommendation.status must be a RecommendationStatus, "
                f"got {self.status!r}"
            )

        is_recommended = self.status is RecommendationStatus.RECOMMENDED
        if self.experiment_class is not None and not isinstance(
            self.experiment_class, ExperimentClass
        ):
            raise TypeError(
                "ExperimentRecommendation.experiment_class must be an ExperimentClass or "
                f"None, got {self.experiment_class!r}"
            )
        if is_recommended != (self.experiment_class is not None):
            raise ValueError(
                "ExperimentRecommendation.experiment_class must be populated if and only if "
                "status is RECOMMENDED"
            )
        if is_recommended != (self.success_criterion is not None):
            raise ValueError(
                "ExperimentRecommendation.success_criterion must be populated if and only if "
                "status is RECOMMENDED"
            )

        object.__setattr__(
            self, "objective", require_non_empty_str(self.objective, field_name="objective")
        )
        object.__setattr__(
            self, "rationale", require_non_empty_str(self.rationale, field_name="rationale")
        )
        object.__setattr__(
            self, "template_id", require_non_empty_str(self.template_id, field_name="template_id")
        )
        object.__setattr__(
            self,
            "template_version",
            require_non_empty_str(self.template_version, field_name="template_version"),
        )
        object.__setattr__(
            self,
            "target_entity_type",
            clean_optional(self.target_entity_type, field_name="target_entity_type"),
        )
        if self.target_entity_id is not None and not isinstance(self.target_entity_id, UUID):
            raise TypeError(
                "ExperimentRecommendation.target_entity_id must be a UUID or None, "
                f"got {self.target_entity_id!r}"
            )
        object.__setattr__(
            self,
            "required_measurement",
            clean_optional(self.required_measurement, field_name="required_measurement"),
        )
        object.__setattr__(
            self,
            "required_comparison",
            clean_optional(self.required_comparison, field_name="required_comparison"),
        )
        object.__setattr__(
            self,
            "experimental_context_requirements",
            require_non_blank_string_tuple(
                self.experimental_context_requirements,
                field_name="experimental_context_requirements",
            ),
        )
        object.__setattr__(
            self,
            "success_criterion",
            clean_optional(self.success_criterion, field_name="success_criterion"),
        )
        object.__setattr__(
            self,
            "supporting_claim_ids",
            require_uuid_tuple(self.supporting_claim_ids, field_name="supporting_claim_ids"),
        )
        object.__setattr__(
            self,
            "supporting_evidence_ids",
            require_uuid_tuple(self.supporting_evidence_ids, field_name="supporting_evidence_ids"),
        )
        object.__setattr__(
            self,
            "reason_codes",
            require_non_blank_string_tuple(self.reason_codes, field_name="reason_codes"),
        )
        if not self.reason_codes:
            raise ValueError("ExperimentRecommendation.reason_codes must not be empty")


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationContext:
    """The minimal, immutable bundle of structured upstream data a template may read.

    ``claims``/``evidence`` are already-loaded ``Claim``/``Evidence`` ORM
    rows keyed by id -- read-only attribute access only, never a query, and
    never a ``Session`` (see module docstring). A key absent from either
    mapping means "this id's data was not supplied," which templates treat
    as ``RecommendationStatus.INSUFFICIENT_INFORMATION``, never as "this
    entity does not exist."

    ``reaction_candidate_protein_ids`` is a forward-compatibility hook for
    ``REACTION_WITHOUT_ENZYME`` (Increment 23 instructions, Step 16): a
    reaction id mapped to protein ids some *other*, not-yet-built process
    has already determined are plausible catalytic candidates. **No code
    in this repository populates this today** -- there is no structured
    "candidate but unconfirmed enzyme" concept anywhere upstream, so this
    field is always empty in current practice. It exists so a future
    increment that does produce such data does not require a template
    rewrite, not because this increment invents that data itself.
    """

    claims: Mapping[UUID, Claim] = field(default_factory=dict)
    evidence: Mapping[UUID, Evidence] = field(default_factory=dict)
    reaction_candidate_protein_ids: Mapping[UUID, tuple[UUID, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, mapping_value in (
            ("claims", self.claims),
            ("evidence", self.evidence),
            ("reaction_candidate_protein_ids", self.reaction_candidate_protein_ids),
        ):
            if not isinstance(mapping_value, Mapping):
                raise TypeError(f"{field_name} must be a Mapping, got {mapping_value!r}")
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(
            self,
            "reaction_candidate_protein_ids",
            MappingProxyType(
                {key: tuple(value) for key, value in self.reaction_candidate_protein_ids.items()}
            ),
        )


__all__ = [
    "ExperimentClass",
    "ExperimentRecommendation",
    "ExperimentRecommendationContext",
    "RecommendationStatus",
]
