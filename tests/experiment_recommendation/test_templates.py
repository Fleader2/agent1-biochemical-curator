"""Tests for ``app.experiment_recommendation.templates``."""

from __future__ import annotations

import pytest

from app.experiment_recommendation.errors import TemplateApplicationError
from app.experiment_recommendation.templates import (
    TEMPLATE_REGISTRY,
    TEMPLATES_BY_GAP_TYPE,
    ExperimentTemplate,
    get_template,
)
from app.experiment_recommendation.types import RecommendationStatus
from app.knowledge_gaps.types import GapType


def test_every_implemented_gap_type_has_at_least_one_template():
    for gap_type in GapType:
        assert gap_type in TEMPLATES_BY_GAP_TYPE, f"{gap_type} has no registered template"
        assert len(TEMPLATES_BY_GAP_TYPE[gap_type]) >= 1


def test_no_duplicate_template_ids():
    ids = [template.template_id for template in TEMPLATE_REGISTRY.values()]
    assert len(ids) == len(set(ids))


def test_template_ids_match_their_registry_key():
    for key, template in TEMPLATE_REGISTRY.items():
        assert key == template.template_id


def test_all_template_versions_nonblank():
    for template in TEMPLATE_REGISTRY.values():
        assert template.version.strip()


def test_all_template_ids_nonblank():
    for template in TEMPLATE_REGISTRY.values():
        assert template.template_id.strip()


def test_all_reason_codes_nonblank():
    for template in TEMPLATE_REGISTRY.values():
        assert template.reason_code.strip()


def test_recommended_templates_have_experiment_class_and_success_criterion():
    for template in TEMPLATE_REGISTRY.values():
        if template.status is RecommendationStatus.RECOMMENDED:
            assert template.experiment_class is not None
            assert template.success_criterion is not None


def test_non_recommended_templates_have_no_experiment_class():
    for template in TEMPLATE_REGISTRY.values():
        if template.status is not RecommendationStatus.RECOMMENDED:
            assert template.experiment_class is None
            assert template.success_criterion is None


def test_get_template_unknown_id_raises():
    with pytest.raises(TemplateApplicationError):
        get_template("does-not-exist-v1")


def test_get_template_returns_registered_template():
    template = get_template("kg-exp-reaction-validation-v1")
    assert template.template_id == "kg-exp-reaction-validation-v1"


def test_unsupported_gap_type_not_silently_mapped():
    """Every GapType maps only to templates that explicitly declare support
    for it -- no template with an empty supported_gap_types tuple (the
    INSUFFICIENT_INFORMATION fallback) is reachable via TEMPLATES_BY_GAP_TYPE."""
    for gap_type, templates in TEMPLATES_BY_GAP_TYPE.items():
        for template in templates:
            assert gap_type in template.supported_gap_types


def test_template_construction_rejects_inconsistent_recommended_flag():
    with pytest.raises(TemplateApplicationError):
        ExperimentTemplate(
            template_id="test-only-v1",
            version="v1",
            supported_gap_types=(GapType.ISOLATED_COMPOUND,),
            status=RecommendationStatus.RECOMMENDED,
            objective="x",
            reason_code="TEST_ONLY",
        )


def test_template_construction_rejects_blank_template_id():
    with pytest.raises(TemplateApplicationError):
        ExperimentTemplate(
            template_id="   ",
            version="v1",
            supported_gap_types=(),
            status=RecommendationStatus.NOT_APPLICABLE,
            objective="x",
            reason_code="TEST_ONLY",
        )
