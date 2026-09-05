"""Centralized policy for Single-Evidence Assessment (Increment 17).

**Increment 17 does not compute final Claim confidence.** It produces a
deterministic, auditable *assessment* of one `CandidateClaim` + one
`EvidenceExtraction`, for a future multi-evidence aggregator (Increment 18)
to consume. See ``docs/13_single_evidence_confidence_contract.md`` for the
full contract and the reasoning behind this split.

**Why this split exists.** ``docs/03_agent_behavior.md``'s "Confidence
Scoring Behavior" table is an *aggregate*-claim specification: its formula
(``base_score x organism_relevance x experimental_relevance +
replication_bonus - conflict_penalty``) requires a replication bonus to
reach anywhere near its own 90-100 VERY_HIGH band, and its own maximum
single-evidence base score (45, "Direct biochemical evidence") sits below
its own MODERATE floor (50). A single evidence item can never meaningfully
produce a final 0-100 score or `ConfidenceClass` under that specification
on its own -- attempting to force one (as an earlier revision of this
increment did) required inventing unspecified numeric Directness/organism/
entity-resolution modifiers with no authoritative source anywhere in this
repository. This revision removes all of that: Increment 17 records
*categorical* state only, and defers every numeric aggregate judgment to
Increment 18.

**What remains authoritative here, unchanged from ``docs/03_agent_behavior
.md``:** the ``EvidenceType`` base-score table (``EVIDENCE_TYPE_BASE_SCORES``)
and which ``EvidenceType`` values it does not cover
(``EVIDENCE_TYPE_UNSCORED``). Both are transcribed verbatim, exactly as
before. Nothing about ``Directness``, organism relevance, or entity
identity resolution has ever had an authoritative numeric source in this
repository -- those are represented here as plain categorical enums with
no attached numbers at all.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import EvidenceType

# --- Authoritative: docs/03_agent_behavior.md "Confidence Scoring Behavior" ------

#: Base evidence-type score, 0-100 -- ``CandidateClaim.evidence_type``'s
#: authoritative starting point, *not* a final Claim confidence value.
#: Absence of a key means "this EvidenceType has no authoritative score" --
#: never fall back to a guess (see ``EVIDENCE_TYPE_UNSCORED``).
EVIDENCE_TYPE_BASE_SCORES: dict[EvidenceType, int] = {
    EvidenceType.DIRECT_BIOCHEMICAL: 45,
    EvidenceType.DIRECT_IN_VIVO: 40,
    EvidenceType.GENETIC: 25,
    EvidenceType.CURATED_DATABASE: 20,  # conservative: "General curated biochemical DB"
    EvidenceType.COMPUTATIONAL: 10,
    EvidenceType.HOMOLOGY: 5,
    EvidenceType.AUTHOR_HYPOTHESIS: 0,  # "LLM inference" bucket -- see docs/13 §6
}

#: EvidenceType members with no authoritative score at all. Recorded
#: explicitly (rather than computed as a set difference at import time) so
#: this list is visible at a glance and can be asserted against directly
#: in tests without re-deriving it.
EVIDENCE_TYPE_UNSCORED: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.LOCALIZATION,
        EvidenceType.PROTEOMICS,
        EvidenceType.METABOLOMICS,
        EvidenceType.FLUXOMICS,
        EvidenceType.TRANSCRIPTOMICS,
        EvidenceType.STRUCTURAL,
        EvidenceType.REVIEW,
        EvidenceType.OTHER,
    }
)

EVIDENCE_SCORE_MIN = 0
EVIDENCE_SCORE_MAX = 100


class ScoringStatus(StrEnum):
    """Whether an authoritative ``evidence_base_score`` exists for this claim's ``EvidenceType``.

    Deliberately not a ``ConfidenceClass`` (or any judgment about the
    claim's overall quality) -- this only reports whether Step 3's table
    lookup succeeded, so Increment 18 knows whether it has a real base
    score to build on for this dimension.
    """

    SCORED_BASE = "SCORED_BASE"
    UNSCORED_EVIDENCE_TYPE = "UNSCORED_EVIDENCE_TYPE"


class EntityRole(StrEnum):
    """Which role of ``CandidateClaim`` an entity-resolution assessment is for."""

    SUBJECT = "SUBJECT"
    OBJECT = "OBJECT"
    ORGANISM = "ORGANISM"
    COMPARTMENT = "COMPARTMENT"


class EntityResolutionQuality(StrEnum):
    """The categorical identity-resolution state for one entity role.

    Unifies the two status vocabularies a ``CandidateEntityReference`` may
    carry -- ``app.normalization.types.NormalizationStatus`` (direct-
    normalization path) and ``app.entity_resolution.types
    .MentionResolutionStatus`` (Entity-Resolution path, Increment 16) --
    into one small, closed, purely categorical set. **No numeric weight,
    cap, or penalty is attached to any value here** -- that judgment
    belongs entirely to a future aggregation increment (Increment 18),
    which can map these states to whatever numeric policy is eventually
    specified.

    * ``RESOLVED`` -- a canonical entity was matched (``NormalizationStatus
      .MATCHED`` or ``MentionResolutionStatus.RESOLVED``), via either the
      direct-normalization or Entity-Resolution path -- both are the same
      scientific outcome, so both map to this one value rather than two.
    * ``VERIFIED_NEW`` -- a single, verified, unconflicted external record
      was found with no existing canonical row (``NormalizationStatus.NEW``,
      or ``MentionResolutionStatus.NEW_CANDIDATE`` with exactly one
      candidate). Explicitly **not** the same as ``AMBIGUOUS`` (Increment 14's
      own finding, preserved here): "not yet persisted" is a different
      scientific state than "identity itself is uncertain".
    * ``AMBIGUOUS`` -- includes a ``MentionResolutionStatus.NEW_CANDIDATE``
      outcome with *more than one* distinct verified candidate: genuine
      uncertainty about which record is canonical, not a clean
      ``VERIFIED_NEW``.
    * ``CONFLICTED`` -- an active identity conflict (a supplied identifier
      already belongs to a different existing entity, or different
      candidates resolved to different existing entities).
    * ``UNRESOLVED`` -- resolution was attempted (or could not even be
      attempted due to missing prerequisite context) and no verified
      candidate emerged.
    * ``NO_CANDIDATE`` -- an Entity-Resolution search was performed and
      returned zero candidates. Not evidence the entity does not exist.
    * ``SOURCE_FAILURE`` -- an external connector call itself failed.
      Infrastructure/retrieval limitation, never scientific counter-evidence.
    * ``UNSUPPORTED`` -- no resolution capability exists for this kind at
      all: either Entity Resolution reported
      ``MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND``, or the
      direct-normalization path had no ``Lookup`` configured for this kind
      (``CandidateEntityReference.normalization_result is None``) -- both
      mean "resolution was never even attempted because no capability
      exists", the same scientific position for a different reason.
    * ``UNKNOWN_KIND`` -- (subject only) the mention's ``EntityKind`` was
      never typed at all (still ``EntityKind.UNKNOWN``).
    * ``NOT_APPLICABLE`` -- (object/compartment only) a reference exists
      but was never typed as an entity at all -- the claim does not
      semantically depend on it as an entity.
    """

    RESOLVED = "RESOLVED"
    VERIFIED_NEW = "VERIFIED_NEW"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"
    UNRESOLVED = "UNRESOLVED"
    NO_CANDIDATE = "NO_CANDIDATE"
    SOURCE_FAILURE = "SOURCE_FAILURE"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN_KIND = "UNKNOWN_KIND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OrganismAssessment(StrEnum):
    """The categorical organism-context state for one ``CandidateClaim``.

    A separate, coarser vocabulary from ``EntityResolutionQuality``
    (Step 5 of this increment's instructions) -- organism context is
    represented only at the granularity single-evidence scope can actually
    distinguish today, with no taxonomic-distance reasoning (no genus/
    kingdom/evolutionary-distance data exists anywhere in this repository;
    see ``docs/12_entity_resolution_architecture.md`` §11/§21).

    * ``NOT_STATED`` -- ``CandidateClaim.organism`` is ``None``: no organism
      text was ever reported. Not a penalty-worthy state -- absence of a
      stated organism is not evidence of anything
      (``.cursor/rules/01-scientific-integrity.mdc``: "Unknown Versus
      Negative Evidence").
    * ``EXPLICIT_RESOLVED`` -- an organism was stated and its
      ``normalized_id`` is set (a canonical organism was matched).
    * ``EXPLICIT_UNRESOLVED`` -- an organism was stated but no canonical
      match was established (``AMBIGUOUS``/``VERIFIED_NEW``/``UNRESOLVED``/
      ``NO_CANDIDATE``/``UNSUPPORTED`` in ``EntityResolutionQuality`` terms).
    * ``CONFLICTED`` -- an organism was stated and its resolution was an
      active identity conflict.
    * ``SOURCE_FAILURE`` -- an organism was stated and its Entity-Resolution
      lookup itself failed (infrastructure limitation).
    * ``UNKNOWN`` -- a defensive fallback for a state this scorer cannot
      otherwise classify; never used as a substitute for one of the above
      when the input data actually determines one of them.
    """

    NOT_STATED = "NOT_STATED"
    EXPLICIT_RESOLVED = "EXPLICIT_RESOLVED"
    EXPLICIT_UNRESOLVED = "EXPLICIT_UNRESOLVED"
    CONFLICTED = "CONFLICTED"
    SOURCE_FAILURE = "SOURCE_FAILURE"
    UNKNOWN = "UNKNOWN"


__all__ = [
    "EVIDENCE_SCORE_MAX",
    "EVIDENCE_SCORE_MIN",
    "EVIDENCE_TYPE_BASE_SCORES",
    "EVIDENCE_TYPE_UNSCORED",
    "EntityResolutionQuality",
    "EntityRole",
    "OrganismAssessment",
    "ScoringStatus",
]
