"""Tests for ``app.persistence.claim``.

Exercises ``persist_claim_with_evidence`` against a real (test) database --
see ``app.persistence.claim``'s module docstring for the full field-mapping
and architecture-gap rationale these tests verify.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.claim_generation.types import EntityKind
from app.confidence.aggregation import aggregate_claim_confidence
from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType
from app.models.experimental_condition import ExperimentalCondition
from app.models.source_cross_reference import SourceCrossReference
from app.persistence.claim import persist_claim_with_evidence
from app.persistence.claim_types import ExperimentalConditionInput
from app.persistence.errors import ContributionConfidenceMismatchError
from app.persistence.provenance import ExternalRecordProvenance
from app.persistence.types import PersistenceAction
from tests.persistence.claim_helpers import (
    make_candidate_claim,
    make_contribution,
    make_reference,
    make_resolved_reference,
)
from tests.persistence.conftest import make_organism


def _single(**overrides):
    contribution = make_contribution(**overrides)
    confidence = aggregate_claim_confidence([contribution])
    return [contribution], confidence


def test_creates_claim_and_one_evidence_row(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)

    assert result.action is PersistenceAction.CREATED
    assert result.claim_id is not None
    assert len(result.evidence_ids) == 1

    claim = db_session.get(Claim, result.claim_id)
    assert claim.predicate == "activates"
    assert claim.status is ClaimStatus.UNKNOWN

    evidence = db_session.get(Evidence, result.evidence_ids[0])
    assert evidence.claim_id == claim.id
    assert evidence.evidence_type is EvidenceType.DIRECT_BIOCHEMICAL


def test_confidence_score_and_class_persisted_verbatim(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.confidence_score == Decimal(confidence.score)
    assert claim.confidence_class is confidence.confidence_class
    assert confidence.confidence_class is not ConfidenceClass.UNKNOWN


def test_never_invents_human_accepted_or_review_event(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)
    assert claim.status is ClaimStatus.UNKNOWN
    assert claim.status is not ClaimStatus.SUPPORTED


def test_resolved_subject_and_organism_ids_persisted(db_session):
    organism_row = make_organism(db_session)
    subject_id = uuid4()
    contributions, confidence = _single(
        subject=make_resolved_reference(entity_kind=EntityKind.GENE, normalized_id=subject_id),
        organism=make_resolved_reference(
            entity_kind=EntityKind.ORGANISM, normalized_id=organism_row.id
        ),
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.subject_type == "GENE"
    assert claim.subject_id == subject_id
    assert claim.organism_id == organism_row.id


def test_unresolved_subject_persists_type_with_null_id(db_session):
    contributions, confidence = _single(
        subject=make_reference(entity_kind=EntityKind.GENE, normalized_id=None)
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.subject_type == "GENE"
    assert claim.subject_id is None


def test_no_object_reference_persists_null_object_columns(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.object_type is None
    assert claim.object_id is None


def test_value_and_unit_persisted_exactly(db_session):
    contributions, confidence = _single(
        value_text="increased", value_numeric=Decimal("3.5"), value_unit="fold"
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.value_text == "increased"
    assert claim.value_numeric == Decimal("3.5")
    assert claim.unit == "fold"


def test_claim_category_and_strain_persisted_exactly(db_session):
    contributions, confidence = _single(claim_category="regulation", strain="MG1655")
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)

    assert claim.claim_category == "regulation"
    assert claim.strain == "MG1655"


def test_compartment_reference_never_persisted_anywhere(db_session):
    """Claim has no compartment column at all -- a disclosed architecture gap."""
    contributions, confidence = _single(
        compartment=make_resolved_reference(
            entity_kind=EntityKind.COMPARTMENT, normalized_id=uuid4()
        )
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    claim = db_session.get(Claim, result.claim_id)
    assert not hasattr(claim, "compartment_id")
    assert not hasattr(claim, "compartment")


def test_evidence_preserves_quotation_and_locators(db_session):
    contributions, confidence = _single(
        figure_reference="Figure 3B", table_reference="Table 1", quoted_text="FadR binds DNA."
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    evidence = db_session.get(Evidence, result.evidence_ids[0])

    assert evidence.quoted_support == "FadR binds DNA."
    assert evidence.figure == "Figure 3B"
    assert evidence.table_reference == "Table 1"
    assert evidence.page is None  # no upstream source -- disclosed gap


def test_curator_summary_sourced_from_normalized_text(db_session):
    contributions, confidence = _single(normalized_text="FadR is a transcription factor.")
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    evidence = db_session.get(Evidence, result.evidence_ids[0])
    assert evidence.curator_summary == "FadR is a transcription factor."


def test_missing_normalized_text_fails_without_writing_anything(db_session):
    contributions, confidence = _single(normalized_text=None)
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)

    assert result.action is PersistenceAction.FAILED
    assert result.claim_id is None
    assert db_session.execute(select(Claim)).first() is None


def test_publication_id_passed_through_verbatim(db_session):
    from app.models.publication import Publication

    publication = Publication(title="A study of FadR regulation")
    db_session.add(publication)
    db_session.flush()

    contributions, confidence = _single(publication_id=publication.id)
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    assert result.action is PersistenceAction.CREATED
    evidence = db_session.get(Evidence, result.evidence_ids[0])
    assert evidence.publication_id == publication.id


def test_multiple_contributions_create_multiple_evidence_rows(db_session):
    subject = make_resolved_reference()
    first = make_contribution(source_identifier="PMID:1", subject=subject)
    second = make_contribution(source_identifier="PMID:2", subject=subject)
    confidence = aggregate_claim_confidence([first, second])
    result = persist_claim_with_evidence([first, second], confidence, session=db_session)

    assert result.action is PersistenceAction.CREATED
    assert len(result.evidence_ids) == 2
    rows = db_session.execute(
        select(Evidence).where(Evidence.claim_id == result.claim_id)
    ).scalars().all()
    assert len(rows) == 2


def test_exact_duplicate_contribution_skipped_but_one_evidence_row_created(db_session):
    subject = make_resolved_reference()
    first = make_contribution(source_identifier="PMID:1", subject=subject)
    duplicate = make_contribution(source_identifier="PMID:1", subject=subject)
    confidence = aggregate_claim_confidence([first, duplicate])
    assert confidence.contribution_breakdown[1].is_duplicate is True

    result = persist_claim_with_evidence([first, duplicate], confidence, session=db_session)
    assert result.action is PersistenceAction.CREATED
    assert len(result.evidence_ids) == 1
    assert result.skipped_duplicate_indices == (1,)


def test_all_duplicate_contributions_fails_conservatively(db_session):
    """A defensive branch: real aggregation output always leaves the first
    occurrence of an exact-duplicate group non-duplicate, so this
    ``is_duplicate``-for-every-entry shape cannot arise from
    ``aggregate_claim_confidence`` itself. Constructed directly here to
    verify the conservative-refusal branch still exists and works."""
    from dataclasses import replace

    contribution = make_contribution()
    confidence = aggregate_claim_confidence([contribution])
    all_duplicate = replace(
        confidence,
        contribution_breakdown=(replace(confidence.contribution_breakdown[0], is_duplicate=True),),
    )

    result = persist_claim_with_evidence([contribution], all_duplicate, session=db_session)
    assert result.action is PersistenceAction.FAILED
    assert db_session.execute(select(Claim)).first() is None


def test_mismatched_contribution_count_raises(db_session):
    subject = make_resolved_reference()
    contribution = make_contribution(subject=subject)
    confidence = aggregate_claim_confidence(
        [contribution, make_contribution(source_identifier="PMID:2", subject=subject)]
    )
    with pytest.raises(ContributionConfidenceMismatchError):
        persist_claim_with_evidence([contribution], confidence, session=db_session)


def test_source_identifier_disagreement_raises(db_session):
    """A contribution whose own ``source_identifier`` disagrees with its
    ``candidate_claim.source_identifier`` is never producible via
    ``build_evidence_contribution``/normal construction (nothing cross-checks
    it in ``EvidenceContribution.__post_init__``) -- constructed directly via
    ``object.__setattr__`` here purely to verify persistence's own
    Step-4 agreement check catches it."""
    from app.confidence.aggregate_types import EvidenceContribution
    from tests.persistence.claim_helpers import make_assessment

    claim = make_candidate_claim(source_identifier="PMID:9")
    mismatched = EvidenceContribution(
        candidate_claim=claim,
        single_evidence_assessment=make_assessment(claim),
        publication_identifier="PMID:9",
        source_identifier="PMID:9",
    )
    object.__setattr__(mismatched, "source_identifier", "PMID:DIFFERENT")

    confidence = aggregate_claim_confidence([mismatched])
    with pytest.raises(ContributionConfidenceMismatchError):
        persist_claim_with_evidence([mismatched], confidence, session=db_session)


def test_primary_index_out_of_range_raises(db_session):
    contributions, confidence = _single()
    with pytest.raises(ValueError, match="primary_index"):
        persist_claim_with_evidence(
            contributions, confidence, session=db_session, primary_index=5
        )


def test_primary_index_selects_which_claim_supplies_scalar_fields(db_session):
    subject = make_resolved_reference()
    first = make_contribution(
        source_identifier="PMID:1", claim_category="category-a", subject=subject
    )
    second = make_contribution(
        source_identifier="PMID:2", claim_category="category-b", subject=subject
    )
    confidence = aggregate_claim_confidence([first, second])

    result = persist_claim_with_evidence(
        [first, second], confidence, session=db_session, primary_index=1
    )
    claim = db_session.get(Claim, result.claim_id)
    assert claim.claim_category == "category-b"


def test_source_cross_reference_attached_for_claim_and_evidence(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)

    claim_refs = db_session.execute(
        select(SourceCrossReference).where(
            SourceCrossReference.entity_type == "claim",
            SourceCrossReference.entity_id == result.claim_id,
        )
    ).scalars().all()
    evidence_refs = db_session.execute(
        select(SourceCrossReference).where(
            SourceCrossReference.entity_type == "evidence",
            SourceCrossReference.entity_id == result.evidence_ids[0],
        )
    ).scalars().all()
    assert len(claim_refs) == 1
    assert len(evidence_refs) == 1


def test_external_record_provenance_attached_when_supplied(db_session):
    from datetime import UTC, datetime

    contributions, confidence = _single()
    provenance = ExternalRecordProvenance(
        retrieval_date=datetime(2024, 1, 1, tzinfo=UTC), raw_response_hash="deadbeef"
    )
    result = persist_claim_with_evidence(
        contributions, confidence, session=db_session, provenance=provenance
    )
    assert result.external_record_id is not None


def test_no_provenance_supplied_means_no_external_record(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    assert result.external_record_id is None


def test_experimental_condition_created_and_associated(db_session):
    contributions, confidence = _single()
    condition = ExperimentalConditionInput(temperature_c=Decimal("37"), ph=Decimal("7.0"))
    result = persist_claim_with_evidence(
        contributions,
        confidence,
        session=db_session,
        experimental_conditions={0: [condition]},
    )
    assert len(result.experimental_condition_ids) == 1
    assert len(result.evidence_condition_ids) == 1

    row = db_session.get(ExperimentalCondition, result.experimental_condition_ids[0])
    assert row.temperature_c == Decimal("37")
    link = db_session.get(EvidenceCondition, result.evidence_condition_ids[0])
    assert link.evidence_id == result.evidence_ids[0]


def test_experimental_condition_deduped_within_one_call(db_session):
    subject = make_resolved_reference()
    first = make_contribution(source_identifier="PMID:1", subject=subject)
    second = make_contribution(source_identifier="PMID:2", subject=subject)
    confidence = aggregate_claim_confidence([first, second])
    condition = ExperimentalConditionInput(medium="M9 minimal medium")

    result = persist_claim_with_evidence(
        [first, second],
        confidence,
        session=db_session,
        experimental_conditions={0: [condition], 1: [condition]},
    )
    assert len(result.experimental_condition_ids) == 1
    assert len(result.evidence_condition_ids) == 2


def test_no_experimental_conditions_supplied_creates_none(db_session):
    contributions, confidence = _single()
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)
    assert result.experimental_condition_ids == ()
    assert result.evidence_condition_ids == ()


def test_integrity_error_rolls_back_whole_claim_as_a_unit(db_session):
    """An organism reference to a non-existent row must violate the real FK
    and roll back only this call's SAVEPOINT, not the caller's transaction."""
    contributions, confidence = _single(
        organism=make_resolved_reference(entity_kind=EntityKind.ORGANISM, normalized_id=uuid4())
    )
    result = persist_claim_with_evidence(contributions, confidence, session=db_session)

    assert result.action is PersistenceAction.FAILED
    assert db_session.execute(select(Claim)).first() is None

    # The caller's own transaction must still be usable afterward.
    organism = make_organism(db_session, suffix="post-rollback")
    assert organism.id is not None


def test_result_reason_populated_on_success_and_failure(db_session):
    contributions, confidence = _single()
    ok = persist_claim_with_evidence(contributions, confidence, session=db_session)
    assert ok.reason

    bad_contributions, bad_confidence = _single(normalized_text=None)
    failed = persist_claim_with_evidence(bad_contributions, bad_confidence, session=db_session)
    assert failed.reason
