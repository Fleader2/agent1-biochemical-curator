"""The Claim Generation data contract.

Two intermediate types and one final output type, in the order data flows
through this package:

1. ``EntityTypingHint`` -- a caller-supplied statement of what kind of
   entity a statement's subject/object *is* (a gene, a protein, a
   compound, ...), if known. This module never infers this from naming
   conventions or context itself (Increment 13 instructions, Step 5) --
   deciding it is either a caller/coordinator's job today, or a later,
   separate LLM-assisted step following
   ``app.claim_generation.prompts``'s prompt contract.
2. ``CandidateEntityReference`` -- one entity mention (subject, object,
   organism, or compartment), together with whatever
   ``app.normalization.*`` result (if any) was obtained for it. Preserves
   unresolved and ambiguous outcomes exactly as normalization returned
   them -- never collapses ``AMBIGUOUS`` down to one guessed candidate.
3. ``CandidateClaim`` -- the final, immutable output of this package: one
   structured biological assertion, source-grounded back to the
   ``EvidenceExtraction`` it came from, ready for a later increment to
   score confidence and persist. Deliberately carries no ``Claim``/
   ``Evidence`` row identity of any kind (no ``claim_id``, no
   ``evidence_id``, no ``confidence_score``/``confidence_class``, no
   persistence status) -- see module docstring below for exactly what was
   excluded and why.

All three are frozen dataclasses that validate themselves in
``__post_init__`` via ``app.claim_generation.validation``, the same
self-validating-at-construction pattern ``app.normalization.*`` and
``app.extraction.types`` both already use. Nothing in this module performs
I/O, calls an LLM, or reads a database.

**Schema provenance, verified directly from the ORM before writing this
module, not assumed** (``app/models/claim.py``):

* ``Claim.subject_type``/``Claim.object_type`` are plain, non-enum
  ``VARCHAR`` columns -- ``subject_type`` is ``NOT NULL``, ``object_type``
  is nullable. Nothing in the schema constrains either to a closed
  enumeration (the same "polymorphic reference with no foreign key"
  limitation already accepted for ``claim.subject_id``/``object_id``
  themselves). ``EntityKind`` below is therefore a **local-only**
  ``StrEnum`` -- the same "vocabulary exists in code, not in the
  database" pattern ``app.extraction.types.Directness`` already uses for
  ``Evidence.directness`` -- not a proposal to add a database enum.
* ``Claim.subject_id``/``Claim.object_id`` are nullable ``UUID`` columns
  with **no foreign key at all** (verified: `Claim`'s own docstring calls
  this out explicitly as a known, deferred referential-integrity
  limitation). A ``CandidateClaim``'s ``normalized_id`` fields are
  therefore already shaped exactly like what persistence will eventually
  write into these columns -- no schema change is anticipated here.
* ``Claim.value_text``/``Claim.value_numeric``/``Claim.unit`` are three
  independent, all-nullable columns with **no mutual-exclusivity
  constraint** against ``object_type``/``object_id`` -- a single ``Claim``
  row can legitimately carry both an object reference *and* a literal
  value at once. ``CandidateClaim`` mirrors this exactly: ``object`` and
  ``value_text``/``value_numeric``/``value_unit`` are independent optional
  fields, never forced to be mutually exclusive (Increment 13
  instructions, Step 7).
* ``Claim.organism_id`` is a real, FK-backed ``UUID | None`` column,
  entirely separate from ``subject_id``/``object_id`` -- a claim's
  "which organism was this observed in" context, not part of its subject/
  object identity. ``CandidateClaim.organism`` mirrors this separation.
* ``Claim.strain`` is a plain, nullable ``VARCHAR`` -- free text, not a
  reference. ``CandidateClaim.strain`` is accordingly ``str | None``, not
  a ``CandidateEntityReference`` -- there is no ``Strain`` entity to
  normalize against anywhere in this repository's schema.
* **``Claim`` has no compartment column of any kind.** Verified directly
  against ``app/models/claim.py`` -- no ``compartment_id``, no
  ``compartment_text``, nothing. Increment 13's own instructions (Steps 3,
  10) nonetheless ask for compartment normalization on
  ``CandidateClaim`` — ``compartment`` is kept as a field here because a
  later increment may need it (e.g. as claim metadata, or via a future
  schema change), but **this is flagged as an open architecture question,
  not silently resolved**: there is currently no ``Claim`` column for a
  future persistence increment to write a resolved compartment id into
  (see this increment's completion report).
* ``Claim.claim_category`` is a plain, nullable ``VARCHAR`` with **no
  documented controlled vocabulary anywhere in this repository** --
  already flagged as an open question in
  ``docs/09_evidence_extraction_contract.md`` (§20) for
  ``EvidenceExtraction.claim_category``, and unchanged here:
  ``CandidateClaim.claim_category`` remains free text, carried through
  from ``EvidenceExtraction.claim_category`` unmodified.
* ``EvidenceType``/``Directness`` are reused directly from
  ``app.models.enums``/``app.extraction.types`` respectively -- carried
  through from the originating ``EvidenceExtraction`` unchanged (Increment
  13 instructions, Step 12: "Carry Directness unchanged ... No
  reinterpretation.").

**Deliberately excluded from ``CandidateClaim``** (Increment 13
instructions, Step 2): any database UUID naming a *row* rather than a
resolved *entity* (``claim_id``, ``evidence_id``), and anything belonging
to confidence scoring or persistence (``confidence_score``,
``confidence_class``, ``status``/persistence state). A resolved entity's
UUID, when one exists, lives on its ``CandidateEntityReference`` as
``normalized_id`` -- entirely different from a ``Claim``/``Evidence``
row's own future primary key, which does not exist yet at this stage of
the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.claim_generation.validation import (
    clean_optional,
    require_non_empty,
    validate_entity_reference_consistency,
    validate_value_fields,
)
from app.extraction.types import Directness, EvidenceExtraction, SourceSpan
from app.models.enums import EvidenceType, SourceType
from app.normalization.types import NormalizationResult


class EntityKind(StrEnum):
    """What kind of entity a subject/object/organism/compartment mention names.

    A local-only ``StrEnum`` -- see module docstring's schema-provenance
    section for why this never touches a database column directly.
    ``UNKNOWN`` is the safe default: it means "this module was not told,
    and therefore never guessed" (Increment 13 instructions, Step 5: "Do
    not infer biological type from naming conventions. If uncertain, mark
    unknown."), not "this is a genuinely typeless entity."
    """

    ORGANISM = "ORGANISM"
    PUBLICATION = "PUBLICATION"
    GENE = "GENE"
    PROTEIN = "PROTEIN"
    COMPOUND = "COMPOUND"
    COMPARTMENT = "COMPARTMENT"
    REACTION = "REACTION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class EntityTypingHint:
    """A caller's explicit statement of a statement's subject/object entity kinds.

    Both default to ``UNKNOWN`` -- the conservative default is "attempt no
    normalization at all," never a guess. ``organism``/``compartment``
    need no such hint: their kind is always exactly ``EntityKind.ORGANISM``/
    ``EntityKind.COMPARTMENT`` respectively whenever the corresponding
    ``EvidenceExtraction`` text field is present at all.
    """

    subject_kind: EntityKind = EntityKind.UNKNOWN
    object_kind: EntityKind = EntityKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class CandidateEntityReference:
    """One entity mention, together with whatever normalization result (if any) exists for it.

    ``normalization_result`` is the *entire* ``NormalizationResult`` --
    including its ``candidate_entity_ids`` when ``AMBIGUOUS``/
    ``CONFLICTED`` -- never collapsed to a single chosen id (Increment 13
    instructions, Step 17: "If subject or object normalization is
    ambiguous, preserve ambiguity. Do not choose one candidate.").
    ``normalized_id`` is populated *only* when ``normalization_result.status
    is NormalizationStatus.MATCHED`` -- for every other status (including
    ``NEW``, which has not been persisted and therefore has no id yet),
    it is ``None``.

    ``normalization_result is None`` means normalization was never
    attempted at all -- either the entity's kind was ``UNKNOWN``, no
    ``Lookup`` was available for its kind, or (for an organism-scoped kind
    such as ``GENE``) the claim's own organism could not be resolved to a
    concrete id. This is distinct from ``normalization_result`` being
    present with status ``UNRESOLVED``, which means normalization *was*
    attempted and concluded there was not enough evidence.
    """

    original_text: str
    entity_kind: EntityKind
    normalization_result: NormalizationResult | None = None
    normalized_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "original_text", require_non_empty(self.original_text, field_name="original_text")
        )
        if not isinstance(self.entity_kind, EntityKind):
            raise TypeError(
                f"CandidateEntityReference.entity_kind must be an EntityKind, "
                f"got {self.entity_kind!r}"
            )
        if self.normalization_result is not None and not isinstance(
            self.normalization_result, NormalizationResult
        ):
            raise TypeError(
                "CandidateEntityReference.normalization_result must be a "
                f"NormalizationResult or None, got {self.normalization_result!r}"
            )
        validate_entity_reference_consistency(
            normalization_result=self.normalization_result, normalized_id=self.normalized_id
        )


@dataclass(frozen=True, slots=True)
class CandidateClaim:
    """One structured, source-grounded biological assertion.

    Immutable, and holds a direct reference to the ``EvidenceExtraction``
    it was generated from (``evidence_extraction``) -- "no information
    loss" (Increment 13 instructions, Step 13). ``supporting_span`` is a
    convenience field equal to ``evidence_extraction.span``, checked for
    consistency in ``__post_init__`` -- provided directly so a later
    increment does not need to traverse ``evidence_extraction.span`` just
    to find the grounding for what is, in this increment, always a
    one-extraction-to-one-claim relationship.
    """

    source: SourceType
    source_identifier: str
    evidence_extraction: EvidenceExtraction
    subject: CandidateEntityReference
    predicate: str
    supporting_span: SourceSpan
    evidence_type: EvidenceType
    directness: Directness

    object: CandidateEntityReference | None = None

    value_text: str | None = None
    value_numeric: Decimal | None = None
    value_unit: str | None = None

    organism: CandidateEntityReference | None = None
    strain: str | None = None
    compartment: CandidateEntityReference | None = None

    claim_category: str | None = None

    qualifiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if not isinstance(self.evidence_extraction, EvidenceExtraction):
            raise TypeError(
                "CandidateClaim.evidence_extraction must be an EvidenceExtraction, "
                f"got {self.evidence_extraction!r}"
            )
        if self.source != self.evidence_extraction.source:
            raise ValueError(
                "CandidateClaim.source must equal evidence_extraction.source -- a claim "
                "must be grounded in the source it was generated from"
            )
        if self.source_identifier != self.evidence_extraction.source_identifier:
            raise ValueError(
                "CandidateClaim.source_identifier must equal "
                "evidence_extraction.source_identifier -- a claim must be grounded in the "
                "document it was generated from"
            )
        if self.supporting_span != self.evidence_extraction.span:
            raise ValueError(
                "CandidateClaim.supporting_span must equal evidence_extraction.span -- "
                "no information may be lost or altered between extraction and claim "
                "generation"
            )

        if not isinstance(self.subject, CandidateEntityReference):
            raise TypeError(
                f"CandidateClaim.subject must be a CandidateEntityReference, got {self.subject!r}"
            )
        object.__setattr__(
            self, "predicate", require_non_empty(self.predicate, field_name="predicate")
        )
        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"CandidateClaim.evidence_type must be an EvidenceType, got {self.evidence_type!r}"
            )
        if not isinstance(self.directness, Directness):
            raise TypeError(
                f"CandidateClaim.directness must be a Directness, got {self.directness!r}"
            )

        for field_name, expected_kind in (
            ("organism", EntityKind.ORGANISM),
            ("compartment", EntityKind.COMPARTMENT),
        ):
            reference = getattr(self, field_name)
            if reference is None:
                continue
            if not isinstance(reference, CandidateEntityReference):
                raise TypeError(
                    f"CandidateClaim.{field_name} must be a CandidateEntityReference or None, "
                    f"got {reference!r}"
                )
            if reference.entity_kind is not expected_kind:
                raise ValueError(
                    f"CandidateClaim.{field_name}'s entity_kind must be {expected_kind!r}, "
                    f"got {reference.entity_kind!r}"
                )

        if self.object is not None and not isinstance(self.object, CandidateEntityReference):
            raise TypeError(
                f"CandidateClaim.object must be a CandidateEntityReference or None, "
                f"got {self.object!r}"
            )

        object.__setattr__(self, "strain", clean_optional(self.strain))
        object.__setattr__(self, "claim_category", clean_optional(self.claim_category))
        object.__setattr__(self, "value_text", clean_optional(self.value_text))
        object.__setattr__(self, "value_unit", clean_optional(self.value_unit))

        validate_value_fields(
            value_text=self.value_text,
            value_numeric=self.value_numeric,
            value_unit=self.value_unit,
        )

        if any(not isinstance(q, str) or not q.strip() for q in self.qualifiers):
            raise ValueError("CandidateClaim.qualifiers must contain only non-empty strings")


__all__ = [
    "CandidateClaim",
    "CandidateEntityReference",
    "EntityKind",
    "EntityTypingHint",
]
