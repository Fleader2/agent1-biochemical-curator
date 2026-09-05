"""The public Single-Evidence Assessment API: ``assess_single_evidence_claim``.

Produces a deterministic, auditable **assessment** of one supporting
``CandidateClaim`` -- never a final Claim confidence. See module docstring
of ``app.confidence.types`` and ``docs/13_single_evidence_confidence_contract.md``
for why: the authoritative ``docs/03_agent_behavior.md`` confidence
formula is an aggregate-claim calculation (it requires a replication
bonus to reach its own MODERATE floor), so a single evidence item cannot
meaningfully produce a final 0-100 score or ``ConfidenceClass`` on its
own. This function only records categorical state for a future
multi-evidence aggregator (Increment 18) to consume.

This is still a strict single-evidence boundary: it never inspects
another ``CandidateClaim``, another ``EvidenceExtraction``, a database
row, or a network resource -- see this module's own structural safety
tests. No database session, no connector, no HTTP client, no LLM call:
every input already exists on ``CandidateClaim``,
``CandidateEntityReference``, ``NormalizationResult``, and
``MentionResolutionResult`` -- all plain, already-validated, immutable
values. Nothing here mutates any of them.
"""

from __future__ import annotations

from app.claim_generation.types import CandidateClaim, CandidateEntityReference, EntityKind
from app.confidence.policy import (
    EVIDENCE_TYPE_BASE_SCORES,
    EntityResolutionQuality,
    EntityRole,
    OrganismAssessment,
    ScoringStatus,
)
from app.confidence.types import SingleEvidenceAssessment
from app.entity_resolution.types import MentionResolutionResult, MentionResolutionStatus
from app.normalization.types import NormalizationStatus

_NORMALIZATION_STATUS_TO_QUALITY: dict[NormalizationStatus, EntityResolutionQuality] = {
    NormalizationStatus.MATCHED: EntityResolutionQuality.RESOLVED,
    NormalizationStatus.NEW: EntityResolutionQuality.VERIFIED_NEW,
    NormalizationStatus.AMBIGUOUS: EntityResolutionQuality.AMBIGUOUS,
    NormalizationStatus.CONFLICTED: EntityResolutionQuality.CONFLICTED,
    NormalizationStatus.UNRESOLVED: EntityResolutionQuality.UNRESOLVED,
}

_MENTION_RESOLUTION_STATUS_TO_QUALITY: dict[MentionResolutionStatus, EntityResolutionQuality] = {
    MentionResolutionStatus.RESOLVED: EntityResolutionQuality.RESOLVED,
    MentionResolutionStatus.AMBIGUOUS: EntityResolutionQuality.AMBIGUOUS,
    MentionResolutionStatus.CONFLICTED: EntityResolutionQuality.CONFLICTED,
    MentionResolutionStatus.UNRESOLVED: EntityResolutionQuality.UNRESOLVED,
    MentionResolutionStatus.NO_CANDIDATE: EntityResolutionQuality.NO_CANDIDATE,
    MentionResolutionStatus.SOURCE_FAILURE: EntityResolutionQuality.SOURCE_FAILURE,
    MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND: EntityResolutionQuality.UNSUPPORTED,
}

_ORGANISM_UNRESOLVED_QUALITIES = frozenset(
    {
        EntityResolutionQuality.AMBIGUOUS,
        EntityResolutionQuality.VERIFIED_NEW,
        EntityResolutionQuality.UNRESOLVED,
        EntityResolutionQuality.NO_CANDIDATE,
        EntityResolutionQuality.UNSUPPORTED,
    }
)


def _classify_mention_resolution(result: MentionResolutionResult) -> EntityResolutionQuality:
    """Map one ``MentionResolutionResult`` to an ``EntityResolutionQuality`` bucket.

    ``NEW_CANDIDATE`` is split further, per Increment 14's own finding
    (preserved here categorically): a single, verified, unconflicted
    external record (exactly one candidate) is ``VERIFIED_NEW`` --
    "not yet persisted" is never equated with "identity is uncertain".
    Multiple simultaneous ``NEW`` candidates for one mention (no single
    canonical match, but more than one distinct verified record) is
    genuine uncertainty about which record is the real one, so it is
    ``AMBIGUOUS`` instead -- this is not something
    ``app.entity_resolution.ranking.classify_outcome`` itself
    distinguishes from a single-candidate ``NEW_CANDIDATE``, so this
    module makes the distinction directly from ``result.candidates``.
    """
    if result.status is MentionResolutionStatus.NEW_CANDIDATE:
        if len(result.candidates) == 1:
            return EntityResolutionQuality.VERIFIED_NEW
        return EntityResolutionQuality.AMBIGUOUS
    return _MENTION_RESOLUTION_STATUS_TO_QUALITY[result.status]


def _assess_entity(
    reference: CandidateEntityReference | None, *, role: EntityRole
) -> EntityResolutionQuality | None:
    """Assess one role's categorical identity-resolution state, or ``None`` if not applicable.

    ``None`` is returned only when ``reference`` itself is ``None`` --
    there is nothing to report at all (a literal-value claim's missing
    object, or a claim with no stated compartment). This is distinct from
    ``EntityResolutionQuality.NOT_APPLICABLE``, which means a reference
    *exists* but was never typed as an entity (only possible for
    ``EntityRole.OBJECT`` -- subject/organism/compartment are always typed
    when present, per ``CandidateClaim``'s own construction).
    """
    if reference is None:
        return None

    if reference.entity_kind is EntityKind.UNKNOWN:
        return (
            EntityResolutionQuality.UNKNOWN_KIND
            if role is EntityRole.SUBJECT
            else EntityResolutionQuality.NOT_APPLICABLE
        )

    if reference.mention_resolution_result is not None:
        return _classify_mention_resolution(reference.mention_resolution_result)
    if reference.normalization_result is not None:
        return _NORMALIZATION_STATUS_TO_QUALITY[reference.normalization_result.status]

    # Normalization was never attempted at all (no Lookup was configured
    # for this kind) -- the same epistemic position as
    # MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND: no resolution
    # capability exists, for either reason.
    return EntityResolutionQuality.UNSUPPORTED


def _organism_assessment(organism: CandidateEntityReference | None) -> OrganismAssessment:
    """Return the categorical ``OrganismAssessment`` for one claim's organism reference.

    Uses the separate, coarser ``OrganismAssessment`` vocabulary (Step 5)
    rather than the generic ``EntityResolutionQuality`` used for
    subject/object/compartment -- see ``app.confidence.policy
    .OrganismAssessment``'s own docstring for exactly why.
    """
    if organism is None:
        return OrganismAssessment.NOT_STATED
    if organism.normalized_id is not None:
        return OrganismAssessment.EXPLICIT_RESOLVED

    quality = _assess_entity(organism, role=EntityRole.ORGANISM)
    if quality is EntityResolutionQuality.CONFLICTED:
        return OrganismAssessment.CONFLICTED
    if quality is EntityResolutionQuality.SOURCE_FAILURE:
        return OrganismAssessment.SOURCE_FAILURE
    if quality in _ORGANISM_UNRESOLVED_QUALITIES:
        return OrganismAssessment.EXPLICIT_UNRESOLVED
    return (
        OrganismAssessment.UNKNOWN
    )  # defensive fallback -- not expected given organism's own typing


def _build_reason_codes(
    *,
    claim: CandidateClaim,
    scoring_status: ScoringStatus,
    subject_resolution: EntityResolutionQuality,
    object_resolution: EntityResolutionQuality | None,
    organism_resolution: OrganismAssessment,
    compartment_resolution: EntityResolutionQuality | None,
) -> tuple[str, ...]:
    codes = [
        f"EVIDENCE_{claim.evidence_type.value}"
        if scoring_status is ScoringStatus.SCORED_BASE
        else f"EVIDENCE_UNSCORED_{claim.evidence_type.value}",
        f"DIRECTNESS_{claim.directness.value}",
        f"SUBJECT_{subject_resolution.value}",
    ]
    if object_resolution is not None:
        codes.append(f"OBJECT_{object_resolution.value}")
    codes.append(f"ORGANISM_{organism_resolution.value}")
    if compartment_resolution is not None:
        codes.append(f"COMPARTMENT_{compartment_resolution.value}")
    return tuple(codes)


def _build_explanation(
    *,
    claim: CandidateClaim,
    evidence_base_score: int | None,
    subject_resolution: EntityResolutionQuality,
    object_resolution: EntityResolutionQuality | None,
    organism_resolution: OrganismAssessment,
    compartment_resolution: EntityResolutionQuality | None,
) -> str:
    lines = [
        f"Evidence type {claim.evidence_type.value} has authoritative base score "
        f"{evidence_base_score}."
        if evidence_base_score is not None
        else f"Evidence type {claim.evidence_type.value} has no authoritative base score "
        "(unscored).",
        f"Directness is {claim.directness.value}.",
        f"Subject identity is {subject_resolution.value}.",
    ]
    if object_resolution is not None:
        lines.append(f"Object identity is {object_resolution.value}.")
    lines.append(f"Organism context is {organism_resolution.value}.")
    if compartment_resolution is not None:
        lines.append(f"Compartment identity is {compartment_resolution.value}.")
    lines.append("Final aggregate Claim confidence is not computed at the single-evidence stage.")
    return "\n".join(lines)


def assess_single_evidence_claim(claim: CandidateClaim) -> SingleEvidenceAssessment:
    """Assess one ``CandidateClaim`` against its own single supporting evidence.

    Uses only ``claim`` itself (its ``evidence_type``, ``directness``,
    ``subject``/``object``/``organism``/``compartment`` entity references,
    and their own ``normalization_result``/``mention_resolution_result``
    provenance) -- never another ``CandidateClaim``, another
    ``EvidenceExtraction``, a database row, or a network call. See module
    docstring. Returns a categorical ``SingleEvidenceAssessment`` -- never
    a final Claim confidence score or ``ConfidenceClass``.
    """
    if not isinstance(claim, CandidateClaim):
        raise TypeError(f"assess_single_evidence_claim requires a CandidateClaim, got {claim!r}")

    evidence_base_score = EVIDENCE_TYPE_BASE_SCORES.get(claim.evidence_type)
    scoring_status = (
        ScoringStatus.SCORED_BASE
        if evidence_base_score is not None
        else ScoringStatus.UNSCORED_EVIDENCE_TYPE
    )

    subject_resolution = _assess_entity(claim.subject, role=EntityRole.SUBJECT)
    assert subject_resolution is not None  # subject is never None on a CandidateClaim
    object_resolution = _assess_entity(claim.object, role=EntityRole.OBJECT)
    organism_resolution = _organism_assessment(claim.organism)
    compartment_resolution = _assess_entity(claim.compartment, role=EntityRole.COMPARTMENT)

    reason_codes = _build_reason_codes(
        claim=claim,
        scoring_status=scoring_status,
        subject_resolution=subject_resolution,
        object_resolution=object_resolution,
        organism_resolution=organism_resolution,
        compartment_resolution=compartment_resolution,
    )
    explanation = _build_explanation(
        claim=claim,
        evidence_base_score=evidence_base_score,
        subject_resolution=subject_resolution,
        object_resolution=object_resolution,
        organism_resolution=organism_resolution,
        compartment_resolution=compartment_resolution,
    )

    return SingleEvidenceAssessment(
        evidence_type=claim.evidence_type,
        evidence_base_score=evidence_base_score,
        scoring_status=scoring_status,
        directness=claim.directness,
        subject_resolution=subject_resolution,
        object_resolution=object_resolution,
        organism_resolution=organism_resolution,
        compartment_resolution=compartment_resolution,
        reason_codes=reason_codes,
        explanation=explanation,
    )


__all__ = ["assess_single_evidence_claim"]
