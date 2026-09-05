"""The Entity Mention Resolution data contract.

Three types, in the order data flows through this package:

1. ``EntityMention`` -- one textual entity mention to resolve, together
   with whatever context is already known about it (organism, strain,
   the ``EvidenceExtraction`` it came from). Produced by a caller (a
   coordinator, or `app.claim_generation` in a future integration -- see
   this package's own ``__init__`` docstring), never by this module.
2. ``IdentifierCandidate`` -- one candidate strong-identifier bundle this
   module retrieved from a trusted external source and already ran
   through the existing normalizer for its kind. Never a raw, untyped
   dictionary: ``normalization_input`` is always a genuine
   ``app.normalization.*`` ``*Identity`` object, and ``normalization_result``
   is always a genuine ``NormalizationResult`` -- the same typed objects
   every other consumer of ``app.normalization.*`` already works with.
3. ``MentionResolutionResult`` -- the final, immutable output: what this
   module concluded about one mention, across every candidate it found.

All three are frozen dataclasses that validate themselves in
``__post_init__``, the same self-validating-at-construction pattern
``app.normalization.*``, ``app.extraction.types``, and
``app.claim_generation.types`` all already use.

**Deliberately not a new status vocabulary confused with identity itself.**
``MentionResolutionStatus`` is a distinct type from
``app.normalization.types.NormalizationStatus`` -- retrieval proposes
identity candidates; only normalization decides identity (this
increment's Core Rule). ``MentionResolutionResult.resolved_entity_id`` may
be populated *only* when at least one candidate's own
``normalization_result.status is NormalizationStatus.MATCHED`` -- this
module never invents a ``MATCHED``-equivalent verdict of its own.

**Two additions beyond this increment's own illustrative status list**
(which is introduced with "outcomes such as", not presented as closed):

* ``UNRESOLVED`` -- resolution could not even be *attempted* because
  required context is missing (an organism-scoped kind with no resolved
  organism id -- Step 8 of this increment's instructions explicitly
  authorizes returning "an unresolved or ambiguous resolution result" for
  exactly this case). Distinct from ``NO_CANDIDATE`` (a search was
  attempted and found nothing) and from ``UNSUPPORTED_ENTITY_KIND`` (no
  connector exists for this kind at all).
* ``NEW_CANDIDATE`` -- real, verified external candidate(s) were found and
  normalized, and at least one reached ``NormalizationStatus.NEW`` (safe
  to create) with no ``MATCHED``/``AMBIGUOUS``/``CONFLICTED`` candidate
  present. This is a genuinely distinct scientific outcome from
  ``NO_CANDIDATE`` (which would misrepresent "we found a real, verified,
  not-yet-catalogued record" as "we found nothing") and from ``RESOLVED``
  (which requires an existing ``MATCHED`` entity) -- collapsing it into
  either would be exactly the kind of imprecise reporting this increment's
  instructions elsewhere forbid ("Do not create negative scientific
  conclusions").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.claim_generation.types import EntityKind
from app.extraction.types import EvidenceExtraction
from app.models.enums import SourceType
from app.normalization.compartment import CompartmentIdentity
from app.normalization.compound import CompoundIdentity
from app.normalization.gene import GeneIdentity
from app.normalization.organism import OrganismIdentity
from app.normalization.protein import ProteinIdentity
from app.normalization.publication import PublicationIdentity
from app.normalization.reaction import ReactionIdentity
from app.normalization.types import NormalizationResult, NormalizationStatus

#: Every existing ``app.normalization.*`` identity type this module may
#: construct and pass to a normalizer. Not a proposal for a new shared base
#: class -- these seven types remain otherwise unrelated, exactly as
#: ``app.normalization.*`` itself defines them.
AnyIdentity = (
    OrganismIdentity
    | PublicationIdentity
    | GeneIdentity
    | ProteinIdentity
    | CompoundIdentity
    | CompartmentIdentity
    | ReactionIdentity
)

_IDENTITY_TYPES = (
    OrganismIdentity,
    PublicationIdentity,
    GeneIdentity,
    ProteinIdentity,
    CompoundIdentity,
    CompartmentIdentity,
    ReactionIdentity,
)


def _require_non_empty(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    return stripped


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class EntityMention:
    """One textual entity mention to resolve, with whatever context is already known.

    ``source_context``/``source_context_identifier`` name the document the
    mention came from (mirroring every other source-neutral type in this
    codebase's own ``source``/``source_identifier`` pair) -- required
    together, the same "always travel together" convention
    ``app.normalization.*``'s own identity types already use. If
    ``evidence_extraction`` is also supplied, it must agree with both.

    ``organism_id`` is the *already-resolved* organism entity id (from
    Claim Generation's own organism normalization, or any other prior
    resolution step) -- never invented or looked up by this module itself.
    A ``None`` value means "no resolved organism is available," which for
    an organism-scoped ``entity_kind`` (``GENE``/``PROTEIN``/``REACTION``)
    means resolution cannot even be attempted (see
    ``app.entity_resolution.resolver``'s ``UNRESOLVED`` outcome).

    Deliberately excludes a normalized UUID *for the mention itself* (that
    is this module's output, not its input) and a confidence score (no
    confidence scoring exists anywhere in this pipeline yet).
    """

    original_text: str
    entity_kind: EntityKind
    source_context: SourceType
    source_context_identifier: str

    organism_context_text: str | None = None
    organism_id: UUID | None = None
    strain_text: str | None = None
    surrounding_text: str | None = None
    evidence_extraction: EvidenceExtraction | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_text",
            _require_non_empty(self.original_text, field_name="original_text"),
        )
        if not isinstance(self.entity_kind, EntityKind):
            raise TypeError(
                f"EntityMention.entity_kind must be an EntityKind, got {self.entity_kind!r}"
            )
        if not isinstance(self.source_context, SourceType):
            raise TypeError(
                f"EntityMention.source_context must be a SourceType, got {self.source_context!r}"
            )
        object.__setattr__(
            self,
            "source_context_identifier",
            _require_non_empty(
                self.source_context_identifier, field_name="source_context_identifier"
            ),
        )
        object.__setattr__(
            self, "organism_context_text", _clean_optional(self.organism_context_text)
        )
        object.__setattr__(self, "strain_text", _clean_optional(self.strain_text))
        object.__setattr__(self, "surrounding_text", _clean_optional(self.surrounding_text))

        if self.evidence_extraction is not None:
            if not isinstance(self.evidence_extraction, EvidenceExtraction):
                raise TypeError(
                    "EntityMention.evidence_extraction must be an EvidenceExtraction or None, "
                    f"got {self.evidence_extraction!r}"
                )
            if self.evidence_extraction.source != self.source_context:
                raise ValueError(
                    "EntityMention.source_context must equal evidence_extraction.source"
                )
            if self.evidence_extraction.source_identifier != self.source_context_identifier:
                raise ValueError(
                    "EntityMention.source_context_identifier must equal "
                    "evidence_extraction.source_identifier"
                )


@dataclass(frozen=True, slots=True)
class IdentifierCandidate:
    """One candidate strong-identifier bundle, already run through the existing normalizer.

    ``normalization_input`` and ``normalization_result`` are always
    genuine, already-validated ``app.normalization.*`` objects -- this
    type never carries an untyped dictionary as its primary payload.
    ``normalization_input.source``/``source_identifier`` must equal this
    candidate's own ``source``/``source_identifier`` (the identity actually
    submitted must be the one this candidate claims), and
    ``normalization_result.source``/``source_identifier`` must too (the
    result actually returned must be for that same submission) -- both
    checked in ``__post_init__``.

    ``retrieved_identifiers`` is a flat, read-only audit view of whatever
    identifier-shaped fields the source record actually exposed (e.g.
    ``{"sgd_id": "S000000123", "systematic_name": "YHR198C"}``) -- for
    provenance/display only; ``normalization_input`` remains the single
    source of truth actually passed to the normalizer.
    """

    entity_kind: EntityKind
    source: SourceType
    source_identifier: str
    original_mention: str
    search_term: str
    source_record_identifier: str
    normalization_input: AnyIdentity
    normalization_result: NormalizationResult

    display_name: str | None = None
    organism_context_text: str | None = None
    organism_id: UUID | None = None
    retrieved_identifiers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entity_kind, EntityKind):
            raise TypeError(
                f"IdentifierCandidate.entity_kind must be an EntityKind, got {self.entity_kind!r}"
            )
        if not isinstance(self.source, SourceType):
            raise TypeError(f"IdentifierCandidate.source must be a SourceType, got {self.source!r}")
        object.__setattr__(
            self,
            "source_identifier",
            _require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(
            self, "search_term", _require_non_empty(self.search_term, field_name="search_term")
        )
        object.__setattr__(
            self,
            "source_record_identifier",
            _require_non_empty(
                self.source_record_identifier, field_name="source_record_identifier"
            ),
        )
        object.__setattr__(
            self,
            "original_mention",
            _require_non_empty(self.original_mention, field_name="original_mention"),
        )

        if not isinstance(self.normalization_input, _IDENTITY_TYPES):
            raise TypeError(
                "IdentifierCandidate.normalization_input must be one of "
                f"app.normalization.*'s Identity types, got {self.normalization_input!r}"
            )
        if self.normalization_input.source != self.source:
            raise ValueError(
                "IdentifierCandidate.normalization_input.source must equal this candidate's "
                "own source"
            )
        if self.normalization_input.source_identifier != self.source_identifier:
            raise ValueError(
                "IdentifierCandidate.normalization_input.source_identifier must equal this "
                "candidate's own source_identifier"
            )

        if not isinstance(self.normalization_result, NormalizationResult):
            raise TypeError(
                "IdentifierCandidate.normalization_result must be a NormalizationResult, "
                f"got {self.normalization_result!r}"
            )
        if self.normalization_result.source != self.source:
            raise ValueError(
                "IdentifierCandidate.normalization_result.source must equal this candidate's "
                "own source"
            )
        if self.normalization_result.source_identifier != self.source_identifier:
            raise ValueError(
                "IdentifierCandidate.normalization_result.source_identifier must equal this "
                "candidate's own source_identifier"
            )

        object.__setattr__(self, "display_name", _clean_optional(self.display_name))
        object.__setattr__(
            self, "organism_context_text", _clean_optional(self.organism_context_text)
        )


class MentionResolutionStatus(StrEnum):
    """The outcome of attempting to resolve one ``EntityMention``.

    See module docstring for ``UNRESOLVED``/``NEW_CANDIDATE``, the two
    additions beyond this increment's own illustrative list.
    """

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"
    NEW_CANDIDATE = "NEW_CANDIDATE"
    NO_CANDIDATE = "NO_CANDIDATE"
    UNRESOLVED = "UNRESOLVED"
    UNSUPPORTED_ENTITY_KIND = "UNSUPPORTED_ENTITY_KIND"
    SOURCE_FAILURE = "SOURCE_FAILURE"


@dataclass(frozen=True, slots=True)
class MentionResolutionResult:
    """What this module concluded about one ``EntityMention``.

    ``resolved_entity_id`` may be populated **only** when
    ``status is MentionResolutionStatus.RESOLVED``, and must then equal the
    single ``matched_entity_id`` every ``MATCHED`` candidate in
    ``candidates`` agrees on (checked in ``__post_init__``) -- this module
    never invents a resolved id independent of what normalization itself
    already returned.
    """

    mention: EntityMention
    status: MentionResolutionStatus
    candidates: tuple[IdentifierCandidate, ...] = ()
    resolved_entity_id: UUID | None = None
    sources_queried: tuple[SourceType, ...] = ()
    reason: str = ""
    failed_source: SourceType | None = None
    error_category: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mention, EntityMention):
            raise TypeError(
                f"MentionResolutionResult.mention must be an EntityMention, got {self.mention!r}"
            )
        if not isinstance(self.status, MentionResolutionStatus):
            raise TypeError(
                "MentionResolutionResult.status must be a MentionResolutionStatus, "
                f"got {self.status!r}"
            )

        if self.status is MentionResolutionStatus.RESOLVED:
            matched_ids = {
                c.normalization_result.matched_entity_id
                for c in self.candidates
                if c.normalization_result.status is NormalizationStatus.MATCHED
            }
            if len(matched_ids) != 1:
                raise ValueError(
                    "RESOLVED requires exactly one agreed-upon MATCHED entity id across "
                    f"candidates, found {matched_ids!r}"
                )
            if self.resolved_entity_id != next(iter(matched_ids)):
                raise ValueError(
                    "RESOLVED requires resolved_entity_id to equal the single agreed-upon "
                    "MATCHED entity id"
                )
        elif self.resolved_entity_id is not None:
            raise ValueError(
                f"resolved_entity_id must be None when status is {self.status!r}, not RESOLVED"
            )

        if self.status is MentionResolutionStatus.SOURCE_FAILURE:
            if self.failed_source is None:
                raise ValueError("SOURCE_FAILURE requires failed_source")
            if self.error_category is None:
                raise ValueError("SOURCE_FAILURE requires error_category")
        else:
            if self.failed_source is not None:
                raise ValueError(f"failed_source must be None when status is {self.status!r}")
            if self.error_category is not None:
                raise ValueError(f"error_category must be None when status is {self.status!r}")


__all__ = [
    "AnyIdentity",
    "EntityMention",
    "IdentifierCandidate",
    "MentionResolutionResult",
    "MentionResolutionStatus",
]
