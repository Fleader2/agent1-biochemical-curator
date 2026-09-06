"""Tests for ``app.knowledge_gaps.rules``.

Pure, DB-free unit tests: every ORM object is constructed in-memory (never
added to a session), with an explicit ``id`` (the ``default=uuid4`` column
default only applies at flush time). SQLAlchemy relationship
back-populates work purely in Python, so ``claim.evidence_records``/
``evidence.conditions`` can be assembled directly.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.knowledge_gaps import rules
from app.knowledge_gaps.types import GapSeverity, GapType
from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.compound import Compound
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType
from app.models.experimental_condition import ExperimentalCondition
from app.models.gene import Gene
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant


def _claim(**overrides) -> Claim:
    merged = {
        "id": uuid4(),
        "subject_type": "GENE",
        "subject_id": uuid4(),
        "predicate": "activates",
        "organism_id": uuid4(),
        "status": ClaimStatus.UNKNOWN,
        "confidence_class": ConfidenceClass.HIGH,
        "confidence_score": Decimal(80),
    } | overrides
    return Claim(**merged)


def _evidence(claim: Claim, **overrides) -> Evidence:
    merged = {
        "id": uuid4(),
        "claim_id": claim.id,
        "source_type": SourceType.PUBMED,
        "source_id": "PMID:1",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": "AUTHORS_OBSERVED",
        "curator_summary": "summary",
    } | overrides
    evidence = Evidence(**merged)
    claim.evidence_records.append(evidence)
    return evidence


def _attach_condition(evidence: Evidence) -> EvidenceCondition:
    condition = ExperimentalCondition(id=uuid4())
    link = EvidenceCondition(
        id=uuid4(), evidence_id=evidence.id, experimental_condition_id=condition.id
    )
    evidence.conditions.append(link)
    return link


# --- Conflicting claims ------------------------------------------------------------


def test_explicit_conflicted_status_detected():
    claim = _claim(status=ClaimStatus.CONFLICTED)
    gaps = rules.detect_conflicting_claims([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.CONFLICTING_CLAIMS
    assert gaps[0].supporting_claim_ids == (claim.id,)
    assert gaps[0].reason_codes == ("CLAIM_STATUS_CONFLICTED",)


def test_same_identity_incompatible_numeric_value_detected():
    subject_id = uuid4()
    organism_id = uuid4()
    claim_a = _claim(subject_id=subject_id, organism_id=organism_id, value_numeric=Decimal(5))
    claim_b = _claim(subject_id=subject_id, organism_id=organism_id, value_numeric=Decimal(10))
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    conflict_gaps = [g for g in gaps if "LITERAL_VALUE_DISAGREEMENT" in g.reason_codes]
    assert len(conflict_gaps) == 1
    assert set(conflict_gaps[0].supporting_claim_ids) == {claim_a.id, claim_b.id}


def test_same_identity_agreeing_values_not_flagged():
    subject_id = uuid4()
    organism_id = uuid4()
    claim_a = _claim(subject_id=subject_id, organism_id=organism_id, value_numeric=Decimal(5))
    claim_b = _claim(subject_id=subject_id, organism_id=organism_id, value_numeric=Decimal(5))
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    assert gaps == []


def test_different_strain_context_not_flagged():
    """Differing context (strain) explains the apparent difference -- not a conflict."""
    subject_id = uuid4()
    organism_id = uuid4()
    claim_a = _claim(
        subject_id=subject_id, organism_id=organism_id, strain="W303", value_numeric=Decimal(5)
    )
    claim_b = _claim(
        subject_id=subject_id, organism_id=organism_id, strain="BY4741", value_numeric=Decimal(10)
    )
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    assert gaps == []


def test_unresolved_subjects_never_grouped_as_same_entity():
    """Two claims with subject_id=None must never be treated as 'the same' entity."""
    claim_a = _claim(subject_id=None, value_numeric=Decimal(5))
    claim_b = _claim(subject_id=None, value_numeric=Decimal(10))
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    assert gaps == []


def test_different_object_not_flagged():
    subject_id = uuid4()
    organism_id = uuid4()
    claim_a = _claim(
        subject_id=subject_id, organism_id=organism_id, object_id=uuid4(), value_numeric=Decimal(5)
    )
    claim_b = _claim(
        subject_id=subject_id, organism_id=organism_id, object_id=uuid4(), value_numeric=Decimal(10)
    )
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    assert gaps == []


def test_no_semantic_guessing_on_differing_predicates():
    """'activates' vs 'inhibits' differ as text only -- never compared semantically."""
    subject_id = uuid4()
    organism_id = uuid4()
    claim_a = _claim(subject_id=subject_id, organism_id=organism_id, predicate="activates")
    claim_b = _claim(subject_id=subject_id, organism_id=organism_id, predicate="inhibits")
    gaps = rules.detect_conflicting_claims([claim_a, claim_b])
    assert gaps == []


# --- Confidence ----------------------------------------------------------------------


def test_low_confidence_claim_flagged():
    claim = _claim(confidence_class=ConfidenceClass.LOW, confidence_score=Decimal(30))
    gaps = rules.detect_low_confidence_claims([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.LOW_CONFIDENCE_CLAIM
    assert gaps[0].reason_codes == ("CONFIDENCE_CLASS_LOW",)


def test_unknown_confidence_claim_flagged():
    claim = _claim(confidence_class=ConfidenceClass.UNKNOWN, confidence_score=None)
    gaps = rules.detect_low_confidence_claims([claim])
    assert len(gaps) == 1
    assert gaps[0].reason_codes == ("CONFIDENCE_CLASS_UNKNOWN",)


def test_high_confidence_claim_not_flagged_for_confidence():
    claim = _claim(confidence_class=ConfidenceClass.HIGH, confidence_score=Decimal(80))
    assert rules.detect_low_confidence_claims([claim]) == []


def test_moderate_confidence_claim_not_flagged():
    claim = _claim(confidence_class=ConfidenceClass.MODERATE, confidence_score=Decimal(60))
    assert rules.detect_low_confidence_claims([claim]) == []


# --- Single-source support -------------------------------------------------------------


def test_one_publication_is_single_source():
    publication_id = uuid4()
    claim = _claim()
    _evidence(claim, publication_id=publication_id)
    gaps = rules.detect_single_source_claims([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.SINGLE_SOURCE_SUPPORT


def test_multiple_evidence_same_publication_still_single_source():
    publication_id = uuid4()
    claim = _claim()
    _evidence(claim, publication_id=publication_id)
    _evidence(claim, publication_id=publication_id)
    _evidence(claim, publication_id=publication_id)
    gaps = rules.detect_single_source_claims([claim])
    assert len(gaps) == 1
    assert len(gaps[0].supporting_evidence_ids) == 3


def test_two_distinct_publications_remove_single_source_gap():
    claim = _claim()
    _evidence(claim, publication_id=uuid4())
    _evidence(claim, publication_id=uuid4())
    assert rules.detect_single_source_claims([claim]) == []


def test_two_distinct_source_ids_without_publication_remove_single_source_gap():
    claim = _claim()
    _evidence(claim, publication_id=None, source_id="PMID:1")
    _evidence(claim, publication_id=None, source_id="PMID:2")
    assert rules.detect_single_source_claims([claim]) == []


def test_same_source_id_without_publication_is_single_source():
    claim = _claim()
    _evidence(claim, publication_id=None, source_id="PMID:1")
    _evidence(claim, publication_id=None, source_id="PMID:1")
    gaps = rules.detect_single_source_claims([claim])
    assert len(gaps) == 1


# --- Evidence quality / primary evidence -------------------------------------------------


def test_review_only_support_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.REVIEW, directness="REVIEW_SUMMARIZES")
    gaps = rules.detect_no_primary_experimental_evidence([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.NO_PRIMARY_EXPERIMENTAL_EVIDENCE


def test_computational_only_support_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.COMPUTATIONAL, directness="AUTHORS_PROPOSED")
    gaps = rules.detect_no_primary_experimental_evidence([claim])
    assert len(gaps) == 1


def test_direct_experimental_evidence_prevents_gap():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, directness="AUTHORS_OBSERVED")
    assert rules.detect_no_primary_experimental_evidence([claim]) == []


def test_direct_evidence_reported_via_review_summary_is_not_primary():
    """Directness overrides a nominally-primary EvidenceType (Step 14)."""
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, directness="REVIEW_SUMMARIZES")
    gaps = rules.detect_no_primary_experimental_evidence([claim])
    assert len(gaps) == 1


def test_mixed_primary_and_non_primary_not_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.REVIEW, directness="REVIEW_SUMMARIZES")
    _evidence(claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL, directness="AUTHORS_OBSERVED")
    assert rules.detect_no_primary_experimental_evidence([claim]) == []


def test_ambiguous_other_evidence_type_preserves_uncertainty():
    """A claim with any OTHER-typed evidence is never confidently labeled either way."""
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.OTHER, directness="AUTHORS_OBSERVED")
    assert rules.detect_no_primary_experimental_evidence([claim]) == []


# --- Missing publication ---------------------------------------------------------------


def test_pubmed_source_without_publication_flagged():
    claim = _claim()
    _evidence(claim, source_type=SourceType.PUBMED, publication_id=None)
    gaps = rules.detect_missing_publication([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.MISSING_PUBLICATION


def test_pmc_source_without_publication_flagged():
    claim = _claim()
    _evidence(claim, source_type=SourceType.PMC, publication_id=None)
    gaps = rules.detect_missing_publication([claim])
    assert len(gaps) == 1


def test_kegg_source_without_publication_not_flagged():
    claim = _claim()
    _evidence(claim, source_type=SourceType.KEGG, publication_id=None)
    assert rules.detect_missing_publication([claim]) == []


def test_pubmed_source_with_publication_not_flagged():
    claim = _claim()
    _evidence(claim, source_type=SourceType.PUBMED, publication_id=uuid4())
    assert rules.detect_missing_publication([claim]) == []


def test_other_source_without_publication_not_flagged():
    """SourceType.OTHER is ambiguous -- never flagged either way."""
    claim = _claim()
    _evidence(claim, source_type=SourceType.OTHER, publication_id=None)
    assert rules.detect_missing_publication([claim]) == []


# --- Missing experimental context -------------------------------------------------------


def test_experimental_evidence_without_condition_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL)
    gaps = rules.detect_missing_experimental_context([claim])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.MISSING_EXPERIMENTAL_CONTEXT


def test_experimental_evidence_with_condition_not_flagged():
    claim = _claim()
    evidence = _evidence(claim, evidence_type=EvidenceType.DIRECT_BIOCHEMICAL)
    _attach_condition(evidence)
    assert rules.detect_missing_experimental_context([claim]) == []


def test_computational_evidence_without_condition_not_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.COMPUTATIONAL)
    assert rules.detect_missing_experimental_context([claim]) == []


def test_review_evidence_without_condition_not_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.REVIEW)
    assert rules.detect_missing_experimental_context([claim]) == []


def test_curated_database_evidence_without_condition_not_flagged():
    claim = _claim()
    _evidence(claim, evidence_type=EvidenceType.CURATED_DATABASE)
    assert rules.detect_missing_experimental_context([claim]) == []


# --- Reaction structure ------------------------------------------------------------------


def _reaction(**overrides) -> Reaction:
    merged = {
        "id": uuid4(),
        "internal_id": f"R{uuid4().hex[:8]}",
        "name": "test reaction",
    } | overrides
    return Reaction(**merged)


def test_reaction_with_zero_participants_flagged():
    reaction = _reaction()
    gaps = rules.detect_reactions_without_participants([reaction])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.REACTION_WITHOUT_PARTICIPANTS
    assert gaps[0].severity is GapSeverity.HIGH


def test_reaction_with_participants_not_flagged():
    reaction = _reaction()
    participant = ReactionParticipant(
        id=uuid4(), reaction_id=reaction.id, compound_id=uuid4(), role="REACTANT", stoichiometry=1
    )
    reaction.participants.append(participant)
    assert rules.detect_reactions_without_participants([reaction]) == []


def test_reaction_without_enzyme_flagged_conservatively():
    reaction = _reaction()
    gaps = rules.detect_reactions_without_enzyme([reaction])
    assert len(gaps) == 1
    assert gaps[0].severity is GapSeverity.LOW


def test_reaction_with_enzyme_association_removes_gap():
    reaction = _reaction()
    enzyme = ReactionEnzyme(
        id=uuid4(), reaction_id=reaction.id, protein_id=uuid4(), relationship="CATALYZES"
    )
    reaction.enzymes.append(enzyme)
    assert rules.detect_reactions_without_enzyme([reaction]) == []


# --- Protein connectivity ----------------------------------------------------------------


def _protein(**overrides) -> Protein:
    merged = {"id": uuid4(), "organism_id": uuid4(), "name": "test protein"} | overrides
    return Protein(**merged)


def test_protein_without_reaction_enzyme_flagged_low_severity():
    protein = _protein()
    gaps = rules.detect_proteins_without_reaction([protein])
    assert len(gaps) == 1
    assert gaps[0].severity is GapSeverity.LOW


def test_protein_with_reaction_enzyme_not_flagged():
    protein = _protein()
    enzyme = ReactionEnzyme(
        id=uuid4(), reaction_id=uuid4(), protein_id=protein.id, relationship="CATALYZES"
    )
    protein.reaction_enzymes.append(enzyme)
    assert rules.detect_proteins_without_reaction([protein]) == []


# --- Gene/Protein ------------------------------------------------------------------------


def _gene(**overrides) -> Gene:
    merged = {"id": uuid4(), "organism_id": uuid4(), "symbol": "TEST1"} | overrides
    return Gene(**merged)


def test_gene_without_protein_flagged_low_severity():
    gene = _gene()
    gaps = rules.detect_genes_without_protein([gene])
    assert len(gaps) == 1
    assert gaps[0].severity is GapSeverity.LOW


def test_gene_with_protein_not_flagged():
    gene = _gene()
    protein = _protein(gene_id=gene.id)
    gene.proteins.append(protein)
    assert rules.detect_genes_without_protein([gene]) == []


# --- Compound connectivity ---------------------------------------------------------------


def _compound(**overrides) -> Compound:
    merged = {"id": uuid4(), "canonical_name": "test compound", "is_generic": False} | overrides
    return Compound(**merged)


def test_unused_compound_flagged():
    compound = _compound()
    gaps = rules.detect_isolated_compounds([compound])
    assert len(gaps) == 1
    assert gaps[0].gap_type is GapType.ISOLATED_COMPOUND


def test_participating_compound_not_flagged():
    compound = _compound()
    participant = ReactionParticipant(
        id=uuid4(), reaction_id=uuid4(), compound_id=compound.id, role="REACTANT", stoichiometry=1
    )
    compound.reaction_participants.append(participant)
    assert rules.detect_isolated_compounds([compound]) == []


def test_generic_compound_never_flagged_even_when_unused():
    compound = _compound(is_generic=True)
    assert rules.detect_isolated_compounds([compound]) == []


# --- Severity policy completeness ---------------------------------------------------------


def test_every_gap_type_has_a_severity():
    for gap_type in GapType:
        assert rules.severity_for(gap_type) in GapSeverity


def test_no_rule_ever_produces_critical():
    """Disclosed policy: nothing in this increment's rule set warrants CRITICAL."""
    assert GapSeverity.CRITICAL not in rules.GAP_SEVERITY.values()
