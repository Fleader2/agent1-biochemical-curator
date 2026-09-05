"""The public Claim Generation API: ``generate_candidate_claims``.

Turns a list of already-validated ``EvidenceExtraction`` objects into a
list of validated, immutable ``CandidateClaim`` objects -- one per
extraction. No I/O, no LLM call, no database access: any I/O a
``NormalizationLookups`` field performs is entirely the caller's concern,
exactly as it already is for every other consumer of
``app.normalization.*``.

**Why one claim per extraction, not extraction-time-style 0/1/many
splitting.** ``app.extraction.extract_evidence`` already decomposes a
source passage into one ``EvidenceExtraction`` per independently grounded
statement (``docs/09_evidence_extraction_contract.md`` §7) -- a passage
like "A activates B and inhibits C" is expected to already have become two
separate ``EvidenceExtraction`` objects *before* this module ever sees
them, each with its own subject/predicate/object. This module does not
re-parse or re-split an extraction's own text: doing so would duplicate
work extraction already does, and risks exactly the kind of ad hoc NLP
inference this package's instructions forbid. The "0, 1, or many"
property Increment 13 describes is therefore a property of the overall
pipeline (call this function with 0, 1, or many extractions and get that
many claims back), not of this function's handling of one extraction in
isolation.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.claim_generation.errors import ClaimGenerationError
from app.claim_generation.mapping import NormalizationLookups, resolve_entity_reference
from app.claim_generation.types import CandidateClaim, EntityKind, EntityTypingHint
from app.extraction.types import EvidenceExtraction


def _parse_numeric_value(measurement_value: str | None) -> tuple[Decimal | None, str | None]:
    """Split a reported measurement string into a clean ``Decimal`` (if any) and the original text.

    The original text is always preserved when present, regardless of
    whether it also parses cleanly as a plain number -- ``"12.3 +/- 0.4"``,
    ``"5.4-fold"``, and ``"<0.01"`` are all kept as ``value_text`` with
    ``value_numeric=None``, since coercing any of them to a single number
    would silently discard information the source actually reported
    (Increment 13 instructions, Step 8: "Do not perform unit
    conversion[/interpretation]."). No rounding, no averaging, ever.
    """
    if measurement_value is None:
        return None, None
    try:
        return Decimal(measurement_value), measurement_value
    except InvalidOperation:
        return None, measurement_value


def _generate_one(
    extraction: EvidenceExtraction, hint: EntityTypingHint, lookups: NormalizationLookups
) -> CandidateClaim:
    organism_reference = resolve_entity_reference(
        text=extraction.organism_text,
        kind=EntityKind.ORGANISM,
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        organism_id=None,
        lookups=lookups,
    )
    resolved_organism_id = (
        organism_reference.normalized_id if organism_reference is not None else None
    )

    subject_reference = resolve_entity_reference(
        text=extraction.subject_text,
        kind=hint.subject_kind,
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        organism_id=resolved_organism_id,
        lookups=lookups,
    )
    assert subject_reference is not None  # subject_text is required on EvidenceExtraction

    object_reference = resolve_entity_reference(
        text=extraction.object_text,
        kind=hint.object_kind,
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        organism_id=resolved_organism_id,
        lookups=lookups,
    )

    compartment_reference = resolve_entity_reference(
        text=extraction.compartment_text,
        kind=EntityKind.COMPARTMENT,
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        organism_id=resolved_organism_id,
        lookups=lookups,
    )

    value_numeric, value_text = _parse_numeric_value(extraction.measurement_value)

    return CandidateClaim(
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        evidence_extraction=extraction,
        subject=subject_reference,
        predicate=extraction.predicate_text,
        supporting_span=extraction.span,
        evidence_type=extraction.evidence_type,
        directness=extraction.directness,
        object=object_reference,
        value_text=value_text,
        value_numeric=value_numeric,
        value_unit=extraction.measurement_units,
        organism=organism_reference,
        strain=extraction.strain_text,
        compartment=compartment_reference,
        claim_category=extraction.claim_category,
        qualifiers=(extraction.qualifier_text,) if extraction.qualifier_text else (),
    )


def generate_candidate_claims(
    extractions: Sequence[EvidenceExtraction],
    *,
    lookups: NormalizationLookups | None = None,
    typing_hints: Sequence[EntityTypingHint | None] | None = None,
) -> list[CandidateClaim]:
    """Generate one ``CandidateClaim`` per supplied ``EvidenceExtraction``.

    ``lookups`` defaults to no lookups at all -- every entity mention is
    then structurally guaranteed to stay unresolved, the maximally
    conservative default. ``typing_hints``, if supplied, must be the same
    length as ``extractions`` and is paired with it positionally; a
    ``None`` entry (or omitting ``typing_hints`` entirely) means "no
    typing information for this extraction," equivalent to
    ``EntityTypingHint()`` (both subject and object kind ``UNKNOWN``).

    Output order mirrors input order exactly, one claim per extraction --
    this function never reorders, merges, or drops an extraction. Raises
    on the first invalid or unresolvable-due-to-a-bug extraction rather
    than silently omitting it, the same conservative-rejection policy
    ``app.extraction.extract_evidence`` already applies to its own
    candidates.
    """
    if not isinstance(extractions, Sequence) or isinstance(extractions, (str, bytes)):
        raise ClaimGenerationError(
            f"generate_candidate_claims requires a sequence of EvidenceExtraction, "
            f"got {extractions!r}"
        )
    if typing_hints is not None and len(typing_hints) != len(extractions):
        raise ClaimGenerationError(
            f"typing_hints (length {len(typing_hints)}) must be the same length as "
            f"extractions (length {len(extractions)})"
        )

    resolved_lookups = lookups if lookups is not None else NormalizationLookups()

    claims: list[CandidateClaim] = []
    for index, extraction in enumerate(extractions):
        if not isinstance(extraction, EvidenceExtraction):
            raise ClaimGenerationError(
                f"extractions[{index}] is not an EvidenceExtraction: {extraction!r}"
            )
        hint = EntityTypingHint()
        if typing_hints is not None and typing_hints[index] is not None:
            hint = typing_hints[index]
        claims.append(_generate_one(extraction, hint, resolved_lookups))

    return claims


__all__ = ["generate_candidate_claims"]
