"""Database tests for Group D claim/evidence models.

See ``docs/02_database_schema.md``: "Table: claim", "Table: evidence",
"Table: evidence_condition".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.enums import ClaimStatus, ConfidenceClass, EvidenceType, SourceType
from app.models.experimental_condition import ExperimentalCondition
from app.models.organism import Organism
from app.models.publication import Publication

pytestmark = pytest.mark.database


def _make_organism(db_session, suffix: str) -> Organism:
    organism = Organism(scientific_name=f"test-only organism {suffix}")
    db_session.add(organism)
    db_session.flush()
    return organism


def _make_claim(db_session, **kwargs) -> Claim:
    kwargs.setdefault("subject_type", "protein")
    kwargs.setdefault("predicate", "LOCALIZED_IN")
    claim = Claim(**kwargs)
    db_session.add(claim)
    db_session.flush()
    return claim


def _make_publication(db_session, suffix: str) -> Publication:
    publication = Publication(title=f"test-only publication {suffix}")
    db_session.add(publication)
    db_session.flush()
    return publication


def _make_evidence(db_session, claim: Claim, **kwargs) -> Evidence:
    kwargs.setdefault("source_type", SourceType.PUBMED)
    kwargs.setdefault("evidence_type", EvidenceType.DIRECT_BIOCHEMICAL)
    kwargs.setdefault("curator_summary", "test-only curator summary")
    evidence = Evidence(claim_id=claim.id, **kwargs)
    db_session.add(evidence)
    db_session.flush()
    return evidence


# --- claim -------------------------------------------------------------


def test_create_claim(db_session):
    claim = _make_claim(db_session, subject_id=uuid.uuid4())
    fetched = db_session.get(Claim, claim.id)
    assert fetched is not None
    assert fetched.subject_type == "protein"
    assert fetched.predicate == "LOCALIZED_IN"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_claim_subject_type_is_required(db_session):
    db_session.add(Claim(subject_type=None, predicate="X"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_claim_predicate_is_required(db_session):
    db_session.add(Claim(subject_type="protein", predicate=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_claim_subject_id_accepts_null(db_session):
    claim = _make_claim(db_session, subject_id=None)
    assert db_session.get(Claim, claim.id).subject_id is None


def test_claim_organism_id_may_be_null(db_session):
    claim = _make_claim(db_session, organism_id=None)
    assert db_session.get(Claim, claim.id).organism_id is None


def test_claim_valid_organism_id_persists(db_session):
    organism = _make_organism(db_session, "claim-valid")
    claim = _make_claim(db_session, organism_id=organism.id)
    assert db_session.get(Claim, claim.id).organism_id == organism.id


def test_claim_invalid_organism_id_is_rejected(db_session):
    db_session.add(Claim(subject_type="protein", predicate="X", organism_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_claim_status_defaults_to_unknown(db_session):
    claim = _make_claim(db_session)
    assert db_session.get(Claim, claim.id).status == ClaimStatus.UNKNOWN


@pytest.mark.parametrize("status", list(ClaimStatus))
def test_claim_every_valid_status_persists(db_session, status):
    claim = _make_claim(db_session, status=status)
    assert db_session.get(Claim, claim.id).status == status


def test_claim_invalid_status_is_rejected_by_postgres(db_session):
    claim = _make_claim(db_session)
    with pytest.raises(DataError):
        db_session.execute(
            text(
                "UPDATE claim SET status = 'NOT_A_REAL_STATUS' WHERE id = :id"
            ),
            {"id": claim.id},
        )


def test_claim_confidence_score_accepts_null(db_session):
    claim = _make_claim(db_session, confidence_score=None)
    assert db_session.get(Claim, claim.id).confidence_score is None


@pytest.mark.parametrize("value", [0, 100])
def test_claim_confidence_score_accepts_boundary_values(db_session, value):
    claim = _make_claim(db_session, confidence_score=value)
    assert db_session.get(Claim, claim.id).confidence_score == value


def test_claim_confidence_score_rejects_below_zero(db_session):
    db_session.add(Claim(subject_type="protein", predicate="X", confidence_score=-1))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_claim_confidence_score_rejects_above_hundred(db_session):
    db_session.add(Claim(subject_type="protein", predicate="X", confidence_score=101))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_claim_confidence_class_accepts_null(db_session):
    claim = _make_claim(db_session, confidence_class=None)
    assert db_session.get(Claim, claim.id).confidence_class is None


@pytest.mark.parametrize("confidence_class", list(ConfidenceClass))
def test_claim_every_confidence_class_persists(db_session, confidence_class):
    claim = _make_claim(db_session, confidence_class=confidence_class)
    assert db_session.get(Claim, claim.id).confidence_class == confidence_class


def test_claim_confidence_class_is_not_forced_to_match_confidence_score(db_session):
    """No database constraint ties confidence_class to confidence_score
    (docs/02_database_schema.md gives the mapping as descriptive guidance,
    not a stated constraint; enforcement is deferred to validation logic)."""
    claim = _make_claim(
        db_session, confidence_score=5, confidence_class=ConfidenceClass.VERY_HIGH
    )
    fetched = db_session.get(Claim, claim.id)
    assert fetched.confidence_score == 5
    assert fetched.confidence_class == ConfidenceClass.VERY_HIGH


def test_distinct_claims_can_coexist_without_invented_uniqueness(db_session):
    """docs/02_database_schema.md defines no uniqueness constraint on claim;
    two claims about the same subject/predicate (e.g. conflicting assertions)
    must both be allowed to persist."""
    db_session.add(
        Claim(subject_type="protein", subject_id=uuid.uuid4(), predicate="LOCALIZED_IN")
    )
    db_session.add(
        Claim(subject_type="protein", subject_id=uuid.uuid4(), predicate="LOCALIZED_IN")
    )
    db_session.flush()  # must not raise


def test_claim_polymorphic_subject_id_has_no_foreign_key(db_session):
    """subject_id/object_id accept arbitrary UUIDs with no referential check:
    the referential-integrity limitation is deferred to the validation layer,
    not approximated with an invented FK here."""
    dangling_uuid = uuid.uuid4()
    claim = _make_claim(
        db_session,
        subject_id=dangling_uuid,
        object_type="compartment",
        object_id=uuid.uuid4(),
    )
    fetched = db_session.get(Claim, claim.id)
    assert fetched.subject_id == dangling_uuid  # persisted despite referencing nothing real


# --- evidence ------------------------------------------------------------


def test_create_evidence(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    fetched = db_session.get(Evidence, evidence.id)
    assert fetched is not None
    assert fetched.claim_id == claim.id
    assert fetched.curator_summary == "test-only curator summary"
    assert fetched.created_at is not None


def test_evidence_claim_id_is_required(db_session):
    db_session.add(
        Evidence(
            claim_id=None,
            source_type=SourceType.PUBMED,
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
            curator_summary="x",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_evidence_invalid_claim_id_fails(db_session):
    db_session.add(
        Evidence(
            claim_id=uuid.uuid4(),
            source_type=SourceType.PUBMED,
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
            curator_summary="x",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_evidence_publication_id_may_be_null(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim, publication_id=None)
    assert db_session.get(Evidence, evidence.id).publication_id is None


def test_evidence_invalid_publication_id_fails(db_session):
    claim = _make_claim(db_session)
    with pytest.raises(IntegrityError):
        _make_evidence(db_session, claim, publication_id=uuid.uuid4())


def test_evidence_curator_summary_is_required(db_session):
    claim = _make_claim(db_session)
    db_session.add(
        Evidence(
            claim_id=claim.id,
            source_type=SourceType.PUBMED,
            evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
            curator_summary=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("source_type", list(SourceType))
def test_evidence_every_source_type_persists(db_session, source_type):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim, source_type=source_type)
    assert db_session.get(Evidence, evidence.id).source_type == source_type


@pytest.mark.parametrize("evidence_type", list(EvidenceType))
def test_evidence_every_evidence_type_persists(db_session, evidence_type):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim, evidence_type=evidence_type)
    assert db_session.get(Evidence, evidence.id).evidence_type == evidence_type


def test_evidence_invalid_source_type_is_rejected_by_postgres(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    with pytest.raises(DataError):
        db_session.execute(
            text(
                "UPDATE evidence SET source_type = 'NOT_A_REAL_SOURCE' WHERE id = :id"
            ),
            {"id": evidence.id},
        )


def test_evidence_invalid_evidence_type_is_rejected_by_postgres(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    with pytest.raises(DataError):
        db_session.execute(
            text(
                "UPDATE evidence SET evidence_type = 'NOT_A_REAL_TYPE' WHERE id = :id"
            ),
            {"id": evidence.id},
        )


def test_deleting_referenced_claim_is_restricted(db_session):
    claim = _make_claim(db_session)
    _make_evidence(db_session, claim)
    claim_id = claim.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Claim).where(Claim.id == claim_id))


def test_deleting_referenced_publication_is_restricted(db_session):
    claim = _make_claim(db_session)
    publication = _make_publication(db_session, "restrict")
    _make_evidence(db_session, claim, publication_id=publication.id)
    publication_id = publication.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Publication).where(Publication.id == publication_id))


def test_evidence_has_only_created_at_no_updated_at(db_session):
    """docs/02_database_schema.md gives evidence only created_at."""
    assert not hasattr(Evidence, "updated_at")


# --- evidence_condition ---------------------------------------------------


def test_create_evidence_condition(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    condition = ExperimentalCondition(medium="test-only-YPD")
    db_session.add(condition)
    db_session.flush()

    ec = EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=condition.id)
    db_session.add(ec)
    db_session.flush()

    fetched = db_session.get(EvidenceCondition, ec.id)
    assert fetched is not None
    assert fetched.evidence_id == evidence.id
    assert fetched.experimental_condition_id == condition.id


def test_evidence_condition_invalid_evidence_id_fails(db_session):
    condition = ExperimentalCondition(medium="test-only")
    db_session.add(condition)
    db_session.flush()

    db_session.add(
        EvidenceCondition(evidence_id=uuid.uuid4(), experimental_condition_id=condition.id)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_evidence_condition_invalid_experimental_condition_id_fails(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)

    db_session.add(
        EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=uuid.uuid4())
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_evidence_condition_duplicate_pair_fails(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    condition = ExperimentalCondition(medium="test-only-dup")
    db_session.add(condition)
    db_session.flush()

    db_session.add(
        EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=condition.id)
    )
    db_session.flush()

    db_session.add(
        EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=condition.id)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_referenced_evidence_is_restricted(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    condition = ExperimentalCondition(medium="test-only-restrict-evidence")
    db_session.add(condition)
    db_session.flush()
    db_session.add(
        EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=condition.id)
    )
    db_session.flush()
    evidence_id = evidence.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Evidence).where(Evidence.id == evidence_id))


def test_deleting_referenced_experimental_condition_is_restricted(db_session):
    claim = _make_claim(db_session)
    evidence = _make_evidence(db_session, claim)
    condition = ExperimentalCondition(medium="test-only-restrict-condition")
    db_session.add(condition)
    db_session.flush()
    db_session.add(
        EvidenceCondition(evidence_id=evidence.id, experimental_condition_id=condition.id)
    )
    db_session.flush()
    condition_id = condition.id
    db_session.expunge_all()

    with pytest.raises(IntegrityError):
        db_session.execute(
            delete(ExperimentalCondition).where(ExperimentalCondition.id == condition_id)
        )


def test_evidence_condition_has_no_timestamp_columns(db_session):
    assert not hasattr(EvidenceCondition, "created_at")
    assert not hasattr(EvidenceCondition, "updated_at")
