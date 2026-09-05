"""Tests for ``app.claim_generation.prompts``."""

from __future__ import annotations

import pytest

from app.claim_generation.errors import EntityTypingError
from app.claim_generation.prompts import (
    CLAIM_GENERATION_PROMPT,
    entity_typing_hint_from_prompt_fields,
)
from app.claim_generation.types import EntityKind


def test_parses_recognized_kinds():
    hint = entity_typing_hint_from_prompt_fields(
        {"SUBJECT_TYPE": "GENE", "OBJECT_TYPE": "COMPOUND"}
    )
    assert hint.subject_kind is EntityKind.GENE
    assert hint.object_kind is EntityKind.COMPOUND


def test_missing_keys_default_to_unknown():
    hint = entity_typing_hint_from_prompt_fields({})
    assert hint.subject_kind is EntityKind.UNKNOWN
    assert hint.object_kind is EntityKind.UNKNOWN


def test_not_stated_maps_to_unknown():
    hint = entity_typing_hint_from_prompt_fields(
        {"SUBJECT_TYPE": "NOT STATED", "OBJECT_TYPE": "NOT STATED"}
    )
    assert hint.subject_kind is EntityKind.UNKNOWN
    assert hint.object_kind is EntityKind.UNKNOWN


def test_unrecognized_value_raises_entity_typing_error():
    with pytest.raises(EntityTypingError):
        entity_typing_hint_from_prompt_fields({"SUBJECT_TYPE": "ENZYME_COMPLEX"})


@pytest.mark.parametrize(
    "expected_substring",
    [
        "SUBJECT_TYPE",
        "OBJECT_TYPE",
        "UNKNOWN",
        "Do not infer",
        "Never guess a kind from the entity's name",
        "negative finding",
        "never rewritten as a positive assertion",
    ],
)
def test_prompt_requires_key_constraints(expected_substring):
    normalized = " ".join(CLAIM_GENERATION_PROMPT.split())
    assert expected_substring in normalized


def test_prompt_lists_every_entity_kind():
    for kind in EntityKind:
        assert kind.value in CLAIM_GENERATION_PROMPT


def test_prompt_is_a_fixed_string_not_a_template():
    assert "{" not in CLAIM_GENERATION_PROMPT
    assert "}" not in CLAIM_GENERATION_PROMPT
