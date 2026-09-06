"""Claim, Evidence, and ExperimentalCondition persistence (Increment 19).

Consumes the fully-completed upstream pipeline for one logical claim:

    EvidenceExtraction -> CandidateClaim -> SingleEvidenceAssessment
        -> EvidenceContribution (+ AggregateClaimConfidence)
        -> Claim / Evidence / EvidenceCondition rows (this module)

and writes exactly what those upstream objects already determined. See
``app.persistence`` (package docstring) for this repository's general
persistence-layer conventions (transaction ownership, ``SAVEPOINT`` usage,
``IntegrityError`` conversion) -- this module follows them, it does not
redefine them.

**Fundamental rule, unchanged from every other increment in this
pipeline:** persistence never reinterprets evidence, never renormalizes an
entity, never recalculates a confidence score, never modifies a predicate,
never canonicalizes a value, and never infers an organism, a compartment, an
experiment, or a publication that upstream did not already resolve. Every
field written here is copied verbatim from a ``CandidateClaim``, an
``EvidenceExtraction``, or an ``AggregateClaimConfidence`` that this module
received as input.

**No Claim-identity/deduplication mechanism exists.** Verified directly
against ``app/models/claim.py``: ``claim`` has no unique constraint or index
on any combination of ``subject_type``/``subject_id``/``predicate``/
``object_type``/``object_id``/``organism_id`` at all. Unlike every other
``app.persistence.*`` module, there is therefore no ``NormalizationResult``-
style MATCHED/NEW/AMBIGUOUS/CONFLICTED/UNRESOLVED decision for this module to
apply -- ``persist_claim_with_evidence`` always either creates exactly one
new ``Claim`` row, or fails outright (see
``app.persistence.claim_types.ClaimPersistenceResult``). Inventing a
deduplication heuristic here (fuzzy subject/predicate matching, for
instance) is exactly the kind of unsupported mapping this pipeline's
scientific-integrity rules forbid; this is reported as an open architecture
gap in ``docs/15_claim_persistence_contract.md``, not silently solved.

**Public API shape.** A logical claim's full evidence set is expressed as a
``Sequence[EvidenceContribution]`` -- the same type
``app.confidence.aggregation.aggregate_claim_confidence`` already consumes
-- paired with the ``AggregateClaimConfidence`` that function already
computed from that exact sequence. This module never re-runs aggregation
and never invents a merged claim from several ``CandidateClaim``\\ s: exactly
one contribution (``primary_index``, default ``0``) supplies every
``Claim``-row scalar field (subject/predicate/object/value/organism/strain/
claim_category); every contribution supplies its own ``Evidence`` row. A
caller with only one piece of evidence for a claim (the common case today,
since nothing in this repository yet groups multiple ``CandidateClaim``\\ s
under one logical claim) passes a single-element sequence.

**Duplicate evidence.** ``confidence.contribution_breakdown[i].is_duplicate``
-- already computed by aggregation's own dedup logic (source, source
identifier, span offsets, quoted text) -- is reused directly here to decide
which contributions get an ``Evidence`` row; this module never re-derives
duplicate status itself (Step 18/Step 5: never duplicate an existing
determination).

**``Evidence.curator_summary`` (``NOT NULL``).** No field on
``EvidenceExtraction`` is literally named "curator summary", but
``docs/09_evidence_extraction_contract.md`` (Sec. 5) states explicitly:
"A paraphrase or curator summary belongs in ``normalized_text``, a distinct
field." This module therefore maps ``EvidenceExtraction.normalized_text``
directly onto ``Evidence.curator_summary`` -- an already-sourced, already-
validated piece of text, never newly fabricated. ``normalized_text`` is
optional on ``EvidenceExtraction`` while ``curator_summary`` is ``NOT NULL``
on ``Evidence``: a contribution whose extraction never populated
``normalized_text`` cannot be written as an ``Evidence`` row at all, and
this module refuses the *entire* call (``FAILED``, nothing written) rather
than inventing placeholder text for one row or silently dropping it -- see
``docs/15_claim_persistence_contract.md`` for this disclosed architecture
gap.

**``EvidenceExtraction.supplementary_reference`` has no destination
column.** Verified directly against ``app/models/claim.py``: ``Evidence``
has ``page``/``figure``/``table_reference`` but no ``supplementary_reference``
column. This module writes ``figure``/``table_reference`` from
``figure_reference``/``table_reference`` and leaves ``page`` always ``NULL``
(``EvidenceExtraction`` itself carries no ``page`` field to source it from)
-- ``supplementary_reference`` is silently unreachable by this schema, not
silently dropped by this module's own choice; disclosed as an architecture
gap, not fabricated a column for.

**``ExperimentalCondition``/``EvidenceCondition``.** No field on
``EvidenceExtraction`` maps deterministically onto ``ExperimentalCondition``'s
structured columns (``medium``, ``carbon_source``, ``temperature_c``, ``ph``,
...) -- only free-text ``experimental_system``/``assay``/``perturbation``
exist upstream, and turning free text into structured fields would be
inference this package refuses to perform. This module therefore never
creates an ``ExperimentalCondition`` row from an ``EvidenceExtraction`` on
its own initiative; a caller may optionally supply already-fully-specified
``ExperimentalConditionInput`` values per contribution index (see
``experimental_conditions``), which are attached exactly as given, deduped
only for byte-identical repeats within this one call. In the current
pipeline, no upstream step produces such structured data, so this parameter
is expected to be omitted in practice -- see
``docs/15_claim_persistence_contract.md`` for the full disclosure.

**Initial ``ClaimStatus``.** Always ``ClaimStatus.UNKNOWN`` -- the schema's
own default, and the only defensible choice: assigning ``SUPPORTED``/
``CONFLICTED`` requires duplicate/contradiction detection this module does
not perform, and ``HUMAN_ACCEPTED`` is never set by automated code anywhere
in this pipeline (``app/models/review_event.py``'s own docstring: that
enforcement belongs to a later API/auth layer). No ``ReviewEvent`` row is
ever created here.

**Confidence.** ``Claim.confidence_score``/``confidence_class`` are written
verbatim from the supplied ``AggregateClaimConfidence.score``/
``confidence_class`` -- this module never recomputes, rescales, or adjusts
them.

**Compartment.** ``CandidateClaim.compartment`` is never written anywhere:
``Claim`` has no compartment column of any kind (already an open question
since the Increment 13 Claim Generation contract) -- disclosed, not
invented.

**Transaction/idempotency.** The ``Claim`` row, every ``Evidence`` row,
every ``ExperimentalCondition``/``EvidenceCondition`` row, and every
``SourceCrossReference``/``ExternalRecord`` this call creates are all
written inside one ``SAVEPOINT`` (``session.begin_nested()``), mirroring
``app.persistence.reaction``. An ``IntegrityError`` raised anywhere inside
is caught and converted to a conservative ``FAILED`` result; nothing else is
caught. This module never calls ``session.commit()``/``session.rollback()``
-- transaction ownership belongs to the caller, exactly as
``app/db/session.py``'s own docstring requires.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.confidence import AggregateClaimConfidence, EvidenceContribution
from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.enums import ClaimStatus
from app.models.experimental_condition import ExperimentalCondition
from app.persistence.claim_types import ClaimPersistenceResult, ExperimentalConditionInput
from app.persistence.errors import ContributionConfidenceMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction


def persist_claim_with_evidence(
    contributions: Sequence[EvidenceContribution],
    confidence: AggregateClaimConfidence,
    *,
    session: Session,
    primary_index: int = 0,
    experimental_conditions: Mapping[int, Sequence[ExperimentalConditionInput]] | None = None,
    provenance: ExternalRecordProvenance | None = None,
    created_by: str | None = None,
) -> ClaimPersistenceResult:
    """Persist one logical claim and its evidence set as a single unit.

    ``contributions``/``confidence`` must be exactly the pair
    ``app.confidence.aggregation.aggregate_claim_confidence`` was given and
    returned -- structurally cross-checked in ``_validate_inputs`` -- never a
    confidence result recomputed or borrowed from a different evidence set.
    See module docstring for the full field-mapping and architecture-gap
    disclosures.
    """
    _validate_inputs(contributions, confidence)
    if not 0 <= primary_index < len(contributions):
        raise ValueError(
            f"primary_index={primary_index!r} is out of range for "
            f"{len(contributions)} contribution(s)"
        )

    missing_summary_reason = _missing_curator_summary_reason(contributions, confidence)
    if missing_summary_reason is not None:
        return ClaimPersistenceResult(
            action=PersistenceAction.FAILED, reason=missing_summary_reason
        )

    primary = contributions[primary_index].candidate_claim

    try:
        with session.begin_nested():
            claim = _build_claim(primary, confidence, created_by=created_by)
            session.add(claim)
            session.flush()

            cross_reference_ids = [
                attach_source_cross_reference(
                    session,
                    entity_type="claim",
                    entity_id=claim.id,
                    source=primary.source,
                    external_id=primary.source_identifier,
                )
            ]

            evidence_ids: list[UUID] = []
            skipped_duplicate_indices: list[int] = []
            experimental_condition_ids: list[UUID] = []
            evidence_condition_ids: list[UUID] = []
            condition_ids_by_key: dict[tuple, UUID] = {}
            seen_evidence_condition_pairs: set[tuple[UUID, UUID]] = set()

            for index, contribution in enumerate(contributions):
                breakdown = confidence.contribution_breakdown[index]
                if breakdown.is_duplicate:
                    skipped_duplicate_indices.append(index)
                    continue

                evidence = _build_evidence(claim.id, contribution)
                session.add(evidence)
                session.flush()
                evidence_ids.append(evidence.id)

                extraction = contribution.candidate_claim.evidence_extraction
                cross_reference_ids.append(
                    attach_source_cross_reference(
                        session,
                        entity_type="evidence",
                        entity_id=evidence.id,
                        source=extraction.source,
                        external_id=extraction.source_identifier,
                    )
                )

                for condition_input in (experimental_conditions or {}).get(index, ()):
                    if not isinstance(condition_input, ExperimentalConditionInput):
                        raise TypeError(
                            "experimental_conditions values must be "
                            f"ExperimentalConditionInput, got {condition_input!r}"
                        )
                    condition_id = condition_ids_by_key.get(condition_input.identity_key())
                    if condition_id is None:
                        condition_id = _create_experimental_condition(session, condition_input)
                        condition_ids_by_key[condition_input.identity_key()] = condition_id
                        experimental_condition_ids.append(condition_id)

                    pair = (evidence.id, condition_id)
                    if pair in seen_evidence_condition_pairs:
                        continue
                    seen_evidence_condition_pairs.add(pair)
                    evidence_condition = EvidenceCondition(
                        evidence_id=evidence.id, experimental_condition_id=condition_id
                    )
                    session.add(evidence_condition)
                    session.flush()
                    evidence_condition_ids.append(evidence_condition.id)

            external_record_id = (
                record_external_record(session, source=primary.source, provenance=provenance)
                if provenance is not None
                else None
            )
    except IntegrityError as exc:
        return ClaimPersistenceResult(
            action=PersistenceAction.FAILED,
            reason=(
                "claim creation rolled back as a unit due to a database integrity "
                f"violation: {exc.orig}"
            ),
        )

    return ClaimPersistenceResult(
        action=PersistenceAction.CREATED,
        claim_id=claim.id,
        evidence_ids=tuple(evidence_ids),
        skipped_duplicate_indices=tuple(skipped_duplicate_indices),
        experimental_condition_ids=tuple(experimental_condition_ids),
        evidence_condition_ids=tuple(evidence_condition_ids),
        source_cross_reference_ids=tuple(cross_reference_ids),
        external_record_id=external_record_id,
        reason="created new claim with evidence",
    )


def _build_claim(
    primary, confidence: AggregateClaimConfidence, *, created_by: str | None
) -> Claim:
    subject = primary.subject
    obj = primary.object
    organism = primary.organism
    return Claim(
        subject_type=subject.entity_kind.value,
        subject_id=subject.normalized_id,
        predicate=primary.predicate,
        object_type=obj.entity_kind.value if obj is not None else None,
        object_id=obj.normalized_id if obj is not None else None,
        value_text=primary.value_text,
        value_numeric=primary.value_numeric,
        unit=primary.value_unit,
        organism_id=organism.normalized_id if organism is not None else None,
        strain=primary.strain,
        claim_category=primary.claim_category,
        status=ClaimStatus.UNKNOWN,
        confidence_score=(Decimal(confidence.score) if confidence.score is not None else None),
        confidence_class=confidence.confidence_class,
        created_by=created_by,
    )


def _build_evidence(claim_id: UUID, contribution: EvidenceContribution) -> Evidence:
    candidate_claim = contribution.candidate_claim
    extraction = candidate_claim.evidence_extraction
    return Evidence(
        claim_id=claim_id,
        publication_id=extraction.publication_id,
        source_type=extraction.source,
        source_id=extraction.source_identifier,
        evidence_type=candidate_claim.evidence_type,
        organism=extraction.organism_text,
        strain=extraction.strain_text,
        experimental_system=extraction.experimental_system,
        assay_type=extraction.assay,
        directness=candidate_claim.directness.value,
        quoted_support=candidate_claim.supporting_span.quoted_text,
        curator_summary=extraction.normalized_text,
        figure=extraction.figure_reference,
        table_reference=extraction.table_reference,
    )


def _create_experimental_condition(
    session: Session, condition_input: ExperimentalConditionInput
) -> UUID:
    row = ExperimentalCondition(
        medium=condition_input.medium,
        carbon_source=condition_input.carbon_source,
        carbon_concentration=condition_input.carbon_concentration,
        carbon_concentration_unit=condition_input.carbon_concentration_unit,
        nitrogen_source=condition_input.nitrogen_source,
        oxygen_status=condition_input.oxygen_status,
        temperature_c=condition_input.temperature_c,
        ph=condition_input.ph,
        growth_phase=condition_input.growth_phase,
        growth_rate=condition_input.growth_rate,
        growth_rate_unit=condition_input.growth_rate_unit,
        culture_mode=condition_input.culture_mode,
        notes=condition_input.notes,
    )
    session.add(row)
    session.flush()
    return row.id


def _validate_inputs(
    contributions: Sequence[EvidenceContribution], confidence: AggregateClaimConfidence
) -> None:
    if isinstance(contributions, (str, bytes)) or not isinstance(contributions, Sequence):
        raise TypeError(
            f"contributions must be a Sequence[EvidenceContribution], got {contributions!r}"
        )
    if not contributions:
        raise ValueError("contributions must not be empty")
    for item in contributions:
        if not isinstance(item, EvidenceContribution):
            raise TypeError(
                f"every item of contributions must be an EvidenceContribution, got {item!r}"
            )
    if not isinstance(confidence, AggregateClaimConfidence):
        raise TypeError(f"confidence must be an AggregateClaimConfidence, got {confidence!r}")

    if len(contributions) != confidence.evidence_count:
        raise ContributionConfidenceMismatchError(
            f"len(contributions)={len(contributions)} does not match "
            f"confidence.evidence_count={confidence.evidence_count} -- confidence was not "
            "computed from these exact contributions"
        )

    for index, (contribution, breakdown) in enumerate(
        zip(contributions, confidence.contribution_breakdown, strict=True)
    ):
        if contribution.source_identifier != contribution.candidate_claim.source_identifier:
            raise ContributionConfidenceMismatchError(
                f"contributions[{index}].source_identifier "
                f"({contribution.source_identifier!r}) must equal "
                "contributions[{index}].candidate_claim.source_identifier "
                f"({contribution.candidate_claim.source_identifier!r})"
            )
        if breakdown.source_identifier != contribution.source_identifier:
            raise ContributionConfidenceMismatchError(
                f"confidence.contribution_breakdown[{index}].source_identifier does not match "
                f"contributions[{index}].source_identifier -- confidence was not computed from "
                "these exact contributions, in this order"
            )
        if breakdown.publication_identifier != contribution.publication_identifier:
            raise ContributionConfidenceMismatchError(
                f"confidence.contribution_breakdown[{index}].publication_identifier does not "
                f"match contributions[{index}].publication_identifier"
            )
        if breakdown.evidence_type is not contribution.candidate_claim.evidence_type:
            raise ContributionConfidenceMismatchError(
                f"confidence.contribution_breakdown[{index}].evidence_type does not match "
                f"contributions[{index}].candidate_claim.evidence_type"
            )


def _missing_curator_summary_reason(
    contributions: Sequence[EvidenceContribution], confidence: AggregateClaimConfidence
) -> str | None:
    if all(breakdown.is_duplicate for breakdown in confidence.contribution_breakdown):
        return (
            "every supplied contribution is an exact duplicate (per "
            "confidence.contribution_breakdown[*].is_duplicate) -- no Evidence row would be "
            "created, and every supported claim must have at least one"
        )
    for index, (contribution, breakdown) in enumerate(
        zip(contributions, confidence.contribution_breakdown, strict=True)
    ):
        if breakdown.is_duplicate:
            continue
        if contribution.candidate_claim.evidence_extraction.normalized_text is None:
            return (
                f"contributions[{index}]'s EvidenceExtraction has no normalized_text -- "
                "Evidence.curator_summary is NOT NULL and this module never fabricates one "
                "(see module docstring's curator_summary mapping note)"
            )
    return None


__all__ = ["persist_claim_with_evidence"]
