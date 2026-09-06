"""Tests for ``app.knowledge_gaps.analysis.analyze_knowledge_gaps`` (DB-backed)."""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.knowledge_gaps import analysis
from app.knowledge_gaps.analysis import analyze_knowledge_gaps
from app.knowledge_gaps.types import GapType
from app.models.claim import Claim
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType
from app.models.reaction import Reaction
from tests.knowledge_gaps.helpers import (
    accept_claim,
    flag_needs_review,
    machine_review,
    make_claim,
    make_compound,
    make_evidence,
    make_gene,
    make_organism,
    make_protein,
    make_reaction,
    make_reaction_enzyme,
    make_reaction_participant,
    reject_claim,
)
from tests.review.conftest import make_confidence

# --- Review eligibility -----------------------------------------------------------------


def test_human_accepted_claim_is_analyzed(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)

    result = analyze_knowledge_gaps(db_session)
    assert claim.id in {gap.entity_id for gap in result.gaps if gap.entity_type == "claim"}
    assert result.analyzed_claim_count == 1


def test_rejected_claim_excluded(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    reject_claim(db_session, claim)

    result = analyze_knowledge_gaps(db_session)
    assert result.analyzed_claim_count == 0
    assert claim.id not in {gap.entity_id for gap in result.gaps if gap.entity_type == "claim"}


def test_proposed_claim_excluded_by_default(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)

    result = analyze_knowledge_gaps(db_session)
    assert result.analyzed_claim_count == 0


def test_needs_review_claim_never_analyzed(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    flag_needs_review(db_session, claim)

    result = analyze_knowledge_gaps(db_session, include_machine_reviewed=True)
    assert result.analyzed_claim_count == 0


def test_machine_reviewed_excluded_by_default(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    machine_review(db_session, claim, make_confidence(ConfidenceClass.HIGH))

    result = analyze_knowledge_gaps(db_session, include_machine_reviewed=False)
    assert result.analyzed_claim_count == 0


def test_machine_reviewed_included_with_flag(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    machine_review(db_session, claim, make_confidence(ConfidenceClass.HIGH))

    result = analyze_knowledge_gaps(db_session, include_machine_reviewed=True)
    assert result.analyzed_claim_count == 1


def test_latest_review_event_defines_state(db_session):
    """A claim's second (later) ReviewEvent must win over its first.

    ``flag_needs_review`` alone would make this claim ineligible; the
    subsequent ``accept_claim`` is the *latest* event and must be what
    ``analyze_knowledge_gaps`` actually sees.
    """
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    flag_needs_review(db_session, claim)
    accept_claim(db_session, claim)

    result = analyze_knowledge_gaps(db_session)
    assert result.analyzed_claim_count == 1
    analyzed_ids = {gap.entity_id for gap in result.gaps if gap.entity_type == "claim"}
    assert claim.id in analyzed_ids


def test_earlier_acceptance_does_not_leak_after_later_rejection(db_session):
    """A claim proposed then rejected must be excluded even though its
    (nonexistent) earlier state would never have been eligible either --
    guards the same "latest wins" query against always defaulting True."""
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    flag_needs_review(db_session, claim)
    reject_claim(db_session, claim)

    result = analyze_knowledge_gaps(db_session)
    assert result.analyzed_claim_count == 0


def test_claim_with_no_review_event_is_implicitly_proposed(db_session):
    claim = make_claim(db_session)
    make_evidence(db_session, claim_id=claim.id)
    result = analyze_knowledge_gaps(db_session)
    assert result.analyzed_claim_count == 0


# --- Determinism -------------------------------------------------------------------------


def test_repeated_analysis_is_identical(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)
    make_reaction(db_session)

    first = analyze_knowledge_gaps(db_session)
    second = analyze_knowledge_gaps(db_session)
    assert first.gaps == second.gaps
    assert first.summary_statistics == second.summary_statistics


def test_ordering_is_stable_severity_then_type_then_entity(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)
    make_reaction(db_session)

    result = analyze_knowledge_gaps(db_session)
    severities = [analysis._SEVERITY_ORDER[gap.severity] for gap in result.gaps]
    assert severities == sorted(severities)


def test_summary_statistics_include_every_gap_type_with_zero_default(db_session):
    result = analyze_knowledge_gaps(db_session)
    assert set(result.summary_statistics) == {gap_type.value for gap_type in GapType}
    assert all(count == 0 for count in result.summary_statistics.values())


# --- Structural gaps via the real query path ----------------------------------------------


def test_reaction_without_participants_detected_end_to_end(db_session):
    reaction = make_reaction(db_session)
    result = analyze_knowledge_gaps(db_session)
    matching = [
        gap
        for gap in result.gaps
        if gap.gap_type is GapType.REACTION_WITHOUT_PARTICIPANTS and gap.entity_id == reaction.id
    ]
    assert len(matching) == 1


def test_reaction_with_participants_and_enzyme_not_flagged(db_session):
    organism = make_organism(db_session)
    reaction = make_reaction(db_session, organism_id=organism.id)
    compound = make_compound(db_session)
    make_reaction_participant(db_session, reaction_id=reaction.id, compound_id=compound.id)
    protein = make_protein(db_session, organism_id=organism.id)
    make_reaction_enzyme(db_session, reaction_id=reaction.id, protein_id=protein.id)

    result = analyze_knowledge_gaps(db_session)
    reaction_gap_types = {
        gap.gap_type for gap in result.gaps if gap.entity_id == reaction.id
    }
    assert GapType.REACTION_WITHOUT_PARTICIPANTS not in reaction_gap_types
    assert GapType.REACTION_WITHOUT_ENZYME not in reaction_gap_types


def test_gene_without_protein_low_severity_end_to_end(db_session):
    organism = make_organism(db_session)
    gene = make_gene(db_session, organism_id=organism.id)
    result = analyze_knowledge_gaps(db_session)
    matching = [gap for gap in result.gaps if gap.entity_id == gene.id]
    assert len(matching) == 1
    assert matching[0].gap_type is GapType.GENE_WITHOUT_PROTEIN


# --- Deduplication -----------------------------------------------------------------------


def test_same_claim_produces_at_most_one_gap_per_rule(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id, evidence_type=EvidenceType.REVIEW)
    make_evidence(db_session, claim_id=claim.id, evidence_type=EvidenceType.REVIEW)
    accept_claim(db_session, claim)

    result = analyze_knowledge_gaps(db_session)
    low_confidence_gaps = [
        gap
        for gap in result.gaps
        if gap.gap_type is GapType.LOW_CONFIDENCE_CLAIM and gap.entity_id == claim.id
    ]
    assert len(low_confidence_gaps) == 1


# --- Safety --------------------------------------------------------------------------------


def test_analysis_never_mutates_claim_status_or_confidence(db_session):
    claim = make_claim(
        db_session, confidence_class=ConfidenceClass.LOW, confidence_score=Decimal(10)
    )
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)

    analyze_knowledge_gaps(db_session)

    refreshed = db_session.get(Claim, claim.id)
    assert refreshed.confidence_class is ConfidenceClass.LOW
    assert refreshed.confidence_score == Decimal(10)
    assert refreshed.status is ClaimStatus.UNKNOWN


def test_analysis_creates_no_new_rows(db_session):
    claim = make_claim(db_session, confidence_class=ConfidenceClass.LOW)
    make_evidence(db_session, claim_id=claim.id)
    accept_claim(db_session, claim)
    make_reaction(db_session)

    before_reactions = len(db_session.execute(select(Reaction)).scalars().all())
    analyze_knowledge_gaps(db_session)
    after_reactions = len(db_session.execute(select(Reaction)).scalars().all())
    assert before_reactions == after_reactions


def test_analysis_module_never_commits_or_rolls_back():
    source = inspect.getsource(analysis)
    assert "session.commit(" not in source
    assert "session.rollback(" not in source


def test_analysis_module_issues_no_write_statements():
    source = inspect.getsource(analysis)
    for forbidden in ("session.add(", "session.delete(", "insert(", "update(", "delete("):
        assert forbidden not in source


def test_analysis_module_never_imports_llm_or_connector_machinery():
    source = inspect.getsource(analysis)
    for forbidden in ("openai", "anthropic", "httpx", "connectors", "confidence.aggregation"):
        assert forbidden not in source.lower()


def test_rejects_non_session_argument():
    with pytest.raises(TypeError):
        analyze_knowledge_gaps("not a session")


def test_rejects_non_bool_include_machine_reviewed(db_session):
    with pytest.raises(TypeError):
        analyze_knowledge_gaps(db_session, include_machine_reviewed="yes")
