"""The Knowledge Gap Detection data contract (Increment 21).

Four types:

1. ``GapType`` -- a controlled, local enum of only the gap categories this
   package actually detects (§ ``app.knowledge_gaps.rules`` for exactly
   which). Several categories the increment's own request listed
   (``UNRESOLVED_ENTITY``, a generic ``ISOLATED_ENTITY``) are deliberately
   **not** members here -- see ``docs/17_knowledge_gap_detection_contract.md``
   for why each was found undetectable from persisted data (or redundant)
   rather than silently guessed at.
2. ``GapSeverity`` -- a small categorical enum. No authoritative severity
   specification exists anywhere in this repository (unlike, say,
   ``ConfidenceClass``'s 0-100 thresholds) -- every ``GapType -> GapSeverity``
   mapping in ``app.knowledge_gaps.rules.GAP_SEVERITY`` is this package's own
   documented policy, not a transcription of an authoritative source. This is
   disclosed, not hidden.
3. ``KnowledgeGapCandidate`` -- one immutable, in-memory finding. Never
   persisted by this package (Increment 21 performs no writes of any kind)
   and never carries a ``KnowledgeGap`` row id, since none has been created.
4. ``KnowledgeGapAnalysisResult`` -- the complete, deterministic output of
   one ``app.knowledge_gaps.analysis.analyze_knowledge_gaps`` call.

Both dataclasses are frozen and validate themselves in ``__post_init__``,
raising ``app.knowledge_gaps.errors.InvalidGapCandidateError`` (this
package's own explicit exception, per Increment 21 instructions) rather
than the generic ``TypeError``/``ValueError`` most other data-contract
modules in this repository use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from app.knowledge_gaps.errors import InvalidGapCandidateError
from app.knowledge_gaps.validation import (
    clean_optional,
    require_non_blank_string_tuple,
    require_non_empty_str,
    require_uuid_tuple,
)


class GapType(StrEnum):
    """Only the gap categories this package actually detects.

    See ``app.knowledge_gaps.rules`` for the exact detection rule behind
    each member, and ``docs/17_knowledge_gap_detection_contract.md`` §5 for
    the full taxonomy discussion (including the categories deliberately
    excluded).
    """

    CONFLICTING_CLAIMS = "CONFLICTING_CLAIMS"
    LOW_CONFIDENCE_CLAIM = "LOW_CONFIDENCE_CLAIM"
    SINGLE_SOURCE_SUPPORT = "SINGLE_SOURCE_SUPPORT"
    NO_PRIMARY_EXPERIMENTAL_EVIDENCE = "NO_PRIMARY_EXPERIMENTAL_EVIDENCE"
    MISSING_PUBLICATION = "MISSING_PUBLICATION"
    MISSING_EXPERIMENTAL_CONTEXT = "MISSING_EXPERIMENTAL_CONTEXT"
    REACTION_WITHOUT_ENZYME = "REACTION_WITHOUT_ENZYME"
    PROTEIN_WITHOUT_REACTION = "PROTEIN_WITHOUT_REACTION"
    GENE_WITHOUT_PROTEIN = "GENE_WITHOUT_PROTEIN"
    REACTION_WITHOUT_PARTICIPANTS = "REACTION_WITHOUT_PARTICIPANTS"
    ISOLATED_COMPOUND = "ISOLATED_COMPOUND"


class GapSeverity(StrEnum):
    """A small, categorical, non-numeric severity bucket.

    No authoritative severity specification exists anywhere in this
    repository -- see ``app.knowledge_gaps.rules.GAP_SEVERITY`` for this
    package's own documented, disclosed-as-policy mapping. ``CRITICAL`` is
    kept as a full member of the closed vocabulary (mirroring
    ``ConfidenceClass``'s own reserved-but-not-always-reachable members) even
    though no rule implemented in this increment ever produces it.
    """

    INFO = "INFO"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class KnowledgeGapCandidate:
    """One immutable, in-memory knowledge-gap finding.

    ``entity_type``/``entity_id`` mirror ``KnowledgeGap.subject_type``/
    ``subject_id``'s own polymorphic-reference shape (no FK, same
    deferred-to-validation-layer limitation) -- this is the single anchor
    entity the gap is "about" (a ``Claim``, a ``Reaction``, a ``Protein``, a
    ``Gene``, or a ``Compound``, per ``gap_type``). ``entity_id`` may be
    ``None`` only when a gap-type genuinely has no single natural anchor
    entity (none of this increment's rules currently need that; kept
    optional for a future rule that might).

    ``subject_text``/``predicate``/``object_text`` mirror ``Claim``'s own
    subject/predicate/object shape for claim-anchored gaps, but are **never
    fabricated from a bare UUID** -- ``Claim`` carries no resolved entity
    *name* after persistence (only ``subject_type``/``subject_id``), so
    ``subject_text``/``object_text`` are always ``None`` in this increment
    (reserved for a future increment that resolves entity display names);
    ``predicate`` is populated directly from ``Claim.predicate`` (already
    free text) whenever the gap is claim-anchored.

    ``explanation`` states only what is missing -- never a suggested
    experiment, never a speculative biological explanation (Increment 21
    instructions, Step 24: "Reaction X has no associated ReactionParticipant
    rows," never "perform LC-MS ..."; see
    ``docs/17_knowledge_gap_detection_contract.md`` §17).

    ``supporting_claim_ids``/``supporting_evidence_ids``/
    ``supporting_entity_ids`` are the full, deterministic evidentiary basis
    for the finding -- always non-empty for a claim/evidence-anchored gap,
    since a gap that can be demonstrated from the database always points
    at the specific rows that demonstrate it.
    """

    gap_type: GapType
    severity: GapSeverity
    entity_type: str
    entity_id: UUID | None
    explanation: str

    subject_text: str | None = None
    predicate: str | None = None
    object_text: str | None = None

    supporting_claim_ids: tuple[UUID, ...] = ()
    supporting_evidence_ids: tuple[UUID, ...] = ()
    supporting_entity_ids: tuple[UUID, ...] = ()

    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.gap_type, GapType):
            raise InvalidGapCandidateError(
                f"KnowledgeGapCandidate.gap_type must be a GapType, got {self.gap_type!r}"
            )
        if not isinstance(self.severity, GapSeverity):
            raise InvalidGapCandidateError(
                f"KnowledgeGapCandidate.severity must be a GapSeverity, got {self.severity!r}"
            )
        object.__setattr__(
            self, "entity_type", require_non_empty_str(self.entity_type, field_name="entity_type")
        )
        if self.entity_id is not None and not isinstance(self.entity_id, UUID):
            raise InvalidGapCandidateError(
                f"KnowledgeGapCandidate.entity_id must be a UUID or None, got {self.entity_id!r}"
            )
        object.__setattr__(
            self, "explanation", require_non_empty_str(self.explanation, field_name="explanation")
        )
        object.__setattr__(
            self, "subject_text", clean_optional(self.subject_text, field_name="subject_text")
        )
        object.__setattr__(
            self, "predicate", clean_optional(self.predicate, field_name="predicate")
        )
        object.__setattr__(
            self, "object_text", clean_optional(self.object_text, field_name="object_text")
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
            "supporting_entity_ids",
            require_uuid_tuple(self.supporting_entity_ids, field_name="supporting_entity_ids"),
        )
        object.__setattr__(
            self,
            "reason_codes",
            require_non_blank_string_tuple(self.reason_codes, field_name="reason_codes"),
        )

    def identity_key(
        self,
    ) -> tuple[GapType, str, UUID | None, tuple[UUID, ...]]:
        """The deduplication key: ``(gap_type, entity_type, entity_id, supporting_claim_ids)``.

        Never the explanation text (Increment 21 instructions, Step 22:
        "Do not dedupe by explanation text.").
        """
        return (self.gap_type, self.entity_type, self.entity_id, self.supporting_claim_ids)


@dataclass(frozen=True, slots=True)
class KnowledgeGapAnalysisResult:
    """The complete, deterministic output of one analysis run.

    ``gaps`` is ordered deterministically (severity descending, then
    ``gap_type``, ``entity_type``, ``entity_id`` -- see
    ``app.knowledge_gaps.analysis`` for the exact sort key) and already
    deduplicated (Step 22). ``summary_statistics`` is an immutable
    (``MappingProxyType``) count of gaps per ``GapType.value``, including
    every implemented ``GapType`` with a count of ``0`` when no instance was
    found -- the key set is always the same regardless of database state,
    only the counts vary.
    """

    gaps: tuple[KnowledgeGapCandidate, ...]
    analyzed_claim_count: int
    analyzed_evidence_count: int
    analyzed_entity_count: int
    summary_statistics: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.gaps, tuple) or not all(
            isinstance(item, KnowledgeGapCandidate) for item in self.gaps
        ):
            raise InvalidGapCandidateError(
                "KnowledgeGapAnalysisResult.gaps must be a tuple of KnowledgeGapCandidate"
            )
        for field_name, value in (
            ("analyzed_claim_count", self.analyzed_claim_count),
            ("analyzed_evidence_count", self.analyzed_evidence_count),
            ("analyzed_entity_count", self.analyzed_entity_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidGapCandidateError(
                    f"KnowledgeGapAnalysisResult.{field_name} must be a non-negative int, "
                    f"got {value!r}"
                )
        if not isinstance(self.summary_statistics, Mapping):
            raise InvalidGapCandidateError(
                "KnowledgeGapAnalysisResult.summary_statistics must be a Mapping, got "
                f"{self.summary_statistics!r}"
            )
        for key, value in self.summary_statistics.items():
            if not isinstance(key, str) or not key:
                raise InvalidGapCandidateError(
                    f"summary_statistics keys must be non-empty strings, got {key!r}"
                )
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidGapCandidateError(
                    f"summary_statistics[{key!r}] must be a non-negative int, got {value!r}"
                )
        object.__setattr__(
            self, "summary_statistics", MappingProxyType(dict(self.summary_statistics))
        )


__all__ = ["GapSeverity", "GapType", "KnowledgeGapAnalysisResult", "KnowledgeGapCandidate"]
