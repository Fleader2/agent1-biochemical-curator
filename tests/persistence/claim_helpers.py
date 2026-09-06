"""Synthetic ``EvidenceContribution``/``AggregateClaimConfidence`` builders.

For ``tests/persistence``.

Built on the real pipeline types (``app.extraction``/``app.claim_generation``/
``app.confidence``), the same convention ``tests/confidence/helpers.py`` uses.
No live paper, no external API, no network access.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.claim_generation.types import CandidateClaim, CandidateEntityReference, EntityKind
from app.confidence.aggregation import build_evidence_contribution
from app.confidence.scoring import assess_single_evidence_claim
from app.confidence.types import SingleEvidenceAssessment
from app.extraction.extractor import extract_evidence
from app.extraction.types import CandidateStatement, Directness, EvidenceExtraction
from app.models.enums import EvidenceType, SourceType
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus


def make_extraction(
    *,
    subject_text: str = "FadR",
    predicate_text: str = "activates",
    object_text: str | None = "fabA transcription",
    organism_text: str | None = "Escherichia coli",
    strain_text: str | None = None,
    compartment_text: str | None = None,
    evidence_type: EvidenceType = EvidenceType.DIRECT_BIOCHEMICAL,
    directness: Directness = Directness.AUTHORS_OBSERVED,
    normalized_text: str | None = "FadR activates fabA transcription.",
    experimental_system: str | None = "in vitro purified protein assay",
    assay: str | None = "EMSA",
    figure_reference: str | None = "Figure 2A",
    table_reference: str | None = None,
    supplementary_reference: str | None = None,
    publication_id: UUID | None = None,
    source_identifier: str = "PMID:1",
    quoted_text: str | None = None,
) -> EvidenceExtraction:
    text = quoted_text or (
        f"FadR activates fabA transcription in Escherichia coli. ({source_identifier})"
    )
    candidate = CandidateStatement(
        quoted_text=text,
        subject_text=subject_text,
        predicate_text=predicate_text,
        object_text=object_text,
        organism_text=organism_text,
        strain_text=strain_text,
        compartment_text=compartment_text,
        evidence_type=evidence_type,
        directness=directness,
        normalized_text=normalized_text,
        experimental_system=experimental_system,
        assay=assay,
        figure_reference=figure_reference,
        table_reference=table_reference,
        supplementary_reference=supplementary_reference,
    )
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier=source_identifier,
        text=text,
        candidates=[candidate],
        publication_id=publication_id,
    )
    return extraction


def make_reference(
    *,
    original_text: str = "FadR",
    entity_kind: EntityKind = EntityKind.GENE,
    normalized_id: UUID | None = None,
) -> CandidateEntityReference:
    return CandidateEntityReference(
        original_text=original_text, entity_kind=entity_kind, normalized_id=normalized_id
    )


def make_resolved_reference(
    *,
    original_text: str = "FadR",
    entity_kind: EntityKind = EntityKind.GENE,
    normalized_id: UUID | None = None,
) -> CandidateEntityReference:
    """A reference whose identity is ``MATCHED`` -- numerically eligible for aggregation."""
    resolved_id = normalized_id or uuid4()
    result = NormalizationResult(
        status=NormalizationStatus.MATCHED,
        source=SourceType.SGD,
        source_identifier=original_text,
        entity_type=entity_kind.value.lower(),
        match_method=MatchMethod.EXACT_IDENTIFIER,
        matched_entity_id=resolved_id,
    )
    return CandidateEntityReference(
        original_text=original_text,
        entity_kind=entity_kind,
        normalization_result=result,
        normalized_id=resolved_id,
    )


def make_candidate_claim(
    *,
    extraction: EvidenceExtraction | None = None,
    subject: CandidateEntityReference | None = None,
    object_: CandidateEntityReference | None = None,
    organism: CandidateEntityReference | None = None,
    compartment: CandidateEntityReference | None = None,
    strain: str | None = None,
    claim_category: str | None = "regulation",
    value_text: str | None = None,
    value_numeric=None,
    value_unit: str | None = None,
    qualifiers: tuple[str, ...] = (),
    **extraction_overrides,
) -> CandidateClaim:
    resolved_extraction = extraction or make_extraction(**extraction_overrides)
    return CandidateClaim(
        source=resolved_extraction.source,
        source_identifier=resolved_extraction.source_identifier,
        evidence_extraction=resolved_extraction,
        subject=subject if subject is not None else make_resolved_reference(),
        predicate=resolved_extraction.predicate_text,
        supporting_span=resolved_extraction.span,
        evidence_type=resolved_extraction.evidence_type,
        directness=resolved_extraction.directness,
        object=object_,
        organism=organism,
        compartment=compartment,
        strain=strain,
        claim_category=claim_category,
        value_text=value_text,
        value_numeric=value_numeric,
        value_unit=value_unit,
        qualifiers=qualifiers,
    )


def make_assessment(candidate_claim: CandidateClaim) -> SingleEvidenceAssessment:
    return assess_single_evidence_claim(candidate_claim)


def make_contribution(
    *,
    candidate_claim: CandidateClaim | None = None,
    publication_identifier: str | None = None,
    source_identifier: str | None = None,
    **candidate_overrides,
):
    """Build one ``EvidenceContribution``.

    ``source_identifier``, when given and no explicit ``candidate_claim`` is
    supplied, is forwarded into ``make_candidate_claim`` too -- so the
    resulting contribution's own ``source_identifier`` and its
    ``candidate_claim.source_identifier`` agree by default, matching the
    real pipeline invariant ``app.persistence.claim`` validates.
    """
    if candidate_claim is None:
        if source_identifier is not None:
            candidate_overrides.setdefault("source_identifier", source_identifier)
        candidate_claim = make_candidate_claim(**candidate_overrides)
    resolved_source_identifier = source_identifier or candidate_claim.source_identifier
    return build_evidence_contribution(
        candidate_claim,
        make_assessment(candidate_claim),
        publication_identifier=publication_identifier or resolved_source_identifier,
        source_identifier=resolved_source_identifier,
    )


__all__ = [
    "make_assessment",
    "make_candidate_claim",
    "make_contribution",
    "make_extraction",
    "make_reference",
    "make_resolved_reference",
]
