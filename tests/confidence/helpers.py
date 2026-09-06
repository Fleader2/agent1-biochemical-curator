"""Synthetic ``CandidateClaim`` builders for ``tests/confidence``.

Built directly on top of ``app.extraction``/``app.claim_generation`` (the
real pipeline types), never a hand-rolled stand-in -- these tests exercise
the real validated data contract, not an approximation of it. No live
paper, no external API, no network access, no database.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.claim_generation.types import CandidateClaim, CandidateEntityReference, EntityKind
from app.entity_resolution.types import (
    EntityMention,
    IdentifierCandidate,
    MentionResolutionResult,
    MentionResolutionStatus,
)
from app.extraction.extractor import extract_evidence
from app.extraction.types import CandidateStatement, Directness, EvidenceExtraction
from app.models.enums import EvidenceType, SourceType
from app.normalization.gene import GeneIdentity
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

DEFAULT_ORGANISM_ID = uuid4()


def make_extraction(
    text: str = "FadR activates fabA transcription in Escherichia coli.",
    *,
    subject_text: str = "FadR",
    predicate_text: str = "activates",
    object_text: str | None = "fabA transcription",
    organism_text: str | None = "Escherichia coli",
    compartment_text: str | None = None,
    evidence_type: EvidenceType = EvidenceType.DIRECT_BIOCHEMICAL,
    directness: Directness = Directness.AUTHORS_OBSERVED,
    measurement_value: str | None = None,
    measurement_units: str | None = None,
    source_identifier: str = "PMID:1",
) -> EvidenceExtraction:
    candidate = CandidateStatement(
        quoted_text=text,
        subject_text=subject_text,
        predicate_text=predicate_text,
        object_text=object_text,
        organism_text=organism_text,
        compartment_text=compartment_text,
        evidence_type=evidence_type,
        directness=directness,
        measurement_value=measurement_value,
        measurement_units=measurement_units,
    )
    [extraction] = extract_evidence(
        source=SourceType.PUBMED,
        source_identifier=source_identifier,
        text=text,
        candidates=[candidate],
    )
    return extraction


def make_matched_result(
    *, matched_entity_id: UUID | None = None, source_identifier: str = "S000000001"
) -> NormalizationResult:
    return NormalizationResult(
        status=NormalizationStatus.MATCHED,
        source=SourceType.SGD,
        source_identifier=source_identifier,
        entity_type="gene",
        match_method=MatchMethod.EXACT_IDENTIFIER,
        matched_entity_id=matched_entity_id or uuid4(),
    )


def make_ambiguous_result(
    *, candidate_entity_ids: tuple[UUID, ...] | None = None, source_identifier: str = "FadR"
) -> NormalizationResult:
    return NormalizationResult(
        status=NormalizationStatus.AMBIGUOUS,
        source=SourceType.SGD,
        source_identifier=source_identifier,
        entity_type="gene",
        match_method=MatchMethod.CANDIDATE_SYNONYM,
        candidate_entity_ids=candidate_entity_ids or (uuid4(), uuid4()),
    )


def make_conflicted_result(*, source_identifier: str = "FadR") -> NormalizationResult:
    return NormalizationResult(
        status=NormalizationStatus.CONFLICTED,
        source=SourceType.SGD,
        source_identifier=source_identifier,
        entity_type="gene",
        match_method=MatchMethod.EXACT_IDENTIFIER,
        matched_entity_id=uuid4(),
    )


def make_new_result(*, source_identifier: str = "FadR") -> NormalizationResult:
    return NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.SGD,
        source_identifier=source_identifier,
        entity_type="gene",
        match_method=MatchMethod.NONE,
    )


def make_unresolved_result(*, source_identifier: str = "FadR") -> NormalizationResult:
    return NormalizationResult(
        status=NormalizationStatus.UNRESOLVED,
        source=SourceType.SGD,
        source_identifier=source_identifier,
        entity_type="gene",
        match_method=MatchMethod.NONE,
    )


def make_reference(
    *,
    original_text: str = "FadR",
    entity_kind: EntityKind = EntityKind.GENE,
    normalization_result: NormalizationResult | None = None,
    normalized_id: UUID | None = None,
    mention_resolution_result: MentionResolutionResult | None = None,
) -> CandidateEntityReference:
    return CandidateEntityReference(
        original_text=original_text,
        entity_kind=entity_kind,
        normalization_result=normalization_result,
        normalized_id=normalized_id,
        mention_resolution_result=mention_resolution_result,
    )


def make_mention(
    *,
    original_text: str = "FadR",
    entity_kind: EntityKind = EntityKind.GENE,
    organism_id: UUID | None = None,
) -> EntityMention:
    return EntityMention(
        original_text=original_text,
        entity_kind=entity_kind,
        source_context=SourceType.PUBMED,
        source_context_identifier="PMID:1",
        organism_id=organism_id,
    )


def make_candidate(
    *,
    entity_kind: EntityKind = EntityKind.GENE,
    original_mention: str = "FadR",
    source_record_identifier: str = "S000000001",
    normalization_result: NormalizationResult | None = None,
) -> IdentifierCandidate:
    identity = GeneIdentity(
        source=SourceType.SGD,
        source_identifier=source_record_identifier,
        sgd_id=source_record_identifier,
    )
    result = normalization_result or make_matched_result(source_identifier=source_record_identifier)
    return IdentifierCandidate(
        entity_kind=entity_kind,
        source=SourceType.SGD,
        source_identifier=source_record_identifier,
        original_mention=original_mention,
        search_term=original_mention,
        source_record_identifier=source_record_identifier,
        normalization_input=identity,
        normalization_result=result,
    )


def make_mention_result(
    *,
    status: MentionResolutionStatus,
    entity_kind: EntityKind = EntityKind.GENE,
    original_text: str = "FadR",
    candidates: tuple[IdentifierCandidate, ...] = (),
    resolved_entity_id: UUID | None = None,
    failed_source: SourceType | None = None,
    error_category: str | None = None,
) -> MentionResolutionResult:
    return MentionResolutionResult(
        mention=make_mention(entity_kind=entity_kind, original_text=original_text),
        status=status,
        candidates=candidates,
        resolved_entity_id=resolved_entity_id,
        failed_source=failed_source,
        error_category=error_category,
    )


def make_claim(
    *,
    evidence_type: EvidenceType = EvidenceType.DIRECT_BIOCHEMICAL,
    directness: Directness = Directness.AUTHORS_OBSERVED,
    subject: CandidateEntityReference | None = None,
    object_: CandidateEntityReference | None = None,
    organism: CandidateEntityReference | None = None,
    compartment: CandidateEntityReference | None = None,
    organism_text: str | None = "Escherichia coli",
    object_text: str | None = "fabA transcription",
    compartment_text: str | None = None,
    predicate: str = "activates",
    subject_text: str = "FadR",
    value_text: str | None = None,
    value_numeric=None,
    value_unit: str | None = None,
    qualifiers: tuple[str, ...] = (),
    source_identifier: str = "PMID:1",
    text: str | None = None,
) -> CandidateClaim:
    extraction = make_extraction(
        text
        if text is not None
        else f"FadR activates fabA transcription in Escherichia coli. ({source_identifier})",
        subject_text=subject_text,
        predicate_text=predicate,
        object_text=object_text,
        organism_text=organism_text,
        compartment_text=compartment_text,
        evidence_type=evidence_type,
        directness=directness,
        measurement_value=value_text,
        measurement_units=value_unit,
        source_identifier=source_identifier,
    )
    return CandidateClaim(
        source=extraction.source,
        source_identifier=extraction.source_identifier,
        evidence_extraction=extraction,
        subject=subject if subject is not None else make_reference(original_text=subject_text),
        predicate=predicate,
        supporting_span=extraction.span,
        evidence_type=evidence_type,
        directness=directness,
        object=object_,
        organism=organism,
        compartment=compartment,
        value_text=value_text,
        value_numeric=value_numeric,
        value_unit=value_unit,
        qualifiers=qualifiers,
    )
