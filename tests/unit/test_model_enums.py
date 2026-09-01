"""Tests for the Agent 1 database enums.

Value lists are transcribed from ``docs/02_database_schema.md`` ("Enumerated
Types") and must match it exactly: no invented, renamed, reordered, or omitted
members (``00-agent1-core.mdc``, ``01-scientific-integrity.mdc``).
"""

from __future__ import annotations

import re

import pytest

from app.models import enums
from app.models.enums import (
    ClaimStatus,
    ConfidenceClass,
    CurationState,
    EvidenceType,
    ReactionParticipantRole,
    RegulatoryEffect,
    SourceType,
)

pytestmark = pytest.mark.unit

_STABLE_ENUM_VALUE = re.compile(r"^[A-Z][A-Z0-9_]*$")

_EXPECTED_NAMES = {
    "ClaimStatus",
    "ConfidenceClass",
    "CurationState",
    "EvidenceType",
    "ReactionParticipantRole",
    "RegulatoryEffect",
    "SourceType",
}

# Expected value sets, transcribed verbatim from docs/02_database_schema.md.
_EXPECTED_VALUES: dict[type, frozenset[str]] = {
    CurationState: frozenset(
        {"PROPOSED", "MACHINE_REVIEWED", "NEEDS_REVIEW", "HUMAN_ACCEPTED", "REJECTED"}
    ),
    ConfidenceClass: frozenset({"VERY_HIGH", "HIGH", "MODERATE", "LOW", "UNKNOWN"}),
    EvidenceType: frozenset(
        {
            "DIRECT_BIOCHEMICAL",
            "DIRECT_IN_VIVO",
            "GENETIC",
            "LOCALIZATION",
            "PROTEOMICS",
            "METABOLOMICS",
            "FLUXOMICS",
            "TRANSCRIPTOMICS",
            "STRUCTURAL",
            "CURATED_DATABASE",
            "COMPUTATIONAL",
            "HOMOLOGY",
            "REVIEW",
            "AUTHOR_HYPOTHESIS",
            "OTHER",
        }
    ),
    ClaimStatus: frozenset({"SUPPORTED", "CONFLICTED", "UNRESOLVED", "REJECTED", "UNKNOWN"}),
    ReactionParticipantRole: frozenset({"REACTANT", "PRODUCT", "MODIFIER"}),
    RegulatoryEffect: frozenset(
        {
            "ACTIVATION",
            "INHIBITION",
            "INDUCTION",
            "REPRESSION",
            "PHOSPHORYLATION",
            "DEPHOSPHORYLATION",
            "STABILIZATION",
            "DESTABILIZATION",
            "DEGRADATION",
            "TRANSLOCATION",
            "UNKNOWN",
            "OTHER",
        }
    ),
    SourceType: frozenset(
        {
            "PUBMED",
            "PMC",
            "KEGG",
            "BRENDA",
            "BIOCYC",
            "METACYC",
            "SGD",
            "UNIPROT",
            "CHEBI",
            "RHEA",
            "NCBI",
            "OTHER",
        }
    ),
}


@pytest.mark.parametrize("enum_cls", list(_EXPECTED_VALUES), ids=lambda c: c.__name__)
def test_enum_matches_specification_exactly(enum_cls: type) -> None:
    """Each enum contains exactly the values in docs/02_database_schema.md, no more, no less."""
    actual = {member.value for member in enum_cls}
    assert actual == _EXPECTED_VALUES[enum_cls]


@pytest.mark.parametrize("enum_cls", list(_EXPECTED_VALUES), ids=lambda c: c.__name__)
def test_enum_values_are_stable_strings(enum_cls: type) -> None:
    """Values must be plain uppercase strings, safe for a native PostgreSQL ENUM."""
    for member in enum_cls:
        assert isinstance(member.value, str)
        assert member.value == member.name, "value must not diverge from the member name"
        assert _STABLE_ENUM_VALUE.match(member.value)


def test_exactly_seven_enums_are_defined() -> None:
    """The module defines exactly the seven enums named in docs/02_database_schema.md."""
    assert set(enums.__all__) == _EXPECTED_NAMES


def test_kinetic_parameter_type_is_not_an_enum() -> None:
    """``kinetic_measurement.parameter_type`` must stay an open VARCHAR, never an enum.

    docs/02_database_schema.md: "Do not restrict the database so tightly that
    future parameter types cannot be added." No enum in this module may stand
    in for it.
    """
    disallowed_names = {"ParameterType", "KineticParameterType", "MeasurementParameterType"}
    assert disallowed_names.isdisjoint(enums.__all__)
    assert set(enums.__all__) == _EXPECTED_NAMES
