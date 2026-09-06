"""Persistence-layer exceptions.

These are raised only for conditions a caller should not have reached at
all -- a programming error in how this layer is invoked, not an ordinary
"this record can't be safely created right now" outcome. Ordinary
conservative refusals (a missing required field, a stale ``NEW`` collision
detected at persistence time, an unsupported allocation) are reported as a
``app.persistence.types.PersistenceResult`` with
``action=PersistenceAction.FAILED`` instead of raising -- exactly the same
philosophy ``app.normalization`` uses for ``AMBIGUOUS``/``CONFLICTED``
(represent the outcome as data, do not raise for something the system is
designed to encounter routinely).
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for persistence-layer exceptions."""


class EntityTypeMismatchError(PersistenceError):
    """A ``NormalizationResult`` was passed to the wrong entity-specific persist function.

    For example, a result with ``entity_type="gene"`` passed to
    ``persist_organism``. This is always a caller bug -- each
    ``app.normalization`` module fixes its own ``entity_type`` constant, so
    a mismatch here can only mean the wrong result/identity pair was routed
    to the wrong persistence function.
    """


class ContributionConfidenceMismatchError(PersistenceError):
    """An ``AggregateClaimConfidence`` was passed alongside contributions it was not computed from.

    Raised by ``app.persistence.claim.persist_claim_with_evidence`` when the
    supplied ``confidence.contribution_breakdown`` does not structurally
    correspond, entry for entry, to the supplied ``contributions`` (same
    count, same ``source_identifier``/``publication_identifier``/
    ``evidence_type`` in the same order) -- always a caller bug, never an
    ordinary data condition: it means the wrong confidence result was routed
    to the wrong evidence set, not that persistence has anything relevant to
    report about the data itself.
    """


class ExperimentRecommendationPersistenceError(PersistenceError):
    """Base class for ``app.persistence.experiment_recommendation`` exceptions."""


class RecommendationIdentityConflictError(ExperimentRecommendationPersistenceError):
    """An existing row's ``recommendation_identity`` matches, but its persisted content differs.

    ``recommendation_identity`` is a deterministic digest of
    ``knowledge_gap_identity``/``template_id``/``template_version`` only
    (``app.experiment_recommendation.recommender
    .compute_recommendation_identity``); the same three inputs should
    always deterministically produce the same recommendation content. An
    identity match with differing content therefore indicates a genuine
    invariant violation -- a template registry entry changed its content
    without bumping its version, or the caller supplied a corrupted/
    mismatched ``ExperimentRecommendation`` -- never an ordinary data
    condition, so this is raised rather than represented as a persistence
    result.
    """


class TerminalKnowledgeGapError(ExperimentRecommendationPersistenceError):
    """Reserved for a future caller that wants a hard failure instead of a structured result.

    ``app.persistence.experiment_recommendation.persist_experiment_recommendation``
    never raises this today: Increment 24 instructions, Step 21 explicitly
    prefers representing "the originating KnowledgeGap is RESOLVED/
    DISMISSED" as data (``PersistenceAction.REQUIRES_REVIEW``), the same
    philosophy every other conservative refusal in this persistence layer
    already uses. This class exists for API completeness and for a future
    override-flow that might want to fail loudly instead.
    """


class ExperimentExecutionPersistenceError(PersistenceError):
    """Base class for ``app.persistence.experiment_execution`` execution-side exceptions."""


class RecommendationNotAcceptedError(ExperimentExecutionPersistenceError):
    """Reserved for a future caller that wants a hard failure instead of a structured result.

    ``app.persistence.experiment_execution.persist_experiment_execution``
    never raises this today: the identical conservative philosophy as
    ``TerminalKnowledgeGapError`` -- "the linked ExperimentRecommendation's
    lifecycle_status is not ACCEPTED" is represented as data
    (``PersistenceAction.REQUIRES_REVIEW``), not raised, since a caller may
    routinely race a recommendation's lifecycle transition against an
    execution-creation attempt. This class exists for API completeness and
    for a future override-flow that might want to fail loudly instead.
    """


class ExperimentResultPersistenceError(PersistenceError):
    """Base class for ``app.persistence.experiment_execution`` result-side exceptions."""


class ExperimentResultIdentityConflictError(ExperimentResultPersistenceError):
    """An existing row's ``result_identity`` matches, but its persisted content differs.

    ``result_identity`` is a deterministic digest of provenance/
    classification fields only (``execution_id``, ``result_type``,
    ``measurement_name``, ``sample_identifier``, ``replicate_identifier``,
    ``time_point``, and the caller-supplied ``result_identifier``) --
    deliberately never of ``value_text``/``value_numeric`` themselves
    (Increment 25 instructions, Step 19). An identity match paired with a
    differing recorded value therefore means a caller is attempting to
    silently overwrite a previously recorded observation with a corrected
    one under the same identity, which this persistence layer always
    refuses -- a correction requires a new, distinct ``result_identifier``
    (and therefore a new ``result_identity``), never an ``UPDATE`` of an
    existing row.
    """


class ResultRecordingNotAllowedError(ExperimentResultPersistenceError):
    """Reserved for a future caller that wants a hard failure instead of a structured result.

    ``app.persistence.experiment_execution.record_experiment_result`` never
    raises this today: "the linked ExperimentExecution's status does not
    permit recording results right now" (Increment 25 instructions, Step 29)
    is represented as data (``PersistenceAction.REQUIRES_REVIEW``), the same
    conservative-result philosophy as ``TerminalKnowledgeGapError``/
    ``RecommendationNotAcceptedError``. This class exists for API
    completeness and for a future override-flow that might want to fail
    loudly instead.
    """


__all__ = [
    "ContributionConfidenceMismatchError",
    "EntityTypeMismatchError",
    "ExperimentExecutionPersistenceError",
    "ExperimentRecommendationPersistenceError",
    "ExperimentResultIdentityConflictError",
    "ExperimentResultPersistenceError",
    "PersistenceError",
    "RecommendationIdentityConflictError",
    "RecommendationNotAcceptedError",
    "ResultRecordingNotAllowedError",
    "TerminalKnowledgeGapError",
]
