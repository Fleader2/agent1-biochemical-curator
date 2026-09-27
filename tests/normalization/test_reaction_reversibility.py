"""Tests for reaction reversibility resolution (Agent 1.x Increment C.8).

Pure unit tests: no database, no HTTP, no live connector access.
"""

from __future__ import annotations

import pytest

from app.models.enums import SourceType
from app.normalization.reaction_reversibility import (
    ReversibilityEvidence,
    ReversibilityResolution,
    resolve_reaction_reversibility,
    reversibility_evidence_from_kegg_equation,
    reversibility_evidence_from_sabiork_kineticlaw,
)

pytestmark = pytest.mark.unit


def _evidence(**overrides) -> ReversibilityEvidence:
    merged = {
        "source": SourceType.RHEA,
        "source_identifier": "RHEA:14996",
        "reversible": True,
        "basis": "explicit source annotation",
    } | overrides
    return ReversibilityEvidence(**merged)


# --- A. Explicit reversible source evidence -----------------------------------------------------


def test_a_explicit_reversible_evidence_resolves_true():
    resolution = resolve_reaction_reversibility((_evidence(reversible=True),))
    assert resolution.reversible is True
    assert resolution.conflicting is False
    assert len(resolution.evidence) == 1


# --- B. Explicit irreversible source evidence ---------------------------------------------------


def test_b_explicit_irreversible_evidence_resolves_false():
    resolution = resolve_reaction_reversibility((_evidence(reversible=False),))
    assert resolution.reversible is False
    assert resolution.conflicting is False


# --- C. Absent evidence remains None ------------------------------------------------------------


def test_c_absent_evidence_remains_none():
    resolution = resolve_reaction_reversibility(())
    assert resolution.reversible is None
    assert resolution.conflicting is False
    assert resolution.evidence == ()


# --- D. Two agreeing sources ---------------------------------------------------------------------


def test_d_two_agreeing_sources_combine_and_preserve_both():
    evidence = (
        _evidence(source=SourceType.RHEA, source_identifier="RHEA:14996", reversible=True),
        _evidence(source=SourceType.BIOCYC, source_identifier="RXN-123", reversible=True),
    )
    resolution = resolve_reaction_reversibility(evidence)
    assert resolution.reversible is True
    assert resolution.conflicting is False
    # Provenance from both agreeing sources is preserved, never collapsed to one.
    assert resolution.evidence == evidence


# --- E. Conflicting sources remain unresolved ---------------------------------------------------


def test_e_conflicting_sources_remain_unresolved_and_disclosed():
    evidence = (
        _evidence(source=SourceType.RHEA, source_identifier="RHEA:14996", reversible=True),
        _evidence(source=SourceType.BIOCYC, source_identifier="RXN-123", reversible=False),
    )
    resolution = resolve_reaction_reversibility(evidence)
    assert resolution.reversible is None
    assert resolution.conflicting is True
    assert "RHEA:14996" in resolution.reason
    assert "RXN-123" in resolution.reason
    # Both conflicting claims remain fully auditable.
    assert resolution.evidence == evidence


# --- F. Source precedence is documentation, never a runtime override ----------------------------


def test_f_precedence_never_silently_overrides_a_conflict():
    """Even though Rhea is this project's own #1-preferred source, a Rhea claim never
    silently wins over a disagreeing lower-priority source's claim -- see module docstring."""
    evidence = (
        _evidence(source=SourceType.RHEA, source_identifier="RHEA:1", reversible=True),
        _evidence(source=SourceType.KEGG, source_identifier="R00001", reversible=False),
    )
    resolution = resolve_reaction_reversibility(evidence)
    assert resolution.reversible is None
    assert resolution.conflicting is True


def test_f_order_independence():
    forward = (
        _evidence(source=SourceType.RHEA, source_identifier="RHEA:1", reversible=True),
        _evidence(source=SourceType.BIOCYC, source_identifier="B1", reversible=True),
    )
    reversed_order = tuple(reversed(forward))
    r1 = resolve_reaction_reversibility(forward)
    r2 = resolve_reaction_reversibility(reversed_order)
    assert r1.reversible == r2.reversible == True  # noqa: E712
    assert r1.conflicting == r2.conflicting is False


# --- G. Idempotent repeated resolution ------------------------------------------------------------


def test_g_repeated_resolution_is_deterministic():
    evidence = (_evidence(reversible=True),)
    r1 = resolve_reaction_reversibility(evidence)
    r2 = resolve_reaction_reversibility(evidence)
    assert r1 == r2


# --- H. No inference from EC/name/pathway position: the function accepts no such input ----------


def test_h_resolution_never_reads_anything_but_explicit_evidence():
    """resolve_reaction_reversibility's own signature accepts only ReversibilityEvidence --
    there is no EC number, reaction name, or pathway-position parameter it could possibly
    read, by construction."""
    import inspect

    signature = inspect.signature(resolve_reaction_reversibility)
    assert list(signature.parameters) == ["evidence"]


# --- KEGG: always declines, regardless of arrow token --------------------------------------------


@pytest.mark.parametrize(
    "equation",
    [
        "C00031 + C00002 <=> C00092 + C00008",
        "C00031 + C00002 => C00092 + C00008",
        "C00031 + C00002 -> C00092 + C00008",
        "C00031 + C00002 <- C00092 + C00008",
        None,
        "",
    ],
)
def test_kegg_equation_never_produces_evidence_regardless_of_arrow(equation):
    assert reversibility_evidence_from_kegg_equation("R00299", equation) is None


def test_kegg_evidence_absence_resolves_to_none_end_to_end():
    evidence = reversibility_evidence_from_kegg_equation("R00299", "C1 <=> C2")
    resolution = resolve_reaction_reversibility((evidence,) if evidence is not None else ())
    assert resolution.reversible is None
    assert resolution.conflicting is False


# --- SABIO-RK: always declines, regardless of the reported value --------------------------------


@pytest.mark.parametrize("value", ["reversible", "irreversible", None, "unknown"])
def test_sabiork_kinlaw_reversible_never_produces_evidence(value):
    assert reversibility_evidence_from_sabiork_kineticlaw("18229:Km", value) is None


# --- Unresolved-with-conflict invariant -----------------------------------------------------------


def test_resolution_rejects_conflicting_with_a_non_none_value():
    with pytest.raises(ValueError, match="conflicting"):
        ReversibilityResolution(
            reversible=True, conflicting=True, evidence=(), reason="invalid by construction"
        )


def test_evidence_requires_non_empty_source_identifier():
    with pytest.raises(ValueError):
        ReversibilityEvidence(
            source=SourceType.RHEA, source_identifier="", reversible=True, basis="x"
        )


def test_evidence_requires_non_empty_basis():
    with pytest.raises(ValueError):
        ReversibilityEvidence(
            source=SourceType.RHEA, source_identifier="RHEA:1", reversible=True, basis=""
        )
