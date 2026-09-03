"""Tests for the shared, source-agnostic normalization primitives.

No database, no HTTP, no fixtures -- these are pure functions.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.normalization.identifiers import (
    CandidateSetState,
    classify_candidates,
    require_non_empty,
)

pytestmark = pytest.mark.unit


# --- classify_candidates() ------------------------------------------------------


def test_classify_candidates_empty_list_is_no_match() -> None:
    assert classify_candidates([]) is CandidateSetState.NO_MATCH


def test_classify_candidates_one_id_is_single_match() -> None:
    assert classify_candidates([uuid4()]) is CandidateSetState.SINGLE_MATCH


def test_classify_candidates_two_ids_is_ambiguous() -> None:
    assert classify_candidates([uuid4(), uuid4()]) is CandidateSetState.AMBIGUOUS


def test_classify_candidates_many_ids_is_ambiguous() -> None:
    assert classify_candidates([uuid4() for _ in range(5)]) is CandidateSetState.AMBIGUOUS


def test_classify_candidates_accepts_any_sequence_not_just_a_list() -> None:
    """A tuple (what a DB query result is typically coerced to) works the same as a list."""
    assert classify_candidates((uuid4(),)) is CandidateSetState.SINGLE_MATCH


def test_identifier_normalization_same_input_same_output() -> None:
    """Determinism (docs/05_testing.md, "Determinism Tests"): same candidate-list
    shape always classifies the same way, called repeatedly.
    """
    candidates = (uuid4(), uuid4())
    results = {classify_candidates(candidates) for _ in range(5)}
    assert results == {CandidateSetState.AMBIGUOUS}


def test_classify_candidates_does_not_map_to_a_normalization_status() -> None:
    """CandidateSetState is deliberately not NormalizationStatus.

    NO_MATCH must not be assumed to mean NEW -- that decision (NEW vs.
    UNRESOLVED) belongs to the entity-specific caller, based on identifier
    strength, not to this module.
    """
    assert not hasattr(CandidateSetState, "NEW")
    assert not hasattr(CandidateSetState, "MATCHED")
    assert not hasattr(CandidateSetState, "UNRESOLVED")


# --- require_non_empty() --------------------------------------------------------


def test_require_non_empty_strips_and_returns_value() -> None:
    assert require_non_empty("  S000000364  ") == "S000000364"


def test_require_non_empty_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="value must not be empty"):
        require_non_empty("")


def test_require_non_empty_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError, match="value must not be empty"):
        require_non_empty("   ")


def test_require_non_empty_uses_supplied_field_name_in_message() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        require_non_empty("", field_name="source_identifier")
