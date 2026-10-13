"""Tests for ``app.connectors.kegg_network_topology``.

Pure unit tests: no database, no HTTP, no live KEGG access. Covers the task's
own six required parsing cases (simple linear, branched, grouped upstream,
nested grouping, unsupported operator preserved, malformed expression).
"""

from __future__ import annotations

from app.connectors.kegg_network_topology import parse_topology
from app.models.enums import NetworkEdgeInteractionType


def _pairs(edges):
    return [(edge.source_token, edge.target_token) for edge in edges]


def test_simple_linear_network() -> None:
    """``A -> B -> C`` becomes ``A -> B``, ``B -> C``."""
    result = parse_topology("A -> B -> C")

    assert not result.incomplete
    assert _pairs(result.edges) == [("A", "B"), ("B", "C")]
    assert all(edge.raw_operator == "->" for edge in result.edges)


def test_branched_network() -> None:
    """``A -> B -> (C,D)`` becomes ``A -> B``, ``B -> C``, ``B -> D``."""
    result = parse_topology("A -> B -> (C,D)")

    assert not result.incomplete
    assert _pairs(result.edges) == [("A", "B"), ("B", "C"), ("B", "D")]


def test_grouped_upstream_nodes() -> None:
    """``(A,B) -> C`` becomes ``A -> C``, ``B -> C``."""
    result = parse_topology("(A,B) -> C")

    assert not result.incomplete
    assert _pairs(result.edges) == [("A", "C"), ("B", "C")]


def test_nested_grouping() -> None:
    """A nested group flattens to the union of its own leaf tokens."""
    result = parse_topology("((A,B),C) -> D")

    assert not result.incomplete
    assert _pairs(result.edges) == [("A", "D"), ("B", "D"), ("C", "D")]


def test_unsupported_operator_preserved_safely() -> None:
    """An operator this module does not recognize is preserved verbatim and
    classified ``UNKNOWN`` -- never dropped, never guessed at, and never
    treated as a parse failure (the topology itself is still fully
    determined)."""
    result = parse_topology("A ~> B")

    assert not result.incomplete
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.source_token == "A"
    assert edge.target_token == "B"
    assert edge.raw_operator == "~>"
    assert edge.interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_malformed_expression_unbalanced_parentheses_produces_review_state() -> None:
    """A structurally invalid expression never creates speculative edges."""
    result = parse_topology("(A -> B")

    assert result.incomplete
    assert result.edges == ()
    assert "unbalanced" in result.reason.lower()


def test_malformed_expression_dangling_operator() -> None:
    result = parse_topology("A ->")

    assert result.incomplete
    assert result.edges == ()


def test_malformed_expression_empty_group() -> None:
    result = parse_topology("A -> ()")

    assert result.incomplete
    assert result.edges == ()


def test_empty_expression_is_incomplete_not_an_error() -> None:
    result = parse_topology("")

    assert result.incomplete
    assert result.edges == ()
    assert result.reason == "empty expression"


def test_plain_arrow_operator_is_classified_unknown_never_guessed() -> None:
    """KEGG's own plain ``->`` operator carries no mechanism by itself --
    never defaulted to ``ACTIVATION``/``REACTION``/anything more specific
    than ``UNKNOWN``."""
    result = parse_topology("A -> B")

    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_plus_operator_is_classified_complex_formation() -> None:
    """``+`` is the one operator this increment confidently classifies, by
    direct notational justification."""
    result = parse_topology("A + B -> C")

    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.COMPLEX_FORMATION
    assert result.edges[1].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_real_kegg_style_gene_identifiers() -> None:
    """Real KEGG gene-id-shaped tokens (``"hsa:1950"``) parse correctly --
    the term-character class must accept ``:`` and digits."""
    result = parse_topology("(hsa:1950,hsa:1956) -> hsa:5290 -> (hsa:207,hsa:208)")

    assert not result.incomplete
    assert _pairs(result.edges) == [
        ("hsa:1950", "hsa:5290"),
        ("hsa:1956", "hsa:5290"),
        ("hsa:5290", "hsa:207"),
        ("hsa:5290", "hsa:208"),
    ]
