"""The evidence-extraction data contract.

Three types, in the order data flows through this package:

1. ``CandidateStatement`` -- one not-yet-grounded decomposed statement, as
   produced by whatever process examined a source passage (an LLM call
   following ``app.extraction.prompts``'s field names, or a human
   transcribing one). This module never produces one itself.
2. ``SourceSpan`` -- one grounded location in one document, with its exact
   quoted text. Produced by ``app.extraction.extractor.extract_evidence``
   once a candidate's ``quoted_text`` has been located in the real source
   text (see that module for the grounding algorithm).
3. ``EvidenceExtraction`` -- the final, immutable output of this package: a
   validated, source-grounded statement, ready for a later increment to
   turn into ``Claim``/``Evidence`` rows. Deliberately carries no
   normalized-entity UUIDs (``organism_id``, ``gene_id``, ...) -- entity
   normalization is a separate, later step (``app.normalization.*``) this
   package never performs itself (Increment 12 instructions, Step 13:
   "Never infer ... from context. Only extract explicit text.").

All three are frozen dataclasses that validate themselves in
``__post_init__`` via ``app.extraction.validation``, the same
self-validating-at-construction pattern every ``app.normalization.*``
identity type already uses. Nothing in this module performs I/O, calls an
LLM, or reads a database -- see ``app.extraction.extractor`` for the one
step (grounding against real source text) that needs more than one object
at a time.

**Schema provenance, verified directly from the ORM before writing this
module, not assumed:**

* ``Evidence.evidence_type`` is backed by the existing
  ``app.models.enums.EvidenceType`` enum -- reused verbatim here rather
  than inventing an extractor-local category set (Increment 12
  instructions, Step 12: "If present, reuse them.").
* ``Evidence.directness`` is a plain, non-enum ``VARCHAR`` column, but
  ``docs/03_agent_behavior.md``'s "Evidence Extraction Behavior" section
  already defines a closed, six-value controlled vocabulary for exactly
  this concept ("authors observed / authors inferred / authors proposed /
  authors discussed / review summarizes / database annotates") -- ``
  Directness`` below transcribes that list verbatim rather than inventing
  one. It is a local ``StrEnum``, not a database enum, matching how
  ``app.normalization.types.NormalizationStatus``/``MatchMethod`` and
  ``app.persistence.types.PersistenceAction`` are all local-only
  ``StrEnum``\\ s that never touch a database column directly.
* ``Claim.claim_category`` is a plain, non-enum, nullable ``VARCHAR`` with
  no documented controlled vocabulary anywhere in this repository, despite
  ``docs/03_agent_behavior.md`` listing "claim category" as a field every
  extracted claim must include. ``claim_category`` below is therefore kept
  as free text, not a closed enum -- inventing one would be exactly the
  kind of unsupported mapping Increment 12's instructions forbid. This is
  recorded as an open question in this increment's completion report, not
  silently resolved here.
* ``EvidenceExtraction.publication_id`` mirrors ``Evidence.publication_id``
  (nullable ``UUID`` FK) in shape only -- this package never sets it to
  anything but ``None``. Publication identity resolution
  (``app.normalization.publication``) is a separate, later step; the field
  exists purely so a later increment does not need a breaking schema
  change to attach a resolved publication id once one exists (the same
  forward-compatibility reasoning
  ``app.normalization.types.NormalizationResult.organism_id`` already
  documents for itself).
* No ``AUTHOR_CERTAINTY`` field is defined separately from ``directness``:
  ``docs/03_agent_behavior.md``'s Evidence Extraction Prompt lists both,
  but nothing in the schema backs a distinct "author certainty" concept,
  and ``Directness``'s own six values (particularly
  ``AUTHORS_PROPOSED``/``AUTHORS_DISCUSSED``) already capture the
  observation-vs-interpretation axis this increment's Step 8 asks for.
  Folding the two together, rather than inventing a fifteenth field with
  no schema backing, is a deliberate design choice -- see this increment's
  completion report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.extraction.validation import (
    clean_optional,
    require_non_empty,
    require_quoted_text,
    validate_measurement_fields,
    validate_non_negative_int,
    validate_span_length_matches_quote,
    validate_span_offsets,
)
from app.models.enums import EvidenceType, SourceType


class Directness(StrEnum):
    """The observation-vs-interpretation axis every extracted statement must carry.

    Transcribed verbatim from ``docs/03_agent_behavior.md``'s "Evidence
    Extraction Behavior" section ("The extractor must distinguish among:
    authors observed / authors inferred / authors proposed / authors
    discussed / review summarizes / database annotates") -- not invented.
    A local ``StrEnum``, not a database enum: ``Evidence.directness`` is a
    plain ``VARCHAR`` column (see module docstring), so nothing here
    proposes a schema change.
    """

    AUTHORS_OBSERVED = "AUTHORS_OBSERVED"
    AUTHORS_INFERRED = "AUTHORS_INFERRED"
    AUTHORS_PROPOSED = "AUTHORS_PROPOSED"
    AUTHORS_DISCUSSED = "AUTHORS_DISCUSSED"
    REVIEW_SUMMARIZES = "REVIEW_SUMMARIZES"
    DATABASE_ANNOTATES = "DATABASE_ANNOTATES"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One grounded location in one document, with its exact quoted text.

    ``quoted_text`` is stored exactly as it appears in the source -- never
    trimmed, case-folded, or otherwise altered (Increment 12 instructions,
    Step 6: "Quoted text should preserve the original wording. No
    paraphrasing."). Every other field describes *where* that text came
    from, at whatever granularity was actually available -- ``section``/
    ``subsection``/``paragraph_index``/``sentence_index`` are all optional,
    since not every source exposes sentence- or even paragraph-level
    structure (an abstract, for instance), but ``character_start``/
    ``character_end`` are always required: a span with no offsets at all
    would not be a span.

    One ``SourceSpan`` is exactly one location -- there is no field here
    that could represent two locations at once, which is how Increment
    12's "one source span per extraction" grounding rule is enforced
    structurally rather than only by convention (see
    ``EvidenceExtraction``'s own docstring).
    """

    document_identifier: str
    character_start: int
    character_end: int
    quoted_text: str

    section: str | None = None
    subsection: str | None = None
    paragraph_index: int | None = None
    sentence_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "document_identifier",
            require_non_empty(self.document_identifier, field_name="document_identifier"),
        )
        object.__setattr__(
            self, "quoted_text", require_quoted_text(self.quoted_text)
        )
        validate_span_offsets(self.character_start, self.character_end)
        validate_span_length_matches_quote(
            self.character_start, self.character_end, self.quoted_text
        )
        object.__setattr__(self, "section", clean_optional(self.section))
        object.__setattr__(self, "subsection", clean_optional(self.subsection))
        object.__setattr__(
            self,
            "paragraph_index",
            validate_non_negative_int(self.paragraph_index, field_name="paragraph_index"),
        )
        object.__setattr__(
            self,
            "sentence_index",
            validate_non_negative_int(self.sentence_index, field_name="sentence_index"),
        )


@dataclass(frozen=True, slots=True)
class CandidateStatement:
    """One not-yet-grounded decomposed statement, prior to source-text validation.

    Produced by whatever examined a source passage -- never by this
    package. ``character_start``/``character_end`` are optional here
    (unlike on ``SourceSpan``): a candidate may know exactly where its
    quote sits in the source, or may only know the quote's exact text and
    rely on ``extract_evidence`` to locate it deterministically (see that
    module's ``ground_candidate``). Content-field validation happens here,
    at construction time; span/grounding validation happens later, once a
    real ``SourceSpan`` can be built against real source text.

    ``subject_text``/``predicate_text`` are required -- a statement with
    neither is not a statement (Increment 12 instructions, Step 7:
    "Extract separately: subject / predicate / object / qualifiers.").
    ``object_text`` is optional: some predicates are not transitive in a
    way that needs a separate object phrase (e.g. "FadD is essential for
    growth on oleate" decomposes naturally as subject="FadD",
    predicate="is essential for", object="growth on oleate" -- but a
    genuinely intransitive statement should not have an object phrase
    invented for it just to fill the field).
    """

    quoted_text: str
    subject_text: str
    predicate_text: str
    evidence_type: EvidenceType
    directness: Directness

    character_start: int | None = None
    character_end: int | None = None

    object_text: str | None = None
    qualifier_text: str | None = None
    normalized_text: str | None = None

    organism_text: str | None = None
    strain_text: str | None = None
    compartment_text: str | None = None

    claim_category: str | None = None

    experimental_system: str | None = None
    assay: str | None = None
    perturbation: str | None = None

    measured_quantity: str | None = None
    measurement_value: str | None = None
    measurement_units: str | None = None
    statistical_support: str | None = None

    figure_reference: str | None = None
    table_reference: str | None = None
    supplementary_reference: str | None = None

    notes: str | None = None

    paragraph_index: int | None = None
    sentence_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quoted_text", require_quoted_text(self.quoted_text))
        object.__setattr__(
            self, "subject_text", require_non_empty(self.subject_text, field_name="subject_text")
        )
        object.__setattr__(
            self,
            "predicate_text",
            require_non_empty(self.predicate_text, field_name="predicate_text"),
        )
        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"CandidateStatement.evidence_type must be an EvidenceType, "
                f"got {self.evidence_type!r}"
            )
        if not isinstance(self.directness, Directness):
            raise TypeError(
                f"CandidateStatement.directness must be a Directness, got {self.directness!r}"
            )

        for field_name in (
            "object_text",
            "qualifier_text",
            "normalized_text",
            "organism_text",
            "strain_text",
            "compartment_text",
            "claim_category",
            "experimental_system",
            "assay",
            "perturbation",
            "measured_quantity",
            "measurement_value",
            "measurement_units",
            "statistical_support",
            "figure_reference",
            "table_reference",
            "supplementary_reference",
            "notes",
        ):
            object.__setattr__(self, field_name, clean_optional(getattr(self, field_name)))

        validate_measurement_fields(
            measurement_value=self.measurement_value, measurement_units=self.measurement_units
        )

        if self.character_start is not None or self.character_end is not None:
            if self.character_start is None or self.character_end is None:
                raise ValueError(
                    "CandidateStatement requires both character_start and character_end "
                    "together, or neither -- never only one"
                )
            validate_span_offsets(self.character_start, self.character_end)
            validate_span_length_matches_quote(
                self.character_start, self.character_end, self.quoted_text
            )

        object.__setattr__(
            self,
            "paragraph_index",
            validate_non_negative_int(self.paragraph_index, field_name="paragraph_index"),
        )
        object.__setattr__(
            self,
            "sentence_index",
            validate_non_negative_int(self.sentence_index, field_name="sentence_index"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceExtraction:
    """One validated, source-grounded statement -- the output of this package.

    Immutable, and deliberately minimal about identity: it carries
    ``source``/``source_identifier`` (mirroring every
    ``app.normalization.*`` identity type's own source-provenance fields)
    and an optional, always-``None``-from-this-package ``publication_id``
    (see module docstring), but no normalized entity UUIDs of any kind --
    normalizing ``organism_text``/``subject_text``/etc. into real
    ``Organism``/``Gene``/``Protein`` rows is a separate, later step this
    package never performs (Increment 12 instructions, Step 13).

    Exactly one ``span`` -- never a list, never a range of spans -- which
    is how "one extraction, one source document, one quoted passage"
    (Increment 12 instructions, Step 5) is enforced structurally: there is
    no field shape here that could hold evidence merged from two
    locations. ``span.document_identifier`` must equal this extraction's
    own ``source_identifier`` (checked in ``__post_init__``) -- an
    extraction is always grounded in the one document it claims to be
    about, never a different one.
    """

    source: SourceType
    source_identifier: str
    span: SourceSpan
    subject_text: str
    predicate_text: str
    evidence_type: EvidenceType
    directness: Directness

    publication_id: UUID | None = None

    object_text: str | None = None
    qualifier_text: str | None = None
    normalized_text: str | None = None

    organism_text: str | None = None
    strain_text: str | None = None
    compartment_text: str | None = None

    claim_category: str | None = None

    experimental_system: str | None = None
    assay: str | None = None
    perturbation: str | None = None

    measured_quantity: str | None = None
    measurement_value: str | None = None
    measurement_units: str | None = None
    statistical_support: str | None = None

    figure_reference: str | None = None
    table_reference: str | None = None
    supplementary_reference: str | None = None

    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError(f"EvidenceExtraction.span must be a SourceSpan, got {self.span!r}")
        if self.span.document_identifier != self.source_identifier:
            raise ValueError(
                f"EvidenceExtraction.span.document_identifier "
                f"({self.span.document_identifier!r}) must equal source_identifier "
                f"({self.source_identifier!r}) -- an extraction must be grounded in the "
                "one document it claims to be about"
            )

        object.__setattr__(
            self, "subject_text", require_non_empty(self.subject_text, field_name="subject_text")
        )
        object.__setattr__(
            self,
            "predicate_text",
            require_non_empty(self.predicate_text, field_name="predicate_text"),
        )
        if not isinstance(self.evidence_type, EvidenceType):
            raise TypeError(
                f"EvidenceExtraction.evidence_type must be an EvidenceType, "
                f"got {self.evidence_type!r}"
            )
        if not isinstance(self.directness, Directness):
            raise TypeError(
                f"EvidenceExtraction.directness must be a Directness, got {self.directness!r}"
            )

        for field_name in (
            "object_text",
            "qualifier_text",
            "normalized_text",
            "organism_text",
            "strain_text",
            "compartment_text",
            "claim_category",
            "experimental_system",
            "assay",
            "perturbation",
            "measured_quantity",
            "measurement_value",
            "measurement_units",
            "statistical_support",
            "figure_reference",
            "table_reference",
            "supplementary_reference",
            "notes",
        ):
            object.__setattr__(self, field_name, clean_optional(getattr(self, field_name)))

        validate_measurement_fields(
            measurement_value=self.measurement_value, measurement_units=self.measurement_units
        )


__all__ = [
    "CandidateStatement",
    "Directness",
    "EvidenceExtraction",
    "SourceSpan",
]
