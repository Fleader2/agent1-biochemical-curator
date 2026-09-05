"""Claim Generation: EvidenceExtraction -> CandidateClaim.

This is Increment 13. It sits directly after Increment 12
(``app.extraction.*``) and directly before a future Confidence Scoring and
Persistence increment: it consumes immutable ``EvidenceExtraction``
objects and produces immutable ``CandidateClaim`` objects, using the
already-existing normalization layer (``app.normalization.*``) to resolve
entity mentions -- never duplicating that layer's own identity-comparison
logic.

**What this package does not do**, by design: it does not persist a
``Claim`` or ``Evidence`` row, does not score confidence, does not merge
duplicate claims, does not resolve contradictions, does not rank evidence,
and does not perform any reasoning across more than one
``EvidenceExtraction`` at a time. See each of those as a distinct, later
increment.

**Pipeline** (see ``app.claim_generation.types`` for the full data
contract):

1. Some caller (a coordinator, or a later LLM-assisted step following
   ``app.claim_generation.prompts.CLAIM_GENERATION_PROMPT``) decides, for
   a given ``EvidenceExtraction``, what kind of entity its subject and
   object are -- an ``EntityTypingHint``. This package never infers this
   itself (Increment 13 instructions, Step 5).
2. ``app.claim_generation.generator.generate_candidate_claims`` resolves
   each entity mention (subject, object, organism, compartment) through
   the appropriate ``app.normalization.*`` module -- via
   ``app.claim_generation.mapping``'s dispatch -- and assembles a
   validated, immutable ``CandidateClaim`` for every supplied
   ``EvidenceExtraction``.

**Where this feeds next** (out of scope for this increment): a later
increment scores each ``CandidateClaim``'s confidence (drawing on its
``directness``, ``evidence_type``, and how well its entity references
resolved) and, once scored, persists it as a real ``Claim`` row (plus an
``Evidence`` row carrying its ``supporting_span``/quoted text) via a new
``app.persistence.claim`` module following the same pattern
``app.persistence.*`` already established for every other entity. None of
that happens here.
"""

from __future__ import annotations
