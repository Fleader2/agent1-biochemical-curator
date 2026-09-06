"""Tests for ``app.confidence.aggregate_policy``: authoritative aggregate numeric tables."""

from __future__ import annotations

import pytest

from app.confidence.aggregate_policy import (
    CONFLICT_PENALTIES,
    EXPERIMENTAL_RELEVANCE_MODIFIERS,
    ORGANISM_RELEVANCE_MODIFIERS,
    REPLICATION_BONUS_MAX,
    REPLICATION_BONUS_PER_STEP,
    ConflictSeverity,
    ExperimentalRelevance,
    OrganismRelevance,
    classify_confidence_class,
)
from app.models.enums import ConfidenceClass

# --- Organism relevance (authoritative, docs/03_agent_behavior.md) --------------


@pytest.mark.parametrize(
    ("relevance", "expected_percent"),
    [
        (OrganismRelevance.SAME_STRAIN, 100),
        (OrganismRelevance.SAME_SPECIES, 95),
        (OrganismRelevance.SAME_GENUS, 70),
        (OrganismRelevance.OTHER_FUNGUS, 55),
        (OrganismRelevance.OTHER_EUKARYOTE, 40),
        (OrganismRelevance.BACTERIUM, 25),
    ],
)
def test_organism_relevance_modifier_exact(relevance, expected_percent):
    assert ORGANISM_RELEVANCE_MODIFIERS[relevance] == expected_percent


def test_organism_relevance_unknown_has_no_table_entry():
    """UNKNOWN is applied as neutral 100% by the aggregator, never a fabricated table entry."""
    assert OrganismRelevance.UNKNOWN not in ORGANISM_RELEVANCE_MODIFIERS


# --- Experimental relevance (authoritative) -------------------------------------


@pytest.mark.parametrize(
    ("relevance", "expected_percent"),
    [
        (ExperimentalRelevance.PHYSIOLOGICAL_IN_VIVO, 100),
        (ExperimentalRelevance.CELL_LYSATE, 90),
        (ExperimentalRelevance.PURIFIED_NATIVE_ENZYME, 90),
        (ExperimentalRelevance.RECOMBINANT_ENZYME, 80),
        (ExperimentalRelevance.HETEROLOGOUS_EXPRESSION, 70),
        (ExperimentalRelevance.COMPUTATIONAL_ONLY, 40),
    ],
)
def test_experimental_relevance_modifier_exact(relevance, expected_percent):
    assert EXPERIMENTAL_RELEVANCE_MODIFIERS[relevance] == expected_percent


def test_experimental_relevance_unknown_has_no_table_entry():
    assert ExperimentalRelevance.UNKNOWN not in EXPERIMENTAL_RELEVANCE_MODIFIERS


# --- Replication (authoritative) -------------------------------------------------


def test_replication_bonus_per_step_is_five():
    assert REPLICATION_BONUS_PER_STEP == 5


def test_replication_bonus_max_is_ten():
    assert REPLICATION_BONUS_MAX == 10


# --- Conflict penalties (authoritative) -------------------------------------------


def test_minor_conflict_penalty_exact():
    assert CONFLICT_PENALTIES[ConflictSeverity.MINOR] == 10


def test_major_conflict_penalty_exact():
    assert CONFLICT_PENALTIES[ConflictSeverity.MAJOR] == 25


def test_none_and_unknown_conflict_have_no_table_entry():
    """NONE/UNKNOWN both apply a zero penalty -- never a fabricated value."""
    assert ConflictSeverity.NONE not in CONFLICT_PENALTIES
    assert ConflictSeverity.UNKNOWN not in CONFLICT_PENALTIES


# --- ConfidenceClass thresholds (authoritative) -----------------------------------


@pytest.mark.parametrize(
    ("score", "expected_class"),
    [
        (100, ConfidenceClass.VERY_HIGH),
        (90, ConfidenceClass.VERY_HIGH),
        (89, ConfidenceClass.HIGH),
        (75, ConfidenceClass.HIGH),
        (74, ConfidenceClass.MODERATE),
        (50, ConfidenceClass.MODERATE),
        (49, ConfidenceClass.LOW),
        (0, ConfidenceClass.LOW),
    ],
)
def test_confidence_class_boundary_exact(score, expected_class):
    assert classify_confidence_class(score) is expected_class


def test_null_score_maps_to_unknown():
    assert classify_confidence_class(None) is ConfidenceClass.UNKNOWN
