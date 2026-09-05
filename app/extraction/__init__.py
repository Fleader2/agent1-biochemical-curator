"""Structured evidence extraction: text -> validated, source-grounded statements.

This is Increment 12. It sits *before* everything Increments 4-11 already
built (``app.normalization.*``, ``app.persistence.*``): those turn an
already-decomposed, already-identified statement into database rows; this
package turns a raw source passage into that already-decomposed statement
in the first place. Nothing here writes to the database, resolves an
entity to a UUID, generates a ``Claim``, creates an ``Evidence`` row, or
scores confidence -- see each of those as a distinct, later increment.

**Philosophy** (Increment 12 instructions): this is not a paper
summarizer, not a knowledge-graph builder, not an inference engine. It
extracts only what one source passage explicitly states, never combining
information across passages or papers, and never inferring an entity
identifier, mechanism, or relationship the passage does not state
directly.

**Pipeline** (see ``app.extraction.types`` for the full data contract):

1. Something upstream of this package -- an LLM call following
   ``app.extraction.prompts.EVIDENCE_EXTRACTION_PROMPT``, or a human
   curator -- produces one or more ``CandidateStatement`` records for a
   source passage. This package never does this step itself.
2. ``app.extraction.prompts.candidate_statement_from_prompt_fields`` may be
   used to parse a raw prompt-contract payload (a mapping of the prompt's
   field names to string values) into a ``CandidateStatement``, raising
   ``LLMFormattingError`` for anything wrong with the payload's shape.
3. ``app.extraction.extractor.extract_evidence`` validates and grounds
   each ``CandidateStatement`` against the real source passage text,
   producing immutable, self-validated ``EvidenceExtraction`` objects --
   or raises one of ``app.extraction.errors``'s structured exceptions if a
   candidate cannot be validated or grounded.

**Where this feeds next** (out of scope for this increment): a later
increment converts a list of ``EvidenceExtraction`` objects into
``Claim``/``Evidence`` rows, at which point ``subject_text``/
``organism_text``/etc. finally get resolved through
``app.normalization.*`` into real entity UUIDs, and confidence scoring/
ranking/contradiction detection can begin. None of that happens here.
"""

from __future__ import annotations
