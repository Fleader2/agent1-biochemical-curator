"""Experiment-recommendation exceptions.

``ExperimentRecommendationError`` is the base class; nothing raises it
directly. Expected ``NOT_APPLICABLE``/``REQUIRES_HUMAN_DESIGN``/
``INSUFFICIENT_INFORMATION`` outcomes are represented as data
(``ExperimentRecommendation`` with the corresponding ``status``), never as
exceptions -- the same philosophy ``app.knowledge_gaps``/
``app.persistence`` already use throughout this repository.
"""

from __future__ import annotations


class ExperimentRecommendationError(Exception):
    """Base class for experiment-recommendation exceptions."""


class InvalidRecommendationContextError(ExperimentRecommendationError):
    """A caller-supplied ``ExperimentRecommendationContext`` (or its absence) is malformed.

    Raised only for a genuine caller bug -- the wrong type entirely, for
    example -- never for "the context doesn't contain the claim/evidence
    this gap references," which is reported as
    ``RecommendationStatus.INSUFFICIENT_INFORMATION`` instead.
    """


class UnsupportedGapTypeError(ExperimentRecommendationError):
    """A ``GapType`` has no registered recommendation handler at all.

    Defensive only: every member of ``app.knowledge_gaps.types.GapType`` has
    an entry in this package's dispatch table, so this should be
    unreachable in practice -- it exists to fail loudly rather than silently
    guess a template for a gap type this package was never told how to
    handle.
    """


class TemplateApplicationError(ExperimentRecommendationError):
    """A template registry invariant was violated (authoring bug, not caller input).

    For example: a ``RECOMMENDED``-status template with no
    ``experiment_class``/``success_criterion``, or a lookup for a
    ``template_id`` that does not exist in
    ``app.experiment_recommendation.templates.TEMPLATE_REGISTRY``.
    """


__all__ = [
    "ExperimentRecommendationError",
    "InvalidRecommendationContextError",
    "TemplateApplicationError",
    "UnsupportedGapTypeError",
]
