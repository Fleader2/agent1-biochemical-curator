"""The public Multi-Evidence Aggregation API: ``aggregate_claim_confidence``.

Combines multiple ``EvidenceContribution``\\ s -- each one evidence item's
``CandidateClaim`` + its own Increment 17 ``SingleEvidenceAssessment`` --
into one final, auditable ``AggregateClaimConfidence`` for one logical
claim: a real 0-100 score and ``ConfidenceClass``, the thing Increment 17
deliberately never produced.

**Increment 18A: the canonical aggregation mathematics.** Increment 18
found that ``docs/03_agent_behavior.md``'s "Confidence Scoring Behavior"
formula never specifies how *multiple, differing* evidence items' own
base scores combine into its single ``base_evidence_score`` term (only
that they must not simply be summed) -- and, per its own explicit
instruction, declined to invent one: heterogeneous eligible evidence
produced ``score=None``/``UNKNOWN``. Increment 18A resolves that gap with
a deterministic, descending-sorted, exponentially-diminishing-weight sum:

1. **Adjusted score** -- for every numerically eligible contribution,
   ``AdjustedScore = BaseScore x OrganismModifier x ExperimentalModifier``
   (unchanged from Increment 18, now kept as an exact ``Decimal`` rather
   than floor-divided to an ``int``, so the diminishing-return sum below
   never compounds rounding error across several contributions).
2. **Order contributions** -- sort eligible, non-duplicate contributions
   by ``AdjustedScore`` descending (ties broken by a stable, input-order-
   independent canonical key -- see ``_ranked_eligible``), so the same
   evidence set in any input order always assigns the same weight to the
   same specific contribution.
3. **Diminishing-return accumulation** -- the ``k``-th ranked contribution
   (1-indexed) is weighted ``WEIGHT_BASE ** (k - 1)`` (1, 1/2, 1/4, 1/8,
   ...; ``app.confidence.aggregate_policy.WEIGHT_BASE``).
   ``AggregateEvidence = sum(AdjustedScore_k * Weight_k)``. This: (a) lets
   the single strongest piece of evidence dominate (its own weight is
   always exactly 1, undiminished), (b) still lets weaker corroborating
   evidence add something, with strictly diminishing effect, (c) can never
   explode linearly with evidence count, and (d) needs no combination
   *choice* for heterogeneous evidence types -- it is defined for any
   adjusted-score sequence, identical or not.
4. **Replication bonus** -- unchanged from Increment 18, applied on top of
   (never inside) the weighted sum: +5 per additional independent, non-
   derivative source beyond the first, capped at +10.
5. **Conflict penalty** -- unchanged: the single most severe supplied
   ``ConflictSeverity`` across all contributions, applied once, never
   stacked (MINOR -10 / MAJOR -25).
6. **Clamp** to ``[0, 100]``.
7. **ConfidenceClass** -- unchanged authoritative thresholds.

**Why this can never itself exceed the authoritative ceiling.** Today's
highest authoritative ``EvidenceType`` base score is 45
(``DIRECT_BIOCHEMICAL``), and ``sum(WEIGHT_BASE ** k for k in
range(n))`` is strictly less than 2 for any finite ``n`` (a geometric
series with ratio 1/2, whose infinite sum is exactly 2). So
``AggregateEvidence < 45 * 2 = 90`` for *any* number of same-strength
eligible contributions, before replication bonus is even added -- the
diminishing-return design keeps confidence "asymptotically approach[ing]
but never exceed[ing] 100" (this increment's own design principle 5) as a
direct mathematical consequence of the weight sequence, not a separate
rule. The final ``[0, 100]`` clamp (step 6) remains a defensive backstop,
never the mechanism actually relied on.

``UNKNOWN`` (``score=None``) now occurs in exactly one case: no
numerically eligible contribution exists at all
(``AGGREGATE_NO_ELIGIBLE_EVIDENCE``) -- heterogeneous evidence strengths
no longer block a final score, and the previous
``AGGREGATE_BASE_COMBINATION_UNSPECIFIED`` reason code no longer exists.

``algorithm_version`` (``app.confidence.aggregate_policy.ALGORITHM_VERSION``,
currently ``"confidence-v1"``) is recorded on every result -- a genuine
future change to this formula would introduce a new version string, never
silently redefine this one.

No database session, no connector, no HTTP client, no LLM call. Nothing
here mutates ``CandidateClaim``, ``EvidenceExtraction``,
``SingleEvidenceAssessment``, ``MentionResolutionResult``, or
``NormalizationResult`` -- all are read-only inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.claim_generation.types import CandidateClaim, CandidateEntityReference
from app.confidence.aggregate_policy import (
    ALGORITHM_VERSION,
    CONFLICT_PENALTIES,
    ELIGIBLE_ENTITY_RESOLUTION_QUALITIES,
    EXPERIMENTAL_RELEVANCE_MODIFIERS,
    ORGANISM_RELEVANCE_MODIFIERS,
    REPLICATION_BONUS_PER_STEP,
    REPLICATION_BONUS_STEPS,
    SCORE_MAX,
    SCORE_MIN,
    WEIGHT_BASE,
    ConflictSeverity,
    ExperimentalRelevance,
    IneligibilityReason,
    OrganismRelevance,
    classify_confidence_class,
)
from app.confidence.aggregate_types import (
    AggregateClaimConfidence,
    ContributionBreakdown,
    EvidenceContribution,
)
from app.confidence.errors import IncompatibleClaimsError
from app.confidence.policy import OrganismAssessment, ScoringStatus
from app.confidence.types import SingleEvidenceAssessment
from app.extraction.types import Directness
from app.models.enums import EvidenceType

#: Directness values that report a *secondhand*, filtered account of
#: underlying evidence -- these never count toward the replication tally
#: merely because they are separate records (Increment 18 instructions,
#: Step 32): "use Directness/EvidenceType to prevent derivative sources
#: from receiving replication bonus merely because they are separate
#: records."
_DERIVATIVE_DIRECTNESS = frozenset({Directness.REVIEW_SUMMARIZES, Directness.DATABASE_ANNOTATES})

_ORGANISM_INELIGIBLE_ASSESSMENTS = frozenset(
    {
        OrganismAssessment.EXPLICIT_UNRESOLVED,
        OrganismAssessment.CONFLICTED,
        OrganismAssessment.SOURCE_FAILURE,
        OrganismAssessment.UNKNOWN,
    }
)


# --- Claim compatibility (Step 4) -------------------------------------------------


def _entity_ref_key(reference: CandidateEntityReference | None) -> object:
    """A hashable identity key for one entity reference, or ``None``.

    Two references are the "same entity" for compatibility purposes only
    if they agree on this key exactly: both resolved to the identical
    canonical UUID, or both unresolved with the identical exact text.
    Deliberately fails closed on a resolved/unresolved *mismatch* -- a
    resolved subject and an unresolved same-named subject are not treated
    as "clearly compatible" (Step 4: "Do not merge claims merely because
    they sound similar").
    """
    if reference is None:
        return None
    if reference.normalized_id is not None:
        return (reference.entity_kind, "ID", reference.normalized_id)
    return (reference.entity_kind, "TEXT", reference.original_text)


def _wildcard_compatible(
    a: CandidateEntityReference | None, b: CandidateEntityReference | None
) -> bool:
    """Organism/compartment compatibility: an absent reference is compatible with anything.

    "Not stated" is never treated as a mismatch (``.cursor/rules
    /01-scientific-integrity.mdc``: "Unknown Versus Negative Evidence") --
    only two *explicitly different* references are incompatible.
    """
    key_a, key_b = _entity_ref_key(a), _entity_ref_key(b)
    if key_a is None or key_b is None:
        return True
    return key_a == key_b


def _are_compatible(a: CandidateClaim, b: CandidateClaim) -> bool:
    """Whether two ``CandidateClaim``\\ s represent the same logical claim (Step 4).

    Inspects subject identity, predicate (exact text, never canonicalized
    -- Step 4 explicitly forbids that here), object identity/shape, literal-
    value shape, organism, compartment, and strain. Subject/object require
    an exact identity-key match (including a "both absent" match for
    object); organism/compartment/strain treat an absent value as
    compatible with anything, per ``_wildcard_compatible`` above.
    """
    if _entity_ref_key(a.subject) != _entity_ref_key(b.subject):
        return False
    if a.predicate != b.predicate:
        return False
    if _entity_ref_key(a.object) != _entity_ref_key(b.object):
        return False
    if (a.value_text is not None) != (b.value_text is not None):
        return False
    if not _wildcard_compatible(a.organism, b.organism):
        return False
    if not _wildcard_compatible(a.compartment, b.compartment):
        return False
    return not (a.strain is not None and b.strain is not None and a.strain != b.strain)


# --- Duplicate detection (Step 31) -------------------------------------------------


def _dedup_key(claim: CandidateClaim) -> tuple[object, ...]:
    """A deterministic duplicate-evidence identity key, from source grounding alone.

    Two ``EvidenceContribution``\\ s are the exact same evidence item iff
    their originating document, character offsets, and quoted text all
    agree -- never guessed from claim content alone (two independently
    reported, textually-similar claims from different passages are not
    duplicates).
    """
    span = claim.evidence_extraction.span
    return (
        claim.source,
        claim.source_identifier,
        span.character_start,
        span.character_end,
        span.quoted_text,
    )


# --- Organism / experimental relevance derivation (Steps 6-8) ---------------------


def _derive_organism_relevance(claim: CandidateClaim) -> OrganismRelevance:
    """Derive one claim's own organism-specificity tier -- see ``OrganismRelevance``'s docstring."""
    if claim.organism is None:
        return OrganismRelevance.UNKNOWN
    if claim.strain is not None and claim.organism.normalized_id is not None:
        return OrganismRelevance.SAME_STRAIN
    return OrganismRelevance.SAME_SPECIES


def _derive_experimental_relevance(assessment: SingleEvidenceAssessment) -> ExperimentalRelevance:
    """Derive one claim's own experimental-system tier (see ``ExperimentalRelevance``)."""
    if assessment.evidence_type is EvidenceType.COMPUTATIONAL:
        return ExperimentalRelevance.COMPUTATIONAL_ONLY
    return ExperimentalRelevance.UNKNOWN


def build_evidence_contribution(
    candidate_claim: CandidateClaim,
    single_evidence_assessment: SingleEvidenceAssessment,
    *,
    publication_identifier: str,
    source_identifier: str,
    experiment_identifier: str | None = None,
    independence_group: str | None = None,
    conflict_status: ConflictSeverity = ConflictSeverity.NONE,
) -> EvidenceContribution:
    """Build one ``EvidenceContribution``, deriving organism/experimental relevance.

    The recommended way to construct an ``EvidenceContribution`` -- the
    caller supplies only what genuinely cannot be derived from
    ``candidate_claim``/``single_evidence_assessment`` (publication/source
    identity, and optional experiment identity/independence override/
    conflict status from an external conflict-detection layer);
    ``organism_relevance``/``experimental_relevance`` are computed here,
    never left for a caller to guess.
    """
    return EvidenceContribution(
        candidate_claim=candidate_claim,
        single_evidence_assessment=single_evidence_assessment,
        publication_identifier=publication_identifier,
        source_identifier=source_identifier,
        experiment_identifier=experiment_identifier,
        organism_relevance=_derive_organism_relevance(candidate_claim),
        experimental_relevance=_derive_experimental_relevance(single_evidence_assessment),
        independence_group=independence_group,
        conflict_status=conflict_status,
    )


# --- Eligibility (Steps 17-21) ------------------------------------------------------


def _eligibility(assessment: SingleEvidenceAssessment) -> tuple[bool, tuple[str, ...]]:
    """Whether one ``SingleEvidenceAssessment`` may contribute numerically, and why not if not.

    Conservative binary gate, never a numeric penalty (Step 17: "Do not
    invent new numeric entity-resolution penalties ... without an approved
    policy"). ``RESOLVED``/``VERIFIED_NEW`` both count as sufficiently
    identified (Step 19 preserves Increment 14's own finding: unpersisted
    is not the same as uncertain); ``NOT_APPLICABLE`` (object/compartment
    not present as a typed entity at all) never blocks eligibility --
    there is nothing to resolve. Every other state (``AMBIGUOUS``/
    ``CONFLICTED``/``UNRESOLVED``/``NO_CANDIDATE``/``SOURCE_FAILURE``/
    ``UNSUPPORTED``/``UNKNOWN_KIND``) makes the contribution ineligible --
    never deleted, only excluded from numeric aggregation.
    """
    reasons: list[str] = []
    if assessment.scoring_status is ScoringStatus.UNSCORED_EVIDENCE_TYPE:
        reasons.append(IneligibilityReason.EVIDENCE_TYPE_UNSCORED.value)
    if assessment.subject_resolution.value not in ELIGIBLE_ENTITY_RESOLUTION_QUALITIES:
        reasons.append(IneligibilityReason.SUBJECT_NOT_RESOLVED.value)
    if (
        assessment.object_resolution is not None
        and assessment.object_resolution.value not in ELIGIBLE_ENTITY_RESOLUTION_QUALITIES
    ):
        reasons.append(IneligibilityReason.OBJECT_NOT_RESOLVED.value)
    if assessment.organism_resolution in _ORGANISM_INELIGIBLE_ASSESSMENTS:
        reasons.append(IneligibilityReason.ORGANISM_NOT_RESOLVED.value)
    if (
        assessment.compartment_resolution is not None
        and assessment.compartment_resolution.value not in ELIGIBLE_ENTITY_RESOLUTION_QUALITIES
    ):
        reasons.append(IneligibilityReason.COMPARTMENT_NOT_RESOLVED.value)
    return (len(reasons) == 0, tuple(reasons))


def _is_derivative(assessment: SingleEvidenceAssessment) -> bool:
    return assessment.directness in _DERIVATIVE_DIRECTNESS


def _effective_independence_group(contribution: EvidenceContribution) -> str:
    """The independence group actually used for replication counting (Steps 10-12).

    ``contribution.independence_group`` wins when explicitly supplied.
    Otherwise defaults to ``publication_identifier`` alone (Step 12: "treat
    same-paper evidence as one replication unit") unless
    ``experiment_identifier`` is also supplied, in which case it further
    subdivides by experiment within that same publication (Step 12's
    conservative allowance for genuinely known same-paper independent
    experiments).
    """
    if contribution.independence_group is not None:
        return contribution.independence_group
    if contribution.experiment_identifier is not None:
        return f"{contribution.publication_identifier}:{contribution.experiment_identifier}"
    return contribution.publication_identifier


def _adjusted_value(
    base_score: int,
    organism_relevance: OrganismRelevance,
    experimental_relevance: ExperimentalRelevance,
) -> Decimal:
    """``base_score x organism_relevance% x experimental_relevance%``, exact ``Decimal`` arithmetic.

    An ``UNKNOWN`` relevance tier is applied as a neutral 100% -- never a
    fabricated reduction (Steps 6/8: "do not invent" a modifier when data
    does not support one). Kept as an exact ``Decimal`` (Increment 18A) --
    not floor-divided to an ``int`` as Increment 18 did -- so the
    diminishing-return sum this feeds into (see ``_rank_eligible``) never
    compounds rounding error across several contributions; only the final
    aggregate is rounded, once, via ``_round_half_up``.
    """
    organism_pct = Decimal(ORGANISM_RELEVANCE_MODIFIERS.get(organism_relevance, 100))
    experimental_pct = Decimal(EXPERIMENTAL_RELEVANCE_MODIFIERS.get(experimental_relevance, 100))
    return Decimal(base_score) * organism_pct / Decimal(100) * experimental_pct / Decimal(100)


def _worst_conflict(severities: Sequence[ConflictSeverity]) -> ConflictSeverity:
    """The single most severe conflict signal, never stacked (Step 16).

    "Do not stack unlimited conflict penalties unless spec explicitly says
    to" -- it does not, so the worst (not the sum) of every eligible
    contribution's own ``conflict_status`` is applied exactly once.
    """
    if any(severity is ConflictSeverity.MAJOR for severity in severities):
        return ConflictSeverity.MAJOR
    if any(severity is ConflictSeverity.MINOR for severity in severities):
        return ConflictSeverity.MINOR
    return ConflictSeverity.NONE


def _round_half_up(value: Decimal) -> int:
    """Round a non-negative ``Decimal`` to the nearest int, ties rounding up.

    Deliberately not Python's default banker's rounding (round-half-to-
    even): this increment's own worked example (``67.5 + 5 - 10 = 62.5`` ->
    final score ``63``) requires ties to round away from zero.
    """
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class _PendingContribution:
    """Internal working record for one contribution, before rank/weight are known."""

    contribution: EvidenceContribution
    assessment: SingleEvidenceAssessment
    is_duplicate: bool
    eligible: bool
    ineligibility_reasons: tuple[str, ...]
    adjusted_score: Decimal | None
    independence_group: str
    counted_for_replication: bool


def _rank_eligible(
    pending: Sequence[_PendingContribution],
) -> dict[int, tuple[Decimal, Decimal, Decimal]]:
    """Rank eligible, non-duplicate contributions by adjusted score descending (Steps 2-3).

    Returns ``{pending_index: (weight, weighted_contribution, cumulative_score)}``
    for every eligible contribution -- ineligible/duplicate indices are
    simply absent. Ties (equal adjusted scores -- the common case, e.g.
    two independent reports of the same ``EvidenceType`` with no relevance
    modifiers) are broken by ``pending`` index, which is itself already a
    canonical, input-order-independent position (``pending`` is built from
    the already-sorted ``ordered`` sequence) -- so the same evidence set in
    any original input order always assigns the same weight to the same
    specific contribution (Step 30: order independence).
    """
    eligible_indices = [i for i, p in enumerate(pending) if p.eligible]
    ranked = sorted(eligible_indices, key=lambda i: (-pending[i].adjusted_score, i))
    result: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
    weight = Decimal(1)
    cumulative = Decimal(0)
    for index in ranked:
        adjusted = pending[index].adjusted_score
        assert adjusted is not None  # eligible guarantees this
        weighted = adjusted * weight
        cumulative += weighted
        result[index] = (weight, weighted, cumulative)
        weight *= WEIGHT_BASE
    return result


def _build_explanation(
    *,
    evidence_count: int,
    eligible_count: int,
    ranked_adjusted: list[Decimal],
    ranked_weighted: list[Decimal],
    aggregate_evidence: Decimal | None,
    replication_bonus: int,
    conflict_penalty: int,
    score: int | None,
    confidence_class_value: str,
    algorithm_version: str,
) -> str:
    lines = [
        f"{evidence_count} evidence contribution(s).",
        f"{eligible_count} numerically eligible.",
    ]
    if aggregate_evidence is not None:
        lines += ["", "Adjusted scores:", "", *[str(value) for value in ranked_adjusted]]
        lines += ["", "Weighted contributions:", "", *[str(value) for value in ranked_weighted]]
        lines += ["", "Aggregate evidence:", str(aggregate_evidence)]
    else:
        lines.append("No numerically eligible evidence was available.")
    if replication_bonus:
        lines += ["", "Replication bonus:", f"+{replication_bonus}"]
    if conflict_penalty:
        lines += ["", "Conflict penalty:", f"-{conflict_penalty}"]
    lines.append("")
    if score is not None:
        lines += ["Final score:", str(score), "", "Confidence class:", confidence_class_value]
    else:
        lines.append("Final score: UNKNOWN")
    lines += ["", "Algorithm:", algorithm_version]
    return "\n".join(lines)


def aggregate_claim_confidence(
    contributions: Sequence[EvidenceContribution],
) -> AggregateClaimConfidence:
    """Aggregate multiple ``EvidenceContribution``\\ s for one logical claim.

    Raises ``TypeError`` for malformed input, ``ValueError`` for an empty
    sequence (there is nothing to aggregate), and ``IncompatibleClaimsError``
    if any two contributions' ``candidate_claim``\\ s are not clearly the
    same logical claim (§ ``_are_compatible``). Otherwise always returns an
    ``AggregateClaimConfidence`` -- never raises merely because no
    contribution was numerically eligible (``score=None``/
    ``ConfidenceClass.UNKNOWN``, never an exception). Heterogeneous
    eligible evidence no longer blocks a final score (Increment 18A) -- see
    module docstring for the canonical diminishing-return weighting
    algorithm.

    Order-independent: the same contribution set in any order produces an
    identical result (contributions are canonically sorted internally
    before ranking/weighting and before ``contribution_breakdown`` is
    built).
    """
    if not isinstance(contributions, Sequence) or isinstance(contributions, (str, bytes)):
        raise TypeError(
            f"aggregate_claim_confidence requires a sequence of EvidenceContribution, "
            f"got {contributions!r}"
        )
    if len(contributions) == 0:
        raise ValueError("aggregate_claim_confidence requires at least one EvidenceContribution")
    for contribution in contributions:
        if not isinstance(contribution, EvidenceContribution):
            raise TypeError(f"every item must be an EvidenceContribution, got {contribution!r}")

    reference_claim = contributions[0].candidate_claim
    for contribution in contributions[1:]:
        if not _are_compatible(reference_claim, contribution.candidate_claim):
            raise IncompatibleClaimsError(
                "Supplied EvidenceContributions do not clearly represent the same logical claim "
                "(subject/predicate/object/value-shape/organism/compartment/strain must agree): "
                f"{contribution.source_identifier!r} is not compatible with "
                f"{contributions[0].source_identifier!r}"
            )

    ordered = sorted(
        contributions,
        key=lambda c: (
            c.candidate_claim.source.value,
            c.candidate_claim.source_identifier,
            c.candidate_claim.evidence_extraction.span.character_start,
            c.candidate_claim.evidence_extraction.span.character_end,
            c.source_identifier,
        ),
    )

    seen_dedup_keys: set[tuple[object, ...]] = set()
    pending: list[_PendingContribution] = []
    independence_groups_for_replication: set[str] = set()
    conflict_severities: list[ConflictSeverity] = []

    for contribution in ordered:
        dedup_key = _dedup_key(contribution.candidate_claim)
        is_duplicate = dedup_key in seen_dedup_keys
        if not is_duplicate:
            seen_dedup_keys.add(dedup_key)

        assessment = contribution.single_evidence_assessment
        eligible, reasons = _eligibility(assessment)
        if is_duplicate:
            eligible = False
            reasons = (*reasons, IneligibilityReason.DUPLICATE_EVIDENCE.value)

        adjusted_score: Decimal | None = None
        if eligible:
            assert assessment.evidence_base_score is not None  # SCORED_BASE guarantees this
            adjusted_score = _adjusted_value(
                assessment.evidence_base_score,
                contribution.organism_relevance,
                contribution.experimental_relevance,
            )

        group = _effective_independence_group(contribution)
        counted_for_replication = eligible and not _is_derivative(assessment)
        if counted_for_replication:
            independence_groups_for_replication.add(group)
        if eligible:
            conflict_severities.append(contribution.conflict_status)

        pending.append(
            _PendingContribution(
                contribution=contribution,
                assessment=assessment,
                is_duplicate=is_duplicate,
                eligible=eligible,
                ineligibility_reasons=reasons,
                adjusted_score=adjusted_score,
                independence_group=group,
                counted_for_replication=counted_for_replication,
            )
        )

    weighting = _rank_eligible(pending)
    aggregate_evidence = max((cumulative for _, _, cumulative in weighting.values()), default=None)

    breakdown: list[ContributionBreakdown] = []
    for index, entry in enumerate(pending):
        weight, weighted_contribution, cumulative_score = weighting.get(index, (None, None, None))
        breakdown.append(
            ContributionBreakdown(
                source_identifier=entry.contribution.source_identifier,
                publication_identifier=entry.contribution.publication_identifier,
                evidence_type=entry.assessment.evidence_type,
                evidence_base_score=entry.assessment.evidence_base_score,
                organism_relevance=entry.contribution.organism_relevance,
                experimental_relevance=entry.contribution.experimental_relevance,
                is_duplicate=entry.is_duplicate,
                numerically_eligible=entry.eligible,
                ineligibility_reasons=entry.ineligibility_reasons,
                independence_group=entry.independence_group,
                counted_for_replication=entry.counted_for_replication,
                conflict_status=entry.contribution.conflict_status,
                adjusted_contribution=entry.adjusted_score,
                weight=weight,
                weighted_contribution=weighted_contribution,
                cumulative_score=cumulative_score,
                reason_codes=entry.assessment.reason_codes,
            )
        )

    reason_codes: list[str] = []
    eligible_count = sum(1 for entry in pending if entry.eligible)
    base_evidence_total: int | None
    if aggregate_evidence is None:
        base_evidence_total = None
        reason_codes.append("AGGREGATE_NO_ELIGIBLE_EVIDENCE")
    else:
        base_evidence_total = max(SCORE_MIN, min(SCORE_MAX, _round_half_up(aggregate_evidence)))
        reason_codes.append("AGGREGATE_BASE_EVIDENCE_APPLIED")

    independent_count = len(independence_groups_for_replication)
    replication_bonus = 0
    if independent_count > 1:
        steps = min(independent_count - 1, REPLICATION_BONUS_STEPS)
        replication_bonus = steps * REPLICATION_BONUS_PER_STEP
        reason_codes.append(f"REPLICATION_BONUS_{replication_bonus}")

    worst_conflict = _worst_conflict(conflict_severities)
    conflict_penalty = CONFLICT_PENALTIES.get(worst_conflict, 0)
    if conflict_penalty:
        reason_codes.append(f"CONFLICT_PENALTY_{worst_conflict.value}")

    score: int | None = None
    if base_evidence_total is not None:
        raw = base_evidence_total + replication_bonus - conflict_penalty
        score = max(SCORE_MIN, min(SCORE_MAX, raw))

    confidence_class = classify_confidence_class(score)

    dependent_count = max(0, eligible_count - independent_count)

    organism_modifier_summary = tuple(
        sorted(
            {entry.organism_relevance.value for entry in breakdown if entry.numerically_eligible}
        )
    )
    experimental_modifier_summary = tuple(
        sorted(
            {
                entry.experimental_relevance.value
                for entry in breakdown
                if entry.numerically_eligible
            }
        )
    )

    ranked_by_weight_desc = sorted(weighting.items(), key=lambda kv: -kv[1][0])
    ranked_adjusted = [pending[index].adjusted_score for index, _ in ranked_by_weight_desc]
    ranked_weighted = [weighted for _, (_, weighted, _) in ranked_by_weight_desc]

    explanation = _build_explanation(
        evidence_count=len(contributions),
        eligible_count=eligible_count,
        ranked_adjusted=ranked_adjusted,
        ranked_weighted=ranked_weighted,
        aggregate_evidence=aggregate_evidence,
        replication_bonus=replication_bonus,
        conflict_penalty=conflict_penalty,
        score=score,
        confidence_class_value=confidence_class.value,
        algorithm_version=ALGORITHM_VERSION,
    )

    return AggregateClaimConfidence(
        score=score,
        confidence_class=confidence_class,
        base_evidence_total=base_evidence_total,
        replication_bonus=replication_bonus,
        conflict_penalty=conflict_penalty,
        organism_modifier_summary=organism_modifier_summary,
        experimental_modifier_summary=experimental_modifier_summary,
        evidence_count=len(contributions),
        independent_evidence_count=independent_count,
        dependent_evidence_count=dependent_count,
        algorithm_version=ALGORITHM_VERSION,
        reason_codes=tuple(reason_codes),
        contribution_breakdown=tuple(breakdown),
        explanation=explanation,
    )


__all__ = ["aggregate_claim_confidence", "build_evidence_contribution"]
