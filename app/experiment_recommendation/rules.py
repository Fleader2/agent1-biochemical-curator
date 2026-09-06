"""Deterministic GapType -> template selection (Increment 23).

Every function here is a pure transformation of a ``_GapView`` (the
gap-shaped fields common to both an in-memory ``KnowledgeGapCandidate`` and
a persisted ``KnowledgeGap`` row -- see
``app.experiment_recommendation.recommender`` for the two adapters) plus an
``ExperimentRecommendationContext`` into one ``ExperimentRecommendation``.
No query, no write, no I/O, no randomness -- exactly the same purity
discipline ``app.knowledge_gaps.rules`` already established for detection.

**Policy reuse, not reinvention.** The "is this Evidence primary" test
reuses ``app.knowledge_gaps.rules.NON_PRIMARY_EVIDENCE_TYPES``/
``AMBIGUOUS_EVIDENCE_TYPES``/``DERIVATIVE_DIRECTNESS`` -- the exact same
public policy constants Increment 21 already established for the identical
concept -- rather than defining a second, potentially-diverging notion of
"primary evidence."

**The one narrow claim-structure inference this package makes.**
``Claim.claim_category``/``predicate`` are free text with no controlled
vocabulary (verified against ``app.claim_generation.types``), so nothing
here parses them (Increment 23 instructions, Step 13: "Do not introduce
fuzzy semantic NLP"). The **one** exception is ``Claim.object_type ==
"COMPARTMENT"`` -- a closed, already-validated ``EntityKind`` value set by
claim generation itself, not inferred here -- which deterministically
identifies a claim as being about compartment localization. No other
``NO_PRIMARY_EXPERIMENTAL_EVIDENCE``/``LOW_CONFIDENCE_CLAIM``/
``SINGLE_SOURCE_SUPPORT`` claim structure (reaction activity, expression,
metabolite abundance, flux) has an equally unambiguous, already-persisted
signal to key off today -- see
``docs/19_experiment_recommendation_contract.md`` §9/§11 for the full
disclosure of why those cases fall back to ``REQUIRES_HUMAN_DESIGN``
instead of the illustrative mappings Step 13 lists as merely "examples."
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.experiment_recommendation.errors import UnsupportedGapTypeError
from app.experiment_recommendation.templates import ExperimentTemplate, get_template
from app.experiment_recommendation.types import (
    ExperimentRecommendation,
    ExperimentRecommendationContext,
    RecommendationStatus,
)
from app.knowledge_gaps.rules import (
    AMBIGUOUS_EVIDENCE_TYPES,
    NON_PRIMARY_EVIDENCE_TYPES,
)
from app.knowledge_gaps.rules import (
    DERIVATIVE_DIRECTNESS as _DERIVATIVE_DIRECTNESS,
)
from app.knowledge_gaps.types import GapSeverity, GapType
from app.models.claim import Claim, Evidence

_DERIVATIVE_DIRECTNESS_VALUES: frozenset[str] = frozenset(d.value for d in _DERIVATIVE_DIRECTNESS)

_COMPARTMENT_OBJECT_TYPE = "COMPARTMENT"


@dataclass(frozen=True, slots=True)
class _GapView:
    """The gap-shaped fields common to both recommendation entry points.

    Built by ``app.experiment_recommendation.recommender`` from either a
    ``KnowledgeGapCandidate`` or a persisted ``KnowledgeGap`` row -- this
    module never imports either of those types directly, keeping the
    decision logic below agnostic to which one produced it.
    """

    knowledge_gap_identity: str
    gap_type: GapType
    severity: GapSeverity
    entity_type: str
    entity_id: UUID | None
    supporting_claim_ids: tuple[UUID, ...]
    supporting_evidence_ids: tuple[UUID, ...]
    reason_codes: tuple[str, ...]


def _is_primary_evidence(evidence: Evidence) -> bool:
    return (
        evidence.evidence_type not in NON_PRIMARY_EVIDENCE_TYPES
        and evidence.evidence_type not in AMBIGUOUS_EVIDENCE_TYPES
        and evidence.directness not in _DERIVATIVE_DIRECTNESS_VALUES
    )


def _resolve_claim(view: _GapView, context: ExperimentRecommendationContext) -> Claim | None:
    if not view.supporting_claim_ids:
        return None
    return context.claims.get(view.supporting_claim_ids[0])


def _resolve_evidence(
    view: _GapView, context: ExperimentRecommendationContext
) -> tuple[Evidence, ...] | None:
    """The claim's ``supporting_evidence_ids``, resolved via context. ``None`` if any is missing."""
    resolved: list[Evidence] = []
    for evidence_id in view.supporting_evidence_ids:
        evidence = context.evidence.get(evidence_id)
        if evidence is None:
            return None
        resolved.append(evidence)
    return tuple(resolved)


def _finish(
    view: _GapView,
    template: ExperimentTemplate,
    *,
    target_entity_type: str | None = None,
    target_entity_id: UUID | None = None,
    required_measurement: str | None = None,
    required_comparison: str | None = None,
    experimental_context_requirements: tuple[str, ...] | None = None,
    supporting_claim_ids: tuple[UUID, ...] = (),
    supporting_evidence_ids: tuple[UUID, ...] = (),
) -> ExperimentRecommendation:
    context_requirements = (
        experimental_context_requirements
        if experimental_context_requirements is not None
        else template.experimental_context_requirements
    )
    rationale = f"{view.gap_type.value} -> {template.template_id} -> {template.status.value}"
    return ExperimentRecommendation(
        knowledge_gap_identity=view.knowledge_gap_identity,
        gap_type=view.gap_type,
        gap_severity=view.severity,
        status=template.status,
        experiment_class=template.experiment_class,
        objective=template.objective,
        target_entity_type=target_entity_type,
        target_entity_id=target_entity_id,
        required_measurement=required_measurement,
        required_comparison=required_comparison,
        experimental_context_requirements=context_requirements,
        success_criterion=template.success_criterion,
        rationale=rationale,
        supporting_claim_ids=supporting_claim_ids,
        supporting_evidence_ids=supporting_evidence_ids,
        reason_codes=(template.reason_code,),
        template_id=template.template_id,
        template_version=template.version,
    )


def _insufficient_information(view: _GapView, detail_reason_code: str) -> ExperimentRecommendation:
    template = get_template("kg-exp-insufficient-context-v1")
    rationale = f"{view.gap_type.value} -> {template.template_id} -> {template.status.value}"
    return ExperimentRecommendation(
        knowledge_gap_identity=view.knowledge_gap_identity,
        gap_type=view.gap_type,
        gap_severity=view.severity,
        status=RecommendationStatus.INSUFFICIENT_INFORMATION,
        objective=template.objective,
        target_entity_type=view.entity_type,
        target_entity_id=view.entity_id,
        rationale=rationale,
        supporting_claim_ids=view.supporting_claim_ids,
        supporting_evidence_ids=view.supporting_evidence_ids,
        reason_codes=(detail_reason_code,),
        template_id=template.template_id,
        template_version=template.version,
    )


# --- Claim-anchored gap types ------------------------------------------------------


def _recommend_conflicting_claims(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    if "LITERAL_VALUE_DISAGREEMENT" in view.reason_codes and len(view.supporting_claim_ids) >= 2:
        template = get_template("kg-exp-conflict-replication-v1")
        ids = view.supporting_claim_ids
        required_comparison = "pairwise comparison among claims: " + ", ".join(
            str(claim_id) for claim_id in ids
        )
        return _finish(
            view,
            template,
            target_entity_type="claim",
            target_entity_id=view.entity_id,
            required_comparison=required_comparison,
            supporting_claim_ids=ids,
        )
    template = get_template("kg-exp-conflict-human-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
    )


def _recommend_low_confidence_claim(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    claim = _resolve_claim(view, context)
    if claim is None:
        return _insufficient_information(view, "SUPPORTING_CLAIM_NOT_IN_CONTEXT")

    evidence_records = tuple(claim.evidence_records)
    if any(_is_primary_evidence(e) for e in evidence_records):
        template = get_template("kg-exp-lowconf-replication-v1")
        return _finish(
            view,
            template,
            target_entity_type="claim",
            target_entity_id=view.entity_id,
            supporting_claim_ids=view.supporting_claim_ids,
            supporting_evidence_ids=tuple(sorted((e.id for e in evidence_records), key=str)),
        )
    if claim.object_type == _COMPARTMENT_OBJECT_TYPE:
        template = get_template("kg-exp-lowconf-localization-v1")
        return _finish(
            view,
            template,
            target_entity_type="claim",
            target_entity_id=view.entity_id,
            supporting_claim_ids=view.supporting_claim_ids,
        )
    template = get_template("kg-exp-lowconf-human-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
    )


def _recommend_single_source_support(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    claim = _resolve_claim(view, context)
    if claim is None:
        return _insufficient_information(view, "SUPPORTING_CLAIM_NOT_IN_CONTEXT")
    resolved_evidence = _resolve_evidence(view, context)
    if resolved_evidence is None:
        return _insufficient_information(view, "SUPPORTING_EVIDENCE_NOT_IN_CONTEXT")

    if any(_is_primary_evidence(e) for e in resolved_evidence):
        template = get_template("kg-exp-singlesource-replication-v1")
        return _finish(
            view,
            template,
            target_entity_type="claim",
            target_entity_id=view.entity_id,
            supporting_claim_ids=view.supporting_claim_ids,
            supporting_evidence_ids=view.supporting_evidence_ids,
        )
    if claim.object_type == _COMPARTMENT_OBJECT_TYPE:
        template = get_template("kg-exp-singlesource-localization-v1")
        return _finish(
            view,
            template,
            target_entity_type="claim",
            target_entity_id=view.entity_id,
            supporting_claim_ids=view.supporting_claim_ids,
            supporting_evidence_ids=view.supporting_evidence_ids,
        )
    template = get_template("kg-exp-singlesource-human-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
        supporting_evidence_ids=view.supporting_evidence_ids,
    )


def _recommend_no_primary_experimental_evidence(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    claim = _resolve_claim(view, context)
    if claim is None:
        return _insufficient_information(view, "SUPPORTING_CLAIM_NOT_IN_CONTEXT")

    if claim.object_type == _COMPARTMENT_OBJECT_TYPE:
        template = get_template("kg-exp-noprimary-localization-v1")
    else:
        template = get_template("kg-exp-noprimary-human-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
        supporting_evidence_ids=view.supporting_evidence_ids,
    )


def _recommend_missing_publication(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-missingpub-na-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
        supporting_evidence_ids=view.supporting_evidence_ids,
    )


def _recommend_missing_experimental_context(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-context-characterization-v1")
    return _finish(
        view,
        template,
        target_entity_type="claim",
        target_entity_id=view.entity_id,
        supporting_claim_ids=view.supporting_claim_ids,
        supporting_evidence_ids=view.supporting_evidence_ids,
    )


# --- Entity-anchored gap types -------------------------------------------------------


def _recommend_reaction_without_enzyme(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    candidate_protein_ids = (
        context.reaction_candidate_protein_ids.get(view.entity_id, ())
        if view.entity_id is not None
        else ()
    )
    if candidate_protein_ids:
        template = get_template("kg-exp-enzyme-candidate-v1")
        return _finish(
            view,
            template,
            target_entity_type="reaction",
            target_entity_id=view.entity_id,
        )
    template = get_template("kg-exp-enzyme-human-v1")
    return _finish(view, template, target_entity_type="reaction", target_entity_id=view.entity_id)


def _recommend_protein_without_reaction(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-protein-na-v1")
    return _finish(view, template, target_entity_type="protein", target_entity_id=view.entity_id)


def _recommend_gene_without_protein(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-gene-na-v1")
    return _finish(view, template, target_entity_type="gene", target_entity_id=view.entity_id)


def _recommend_reaction_without_participants(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-reaction-validation-v1")
    return _finish(view, template, target_entity_type="reaction", target_entity_id=view.entity_id)


def _recommend_isolated_compound(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    template = get_template("kg-exp-compound-na-v1")
    return _finish(view, template, target_entity_type="compound", target_entity_id=view.entity_id)


_GAP_TYPE_HANDLERS = {
    GapType.CONFLICTING_CLAIMS: _recommend_conflicting_claims,
    GapType.LOW_CONFIDENCE_CLAIM: _recommend_low_confidence_claim,
    GapType.SINGLE_SOURCE_SUPPORT: _recommend_single_source_support,
    GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE: _recommend_no_primary_experimental_evidence,
    GapType.MISSING_PUBLICATION: _recommend_missing_publication,
    GapType.MISSING_EXPERIMENTAL_CONTEXT: _recommend_missing_experimental_context,
    GapType.REACTION_WITHOUT_ENZYME: _recommend_reaction_without_enzyme,
    GapType.PROTEIN_WITHOUT_REACTION: _recommend_protein_without_reaction,
    GapType.GENE_WITHOUT_PROTEIN: _recommend_gene_without_protein,
    GapType.REACTION_WITHOUT_PARTICIPANTS: _recommend_reaction_without_participants,
    GapType.ISOLATED_COMPOUND: _recommend_isolated_compound,
}


def build_recommendation(
    view: _GapView, context: ExperimentRecommendationContext
) -> ExperimentRecommendation:
    """Dispatch one ``_GapView`` to its ``GapType``-specific handler.

    Raises ``UnsupportedGapTypeError`` for a ``GapType`` with no registered
    handler at all -- unreachable in practice, since every current
    ``GapType`` member has one, but this never silently falls through to an
    arbitrary default template (Increment 23 instructions, Step 8/§ "no
    duplicate template registry" tests).
    """
    handler = _GAP_TYPE_HANDLERS.get(view.gap_type)
    if handler is None:
        raise UnsupportedGapTypeError(
            f"no recommendation handler is registered for gap_type={view.gap_type!r}"
        )
    return handler(view, context)


__all__ = ["build_recommendation"]
