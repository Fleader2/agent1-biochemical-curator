"""The deterministic evidence-extraction prompt contract.

``EVIDENCE_EXTRACTION_PROMPT`` is a fixed string, not a template -- calling
code may prepend the source passage and publication metadata, but this
module never interpolates anything into it and never calls an LLM itself
(Increment 12's instructions list "LLM prompt optimization" as explicitly
out of scope). Its base content is transcribed, not paraphrased, from
``docs/03_agent_behavior.md``'s "Evidence Extraction Prompt" section (one
of that document's "Required Prompts") -- this module extends it with one
disclosed addition, documented below, rather than silently diverging from
what is already specified.

**Extension over ``docs/03_agent_behavior.md``'s original prompt.** The
original field list (``SUBJECT`` .. ``AUTHOR_CERTAINTY``) has no field
asking for an exact, verbatim quotation or a structured source location --
only ``SOURCE_LOCATION`` (an unstructured description) and
``CURATOR_SUMMARY`` (a paraphrase). Increment 12's own instructions (Steps
3, 4, 6) require byte-exact grounding: a quoted passage whose exact
character offsets can be verified against the real source text. Asking an
LLM to also invent numeric character offsets would be unreliable (Step 13:
"Never infer ... from context"), so this prompt instead requires
``QUOTED_TEXT`` -- an exact, verbatim substring of the source -- and lets
``app.extraction.extractor.ground_candidate`` locate that substring's
offsets deterministically in code, never asking the model for the numbers
directly. ``FIGURE_REFERENCE``/``TABLE_REFERENCE``/
``SUPPLEMENTARY_REFERENCE`` similarly replace the vaguer
``SOURCE_LOCATION`` field with the structured references Increment 12's
Step 11 asks for. This is an addition, not a contradiction: every field
name from the original prompt that has a direct counterpart is preserved
verbatim (see ``candidate_statement_from_prompt_fields``'s alias table for
the exact correspondence), and nothing already specified was removed.

**Fields with no direct schema-backed counterpart.** ``AUTHOR_CERTAINTY``
from the original prompt is intentionally not requested here --
``app.extraction.types.Directness`` already captures the
observation-vs-interpretation axis (see that module's docstring for why a
separate field was not added). ``EXPERIMENTAL_CONDITIONS`` (a broad,
unstructured field in the original prompt covering things like medium,
temperature, and pH) has no single dedicated slot in
``CandidateStatement``'s fixed field list; this prompt keeps requesting it
under that name and ``candidate_statement_from_prompt_fields`` maps it into
``notes`` (documented there) rather than inventing new dedicated fields
Increment 12's instructions did not ask for.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.extraction.errors import LLMFormattingError
from app.extraction.types import CandidateStatement, Directness
from app.models.enums import EvidenceType

EVIDENCE_EXTRACTION_PROMPT = """\
Examine the supplied scientific source passage.

Extract only statements directly supported by this one passage. Do not
combine information from any other passage, section, or publication. Do
not summarize an "overall conclusion" across multiple experiments -- each
experiment remains a separate statement.

For each statement, return exactly the following fields. Use the literal
text "NOT STATED" for any field the passage does not address -- never
guess, infer, or fill in a plausible-sounding value.

QUOTED_TEXT
    The exact, verbatim quotation from the passage that supports this
    statement -- copied character-for-character, with no paraphrasing, no
    ellipsis, and no correction of the source's own spelling or
    formatting. This is the single passage this statement is grounded in.

SUBJECT
    The subject of the statement (e.g. a gene, protein, compound, or
    pathway name), exactly as named in the passage. Do not resolve
    abbreviations, expand acronyms, or substitute a systematic name for a
    common one.

PREDICATE
    The relationship or action asserted, exactly as stated. Do not infer
    a mechanism the passage does not state explicitly.

OBJECT_OR_VALUE
    The object of the statement, or the reported value if the statement is
    a measurement. "NOT STATED" if the predicate has no object (e.g. an
    intransitive statement).

QUALIFIER
    Any explicit qualifier attached to the statement (e.g. "in vitro",
    "under anaerobic conditions"). "NOT STATED" if none is given -- never
    invent one.

CLAIM_CATEGORY
    The kind of statement this is, in the passage's own terms, if the
    passage itself categorizes it. "NOT STATED" otherwise.

ORGANISM
    The organism this statement concerns, exactly as named. If the
    passage's organism differs from the organism you were asked to
    curate, retain the passage's own organism -- never silently transfer a
    conclusion to a different organism.

STRAIN
    The strain, exactly as named. "NOT STATED" if none is given.

COMPARTMENT
    The subcellular compartment, exactly as named. "NOT STATED" if none is
    given.

EVIDENCE_TYPE
    One of the following categories, chosen only from this list:
    DIRECT_BIOCHEMICAL, DIRECT_IN_VIVO, GENETIC, LOCALIZATION, PROTEOMICS,
    METABOLOMICS, FLUXOMICS, TRANSCRIPTOMICS, STRUCTURAL,
    CURATED_DATABASE, COMPUTATIONAL, HOMOLOGY, REVIEW, AUTHOR_HYPOTHESIS,
    OTHER. If the authors speculate -- using language such as "may",
    "might", "suggests", "could", "possibly", "appears to", or "is
    consistent with" -- classify the statement as AUTHOR_HYPOTHESIS rather
    than as a direct experimental category, even if it otherwise resembles
    one.

DIRECTNESS
    Exactly one of: AUTHORS_OBSERVED, AUTHORS_INFERRED, AUTHORS_PROPOSED,
    AUTHORS_DISCUSSED, REVIEW_SUMMARIZES, DATABASE_ANNOTATES. Do not
    promote discussion or speculative text into AUTHORS_OBSERVED.

EXPERIMENTAL_METHOD
    The experimental system or method used, exactly as named. "NOT
    STATED" if none is given.

ASSAY
    The specific assay performed, exactly as named. "NOT STATED" if none
    is given.

PERTURBATION
    Any explicit genetic or chemical perturbation (e.g. "knockout",
    "overexpression", a named inhibitor). "NOT STATED" if none is given.

EXPERIMENTAL_CONDITIONS
    Any other explicit experimental context stated in the passage (medium,
    temperature, pH, growth phase, time point, instrument). "NOT STATED"
    if none is given. Do not infer conditions the passage does not state.

MEASURED_QUANTITY
    The name of the quantity measured (e.g. "specific activity", "growth
    rate"). "NOT STATED" if this statement is not a measurement.

MEASUREMENT_VALUE
    The reported value, exactly as printed (including any reported
    uncertainty, e.g. "12.3 +/- 0.4"). Never convert units, never average
    across replicates, never round.

MEASUREMENT_UNITS
    The reported units, exactly as printed. "NOT STATED" if the value is
    unitless.

STATISTICAL_SUPPORT
    Any reported statistical support (p-value, confidence interval,
    replicate count), exactly as printed. "NOT STATED" if none is given.

FIGURE_REFERENCE
    Any figure this statement cites (e.g. "Figure 2A"). "NOT STATED" if
    none is given. Do not attempt to interpret the figure itself.

TABLE_REFERENCE
    Any table this statement cites (e.g. "Table 1"). "NOT STATED" if none
    is given.

SUPPLEMENTARY_REFERENCE
    Any supplementary material this statement cites (e.g. "Supplementary
    Figure 3"). "NOT STATED" if none is given.

NOTES
    Anything else explicitly relevant that does not fit another field.
    "NOT STATED" if nothing applies. Never use this field to add inference
    or interpretation the passage does not itself state.

Extract one such record per independently supported statement -- zero,
one, or many per passage. If the passage supports no statement at all,
return no records. If a pronoun or reference is ambiguous, preserve the
ambiguity in the extracted text rather than resolving it yourself.
"""


_DIRECT_FIELDS: dict[str, str] = {
    "SUBJECT": "subject_text",
    "PREDICATE": "predicate_text",
    "EVIDENCE_TYPE": "evidence_type",
    "DIRECTNESS": "directness",
    "OBJECT_TEXT": "object_text",
    "QUALIFIER_TEXT": "qualifier_text",
    "NORMALIZED_TEXT": "normalized_text",
    "ORGANISM_TEXT": "organism_text",
    "STRAIN_TEXT": "strain_text",
    "COMPARTMENT_TEXT": "compartment_text",
    "CLAIM_CATEGORY": "claim_category",
    "EXPERIMENTAL_SYSTEM": "experimental_system",
    "ASSAY": "assay",
    "PERTURBATION": "perturbation",
    "MEASURED_QUANTITY": "measured_quantity",
    "MEASUREMENT_VALUE": "measurement_value",
    "MEASUREMENT_UNITS": "measurement_units",
    "STATISTICAL_SUPPORT": "statistical_support",
    "FIGURE_REFERENCE": "figure_reference",
    "TABLE_REFERENCE": "table_reference",
    "SUPPLEMENTARY_REFERENCE": "supplementary_reference",
    "NOTES": "notes",
    "QUOTED_TEXT": "quoted_text",
    "CHARACTER_START": "character_start",
    "CHARACTER_END": "character_end",
    "PARAGRAPH_INDEX": "paragraph_index",
    "SENTENCE_INDEX": "sentence_index",
}

# Historical field names from docs/03_agent_behavior.md's original Evidence
# Extraction Prompt, mapped onto this module's field names -- see module
# docstring's "Extension" section for why each mapping was chosen.
_ALIAS_FIELDS: dict[str, str] = {
    "OBJECT_OR_VALUE": "object_text",
    "QUALIFIER": "qualifier_text",
    "ORGANISM": "organism_text",
    "STRAIN": "strain_text",
    "COMPARTMENT": "compartment_text",
    "EXPERIMENTAL_METHOD": "experimental_system",
    "CURATOR_SUMMARY": "normalized_text",
    "EXPERIMENTAL_CONDITIONS": "notes",
}

# Recognized but deliberately unused -- see module docstring's "Fields with
# no direct schema-backed counterpart" section.
_IGNORED_FIELDS = frozenset({"AUTHOR_CERTAINTY", "SOURCE_LOCATION"})

_REQUIRED_TARGET_FIELDS = ("quoted_text", "subject_text", "predicate_text")
_NOT_STATED = "NOT STATED"

_INT_FIELDS = frozenset(
    {"character_start", "character_end", "paragraph_index", "sentence_index"}
)


def candidate_statement_from_prompt_fields(
    raw: Mapping[str, str | int | None],
) -> CandidateStatement:
    """Parse one raw prompt-contract record into a ``CandidateStatement``.

    ``raw`` is expected to use this module's field names (see
    ``EVIDENCE_EXTRACTION_PROMPT``) or the historical
    ``docs/03_agent_behavior.md`` names listed in ``_ALIAS_FIELDS`` above.
    The literal string ``"NOT STATED"`` (the prompt's required placeholder
    for an inapplicable field) is treated exactly like an absent/``None``
    value.

    Raises ``LLMFormattingError`` -- never ``ExtractionValidationError`` or
    ``MalformedSpanError`` -- for anything wrong with the raw payload's
    *shape*: an unrecognized key, a missing required key, an
    ``EVIDENCE_TYPE``/``DIRECTNESS`` value outside its controlled
    vocabulary, or a non-integer offset/index. Once the payload has been
    successfully parsed into typed values, constructing the
    ``CandidateStatement`` itself may still raise
    ``ExtractionValidationError``/``MalformedSpanError`` for a genuine
    content problem (e.g. measurement units with no value) -- those are
    deliberately not caught or reclassified here, since they are the same
    kind of failure regardless of whether the ``CandidateStatement`` was
    parsed from a raw payload or constructed directly.
    """
    fields: dict[str, object] = {}
    for key, value in raw.items():
        if key in _IGNORED_FIELDS:
            continue
        target = _DIRECT_FIELDS.get(key) or _ALIAS_FIELDS.get(key)
        if target is None:
            raise LLMFormattingError(f"unrecognized evidence-extraction field: {key!r}")
        fields[target] = None if value == _NOT_STATED else value

    missing = [name for name in _REQUIRED_TARGET_FIELDS if not fields.get(name)]
    if missing:
        raise LLMFormattingError(
            f"evidence-extraction payload is missing required field(s): {', '.join(missing)}"
        )

    raw_evidence_type = fields.pop("evidence_type", None)
    if not raw_evidence_type:
        raise LLMFormattingError(
            "evidence-extraction payload is missing required field: EVIDENCE_TYPE"
        )
    try:
        evidence_type = EvidenceType(raw_evidence_type)
    except ValueError as exc:
        raise LLMFormattingError(
            f"EVIDENCE_TYPE value {raw_evidence_type!r} is not a recognized EvidenceType"
        ) from exc

    raw_directness = fields.pop("directness", None)
    if not raw_directness:
        raise LLMFormattingError(
            "evidence-extraction payload is missing required field: DIRECTNESS"
        )
    try:
        directness = Directness(raw_directness)
    except ValueError as exc:
        raise LLMFormattingError(
            f"DIRECTNESS value {raw_directness!r} is not a recognized Directness"
        ) from exc

    for field_name in _INT_FIELDS:
        value = fields.get(field_name)
        if value is None:
            continue
        try:
            fields[field_name] = int(value)
        except (TypeError, ValueError) as exc:
            raise LLMFormattingError(f"{field_name} must be an integer, got {value!r}") from exc

    return CandidateStatement(evidence_type=evidence_type, directness=directness, **fields)


__all__ = [
    "EVIDENCE_EXTRACTION_PROMPT",
    "candidate_statement_from_prompt_fields",
]
