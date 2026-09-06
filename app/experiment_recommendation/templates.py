"""The deterministic template registry (Increment 23).

Every ``ExperimentTemplate`` below is a fixed, versioned bundle of
recommendation *content* -- ``objective``/``success_criterion``/
``experiment_class``/``status``/``reason_code`` -- selected by
``app.experiment_recommendation.rules`` based purely on already-persisted
structured fields (``GapType``, reason codes, claim/evidence composition).
No template contains generated prose: every string here is a fixed
constant, never formatted with anything that could vary the *meaning* of
the recommendation (only ``app.experiment_recommendation.rules._finish``
ever substitutes structural facts -- ids -- into the surrounding
dataclass fields, never into ``objective``/``success_criterion`` text
itself).

**Versioning** (Increment 23 instructions, Step 25): every ``template_id``
embeds its version (e.g. ``kg-exp-conflict-replication-v1``), and
``ExperimentTemplate.version`` restates it as an independent field.
Changing a template's scientific content must create a new
``template_id``/``version`` rather than silently redefining an existing
one's meaning -- exactly the same discipline
``app.confidence.aggregate_policy.ALGORITHM_VERSION`` already established
for this repository.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.experiment_recommendation.errors import TemplateApplicationError
from app.experiment_recommendation.types import ExperimentClass, RecommendationStatus
from app.knowledge_gaps.types import GapType


@dataclass(frozen=True, slots=True)
class ExperimentTemplate:
    """One fixed, versioned recommendation template. See module docstring."""

    template_id: str
    version: str
    supported_gap_types: tuple[GapType, ...]
    status: RecommendationStatus
    objective: str
    reason_code: str
    experiment_class: ExperimentClass | None = None
    success_criterion: str | None = None
    experimental_context_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise TemplateApplicationError("ExperimentTemplate.template_id must not be blank")
        if not self.version.strip():
            raise TemplateApplicationError("ExperimentTemplate.version must not be blank")
        if not self.objective.strip():
            raise TemplateApplicationError("ExperimentTemplate.objective must not be blank")
        if not self.reason_code.strip():
            raise TemplateApplicationError("ExperimentTemplate.reason_code must not be blank")
        is_recommended = self.status is RecommendationStatus.RECOMMENDED
        if is_recommended != (self.experiment_class is not None):
            raise TemplateApplicationError(
                f"ExperimentTemplate {self.template_id!r}: experiment_class must be populated "
                "if and only if status is RECOMMENDED"
            )
        if is_recommended != (self.success_criterion is not None):
            raise TemplateApplicationError(
                f"ExperimentTemplate {self.template_id!r}: success_criterion must be populated "
                "if and only if status is RECOMMENDED"
            )


# --- Experimental-condition column names, transcribed verbatim from --------------
# app.models.experimental_condition.ExperimentalCondition -- the exact set of
# context *fields* MISSING_EXPERIMENTAL_CONTEXT asks a future experiment to
# record, never invented values for them.
_EXPERIMENTAL_CONDITION_FIELDS: tuple[str, ...] = (
    "medium",
    "carbon_source",
    "nitrogen_source",
    "oxygen_status",
    "temperature_c",
    "ph",
    "growth_phase",
    "culture_mode",
)

# --- Context fields a replication-style recommendation should record -------------
# (already-available Claim/Evidence columns -- organism_id/strain -- never
# invented values).
_REPLICATION_CONTEXT_FIELDS: tuple[str, ...] = ("organism", "strain")

_TEMPLATES: tuple[ExperimentTemplate, ...] = (
    ExperimentTemplate(
        template_id="kg-exp-conflict-replication-v1",
        version="v1",
        supported_gap_types=(GapType.CONFLICTING_CLAIMS,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.REPLICATION_EXPERIMENT,
        objective="Discriminate between the conflicting observations under controlled conditions.",
        success_criterion=(
            "The conflicting observations are discriminated under controlled, explicitly "
            "recorded conditions."
        ),
        experimental_context_requirements=_REPLICATION_CONTEXT_FIELDS,
        reason_code="LITERAL_VALUE_DISAGREEMENT_WITH_COMPARABLE_CLAIMS",
    ),
    ExperimentTemplate(
        template_id="kg-exp-conflict-human-v1",
        version="v1",
        supported_gap_types=(GapType.CONFLICTING_CLAIMS,),
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        objective="Resolve the persisted conflict recorded for this claim.",
        reason_code="CONFLICT_STRUCTURE_INSUFFICIENT_FOR_COMPARISON",
    ),
    ExperimentTemplate(
        template_id="kg-exp-lowconf-replication-v1",
        version="v1",
        supported_gap_types=(GapType.LOW_CONFIDENCE_CLAIM,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.REPLICATION_EXPERIMENT,
        objective=(
            "Independently replicate the most direct existing experimental evidence "
            "supporting this claim."
        ),
        success_criterion=(
            "The reported effect is independently reproduced under explicitly recorded "
            "experimental conditions."
        ),
        experimental_context_requirements=_REPLICATION_CONTEXT_FIELDS,
        reason_code="PRIMARY_EVIDENCE_PRESENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-lowconf-localization-v1",
        version="v1",
        supported_gap_types=(GapType.LOW_CONFIDENCE_CLAIM,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.PROTEIN_LOCALIZATION_ASSAY,
        objective="Directly measure the target's compartment localization to validate this claim.",
        success_criterion="The target's compartment localization is directly measured.",
        reason_code="NO_PRIMARY_EVIDENCE_OBJECT_IS_COMPARTMENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-lowconf-human-v1",
        version="v1",
        supported_gap_types=(GapType.LOW_CONFIDENCE_CLAIM,),
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        objective="Determine an appropriate direct experimental validation for this claim.",
        reason_code="NO_PRIMARY_EVIDENCE_AND_CLAIM_STRUCTURE_UNMAPPABLE",
    ),
    ExperimentTemplate(
        template_id="kg-exp-singlesource-replication-v1",
        version="v1",
        supported_gap_types=(GapType.SINGLE_SOURCE_SUPPORT,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.REPLICATION_EXPERIMENT,
        objective="Independently replicate the reported effect in a separate experiment or source.",
        success_criterion=(
            "The reported effect is independently reproduced under explicitly recorded "
            "experimental conditions from a distinct source."
        ),
        experimental_context_requirements=_REPLICATION_CONTEXT_FIELDS,
        reason_code="PRIMARY_EVIDENCE_PRESENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-singlesource-localization-v1",
        version="v1",
        supported_gap_types=(GapType.SINGLE_SOURCE_SUPPORT,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.PROTEIN_LOCALIZATION_ASSAY,
        objective="Directly measure the target's compartment localization to validate this claim.",
        success_criterion="The target's compartment localization is directly measured.",
        reason_code="NO_PRIMARY_EVIDENCE_OBJECT_IS_COMPARTMENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-singlesource-human-v1",
        version="v1",
        supported_gap_types=(GapType.SINGLE_SOURCE_SUPPORT,),
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        objective="Determine an appropriate direct experimental validation for this claim.",
        reason_code="NO_PRIMARY_EVIDENCE_AND_CLAIM_STRUCTURE_UNMAPPABLE",
    ),
    ExperimentTemplate(
        template_id="kg-exp-noprimary-localization-v1",
        version="v1",
        supported_gap_types=(GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.PROTEIN_LOCALIZATION_ASSAY,
        objective="Directly measure the target's compartment localization to validate this claim.",
        success_criterion="The target's compartment localization is directly measured.",
        reason_code="OBJECT_IS_COMPARTMENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-noprimary-human-v1",
        version="v1",
        supported_gap_types=(GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,),
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        objective=(
            "Determine an appropriate direct experimental validation for this claim, "
            "currently supported only by non-primary evidence."
        ),
        reason_code="CLAIM_STRUCTURE_UNMAPPABLE_WITHOUT_FREE_TEXT_INTERPRETATION",
    ),
    ExperimentTemplate(
        template_id="kg-exp-missingpub-na-v1",
        version="v1",
        supported_gap_types=(GapType.MISSING_PUBLICATION,),
        status=RecommendationStatus.NOT_APPLICABLE,
        objective="Recover or verify the primary publication provenance for this evidence.",
        reason_code="PROVENANCE_GAP_NOT_AN_EXPERIMENTAL_GAP",
    ),
    ExperimentTemplate(
        template_id="kg-exp-context-characterization-v1",
        version="v1",
        supported_gap_types=(GapType.MISSING_EXPERIMENTAL_CONTEXT,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.EXPERIMENTAL_CONTEXT_CHARACTERIZATION,
        objective=(
            "Repeat or document the experiment with explicit, recorded experimental "
            "conditions."
        ),
        success_criterion=(
            "The experimental conditions under which the existing evidence was obtained are "
            "explicitly recorded and reported."
        ),
        experimental_context_requirements=_EXPERIMENTAL_CONDITION_FIELDS,
        reason_code="EXPERIMENTAL_EVIDENCE_MISSING_RECORDED_CONDITIONS",
    ),
    ExperimentTemplate(
        template_id="kg-exp-enzyme-candidate-v1",
        version="v1",
        supported_gap_types=(GapType.REACTION_WITHOUT_ENZYME,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.ENZYME_SUBSTRATE_ASSAY,
        objective="Determine whether candidate enzyme activity can be assigned to this reaction.",
        success_criterion=(
            "Catalytic activity toward this reaction is directly measured for the candidate "
            "protein(s)."
        ),
        reason_code="STRUCTURED_CANDIDATE_PROTEINS_PRESENT",
    ),
    ExperimentTemplate(
        template_id="kg-exp-enzyme-human-v1",
        version="v1",
        supported_gap_types=(GapType.REACTION_WITHOUT_ENZYME,),
        status=RecommendationStatus.REQUIRES_HUMAN_DESIGN,
        objective="Identify catalytic activity associated with this reaction.",
        reason_code="NO_STRUCTURED_CANDIDATE_PROTEINS",
    ),
    ExperimentTemplate(
        template_id="kg-exp-protein-na-v1",
        version="v1",
        supported_gap_types=(GapType.PROTEIN_WITHOUT_REACTION,),
        status=RecommendationStatus.NOT_APPLICABLE,
        objective="Determine whether this protein has an associated catalytic or reaction role.",
        reason_code="CONNECTIVITY_GAP_NOT_A_CONFIRMED_DEFICIENCY",
    ),
    ExperimentTemplate(
        template_id="kg-exp-gene-na-v1",
        version="v1",
        supported_gap_types=(GapType.GENE_WITHOUT_PROTEIN,),
        status=RecommendationStatus.NOT_APPLICABLE,
        objective=(
            "Determine whether this gene's protein product has been characterized or "
            "normalized."
        ),
        reason_code="CURATION_COMPLETENESS_NOT_CONFIRMED_BIOLOGICAL_ABSENCE",
    ),
    ExperimentTemplate(
        template_id="kg-exp-reaction-validation-v1",
        version="v1",
        supported_gap_types=(GapType.REACTION_WITHOUT_PARTICIPANTS,),
        status=RecommendationStatus.RECOMMENDED,
        experiment_class=ExperimentClass.REACTION_VALIDATION,
        objective="Establish the chemical participants and stoichiometry of this reaction.",
        success_criterion="Reactants, products, and stoichiometry are experimentally established.",
        reason_code="REACTION_HAS_ZERO_PARTICIPANTS",
    ),
    ExperimentTemplate(
        template_id="kg-exp-compound-na-v1",
        version="v1",
        supported_gap_types=(GapType.ISOLATED_COMPOUND,),
        status=RecommendationStatus.NOT_APPLICABLE,
        objective=(
            "Determine this compound's intended role and connectivity within the "
            "curated reaction network."
        ),
        reason_code="ISOLATION_NOT_A_CONFIRMED_EXPERIMENTAL_OPPORTUNITY",
    ),
    ExperimentTemplate(
        template_id="kg-exp-insufficient-context-v1",
        version="v1",
        supported_gap_types=(),
        status=RecommendationStatus.INSUFFICIENT_INFORMATION,
        objective="Supply the referenced claim/evidence records so this gap can be evaluated.",
        reason_code="INSUFFICIENT_CONTEXT",
    ),
)

TEMPLATE_REGISTRY: dict[str, ExperimentTemplate] = {}
TEMPLATES_BY_GAP_TYPE: dict[GapType, tuple[ExperimentTemplate, ...]] = {}


def _register(templates: tuple[ExperimentTemplate, ...]) -> None:
    by_gap_type: dict[GapType, list[ExperimentTemplate]] = {}
    for template in templates:
        if template.template_id in TEMPLATE_REGISTRY:
            raise TemplateApplicationError(
                f"duplicate template_id in registry: {template.template_id!r}"
            )
        TEMPLATE_REGISTRY[template.template_id] = template
        for gap_type in template.supported_gap_types:
            by_gap_type.setdefault(gap_type, []).append(template)
    for gap_type, matching in by_gap_type.items():
        TEMPLATES_BY_GAP_TYPE[gap_type] = tuple(matching)


_register(_TEMPLATES)


def get_template(template_id: str) -> ExperimentTemplate:
    """Look up one template by id. Raises if it does not exist in the registry."""
    try:
        return TEMPLATE_REGISTRY[template_id]
    except KeyError as exc:
        raise TemplateApplicationError(
            f"no template registered with template_id={template_id!r}"
        ) from exc


__all__ = [
    "TEMPLATES_BY_GAP_TYPE",
    "TEMPLATE_REGISTRY",
    "ExperimentTemplate",
    "get_template",
]
