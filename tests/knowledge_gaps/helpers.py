"""Shared row factories for ``tests/knowledge_gaps``.

Mirrors ``tests/persistence/conftest.py``'s plain-function factory pattern:
create and flush a minimal valid row directly against ``db_session``. Review
transitions are applied via the real ``app.review.workflow`` functions
(never a hand-rolled ``ReviewEvent`` insert) so these tests exercise the
actual review-state derivation ``app.knowledge_gaps.analysis`` depends on.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.compound import Compound
from app.models.enums import (
    ClaimStatus,
    ConfidenceClass,
    CurationState,
    EvidenceType,
    ReactionParticipantRole,
    SourceType,
)
from app.models.experimental_condition import ExperimentalCondition
from app.models.gene import Gene
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.review.types import ReviewDecision
from app.review.workflow import human_review_claim, machine_review_claim

_counter = itertools.count()
_timestamp_counter = itertools.count()


def _suffix(explicit: str | None) -> str:
    return explicit or uuid4().hex[:8]


def _next_timestamp() -> datetime:
    """A strictly increasing timestamp, one call-site apart.

    ``ReviewEvent.created_at`` has no monotonic sequence column to break a
    tie (see ``app.review.workflow``'s own disclosed limitation) -- tests
    that create more than one ``ReviewEvent`` for the same claim must give
    each a distinct, ordered timestamp, or "latest wins" is undefined.
    """
    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=next(_timestamp_counter))


def make_organism(session: Session, *, suffix: str | None = None) -> Organism:
    organism = Organism(scientific_name=f"Test organism {_suffix(suffix)}")
    session.add(organism)
    session.flush()
    return organism


def make_publication(session: Session, *, suffix: str | None = None) -> Publication:
    publication = Publication(title=f"Test publication {_suffix(suffix)}")
    session.add(publication)
    session.flush()
    return publication


def make_claim(
    session: Session,
    *,
    subject_type: str = "GENE",
    subject_id=None,
    predicate: str = "activates",
    object_type: str | None = None,
    object_id=None,
    value_text: str | None = None,
    value_numeric: Decimal | None = None,
    unit: str | None = None,
    organism_id=None,
    strain: str | None = None,
    claim_category: str | None = "regulation",
    status: ClaimStatus = ClaimStatus.UNKNOWN,
    confidence_score: Decimal | int | None = None,
    confidence_class: ConfidenceClass = ConfidenceClass.UNKNOWN,
) -> Claim:
    claim = Claim(
        subject_type=subject_type,
        subject_id=subject_id,
        predicate=predicate,
        object_type=object_type,
        object_id=object_id,
        value_text=value_text,
        value_numeric=value_numeric,
        unit=unit,
        organism_id=organism_id,
        strain=strain,
        claim_category=claim_category,
        status=status,
        confidence_score=confidence_score,
        confidence_class=confidence_class,
    )
    session.add(claim)
    session.flush()
    return claim


def make_evidence(
    session: Session,
    *,
    claim_id,
    source_type: SourceType = SourceType.PUBMED,
    source_id: str | None = None,
    publication_id=None,
    evidence_type: EvidenceType = EvidenceType.DIRECT_BIOCHEMICAL,
    directness: str = "AUTHORS_OBSERVED",
    curator_summary: str = "test-only curator summary",
) -> Evidence:
    evidence = Evidence(
        claim_id=claim_id,
        publication_id=publication_id,
        source_type=source_type,
        source_id=source_id or f"PMID:{next(_counter)}",
        evidence_type=evidence_type,
        directness=directness,
        curator_summary=curator_summary,
    )
    session.add(evidence)
    session.flush()
    return evidence


def make_evidence_condition(
    session: Session, *, evidence_id, condition: ExperimentalCondition | None = None
) -> EvidenceCondition:
    resolved_condition = condition
    if resolved_condition is None:
        resolved_condition = ExperimentalCondition(temperature_c=Decimal("30"))
        session.add(resolved_condition)
        session.flush()
    row = EvidenceCondition(
        evidence_id=evidence_id, experimental_condition_id=resolved_condition.id
    )
    session.add(row)
    session.flush()
    return row


def accept_claim(session: Session, claim: Claim, *, reviewer: str = "curator@example.com") -> None:
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.HUMAN_ACCEPTED,
            reviewer=reviewer,
            reason="test-only acceptance",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=session,
    )


def machine_review(session: Session, claim: Claim, confidence) -> None:
    machine_review_claim(claim, confidence, session=session)


def flag_needs_review(
    session: Session, claim: Claim, *, reviewer: str = "curator@example.com"
) -> None:
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.NEEDS_REVIEW,
            reviewer=reviewer,
            reason="test-only escalation",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=session,
    )


def reject_claim(session: Session, claim: Claim, *, reviewer: str = "curator@example.com") -> None:
    human_review_claim(
        ReviewDecision(
            claim_id=claim.id,
            decision=CurationState.REJECTED,
            reviewer=reviewer,
            reason="test-only rejection",
            timestamp=_next_timestamp(),
        ),
        claim=claim,
        session=session,
    )


def make_reaction(session: Session, *, organism_id=None, suffix: str | None = None) -> Reaction:
    reaction = Reaction(
        internal_id=f"TEST_KG_R{next(_counter):05d}",
        name=f"test-only reaction {_suffix(suffix)}",
        organism_id=organism_id,
    )
    session.add(reaction)
    session.flush()
    return reaction


def make_reaction_participant(
    session: Session,
    *,
    reaction_id,
    compound_id,
    role: ReactionParticipantRole = ReactionParticipantRole.REACTANT,
    stoichiometry: Decimal = Decimal("1"),
    compartment_id=None,
) -> ReactionParticipant:
    row = ReactionParticipant(
        reaction_id=reaction_id,
        compound_id=compound_id,
        compartment_id=compartment_id,
        role=role,
        stoichiometry=stoichiometry,
    )
    session.add(row)
    session.flush()
    return row


def make_reaction_enzyme(
    session: Session, *, reaction_id, protein_id=None, complex_id=None, relationship="CATALYZES"
) -> ReactionEnzyme:
    row = ReactionEnzyme(
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        relationship=relationship,
    )
    session.add(row)
    session.flush()
    return row


def make_protein(
    session: Session, *, organism_id, gene_id=None, suffix: str | None = None
) -> Protein:
    protein = Protein(
        organism_id=organism_id, gene_id=gene_id, name=f"Test protein {_suffix(suffix)}"
    )
    session.add(protein)
    session.flush()
    return protein


def make_gene(session: Session, *, organism_id, suffix: str | None = None) -> Gene:
    gene = Gene(organism_id=organism_id, symbol=f"g{_suffix(suffix)}")
    session.add(gene)
    session.flush()
    return gene


def make_compound(
    session: Session, *, suffix: str | None = None, is_generic: bool = False
) -> Compound:
    compound = Compound(
        canonical_name=f"test-only compound {_suffix(suffix)}", is_generic=is_generic
    )
    session.add(compound)
    session.flush()
    return compound


__all__ = [
    "accept_claim",
    "flag_needs_review",
    "machine_review",
    "make_claim",
    "make_compound",
    "make_evidence",
    "make_evidence_condition",
    "make_gene",
    "make_organism",
    "make_protein",
    "make_publication",
    "make_reaction",
    "make_reaction_enzyme",
    "make_reaction_participant",
    "reject_claim",
]
