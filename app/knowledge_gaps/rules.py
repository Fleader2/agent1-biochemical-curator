"""Centralized policy and detection rules for Knowledge Gap Detection (Increment 21).

Every function here is a pure, deterministic transformation of already-
loaded ORM objects into ``KnowledgeGapCandidate`` values -- no query, no
write, no I/O of any kind (``app.knowledge_gaps.analysis`` owns all
database access). No LLM call, no randomness, no free-text heuristics:
every rule below either reads a closed enum column directly or compares
literal stored values for exact (in)equality.

**Severity policy is this package's own, not an authoritative
specification.** No document in this repository defines numeric or
categorical knowledge-gap severity (contrast ``ConfidenceClass``, which
docs/03_agent_behavior.md gives exact 0-100 thresholds for). ``GAP_SEVERITY``
below is a conservative starting policy this increment adopts and
discloses -- see ``docs/17_knowledge_gap_detection_contract.md`` §6 for the
full rationale behind each mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.extraction.types import Directness
from app.knowledge_gaps.errors import UnsupportedGapRuleError
from app.knowledge_gaps.types import GapSeverity, GapType, KnowledgeGapCandidate
from app.models.claim import Claim, Evidence
from app.models.compound import Compound
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType
from app.models.gene import Gene
from app.models.protein import Protein
from app.models.reaction import Reaction

# --- Severity policy (this package's own; see module docstring) ------------------

GAP_SEVERITY: dict[GapType, GapSeverity] = {
    # Structural impossibility for downstream modeling.
    GapType.REACTION_WITHOUT_PARTICIPANTS: GapSeverity.HIGH,
    # An explicit, persisted scientific conflict.
    GapType.CONFLICTING_CLAIMS: GapSeverity.HIGH,
    # Accepted knowledge with weak or absent numeric support.
    GapType.LOW_CONFIDENCE_CLAIM: GapSeverity.MODERATE,
    GapType.SINGLE_SOURCE_SUPPORT: GapSeverity.MODERATE,
    GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE: GapSeverity.MODERATE,
    # Informational provenance/context omissions.
    GapType.MISSING_PUBLICATION: GapSeverity.LOW,
    GapType.MISSING_EXPERIMENTAL_CONTEXT: GapSeverity.LOW,
    # Connectivity gaps -- real, but not demonstrated scientific deficiencies.
    GapType.REACTION_WITHOUT_ENZYME: GapSeverity.LOW,
    GapType.PROTEIN_WITHOUT_REACTION: GapSeverity.LOW,
    GapType.GENE_WITHOUT_PROTEIN: GapSeverity.LOW,
    GapType.ISOLATED_COMPOUND: GapSeverity.LOW,
}


def severity_for(gap_type: GapType) -> GapSeverity:
    """The policy severity for one ``GapType``. Raises if the table is incomplete."""
    try:
        return GAP_SEVERITY[gap_type]
    except KeyError as exc:
        raise UnsupportedGapRuleError(
            f"no severity policy exists for gap type {gap_type!r} -- every implemented "
            "GapType must have an entry in GAP_SEVERITY"
        ) from exc


# --- Evidence-type policy (Increment 21 instructions, Steps 14/16) ---------------

#: Explicitly named in the increment's own instructions (Step 14), grounded in
#: ``.cursor/rules/01-scientific-integrity.mdc``'s "Evidence Categories" list
#: distinguishing curated-database/computational/homology/review/author-
#: hypothesis from direct experimental evidence categories.
NON_PRIMARY_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.REVIEW,
        EvidenceType.CURATED_DATABASE,
        EvidenceType.COMPUTATIONAL,
        EvidenceType.HOMOLOGY,
        EvidenceType.AUTHOR_HYPOTHESIS,
    }
)

#: ``EvidenceType.OTHER`` is deliberately excluded from both the primary and
#: non-primary sets -- Step 14: "If evidence type is ambiguous or unmapped,
#: preserve uncertainty rather than labeling it primary/non-primary."
AMBIGUOUS_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset({EvidenceType.OTHER})

#: Directness values that make even a nominally-primary EvidenceType
#: non-primary in practice -- transcribed from the same closed set
#: ``app.confidence.aggregation``'s own ``_is_derivative`` already uses for
#: the identical concept (excluding derivative directness from the
#: replication count), not re-derived independently.
DERIVATIVE_DIRECTNESS: frozenset[Directness] = frozenset(
    {Directness.REVIEW_SUMMARIZES, Directness.DATABASE_ANNOTATES}
)

#: ``Evidence.directness`` is a plain, non-enum ``VARCHAR`` column (see
#: ``app/models/claim.py``), so it is compared against string values, not
#: ``Directness`` members directly.
_DERIVATIVE_DIRECTNESS_VALUES: frozenset[str] = frozenset(d.value for d in DERIVATIVE_DIRECTNESS)

#: SourceTypes that are literature citations -- a missing ``publication_id``
#: is only meaningful for these (Step 15). Database sources
#: (KEGG/BRENDA/BioCyc/MetaCyc/SGD/UniProt/ChEBI/Rhea/NCBI) are curated
#: records, not literature citations; the schema does not require them to
#: carry a ``Publication`` row at all (``Evidence.database_name``/
#: ``database_accession`` serve that role instead). ``SourceType.OTHER`` is
#: ambiguous and is not included, for the same "preserve uncertainty" reason
#: as ``AMBIGUOUS_EVIDENCE_TYPES``.
PUBLICATION_EXPECTED_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.PUBMED, SourceType.PMC}
)

#: EvidenceTypes describing an actual wet-lab/experimental measurement, where
#: "what conditions was this measured under" is a meaningful question (Step
#: 16). Deliberately excludes CURATED_DATABASE/COMPUTATIONAL/REVIEW (named
#: explicitly in Step 16) plus HOMOLOGY/AUTHOR_HYPOTHESIS (neither is an
#: experiment) and OTHER (ambiguous -- preserve uncertainty).
EXPERIMENTAL_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.DIRECT_BIOCHEMICAL,
        EvidenceType.DIRECT_IN_VIVO,
        EvidenceType.GENETIC,
        EvidenceType.LOCALIZATION,
        EvidenceType.PROTEOMICS,
        EvidenceType.METABOLOMICS,
        EvidenceType.FLUXOMICS,
        EvidenceType.TRANSCRIPTOMICS,
        EvidenceType.STRUCTURAL,
    }
)

#: The confidence classes that make an already-accepted/machine-reviewed
#: claim's numeric support a knowledge gap (Step 12). Both are treated
#: identically ("unknown" is never treated as "false" -- ``UNKNOWN`` is
#: absence of eligible evidence, not evidence of weakness, but both leave an
#: accepted claim without a demonstrated strong evidentiary basis, which is
#: exactly what this gap reports).
LOW_CONFIDENCE_CLASSES: frozenset[ConfidenceClass] = frozenset(
    {ConfidenceClass.LOW, ConfidenceClass.UNKNOWN}
)


def _is_primary_evidence(evidence: Evidence) -> bool:
    return (
        evidence.evidence_type not in NON_PRIMARY_EVIDENCE_TYPES
        and evidence.evidence_type not in AMBIGUOUS_EVIDENCE_TYPES
        and evidence.directness not in _DERIVATIVE_DIRECTNESS_VALUES
    )


def _evidence_source_identity(evidence: Evidence) -> tuple:
    """A deterministic "independent source" key for one ``Evidence`` row.

    ``publication_id`` when resolved; otherwise ``(source_type,
    source_id)`` -- the only currently-populated provenance
    (``Evidence.publication_id`` is always ``None`` in the current pipeline;
    see ``docs/15_claim_persistence_contract.md``). Never infers
    independence beyond what is actually stored (Step 13).
    """
    if evidence.publication_id is not None:
        return ("publication", evidence.publication_id)
    return ("source", evidence.source_type, evidence.source_id)


# --- Claim-anchored rules ---------------------------------------------------------


def detect_conflicting_claims(claims: Sequence[Claim]) -> list[KnowledgeGapCandidate]:
    """Structurally provable conflicts only (Step 11).

    Two independent, non-overlapping signals, both purely mechanical:

    1. ``Claim.status == ClaimStatus.CONFLICTED`` -- an explicit, already-
       persisted determination from some other process. Reported as-is, one
       candidate per such claim.
    2. Two or more claims sharing an identical resolved identity key
       (``subject_id``, ``predicate`` exact text, ``object_type``/
       ``object_id``, ``organism_id``, ``strain``) whose literal
       ``value_text``/``value_numeric`` disagree. ``subject_id``/
       ``organism_id`` must both be resolved (non-``None``) for two claims
       to be treated as being about "the same" entity at all -- two
       unresolved subjects are never assumed identical. No semantic
       reasoning about predicates, no LLM: exact-text/exact-value
       comparison only.
    """
    candidates: list[KnowledgeGapCandidate] = []

    for claim in claims:
        if claim.status is ClaimStatus.CONFLICTED:
            candidates.append(
                KnowledgeGapCandidate(
                    gap_type=GapType.CONFLICTING_CLAIMS,
                    severity=severity_for(GapType.CONFLICTING_CLAIMS),
                    entity_type="claim",
                    entity_id=claim.id,
                    predicate=claim.predicate,
                    explanation=(
                        f"Claim {claim.id} has status CONFLICTED, an explicit persisted "
                        "conflict determination."
                    ),
                    supporting_claim_ids=(claim.id,),
                    reason_codes=("CLAIM_STATUS_CONFLICTED",),
                )
            )

    groups: dict[tuple, list[Claim]] = {}
    for claim in claims:
        if claim.subject_id is None or claim.organism_id is None:
            continue
        key = (
            claim.subject_id,
            claim.predicate,
            claim.object_type,
            claim.object_id,
            claim.organism_id,
            claim.strain,
        )
        groups.setdefault(key, []).append(claim)

    for members in groups.values():
        if len(members) < 2:
            continue
        conflicting = False
        for i, first in enumerate(members):
            for second in members[i + 1 :]:
                if (
                    first.value_numeric is not None
                    and second.value_numeric is not None
                    and first.value_numeric != second.value_numeric
                ) or (
                    first.value_text is not None
                    and second.value_text is not None
                    and first.value_text != second.value_text
                ):
                    conflicting = True
                    break
            if conflicting:
                break
        if not conflicting:
            continue

        ordered_ids = tuple(sorted((member.id for member in members), key=str))
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.CONFLICTING_CLAIMS,
                severity=severity_for(GapType.CONFLICTING_CLAIMS),
                entity_type="claim",
                entity_id=ordered_ids[0],
                predicate=members[0].predicate,
                explanation=(
                    f"{len(members)} claims share the same subject/predicate/object/organism/"
                    "strain identity but disagree on their literal value_text/value_numeric."
                ),
                supporting_claim_ids=ordered_ids,
                reason_codes=("LITERAL_VALUE_DISAGREEMENT",),
            )
        )

    return candidates


def detect_low_confidence_claims(claims: Sequence[Claim]) -> list[KnowledgeGapCandidate]:
    """Accepted/machine-reviewed claims whose persisted confidence is weak (Step 12).

    Uses ``Claim.confidence_score``/``confidence_class`` exactly as
    persisted -- never recomputed.
    """
    candidates = []
    for claim in claims:
        if claim.confidence_class not in LOW_CONFIDENCE_CLASSES:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.LOW_CONFIDENCE_CLAIM,
                severity=severity_for(GapType.LOW_CONFIDENCE_CLAIM),
                entity_type="claim",
                entity_id=claim.id,
                predicate=claim.predicate,
                explanation=(
                    f"Claim {claim.id} has persisted confidence_class "
                    f"{claim.confidence_class.value} (confidence_score={claim.confidence_score!r})."
                ),
                supporting_claim_ids=(claim.id,),
                reason_codes=(f"CONFIDENCE_CLASS_{claim.confidence_class.value}",),
            )
        )
    return candidates


def detect_single_source_claims(claims: Sequence[Claim]) -> list[KnowledgeGapCandidate]:
    """Accepted/machine-reviewed claims supported by exactly one distinct source (Step 13)."""
    candidates = []
    for claim in claims:
        evidence_records = list(claim.evidence_records)
        if not evidence_records:
            continue
        distinct_sources = {_evidence_source_identity(evidence) for evidence in evidence_records}
        if len(distinct_sources) != 1:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.SINGLE_SOURCE_SUPPORT,
                severity=severity_for(GapType.SINGLE_SOURCE_SUPPORT),
                entity_type="claim",
                entity_id=claim.id,
                predicate=claim.predicate,
                explanation=(
                    f"Claim {claim.id} is supported by {len(evidence_records)} evidence "
                    "record(s), all from a single distinct source."
                ),
                supporting_claim_ids=(claim.id,),
                supporting_evidence_ids=tuple(sorted((e.id for e in evidence_records), key=str)),
                reason_codes=("SINGLE_DISTINCT_SOURCE",),
            )
        )
    return candidates


def detect_no_primary_experimental_evidence(
    claims: Sequence[Claim],
) -> list[KnowledgeGapCandidate]:
    """Accepted/machine-reviewed claims with no confidently-primary evidence (Step 14)."""
    candidates = []
    for claim in claims:
        evidence_records = list(claim.evidence_records)
        if not evidence_records:
            continue
        if any(e.evidence_type in AMBIGUOUS_EVIDENCE_TYPES for e in evidence_records):
            continue  # preserve uncertainty -- never label primary/non-primary
        if any(_is_primary_evidence(e) for e in evidence_records):
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE,
                severity=severity_for(GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE),
                entity_type="claim",
                entity_id=claim.id,
                predicate=claim.predicate,
                explanation=(
                    f"Claim {claim.id} is supported only by non-primary evidence types "
                    f"({sorted({e.evidence_type.value for e in evidence_records})})."
                ),
                supporting_claim_ids=(claim.id,),
                supporting_evidence_ids=tuple(sorted((e.id for e in evidence_records), key=str)),
                reason_codes=("NO_PRIMARY_EVIDENCE_TYPE",),
            )
        )
    return candidates


def detect_missing_publication(claims: Sequence[Claim]) -> list[KnowledgeGapCandidate]:
    """Evidence rows whose SourceType implies a citation but carry no Publication (Step 15).

    Only ``PUBMED``/``PMC`` trigger this rule (see
    ``PUBLICATION_EXPECTED_SOURCE_TYPES``'s own docstring for why every
    other ``SourceType`` is excluded).
    """
    candidates = []
    for claim in claims:
        missing = [
            e
            for e in claim.evidence_records
            if e.source_type in PUBLICATION_EXPECTED_SOURCE_TYPES and e.publication_id is None
        ]
        if not missing:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.MISSING_PUBLICATION,
                severity=severity_for(GapType.MISSING_PUBLICATION),
                entity_type="claim",
                entity_id=claim.id,
                predicate=claim.predicate,
                explanation=(
                    f"Claim {claim.id} has {len(missing)} evidence record(s) from a "
                    "literature source (PUBMED/PMC) with no attached Publication."
                ),
                supporting_claim_ids=(claim.id,),
                supporting_evidence_ids=tuple(sorted((e.id for e in missing), key=str)),
                reason_codes=("MISSING_PUBLICATION_FOR_LITERATURE_SOURCE",),
            )
        )
    return candidates


def detect_missing_experimental_context(
    claims: Sequence[Claim],
) -> list[KnowledgeGapCandidate]:
    """Experimental-type evidence with no attached ExperimentalCondition (Step 16)."""
    candidates = []
    for claim in claims:
        missing = [
            e
            for e in claim.evidence_records
            if e.evidence_type in EXPERIMENTAL_EVIDENCE_TYPES and not e.conditions
        ]
        if not missing:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.MISSING_EXPERIMENTAL_CONTEXT,
                severity=severity_for(GapType.MISSING_EXPERIMENTAL_CONTEXT),
                entity_type="claim",
                entity_id=claim.id,
                predicate=claim.predicate,
                explanation=(
                    f"Claim {claim.id} has {len(missing)} experimental-type evidence "
                    "record(s) with no attached ExperimentalCondition."
                ),
                supporting_claim_ids=(claim.id,),
                supporting_evidence_ids=tuple(sorted((e.id for e in missing), key=str)),
                reason_codes=("EXPERIMENTAL_EVIDENCE_WITHOUT_CONDITION",),
            )
        )
    return candidates


# --- Entity-connectivity rules (not scoped by any review state) ------------------


def detect_reactions_without_participants(
    reactions: Sequence[Reaction],
) -> list[KnowledgeGapCandidate]:
    """Reaction rows with zero ReactionParticipant rows (Step 20). Deterministic, no inference."""
    candidates = []
    for reaction in reactions:
        if reaction.participants:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.REACTION_WITHOUT_PARTICIPANTS,
                severity=severity_for(GapType.REACTION_WITHOUT_PARTICIPANTS),
                entity_type="reaction",
                entity_id=reaction.id,
                explanation=(
                    f"Reaction {reaction.id} ({reaction.internal_id}) has no associated "
                    "ReactionParticipant rows."
                ),
                reason_codes=("REACTION_HAS_ZERO_PARTICIPANTS",),
            )
        )
    return candidates


def detect_reactions_without_enzyme(reactions: Sequence[Reaction]) -> list[KnowledgeGapCandidate]:
    """Reaction rows with zero ReactionEnzyme rows (Step 17).

    ``Reaction.reaction_type`` is free text with no controlled vocabulary
    (verified against ``app.normalization.reaction``: "NOT identity, ever"),
    so there is no reliable way to exclude reactions that are genuinely
    nonenzymatic/transport. This rule therefore flags conservatively --
    every reaction lacking an enzyme association, not only those inferred
    to require one -- at LOW severity to reflect that uncertainty.
    """
    candidates = []
    for reaction in reactions:
        if reaction.enzymes:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.REACTION_WITHOUT_ENZYME,
                severity=severity_for(GapType.REACTION_WITHOUT_ENZYME),
                entity_type="reaction",
                entity_id=reaction.id,
                explanation=(
                    f"Reaction {reaction.id} ({reaction.internal_id}) has no associated "
                    "ReactionEnzyme rows. reaction_type has no controlled vocabulary, so this "
                    "may be a genuinely nonenzymatic or transport process."
                ),
                reason_codes=("REACTION_HAS_ZERO_ENZYME_ASSOCIATIONS",),
            )
        )
    return candidates


def detect_proteins_without_reaction(proteins: Sequence[Protein]) -> list[KnowledgeGapCandidate]:
    """Protein rows with zero ReactionEnzyme rows (Step 18): a connectivity gap, not a defect."""
    candidates = []
    for protein in proteins:
        if protein.reaction_enzymes:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.PROTEIN_WITHOUT_REACTION,
                severity=severity_for(GapType.PROTEIN_WITHOUT_REACTION),
                entity_type="protein",
                entity_id=protein.id,
                explanation=(
                    f"Protein {protein.id} ({protein.name}) has no associated ReactionEnzyme "
                    "rows. This may reflect a structural, regulatory, or otherwise "
                    "nonenzymatic protein rather than a curation deficiency."
                ),
                reason_codes=("PROTEIN_HAS_ZERO_REACTION_ENZYME_ASSOCIATIONS",),
            )
        )
    return candidates


def detect_genes_without_protein(genes: Sequence[Gene]) -> list[KnowledgeGapCandidate]:
    """Gene rows with zero Protein rows (Step 19).

    ``Protein.gene_id`` is optional by design (not every gene corresponds
    to a curated protein product), so this is documented as a low-severity
    connectivity observation, never a claim that every gene must have one.
    """
    candidates = []
    for gene in genes:
        if gene.proteins:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.GENE_WITHOUT_PROTEIN,
                severity=severity_for(GapType.GENE_WITHOUT_PROTEIN),
                entity_type="gene",
                entity_id=gene.id,
                explanation=(
                    f"Gene {gene.id} ({gene.symbol or gene.systematic_name}) has no associated "
                    "Protein rows."
                ),
                reason_codes=("GENE_HAS_ZERO_PROTEINS",),
            )
        )
    return candidates


def detect_isolated_compounds(compounds: Sequence[Compound]) -> list[KnowledgeGapCandidate]:
    """Non-generic Compound rows with zero ReactionParticipant rows (Step 21).

    ``is_generic`` compounds (broad placeholder/reference species) are
    excluded -- their intended role does not require direct participation
    in a specific curated reaction.
    """
    candidates = []
    for compound in compounds:
        if compound.is_generic or compound.reaction_participants:
            continue
        candidates.append(
            KnowledgeGapCandidate(
                gap_type=GapType.ISOLATED_COMPOUND,
                severity=severity_for(GapType.ISOLATED_COMPOUND),
                entity_type="compound",
                entity_id=compound.id,
                explanation=(
                    f"Compound {compound.id} ({compound.canonical_name}) has no associated "
                    "ReactionParticipant rows."
                ),
                reason_codes=("COMPOUND_HAS_ZERO_REACTION_PARTICIPANTS",),
            )
        )
    return candidates


__all__ = [
    "AMBIGUOUS_EVIDENCE_TYPES",
    "DERIVATIVE_DIRECTNESS",
    "EXPERIMENTAL_EVIDENCE_TYPES",
    "GAP_SEVERITY",
    "LOW_CONFIDENCE_CLASSES",
    "NON_PRIMARY_EVIDENCE_TYPES",
    "PUBLICATION_EXPECTED_SOURCE_TYPES",
    "detect_conflicting_claims",
    "detect_genes_without_protein",
    "detect_isolated_compounds",
    "detect_low_confidence_claims",
    "detect_missing_experimental_context",
    "detect_missing_publication",
    "detect_no_primary_experimental_evidence",
    "detect_proteins_without_reaction",
    "detect_reactions_without_enzyme",
    "detect_reactions_without_participants",
    "detect_single_source_claims",
    "severity_for",
]
