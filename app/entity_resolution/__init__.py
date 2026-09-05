"""Entity Mention Resolution and Strong-Identifier Enrichment.

This is Increment 14 (extended by Increment 15 to add Protein support via
UniProt). It solves a gap Increment 13 (``app.claim_generation``)
explicitly reported: ordinary biological text supplies names, but the
existing normalizers (``app.normalization.*``) intentionally treat
name-only input as weak, candidate-generation-only signal and rarely
return ``MATCHED``. This package retrieves *strong* identifiers (an SGD
id, a KEGG compound/reaction id, a verified PMID, a verified UniProt
accession) from trusted external sources for a given textual mention, so
that the *existing, unmodified* normalizers have a real chance of
returning ``MATCHED``.

**Core Rule**: retrieval proposes identity candidates; normalization
decides identity. This package never itself declares two entities the
same, never bypasses `app.normalization.*`, and never weakens any
existing normalizer's rules to improve its own match rate.

**Pipeline**:

1. Some caller (a coordinator, or a future integration with
   ``app.claim_generation`` -- see below) builds an ``EntityMention`` for
   one textual entity mention, with whatever organism/strain context is
   already known.
2. ``app.entity_resolution.resolver.resolve_entity_mention`` dispatches by
   the mention's ``entity_kind`` to the one trustworthy connector this
   repository has for it today (SGD for genes, KEGG for compounds and
   reactions, PubMed for publications, UniProt for proteins -- see this
   increment's completion report for the full inventory), via
   ``app.entity_resolution.adapters``.
3. Each retrieved external record is converted to an existing
   ``app.normalization.*`` ``*Identity`` using an *existing* adapter
   (``gene_identity_from_sgd``, ``compound_identity_from_kegg``,
   ``reaction_identity_from_kegg``, ``publication_identity_from_pubmed``,
   ``protein_identity_from_uniprot`` -- none invented here) and run through
   the corresponding, completely unmodified normalizer.
4. ``app.entity_resolution.ranking`` classifies the resulting set of
   already-normalized candidates into one ``MentionResolutionResult`` --
   never choosing a candidate itself, only reporting what normalization
   already established.

**No persistence of any kind** occurs anywhere in this package: no
database write, no ``SourceCrossReference``, no ``ExternalRecord``, no
``Claim``/``Evidence`` row.

**Relationship to Claim Generation** (Increment 13): this package is
exposed independently and is *not* wired into
``app.claim_generation.generate_candidate_claims`` in this increment --
doing so safely would require Claim Generation to accept an entity
mention resolver as an alternative (or preceding) path to its existing
bare-text normalization dispatch, which this increment's own instructions
say not to do without a clean, narrow interface. See this increment's
completion report for the planned integration shape.
"""

from __future__ import annotations
