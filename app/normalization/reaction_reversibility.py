"""Reaction reversibility resolution from explicit source evidence (Agent 1.x Increment C.8).

**Why ``Reaction.reversible`` has stayed ``None`` for every real reaction
to date.** Confirmed by direct inspection of every currently-connected
source in this repository, live, this increment:

* **KEGG** (``app.connectors.kegg``/``app.pathway_curation.equation_parser``):
  a REACTION entry's own flat-file format has no field for reversibility
  distinct from its ``EQUATION`` line's arrow token (`` <=> `` vs ``=>``/
  ``->``/``<-``) -- confirmed live against KEGG's real REST API (no
  ``EQUATION``-adjacent field states direction independently; see, e.g.,
  ``rn:R05188``'s real flat-file text, which has no such field). This
  repository's own foundational specification
  (``docs/03_agent_behavior.md``, "Reversibility Behavior") already,
  explicitly, deliberately forbids inferring reversibility "solely from...
  arrow notation in a database" -- ``equation_parser.parse_kegg_equation``'s
  own module docstring already documents this exact policy and reports the
  arrow token's own boolean back to its caller *only* as a description of
  "what the arrow token literally is," never as ``Reaction.reversible``
  evidence. This module does not revisit that decision; it enforces it in
  one dedicated, testable place (``reversibility_evidence_from_kegg_equation``,
  below) rather than leaving it implicit. Confirmed live, this increment,
  as further, independent justification: KEGG's own ``<=>`` arrow is used
  near-universally across its REACTION database regardless of true
  biochemistry (a well-documented KEGG modeling convention, not a
  per-reaction claim) -- even a real, textbook-irreversible reaction (e.g.
  hexokinase, EC 2.7.1.1) is written with ``<=>`` in KEGG.
* **SABIO-RK** (``app.connectors.sabiork``): its real ``kineticlaw`` JSON
  section carries an explicit, structured ``reversible`` field (undocumented
  anywhere in this repository until this increment's own live inspection) --
  but a broad, live sample this increment took (67 entries: all 7 real yeast
  FAS entries plus 60 entries spanning EC 2.7.1.1 and an unfiltered range of
  low `EntryID`s) reports the identical literal value, ``"reversible"``, on
  **every single entry sampled**, with zero variation. A field that never
  varies carries no discriminating, per-reaction information regardless of
  how "explicit" its syntax looks -- using it would functionally assign
  every reaction ``reversible=True``, which is not evidence-based curation.
  ``reversibility_evidence_from_sabiork_kineticlaw`` (below) documents this
  finding and, like the KEGG function, always returns ``None`` today.
* **BRENDA** (``app.connectors.brenda``): only 7 SOAP methods are
  currently implemented (``getKmValue``/``getKiValue``/
  ``getTurnoverNumber``/``getKcatKmValue``/``getPhOptimum``/
  ``getTemperatureOptimum``/``getSpecificActivity``) -- none reports
  reversibility or an equilibrium constant. BRENDA's real API does expose
  a ``getEquilibriumConstant`` method, but even if it were added, a raw
  measured K_eq value alone would require an arbitrary numeric threshold to
  become a boolean -- exactly the "numeric scoring" this increment's own
  instructions forbid -- so it would not qualify as explicit reversibility
  evidence on its own even with a new connector method.
* **OED, SGD, UniProt, PubMed**: none is a reaction-kinetics or
  reaction-directionality source at all.
* **Rhea/BioCyc/MetaCyc**: ``SourceType`` already reserves ``RHEA``/
  ``BIOCYC``/``METACYC`` members (and ``ReactionIdentity``/
  ``ReactionCandidate`` already reserve ``rhea_id``/``metacyc_reaction_id``
  fields), but **no connector for any of the three exists in this
  repository**. Rhea's own real, live, officially documented REST API
  (``https://www.rhea-db.org/help/rest-api``, confirmed this increment) has
  no "direction" column at all among its documented, queryable fields
  (``rhea-id``/``equation``/``chebi``/``chebi-id``/``ec``/``uniprot``/``go``/
  ``pubmed``/``reaction-xref(...)``) -- Rhea's well-known convention of four
  related identifiers per reaction (undefined/left-to-right/right-to-left/
  bidirectional) is real and public, but is not exposed through this REST
  surface; obtaining it would require ingesting Rhea's full RDF/OBO data
  release, a substantially larger integration than this tightly-scoped
  increment's own bounds. BioCyc's real API additionally requires a paid
  subscription for most organism databases. Building any of the three is
  therefore, correctly, out of this increment's own scope -- consistent
  with this repository's already-established precedent of never fabricating
  a connector to a source it does not yet call
  (``app.normalization.compound``'s own module docstring: "No ChEBI,
  PubChem, or MetaCyc connector exists yet... fabricating one... would be
  speculative, not justified by existing code").

**What this module actually provides**: a deterministic, source-neutral
evidence type (``ReversibilityEvidence``) and combining function
(``resolve_reaction_reversibility``) -- real, tested infrastructure that a
future Rhea/BioCyc connector (or a future BRENDA categorical-annotation
method, should one ever exist) can plug into with no further plumbing
changes, plus the two source-specific extractor functions above that
correctly, deterministically decline to produce evidence from either
currently-connected source, each with its own precise, documented, and
now machine-verified rationale rather than a bare, unexplained ``None``.

**Precedence is documentation, not a runtime override.** This increment's
own evidence-priority guidance (Rhea > BioCyc/MetaCyc > a documented,
deterministic KEGG signal > other structured sources) states which source
this repository should integrate *first*, were any of them to become
available -- it is never used to let one source silently outvote another
at combination time. ``resolve_reaction_reversibility`` never picks a
"winning" source: when supplied evidence disagrees at all, the result is
always ``reversible=None`` plus an explicit, disclosed conflict,
regardless of which sources are involved or in what priority order they
would otherwise be preferred.

**No numeric scoring, no LLM adjudication, no inference from EC number,
reaction name, pathway position, enzyme identity, mass-action assumptions,
or thermodynamic intuition** -- every evidence-producing function accepts
only a source's own already-structured, already-explicit field and either
returns one definite ``bool`` claim or ``None``; nothing here computes,
guesses, or estimates a value.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import SourceType
from app.normalization.identifiers import require_non_empty


@dataclass(frozen=True, slots=True)
class ReversibilityEvidence:
    """One source's own explicit, structured claim about one reaction's reversibility.

    ``reversible`` is a definite ``bool`` -- the very existence of a
    ``ReversibilityEvidence`` instance already *is* the positive claim;
    "the source said nothing" is represented by producing no evidence
    object at all, never by a ``None`` value on this type. ``basis``
    records, in one short, human-readable sentence, exactly what
    structured field was inspected and what it said -- never free
    narrative, and never itself re-interpreted by anything downstream.
    """

    source: SourceType
    source_identifier: str
    reversible: bool
    basis: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        object.__setattr__(self, "basis", require_non_empty(self.basis, field_name="basis"))


@dataclass(frozen=True, slots=True)
class ReversibilityResolution:
    """The deterministic outcome of combining zero or more ``ReversibilityEvidence`` claims.

    ``reversible`` is ``True``/``False`` only when every supplied evidence
    item agrees; it is ``None`` both when no evidence was supplied at all
    and when supplied evidence conflicts (``conflicting`` distinguishes the
    two cases). ``evidence`` always preserves every claim it was given,
    regardless of outcome -- including on a conflict, so the disagreement
    itself remains fully auditable, and including when all evidence
    agrees, so provenance from every agreeing source is preserved (never
    collapsed down to just one).
    """

    reversible: bool | None
    conflicting: bool
    evidence: tuple[ReversibilityEvidence, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.conflicting and self.reversible is not None:
            raise ValueError(
                "ReversibilityResolution.reversible must be None when conflicting=True"
            )
        object.__setattr__(self, "reason", require_non_empty(self.reason, field_name="reason"))


def resolve_reaction_reversibility(
    evidence: tuple[ReversibilityEvidence, ...],
) -> ReversibilityResolution:
    """Deterministically combine zero or more explicit reversibility claims.

    * No evidence at all -> ``reversible=None``, ``conflicting=False``.
    * Every evidence item agrees (a single distinct ``bool`` value) ->
      that value, ``conflicting=False`` -- regardless of how many sources
      contributed it, or which one a precedence list would otherwise
      prefer (see module docstring: precedence is documentation, never a
      runtime override).
    * Evidence disagrees (more than one distinct ``bool`` value present) ->
      ``reversible=None``, ``conflicting=True`` -- never a silent choice
      between them.

    Pure and deterministic: no database, no network, no numeric scoring,
    no LLM call.
    """
    if not evidence:
        return ReversibilityResolution(
            reversible=None,
            conflicting=False,
            evidence=(),
            reason="no explicit reversibility evidence supplied",
        )

    distinct_values = {item.reversible for item in evidence}
    if len(distinct_values) == 1:
        (value,) = distinct_values
        sources = ", ".join(f"{item.source.value}:{item.source_identifier}" for item in evidence)
        return ReversibilityResolution(
            reversible=value,
            conflicting=False,
            evidence=evidence,
            reason=f"{len(evidence)} source(s) agree (reversible={value}): {sources}",
        )

    conflict_detail = ", ".join(
        f"{item.source.value}:{item.source_identifier}=reversible:{item.reversible}"
        for item in evidence
    )
    return ReversibilityResolution(
        reversible=None,
        conflicting=True,
        evidence=evidence,
        reason=f"conflicting reversibility evidence across sources: {conflict_detail}",
    )


def reversibility_evidence_from_kegg_equation(
    kegg_reaction_id: str, equation: str | None
) -> ReversibilityEvidence | None:
    """Always returns ``None`` -- KEGG's own equation arrow does not qualify as reversibility
    evidence.

    Deliberately never inspects ``equation``'s own arrow token at all
    (unlike ``app.pathway_curation.equation_parser.parse_kegg_equation``,
    which parses the arrow only to separate reactants from products, and
    whose own module docstring already establishes this same policy for
    that separate purpose). This function exists so "KEGG never qualifies"
    is a real, callable, testable decision in reversibility resolution's
    own call graph -- not merely an absence of a KEGG adapter, and not
    something a future contributor could accidentally "complete" by
    reading the arrow -- see the module docstring's full rationale
    (``docs/03_agent_behavior.md``'s explicit prohibition, plus this
    increment's own live confirmation that ``<=>`` is used near-
    universally in KEGG's REACTION database regardless of true
    biochemistry).
    """
    return None


def reversibility_evidence_from_sabiork_kineticlaw(
    source_id: str, kinlaw_reversible: str | None
) -> ReversibilityEvidence | None:
    """Always returns ``None`` -- SABIO-RK's own ``kineticlaw.reversible`` field does not
    qualify as reversibility evidence.

    Confirmed live, this increment: a broad sample of 67 real SABIO-RK
    entries (all 7 real yeast FAS EC 2.3.1.86 entries, plus 60 entries
    spanning EC 2.7.1.1 and an unfiltered low-``EntryID`` range) reports
    the identical literal value, ``"reversible"``, on every single entry
    -- zero variation observed. A field that never varies across a broad,
    diverse sample carries no discriminating, per-reaction information,
    regardless of how explicit and structured its syntax looks; treating
    it as evidence would functionally assign ``reversible=True`` to every
    reaction, which is not evidence-based curation. This function exists
    so that finding is a real, callable, testable decision, not a bare
    unexplained absence.
    """
    return None


__all__ = [
    "ReversibilityEvidence",
    "ReversibilityResolution",
    "resolve_reaction_reversibility",
    "reversibility_evidence_from_kegg_equation",
    "reversibility_evidence_from_sabiork_kineticlaw",
]
