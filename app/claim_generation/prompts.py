"""The deterministic Claim Generation prompt contract.

Unlike ``app.extraction.prompts.EVIDENCE_EXTRACTION_PROMPT``, no existing
prompt in ``docs/03_agent_behavior.md`` covers this step -- that
document's "Required Prompts" section defines an Evidence Extraction
Prompt, a Kinetics/Regulation Extraction Prompt, a Scientific Critic
Prompt, and others, but nothing for turning an already-extracted statement
into a typed claim. ``CLAIM_GENERATION_PROMPT`` below is therefore new,
not transcribed -- written to satisfy Increment 13's own explicit
requirements (Step 21): no inference, literal predicates, explicit
negatives, one claim per assertion, preservation of grounding, unresolved
entities remain unresolved.

**What this prompt is for.** ``app.claim_generation.generator.
generate_candidate_claims`` itself never infers a subject's or object's
entity kind from its text (Increment 13 instructions, Step 5) -- deciding
whether "FadD" should be typed ``GENE`` or ``PROTEIN`` for one particular
statement requires reading that statement's own context (e.g. "the fadD
gene" vs. "the FadD protein"), which this deterministic package
deliberately does not attempt itself. This prompt is what a later,
separate LLM-assisted step would follow to produce that typing decision
from an ``EvidenceExtraction``, which is then supplied to
``generate_candidate_claims`` as an ``EntityTypingHint`` --
``entity_typing_hint_from_prompt_fields`` below parses that step's raw
output into one.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.claim_generation.errors import EntityTypingError
from app.claim_generation.types import EntityKind, EntityTypingHint

CLAIM_GENERATION_PROMPT = """\
Examine the supplied EvidenceExtraction. It already contains a subject,
predicate, object (if any), and supporting quotation grounded in one
source passage. Your task is only to classify the entity kind of the
subject and, if present, the object -- nothing else about the statement
may be changed.

Do not infer a mechanism, relationship, or entity the extraction does not
already state. Do not paraphrase the subject or object text. Do not
resolve a pronoun or expand an abbreviation the extraction left as-is.

For SUBJECT_TYPE and OBJECT_TYPE, choose exactly one value from this list,
based only on how the passage itself refers to the entity (e.g. "the fadD
gene" is GENE; "the FadD protein" is PROTEIN; a named small molecule is
COMPOUND):

ORGANISM
PUBLICATION
GENE
PROTEIN
COMPOUND
COMPARTMENT
REACTION
UNKNOWN

If the passage's own wording does not make the entity kind unambiguous,
or the object slot is empty, answer UNKNOWN (for OBJECT_TYPE, UNKNOWN also
covers "there is no object"). Never guess a kind from the entity's name
alone (e.g. capitalization, gene-symbol-like formatting) -- only from what
the passage explicitly says the entity is.

Preserve the statement's polarity exactly. If the passage states a
negative finding (e.g. "did not bind", "no increase was observed", "failed
to detect"), that negative predicate must be classified the same as any
other predicate -- never rewritten as a positive assertion and never
dropped.

Extract one classification per EvidenceExtraction. Do not merge or split
statements: this step only classifies entities the extraction already
decomposed.

Return exactly:

SUBJECT_TYPE

OBJECT_TYPE
"""


_KIND_BY_NAME: dict[str, EntityKind] = {kind.value: kind for kind in EntityKind}
_NOT_STATED = "NOT STATED"


def _parse_kind(raw_value: str | None, *, field_name: str) -> EntityKind:
    if raw_value is None or raw_value == _NOT_STATED:
        return EntityKind.UNKNOWN
    try:
        return _KIND_BY_NAME[raw_value]
    except KeyError as exc:
        raise EntityTypingError(
            f"{field_name} value {raw_value!r} is not a recognized EntityKind"
        ) from exc


def entity_typing_hint_from_prompt_fields(raw: Mapping[str, str | None]) -> EntityTypingHint:
    """Parse one raw ``{SUBJECT_TYPE, OBJECT_TYPE}`` payload into an ``EntityTypingHint``.

    Both keys are optional -- an absent key is treated exactly like
    ``UNKNOWN`` (Increment 13's safe default: no key means no typing
    information was supplied, which is not distinguishable from "the
    passage's wording did not make it unambiguous"). Raises
    ``EntityTypingError`` for a value that is not one of ``EntityKind``'s
    members.
    """
    subject_kind = _parse_kind(raw.get("SUBJECT_TYPE"), field_name="SUBJECT_TYPE")
    object_kind = _parse_kind(raw.get("OBJECT_TYPE"), field_name="OBJECT_TYPE")
    return EntityTypingHint(subject_kind=subject_kind, object_kind=object_kind)


__all__ = ["CLAIM_GENERATION_PROMPT", "entity_typing_hint_from_prompt_fields"]
