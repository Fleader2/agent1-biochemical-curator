"""The public evidence-extraction API: ``extract_evidence``.

Turns a source passage's already-decomposed candidate statements into
validated, source-grounded ``EvidenceExtraction`` objects. This module
never produces a candidate statement itself -- it only validates,
grounds, and assembles. No I/O, no LLM call, no database access (Increment
12 instructions: "No database interaction.").

**Grounding algorithm.** A candidate may already know exactly where its
quote sits in the source (``character_start``/``character_end`` supplied);
if so, this module only verifies ``text[start:end] == quoted_text`` and
raises ``GroundingError`` on any mismatch -- it never adjusts the offsets
to make them fit. If a candidate supplies no offsets at all, this module
locates ``quoted_text`` in ``text`` itself, deterministically, via exact
substring search -- deliberately never asking an upstream LLM to invent
numeric character offsets, which are exactly the kind of detail language
models fabricate unreliably. If the quoted text does not appear in the
source at all, or appears more than once (an ambiguous location this
module refuses to guess between), grounding fails with ``GroundingError``
rather than silently picking an occurrence.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.extraction.errors import GroundingError, UnsupportedInputError
from app.extraction.types import CandidateStatement, EvidenceExtraction, SourceSpan
from app.models.enums import SourceType


def _find_all_occurrences(text: str, substring: str) -> list[int]:
    """Every non-overlapping starting index of ``substring`` within ``text``."""
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(substring, start)
        if index == -1:
            break
        positions.append(index)
        start = index + 1
    return positions


def ground_candidate(
    candidate: CandidateStatement,
    *,
    text: str,
    document_identifier: str,
    section: str | None,
    subsection: str | None,
) -> SourceSpan:
    """Resolve one candidate's quoted text to a concrete, verified ``SourceSpan``.

    See module docstring for the algorithm. Raises ``GroundingError`` if
    the quoted text cannot be uniquely and exactly located in ``text``.
    """
    if candidate.character_start is not None:
        character_start = candidate.character_start
        character_end = candidate.character_end
        assert character_end is not None  # CandidateStatement requires both together
        actual = text[character_start:character_end]
        if actual != candidate.quoted_text:
            raise GroundingError(
                f"candidate supplied character_start={character_start!r}, "
                f"character_end={character_end!r}, but text[{character_start}:"
                f"{character_end}] is {actual!r}, not the candidate's own "
                f"quoted_text {candidate.quoted_text!r}"
            )
    else:
        occurrences = _find_all_occurrences(text, candidate.quoted_text)
        if not occurrences:
            raise GroundingError(
                f"candidate's quoted_text {candidate.quoted_text!r} does not appear "
                "verbatim anywhere in the supplied source text"
            )
        if len(occurrences) > 1:
            raise GroundingError(
                f"candidate's quoted_text {candidate.quoted_text!r} appears "
                f"{len(occurrences)} times in the supplied source text, and the "
                "candidate supplied no explicit character_start/character_end to "
                "disambiguate which occurrence it means"
            )
        character_start = occurrences[0]
        character_end = character_start + len(candidate.quoted_text)

    return SourceSpan(
        document_identifier=document_identifier,
        character_start=character_start,
        character_end=character_end,
        quoted_text=candidate.quoted_text,
        section=section,
        subsection=subsection,
        paragraph_index=candidate.paragraph_index,
        sentence_index=candidate.sentence_index,
    )


def extract_evidence(
    *,
    source: SourceType,
    source_identifier: str,
    text: str,
    candidates: Sequence[CandidateStatement],
    publication_id: UUID | None = None,
    section: str | None = None,
    subsection: str | None = None,
) -> list[EvidenceExtraction]:
    """Validate, ground, and assemble candidate statements from one source passage.

    ``candidates`` may be empty -- a passage that contains no independently
    extractable statement yields an empty list, not an error (Increment 12
    instructions, Step 15: "One paragraph may yield 0, 1, or many
    ``EvidenceExtraction`` objects.").

    A malformed candidate fails the whole call: this function raises on the
    first invalid or ungroundable candidate rather than silently dropping
    it and returning only the good ones, matching this package's
    conservative-rejection policy (Step 4: "Reject malformed extractions
    conservatively.") -- silently discarding bad candidates from a batch
    could hide a systematic extraction bug instead of surfacing it.

    The returned list is always ordered by source position
    (``(character_start, character_end)``), regardless of the order
    ``candidates`` was supplied in -- output ordering depends only on
    source order, never on caller-supplied order or any other
    nondeterministic signal (Increment 12 instructions, Step 18).

    ``publication_id`` is passed straight through to every extraction and
    is otherwise untouched -- this function never resolves, looks up, or
    normalizes a publication identity itself.
    """
    if not isinstance(text, str) or not text.strip():
        raise UnsupportedInputError("extract_evidence requires non-empty source text")
    if not isinstance(source_identifier, str) or not source_identifier.strip():
        raise UnsupportedInputError("extract_evidence requires a non-empty source_identifier")
    if not isinstance(source, SourceType):
        raise UnsupportedInputError(
            f"extract_evidence requires a SourceType for source, got {source!r}"
        )
    for candidate in candidates:
        if not isinstance(candidate, CandidateStatement):
            raise UnsupportedInputError(
                f"extract_evidence received a non-CandidateStatement candidate: {candidate!r}"
            )

    extractions = [
        EvidenceExtraction(
            source=source,
            source_identifier=source_identifier,
            span=ground_candidate(
                candidate,
                text=text,
                document_identifier=source_identifier,
                section=section,
                subsection=subsection,
            ),
            subject_text=candidate.subject_text,
            predicate_text=candidate.predicate_text,
            evidence_type=candidate.evidence_type,
            directness=candidate.directness,
            publication_id=publication_id,
            object_text=candidate.object_text,
            qualifier_text=candidate.qualifier_text,
            normalized_text=candidate.normalized_text,
            organism_text=candidate.organism_text,
            strain_text=candidate.strain_text,
            compartment_text=candidate.compartment_text,
            claim_category=candidate.claim_category,
            experimental_system=candidate.experimental_system,
            assay=candidate.assay,
            perturbation=candidate.perturbation,
            measured_quantity=candidate.measured_quantity,
            measurement_value=candidate.measurement_value,
            measurement_units=candidate.measurement_units,
            statistical_support=candidate.statistical_support,
            figure_reference=candidate.figure_reference,
            table_reference=candidate.table_reference,
            supplementary_reference=candidate.supplementary_reference,
            notes=candidate.notes,
        )
        for candidate in candidates
    ]

    return sorted(
        extractions,
        key=lambda extraction: (extraction.span.character_start, extraction.span.character_end),
    )


__all__ = ["extract_evidence", "ground_candidate"]
