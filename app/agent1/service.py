"""The final Agent 1 read API (Increment 27, scope freeze).

``get_agent1_knowledge_package`` is the single public entry point. It is
read-only: no ``session.add``/``session.flush``/``session.commit``/
``session.rollback`` occurs anywhere in this module, and no connector is
ever called. It coordinates existing, already-completed Agent 1
subsystems -- ``app.review.workflow.get_current_curation_state``,
``app.review.history.get_review_history`` -- and otherwise issues only
plain ``SELECT`` queries against already-persisted rows. It never
recomputes normalization, confidence, review state, knowledge-gap
detection, or experiment-recommendation logic (Increment 27 instructions,
Step 6).

**Scoping.** ``organism_id=None`` returns every row of every category in
the database (a whole-database export, useful for a small demonstration
database or a diagnostic dump). A concrete ``organism_id`` scopes
``Organism``/``Gene``/``Protein``/``Reaction``/``RegulatoryInteraction``/
``Claim`` directly (each carries its own ``organism_id`` column), and
everything else by traversal from that scoped set: ``Compartment`` by
``organism_id`` (including organism-agnostic standard compartments, whose
``organism_id`` is ``NULL``), ``ReactionParticipant``/``ReactionEnzyme`` by
the scoped reactions' ids, ``Compound`` by the scoped participants'
``compound_id``\\ s (``Compound`` itself carries no ``organism_id`` -- it is
organism-agnostic in this schema), ``Evidence`` by the scoped claims' ids,
``Publication`` by the scoped evidence's ``publication_id``\\ s,
``KnowledgeGap`` by whether its own ``subject_id`` names a scoped entity or
its ``supporting_claim_ids_json`` intersects the scoped claim ids,
``ExperimentRecommendationRecord`` by the scoped gaps'
``knowledge_gap_id``, ``ExperimentExecution`` by the scoped recommendations'
``recommendation_id``, and ``ExperimentResult`` by the scoped executions'
``execution_id``. No field here requires this traversal to be perfect --
it is a best-effort, deterministic scoping convenience, never a source of
truth about what exists (``organism_id=None`` always sees everything).

**Regulation limitation** (``docs/23_agent1_v1_scope_and_completion.md``
§11). ``RegulatoryInteraction`` rows are read and returned exactly as
persisted, but no normalization, extraction, or claim-generation pipeline
in this repository writes them today (verified by inspection: no
``app/normalization/*``/``app/persistence/*`` module references
``RegulatoryInteraction`` at all) -- this function exposes whatever exists,
it does not claim the regulation pipeline is complete.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1KnowledgePackage,
    ClaimConfidenceSummary,
    ClaimReviewState,
    ProvenanceSummary,
)
from app.models.claim import Claim, Evidence
from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import CurationState
from app.models.experiment_execution import ExperimentExecution, ExperimentResult
from app.models.experiment_recommendation import ExperimentRecommendationRecord
from app.models.gene import Gene
from app.models.knowledge_gap import KnowledgeGap
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.review.history import get_review_history
from app.review.workflow import get_current_curation_state

_CLAIM_ENTITY_TYPE = "claim"

#: Known, deliberately disclosed Agent 1 v1 limitations -- never silently
#: hidden. See ``docs/23_agent1_v1_scope_and_completion.md`` §21.
_KNOWN_LIMITATIONS: tuple[str, ...] = (
    "Regulation: RegulatoryInteraction rows can be persisted and read, but "
    "no extraction/normalization/claim-generation pipeline in this "
    "repository writes them today -- regulation is schema-ready, not "
    "curated end-to-end.",
    "Cofactors: no separate Cofactor entity or ReactionParticipant role "
    "exists. A cofactor (e.g. ATP, NAD+) is preserved as an ordinary "
    "Compound participating via ReactionParticipant like any other "
    "reactant/product/modifier, and is not deterministically "
    "distinguishable from a non-cofactor participant by this schema alone.",
    "Candidate subject/predicate/object text for experiment-derived "
    "evidence candidates (app.result_interpretation) is populated only "
    "when a caller explicitly supplies it -- nothing upstream currently "
    "produces this text automatically.",
    "MetaCyc/BioCyc connectors are not implemented -- only KEGG, BRENDA, "
    "PubMed, SGD, and UniProt connectors exist today.",
    "app.persistence.claim exposes no read/list API of its own -- this "
    "package reads Claim/Evidence directly via SELECT rather than "
    "duplicating a nonexistent read function.",
)


def get_agent1_knowledge_package(
    session: Session,
    *,
    organism_id: UUID | None = None,
) -> Agent1KnowledgePackage:
    """Assemble the complete, coherent Agent 1 v1 knowledge product. Read-only.

    See module docstring for the exact scoping rules and disclosed
    limitations.
    """
    organisms = _select_organisms(session, organism_id)
    genes = _select_by_organism(session, Gene, organism_id)
    proteins = _select_by_organism(session, Protein, organism_id)
    compartments = _select_compartments(session, organism_id)
    reactions = _select_by_organism(session, Reaction, organism_id)
    reaction_ids = tuple(reaction.id for reaction in reactions)

    reaction_participants = _select_in(
        session, ReactionParticipant, ReactionParticipant.reaction_id, reaction_ids
    )
    reaction_enzyme_associations = _select_in(
        session, ReactionEnzyme, ReactionEnzyme.reaction_id, reaction_ids
    )
    compound_ids = tuple({participant.compound_id for participant in reaction_participants})
    compounds = _select_compounds(session, compound_ids, organism_id)

    regulatory_interactions = _select_by_organism(session, RegulatoryInteraction, organism_id)

    claims = _select_by_organism(session, Claim, organism_id)
    claim_ids = tuple(claim.id for claim in claims)

    evidence = _select_in(session, Evidence, Evidence.claim_id, claim_ids)
    publication_ids = tuple(
        {record.publication_id for record in evidence if record.publication_id is not None}
    )
    publications = _select_publications(session, publication_ids, organism_id)

    confidence_summaries = tuple(
        ClaimConfidenceSummary(
            claim_id=claim.id,
            confidence_score=claim.confidence_score,
            confidence_class=claim.confidence_class,
            status=claim.status,
        )
        for claim in claims
    )
    review_states = tuple(
        ClaimReviewState(
            claim_id=claim.id,
            curation_state=get_current_curation_state(session, claim.id),
            history=get_review_history(
                session, entity_type=_CLAIM_ENTITY_TYPE, entity_id=claim.id
            ),
        )
        for claim in claims
    )

    knowledge_gaps = _select_knowledge_gaps(session, claim_ids, organism_id)
    gap_ids = tuple(gap.id for gap in knowledge_gaps)

    experiment_recommendations = _select_recommendations(session, gap_ids, organism_id)
    recommendation_ids = tuple(record.id for record in experiment_recommendations)

    if organism_id is None:
        experiment_executions = tuple(session.execute(select(ExperimentExecution)).scalars().all())
    else:
        experiment_executions = _select_in(
            session, ExperimentExecution, ExperimentExecution.recommendation_id, recommendation_ids
        )
    execution_ids = tuple(execution.id for execution in experiment_executions)

    if organism_id is None:
        experiment_results = tuple(session.execute(select(ExperimentResult)).scalars().all())
    else:
        experiment_results = _select_in(
            session, ExperimentResult, ExperimentResult.execution_id, execution_ids
        )

    provenance_summary = _build_provenance_summary(claims, evidence, publications)

    return Agent1KnowledgePackage(
        contract_version=AGENT1_CONTRACT_VERSION,
        organism_id=organism_id,
        organisms=organisms,
        genes=genes,
        proteins=proteins,
        compounds=compounds,
        compartments=compartments,
        reactions=reactions,
        reaction_participants=reaction_participants,
        reaction_enzyme_associations=reaction_enzyme_associations,
        regulatory_interactions=regulatory_interactions,
        publications=publications,
        claims=claims,
        evidence=evidence,
        confidence_summaries=confidence_summaries,
        review_states=review_states,
        knowledge_gaps=knowledge_gaps,
        experiment_recommendations=experiment_recommendations,
        experiment_executions=experiment_executions,
        experiment_results=experiment_results,
        provenance_summary=provenance_summary,
        limitations=_KNOWN_LIMITATIONS,
    )


def curated_claims(package: Agent1KnowledgePackage) -> tuple[Claim, ...]:
    """Claims whose current curation state is ``HUMAN_ACCEPTED``. See Step 10 policy.

    A single, obviously-correct filter over ``package.review_states`` --
    never a reimplementation of curation-state derivation.
    """
    accepted_ids = {
        state.claim_id
        for state in package.review_states
        if state.curation_state is CurationState.HUMAN_ACCEPTED
    }
    return tuple(claim for claim in package.claims if claim.id in accepted_ids)


def machine_reviewed_claims(package: Agent1KnowledgePackage) -> tuple[Claim, ...]:
    """``MACHINE_REVIEWED`` claims -- surfaced separately, never treated as curated."""
    ids = {
        state.claim_id
        for state in package.review_states
        if state.curation_state is CurationState.MACHINE_REVIEWED
    }
    return tuple(claim for claim in package.claims if claim.id in ids)


def non_curated_claims(package: Agent1KnowledgePackage) -> tuple[Claim, ...]:
    """Claims in ``PROPOSED``/``MACHINE_REVIEWED``/``NEEDS_REVIEW`` -- not yet fully curated."""
    non_curated_states = {
        CurationState.PROPOSED,
        CurationState.MACHINE_REVIEWED,
        CurationState.NEEDS_REVIEW,
    }
    ids = {
        state.claim_id
        for state in package.review_states
        if state.curation_state in non_curated_states
    }
    return tuple(claim for claim in package.claims if claim.id in ids)


def rejected_claims(package: Agent1KnowledgePackage) -> tuple[Claim, ...]:
    """Claims whose current curation state is ``REJECTED`` -- excluded from curated output."""
    ids = {
        state.claim_id
        for state in package.review_states
        if state.curation_state is CurationState.REJECTED
    }
    return tuple(claim for claim in package.claims if claim.id in ids)


def _select_organisms(session: Session, organism_id: UUID | None) -> tuple[Organism, ...]:
    statement = select(Organism)
    if organism_id is not None:
        statement = statement.where(Organism.id == organism_id)
    return tuple(session.execute(statement).scalars().all())


def _select_by_organism(session: Session, model, organism_id: UUID | None) -> tuple:
    statement = select(model)
    if organism_id is not None:
        statement = statement.where(model.organism_id == organism_id)
    return tuple(session.execute(statement).scalars().all())


def _select_compartments(session: Session, organism_id: UUID | None) -> tuple[Compartment, ...]:
    statement = select(Compartment)
    if organism_id is not None:
        statement = statement.where(
            (Compartment.organism_id == organism_id) | (Compartment.organism_id.is_(None))
        )
    return tuple(session.execute(statement).scalars().all())


def _select_compounds(
    session: Session, compound_ids: tuple[UUID, ...], organism_id: UUID | None
) -> tuple[Compound, ...]:
    if organism_id is None:
        return tuple(session.execute(select(Compound)).scalars().all())
    return _select_in(session, Compound, Compound.id, compound_ids)


def _select_publications(
    session: Session, publication_ids: tuple[UUID, ...], organism_id: UUID | None
) -> tuple[Publication, ...]:
    if organism_id is None:
        return tuple(session.execute(select(Publication)).scalars().all())
    return _select_in(session, Publication, Publication.id, publication_ids)


def _select_in(session: Session, model, column, ids: tuple) -> tuple:
    if not ids:
        return ()
    statement = select(model).where(column.in_(ids))
    return tuple(session.execute(statement).scalars().all())


def _select_knowledge_gaps(
    session: Session, claim_ids: tuple[UUID, ...], organism_id: UUID | None
) -> tuple[KnowledgeGap, ...]:
    if organism_id is None:
        return tuple(session.execute(select(KnowledgeGap)).scalars().all())
    claim_id_strings = {str(claim_id) for claim_id in claim_ids}
    all_gaps = session.execute(select(KnowledgeGap)).scalars().all()
    scoped = [
        gap
        for gap in all_gaps
        if gap.subject_id in claim_ids
        or bool(claim_id_strings.intersection(gap.supporting_claim_ids_json or ()))
    ]
    return tuple(scoped)


def _select_recommendations(
    session: Session, gap_ids: tuple[UUID, ...], organism_id: UUID | None
) -> tuple[ExperimentRecommendationRecord, ...]:
    if organism_id is None:
        return tuple(session.execute(select(ExperimentRecommendationRecord)).scalars().all())
    return _select_in(
        session,
        ExperimentRecommendationRecord,
        ExperimentRecommendationRecord.knowledge_gap_id,
        gap_ids,
    )


def _build_provenance_summary(
    claims: tuple[Claim, ...], evidence: tuple[Evidence, ...], publications: tuple[Publication, ...]
) -> ProvenanceSummary:
    claims_with_evidence_ids = {record.claim_id for record in evidence}
    return ProvenanceSummary(
        total_claims=len(claims),
        claims_with_evidence=sum(1 for claim in claims if claim.id in claims_with_evidence_ids),
        claims_without_evidence=sum(
            1 for claim in claims if claim.id not in claims_with_evidence_ids
        ),
        total_evidence=len(evidence),
        evidence_with_publication=sum(
            1 for record in evidence if record.publication_id is not None
        ),
        evidence_with_quoted_support=sum(
            1 for record in evidence if record.quoted_support is not None
        ),
        publications_referenced=len(publications),
    )


__all__ = [
    "curated_claims",
    "get_agent1_knowledge_package",
    "machine_reviewed_claims",
    "non_curated_claims",
    "rejected_claims",
]
