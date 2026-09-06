"""Centralized policy for Multi-Evidence Claim Confidence Aggregation (Increment 18/18A).

This is the layer that finally produces a final 0-100 ``Claim`` confidence
score and ``ConfidenceClass`` -- Increment 17's per-evidence
``SingleEvidenceAssessment`` deliberately never did (see
``docs/13_single_evidence_confidence_contract.md``).

**Authoritative source**: ``docs/03_agent_behavior.md``'s "Confidence
Scoring Behavior" / "Multiple Evidence Sources" / "Confidence Classes"
sections. Every authoritative number below is transcribed verbatim;
every ``# PROVISIONAL``-marked policy is this increment's own disclosed,
conservative, deterministic-data-only choice, made because the
authoritative text does not specify one at all -- never invented as if it
were authoritative. See ``docs/14_multi_evidence_confidence_contract.md``
for the complete rationale behind every one of these.

**Increment 18A: the multi-evidence base-combination gap is now resolved.**
Increment 18 found that the authoritative specification never says how
multiple *differing* evidence base scores combine (only that they must not
simply be summed), and declined to invent one -- contributions that
disagreed produced ``score=None``/``UNKNOWN``. Increment 18A establishes
the canonical combination rule: a descending-sorted, exponentially-
diminishing-weight sum (see ``ALGORITHM_VERSION``/``WEIGHT_BASE`` below,
and ``app.confidence.aggregation``'s own docstring for the full algorithm
and its scientific justification). ``UNKNOWN`` is now reserved for "no
numerically eligible evidence exists at all" only.

**A genuine specification tension, disclosed rather than silently
resolved**: ``docs/03_agent_behavior.md``'s own "Measurement Confidence vs
Model Applicability" section states organism/strain/temperature/pH/
substrate/"physiological environment" are *Model Applicability* factors,
and that "these two scores must never be conflated" with Measurement/
Claim confidence. The very same document's "Confidence Scoring Behavior"
section nonetheless uses organism relevance and "experimental relevance"
(cell lysate / purified enzyme / recombinant / heterologous / computational)
as direct multipliers on confidence score. This increment implements the
"Confidence Scoring Behavior" formula literally, as this increment's own
instructions explicitly and repeatedly direct (Steps 6-8, 22) -- but this
tension is not resolved unilaterally here; it is reported prominently in
the contract document and completion report for a human decision.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from app.confidence.policy import (
    EVIDENCE_TYPE_BASE_SCORES,
    EVIDENCE_TYPE_UNSCORED,
)
from app.models.enums import ConfidenceClass

# --- Authoritative: docs/03_agent_behavior.md "Confidence Scoring Behavior" ------


class OrganismRelevance(StrEnum):
    """How specifically one evidence item's own organism/strain context is stated.

    "Evidence-specific" (Increment 18 instructions, Step 6's own header) --
    an intrinsic property of *one* ``CandidateClaim``, not a pairwise
    comparison against other evidence in the aggregate group. Only
    ``SAME_STRAIN``/``SAME_SPECIES``/``UNKNOWN`` are ever produced by this
    repository's actual derivation logic (``app.confidence.aggregation
    ._derive_organism_relevance``) -- ``SAME_GENUS``/``OTHER_FUNGUS``/
    ``OTHER_EUKARYOTE``/``BACTERIUM`` are listed here only because
    ``docs/03_agent_behavior.md`` names them authoritatively; producing
    any of them would require taxonomic-distance data (genus/kingdom/
    evolutionary distance) that exists nowhere in this repository (see
    ``docs/12_entity_resolution_architecture.md`` §11/§21) -- never
    invented. Also structurally moot within one aggregate group: claim
    compatibility (Step 4) already requires every contribution's organism
    to agree (or be unstated), so two *different* organisms never coexist
    in one aggregation to begin with.
    """

    SAME_STRAIN = "SAME_STRAIN"
    SAME_SPECIES = "SAME_SPECIES"
    SAME_GENUS = "SAME_GENUS"  # authoritative name; never produced today -- see class docstring
    OTHER_FUNGUS = "OTHER_FUNGUS"  # authoritative name; never produced today
    OTHER_EUKARYOTE = "OTHER_EUKARYOTE"  # authoritative name; never produced today
    BACTERIUM = "BACTERIUM"  # authoritative name; never produced today
    UNKNOWN = "UNKNOWN"


#: Authoritative, transcribed verbatim (as an integer percentage of the
#: documented decimal, e.g. 0.95 -> 95, for the same deterministic-integer-
#: arithmetic reasons Increment 17 adopted). No entry for ``UNKNOWN`` --
#: an unknown organism relevance is applied as a neutral 100% (no
#: reduction), never a fabricated number (see
#: ``app.confidence.aggregation``'s use of this table).
ORGANISM_RELEVANCE_MODIFIERS: dict[OrganismRelevance, int] = {
    OrganismRelevance.SAME_STRAIN: 100,
    OrganismRelevance.SAME_SPECIES: 95,
    OrganismRelevance.SAME_GENUS: 70,
    OrganismRelevance.OTHER_FUNGUS: 55,
    OrganismRelevance.OTHER_EUKARYOTE: 40,
    OrganismRelevance.BACTERIUM: 25,
}


class ExperimentalRelevance(StrEnum):
    """How the experimental system of one evidence item is classified.

    Only ``COMPUTATIONAL_ONLY``/``UNKNOWN`` are ever produced by this
    repository's actual derivation logic (``app.confidence.aggregation
    ._derive_experimental_relevance``): ``EvidenceType.COMPUTATIONAL`` is
    the one case where the experimental system is *already* unambiguously
    known from a controlled, already-validated field -- no free-text
    classification is needed. Every other authoritative tier
    (``PHYSIOLOGICAL_IN_VIVO``/``CELL_LYSATE``/``PURIFIED_NATIVE_ENZYME``/
    ``RECOMBINANT_ENZYME``/``HETEROLOGOUS_EXPRESSION``) would require
    classifying ``EvidenceExtraction.experimental_system``/``assay`` --
    both plain free text with no controlled vocabulary
    (``docs/09_evidence_extraction_contract.md``) -- which this increment's
    own instructions explicitly forbid inventing ("Do not invent
    classification from vague prose", Step 8). ``EvidenceType
    .DIRECT_IN_VIVO`` was considered and deliberately rejected as a
    deterministic proxy for ``PHYSIOLOGICAL_IN_VIVO``: "in vivo" alone does
    not confirm a *physiological* (unperturbed, native-context) system --
    a heterologous-expression assay can also be reported as "in vivo".
    """

    PHYSIOLOGICAL_IN_VIVO = "PHYSIOLOGICAL_IN_VIVO"  # authoritative name; never produced today
    CELL_LYSATE = "CELL_LYSATE"  # authoritative name; never produced today
    PURIFIED_NATIVE_ENZYME = "PURIFIED_NATIVE_ENZYME"  # authoritative name; never produced today
    RECOMBINANT_ENZYME = "RECOMBINANT_ENZYME"  # authoritative name; never produced today
    HETEROLOGOUS_EXPRESSION = "HETEROLOGOUS_EXPRESSION"  # authoritative name; never produced today
    COMPUTATIONAL_ONLY = "COMPUTATIONAL_ONLY"
    UNKNOWN = "UNKNOWN"


#: Authoritative, transcribed verbatim (percent form). No entry for
#: ``UNKNOWN`` -- applied as neutral 100%, never fabricated.
EXPERIMENTAL_RELEVANCE_MODIFIERS: dict[ExperimentalRelevance, int] = {
    ExperimentalRelevance.PHYSIOLOGICAL_IN_VIVO: 100,
    ExperimentalRelevance.CELL_LYSATE: 90,
    ExperimentalRelevance.PURIFIED_NATIVE_ENZYME: 90,
    ExperimentalRelevance.RECOMBINANT_ENZYME: 80,
    ExperimentalRelevance.HETEROLOGOUS_EXPRESSION: 70,
    ExperimentalRelevance.COMPUTATIONAL_ONLY: 40,
}

#: Authoritative, transcribed verbatim ("second independent source +5,
#: third independent source +5", "Maximum replication bonus: +10").
REPLICATION_BONUS_PER_STEP = 5
REPLICATION_BONUS_MAX = 10
#: How many *additional* independent, non-derivative sources (beyond the
#: first) earn a bonus step, before the cap takes over.
REPLICATION_BONUS_STEPS = REPLICATION_BONUS_MAX // REPLICATION_BONUS_PER_STEP


class ConflictSeverity(StrEnum):
    """Explicit, caller-supplied conflict severity for one evidence contribution.

    Increment 18 never *discovers* conflicts from claim text or predicate
    comparison (instructions, Step 15: "must not discover scientific
    conflicts from raw claims") -- this is a plain, closed classification
    a conflict-detection layer (not built in this increment) is expected
    to supply. ``UNKNOWN`` means "conflict status was not determined" --
    distinct from ``NONE`` ("determined: no conflict") -- and, like every
    other UNKNOWN state in this codebase, is never treated as though it
    were a known penalty-worthy state.
    """

    NONE = "NONE"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    UNKNOWN = "UNKNOWN"


#: Authoritative, transcribed verbatim ("minor unresolved conflict -10",
#: "major unresolved conflict -25"). No entry for ``NONE``/``UNKNOWN`` --
#: both apply a zero penalty (never fabricated); see
#: ``app.confidence.aggregation`` for exactly how multiple simultaneous
#: conflict signals are combined (the single most severe one, not summed).
CONFLICT_PENALTIES: dict[ConflictSeverity, int] = {
    ConflictSeverity.MINOR: 10,
    ConflictSeverity.MAJOR: 25,
}

#: Authoritative, transcribed verbatim ("Confidence Classes").
CONFIDENCE_CLASS_THRESHOLDS: tuple[tuple[int, ConfidenceClass], ...] = (
    (90, ConfidenceClass.VERY_HIGH),
    (75, ConfidenceClass.HIGH),
    (50, ConfidenceClass.MODERATE),
    (0, ConfidenceClass.LOW),
)

SCORE_MIN = 0
SCORE_MAX = 100

#: The canonical aggregation-mathematics algorithm identifier (Increment
#: 18A). Recorded on every ``AggregateClaimConfidence`` so a future
#: revision of the weighting formula can be distinguished from results
#: produced by this one. This exact string must remain constant for this
#: algorithm -- a genuine formula change requires a new version string,
#: never a silent redefinition of ``"confidence-v1"`` itself.
ALGORITHM_VERSION = "confidence-v1"

#: The diminishing-return weight base (Increment 18A, Step 3): the
#: ``k``-th strongest numerically eligible contribution (1-indexed, after
#: sorting adjusted scores descending) is weighted ``WEIGHT_BASE ** (k - 1)``
#: -- 1, 1/2, 1/4, 1/8, .... Never a numeric cap in itself: with today's
#: authoritative base-score ceiling (45, ``DIRECT_BIOCHEMICAL``) and a
#: weight sequence summing to strictly less than 2 for any finite
#: contribution count, the weighted sum itself can never reach 100 even
#: before replication bonus/clamping -- see
#: ``app.confidence.aggregation``'s module docstring for the full proof.
WEIGHT_BASE = Decimal("0.5")


def classify_confidence_class(score: int | None) -> ConfidenceClass:
    """Map a clamped 0-100 aggregate score (or ``None``) to its ``ConfidenceClass``.

    ``None`` -> ``UNKNOWN`` always (docs/03: "NULL -> UNKNOWN") -- never a
    fabricated midpoint class. This is the *only* place in this codebase
    that maps a score to a class today -- Increment 17 deliberately
    removed its own single-evidence version of this function (single
    evidence never produces a final score to classify).
    """
    if score is None:
        return ConfidenceClass.UNKNOWN
    for lower_bound, confidence_class in CONFIDENCE_CLASS_THRESHOLDS:
        if score >= lower_bound:
            return confidence_class
    return ConfidenceClass.LOW  # unreachable given SCORE_MIN == 0, kept for exhaustiveness


class IneligibilityReason(StrEnum):
    """Why one ``EvidenceContribution`` was excluded from numeric aggregation.

    Coarse, aggregate-specific reasons -- the fine-grained *why* (e.g.
    ``SUBJECT_AMBIGUOUS``) remains available on
    ``ContributionBreakdown.reason_codes``, inherited unchanged from the
    underlying ``SingleEvidenceAssessment``. Ineligible contributions are
    never deleted -- they remain fully visible in ``contribution_breakdown``
    (Increment 18 instructions, Step 18: "Do not delete ineligible
    evidence").
    """

    EVIDENCE_TYPE_UNSCORED = "EVIDENCE_TYPE_UNSCORED"
    SUBJECT_NOT_RESOLVED = "SUBJECT_NOT_RESOLVED"
    OBJECT_NOT_RESOLVED = "OBJECT_NOT_RESOLVED"
    ORGANISM_NOT_RESOLVED = "ORGANISM_NOT_RESOLVED"
    COMPARTMENT_NOT_RESOLVED = "COMPARTMENT_NOT_RESOLVED"
    DUPLICATE_EVIDENCE = "DUPLICATE_EVIDENCE"


#: PROVISIONAL entity-resolution eligibility policy (Increment 18
#: instructions, Step 17-19; no authoritative numeric entity-resolution
#: rule exists anywhere, so no numeric cap/penalty is invented -- this is
#: a binary eligibility gate only). ``RESOLVED`` and ``VERIFIED_NEW`` are
#: both treated as "sufficiently identified" for numeric aggregation --
#: preserving Increment 14/17's own finding that "not yet persisted" must
#: never be conflated with "identity uncertain" (Step 19). Every other
#: ``EntityResolutionQuality`` value (``AMBIGUOUS``/``CONFLICTED``/
#: ``UNRESOLVED``/``NO_CANDIDATE``/``SOURCE_FAILURE``/``UNSUPPORTED``/
#: ``UNKNOWN_KIND``/``NOT_APPLICABLE``... wait, ``NOT_APPLICABLE`` is
#: itself eligible, since it means the role does not apply to this claim
#: at all, not that identity is uncertain) is ineligible.
ELIGIBLE_ENTITY_RESOLUTION_QUALITIES = frozenset({"RESOLVED", "VERIFIED_NEW", "NOT_APPLICABLE"})

__all__ = [
    "ALGORITHM_VERSION",
    "CONFIDENCE_CLASS_THRESHOLDS",
    "CONFLICT_PENALTIES",
    "ELIGIBLE_ENTITY_RESOLUTION_QUALITIES",
    "EVIDENCE_TYPE_BASE_SCORES",
    "EVIDENCE_TYPE_UNSCORED",
    "EXPERIMENTAL_RELEVANCE_MODIFIERS",
    "ORGANISM_RELEVANCE_MODIFIERS",
    "REPLICATION_BONUS_MAX",
    "REPLICATION_BONUS_PER_STEP",
    "REPLICATION_BONUS_STEPS",
    "SCORE_MAX",
    "SCORE_MIN",
    "WEIGHT_BASE",
    "ConflictSeverity",
    "ExperimentalRelevance",
    "IneligibilityReason",
    "OrganismRelevance",
    "classify_confidence_class",
]
