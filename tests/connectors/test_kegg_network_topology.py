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


# --- KEGG NETWORK operator-semantics increment ---------------------------------------------
#
# Every operator case below is evidence-backed -- see
# docs/28_kegg_network_support.md ("Operator semantics") for the full citation trail
# (https://www.genome.jp/kegg/document/help_network.html plus the specific real NETWORK
# records quoted in each test's own docstring). Operators with no real-record example found
# in this increment's own research are explicitly disclosed as such.


def test_inhibition_operator_real_record_n00270() -> None:
    """``-|`` -> ``INHIBITION``. Real record: KEGG N00270 ("HCV Core to IFN signaling
    pathway"), ``DEFINITION  Core -| (STAT1+STAT2+IRF9)`` -- HCV core protein inhibits the
    STAT1-STAT2-IRF9 (ISGF3) complex, corroborated by that entry's own REFERENCE/TITLE text
    ("core protein inhibits interferon-induced nuclear import of STATs")."""
    result = parse_topology("Core -| (STAT1+STAT2+IRF9)")

    assert not result.incomplete
    outer_edges = [e for e in result.edges if e.raw_operator == "-|"]
    assert {(e.source_token, e.target_token) for e in outer_edges} == {
        ("Core", "STAT1"),
        ("Core", "STAT2"),
        ("Core", "IRF9"),
    }
    assert all(e.interaction_type is NetworkEdgeInteractionType.INHIBITION for e in outer_edges)


def test_expression_operator_real_record_n00062() -> None:
    """``=>`` -> ``EXPRESSION``. Real record: KEGG N00062 ("Hedgehog signaling pathway"),
    ``DEFINITION`` ends ``... GLI => (GLI1,PTCH,HHIP)`` -- the GLI transcription factor
    drives expression of its own target genes."""
    result = parse_topology("GLI => (GLI1,PTCH,HHIP)")

    assert not result.incomplete
    assert {(e.source_token, e.target_token) for e in result.edges} == {
        ("GLI", "GLI1"),
        ("GLI", "PTCH"),
        ("GLI", "HHIP"),
    }
    assert all(e.interaction_type is NetworkEdgeInteractionType.EXPRESSION for e in result.edges)
    assert all(e.raw_operator == "=>" for e in result.edges)


def test_binding_operator_real_record_n00888() -> None:
    """``--`` -> ``BINDING``. Real record: KEGG N00888 ("Hypoxanthine oxidation"),
    ``DEFINITION  Hypoxanthine -- XDH -> Urate`` -- Hypoxanthine (substrate) binds XDH
    (enzyme), confirmed by KEGG's own metabolic-network legend entry "Substrate-enzyme
    relation" (https://www.genome.jp/kegg/document/help_network.html)."""
    result = parse_topology("Hypoxanthine -- XDH -> Urate")

    assert not result.incomplete
    binding_edge = result.edges[0]
    assert binding_edge.source_token == "Hypoxanthine"
    assert binding_edge.target_token == "XDH"
    assert binding_edge.raw_operator == "--"
    assert binding_edge.interaction_type is NetworkEdgeInteractionType.BINDING
    # The second edge ("->" in a metabolic context) stays UNKNOWN -- see
    # test_arrow_operator_remains_unknown_because_it_is_genuinely_context_dependent.
    assert result.edges[1].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_repression_operator_promoted_on_kegg_documentation_alone() -> None:
    """``=|`` -> ``REPRESSION``, promoted on KEGG's own documented NETWORK notation table
    (https://www.genome.jp/kegg/document/help_network.html: "=| Repression (single-step)").
    Disclosed: no real NETWORK record using this literal operator was found in this
    increment's own research sample; the expression below is constructed to exercise the
    documented grammar, not copied from an observed record."""
    result = parse_topology("TF =| TargetGene")

    assert not result.incomplete
    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.REPRESSION
    assert result.edges[0].raw_operator == "=|"


def test_complex_formation_via_double_equals_promoted_on_kegg_documentation_alone() -> None:
    """``==`` -> ``COMPLEX_FORMATION``, promoted on the same documentation table ("==
    Complex formation"). Disclosed: no real record using ``==`` as a standalone edge
    operator was found -- real records instead use ``+`` *inside* a group (see
    ``test_plus_inside_parens_produces_internal_complex_formation_edges``); ``==`` maps to
    the identical interaction type for consistency, since KEGG documents both as meaning
    the same thing."""
    result = parse_topology("A == B")

    assert not result.incomplete
    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.COMPLEX_FORMATION
    assert result.edges[0].raw_operator == "=="


def test_arrow_operator_remains_unknown_because_it_is_genuinely_context_dependent() -> None:
    """``->`` is never promoted: KEGG's own documentation gives it two different meanings
    ("Activation" in a signaling network, "Enzyme-product relation" in a metabolic one),
    and this parser has no access to which kind of network the expression came from."""
    result = parse_topology("A -> B")

    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_missing_interaction_operator_remains_unknown_real_record_n00075() -> None:
    """``//`` ("missing interaction/reaction" per KEGG's own documentation) is real,
    unambiguous, and KEGG-documented -- but has no matching ``NetworkEdgeInteractionType``
    member, so it is deliberately left ``UNKNOWN`` rather than force-fit into one. Real
    record: KEGG N00075, ``DEFINITION  RB1* // E2F`` (mutation-inactivated RB1 no longer
    represses E2F -- the interaction is now missing)."""
    result = parse_topology("RB1* // E2F")

    assert not result.incomplete
    assert result.edges[0].raw_operator == "//"
    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_enzyme_enzyme_relation_operator_remains_unknown_real_record_n00890() -> None:
    """``>>`` ("enzyme-enzyme relation of successive reactions") is real and KEGG-documented
    -- real record: KEGG N00890, ``... cPMP -- MOCS2 >> GPHN -> Moco ...`` -- but does not
    cleanly fit any of ACTIVATION/INHIBITION/EXPRESSION/REPRESSION/BINDING/
    COMPLEX_FORMATION/REACTION/TRANSPORT, so it is left ``UNKNOWN``."""
    result = parse_topology("MOCS2 >> GPHN")

    assert not result.incomplete
    assert result.edges[0].raw_operator == ">>"
    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_gain_of_function_operator_remains_unknown() -> None:
    """``-^`` ("gain of function" / "enhanced reaction" per KEGG's own documentation) is
    left ``UNKNOWN`` for the same reason as ``//``/``>>`` -- real, documented, but not a fit
    for any existing interaction-type member. No real record using it was found in this
    increment's own sample."""
    result = parse_topology("SMO* -^ GLI")

    assert not result.incomplete
    assert result.edges[0].raw_operator == "-^"
    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_plus_inside_parens_produces_internal_complex_formation_edges() -> None:
    """Real KEGG usage of ``+`` is *inside* a group, not as a bare top-level chain operator
    (confirmed: KEGG N00270's ``(STAT1+STAT2+IRF9)``, N00017's ``(SUFU+KIF7)``). A
    ``+``-group contributes its full leaf set to the *outer* edge (exactly like a
    ``,``-group), and additionally produces its own internal ``COMPLEX_FORMATION`` edges
    between adjacent members."""
    result = parse_topology("Core -| (STAT1+STAT2+IRF9)")

    assert not result.incomplete
    outer = {(e.source_token, e.target_token) for e in result.edges if e.raw_operator == "-|"}
    internal = {(e.source_token, e.target_token) for e in result.edges if e.raw_operator == "+"}
    assert outer == {("Core", "STAT1"), ("Core", "STAT2"), ("Core", "IRF9")}
    assert internal == {("STAT1", "STAT2"), ("STAT2", "IRF9")}
    assert all(
        e.interaction_type is NetworkEdgeInteractionType.COMPLEX_FORMATION
        for e in result.edges
        if e.raw_operator == "+"
    )


def test_top_level_bare_plus_still_classified_complex_formation() -> None:
    """A bare ``+`` between two top-level chain steps (never observed in a real record, but
    syntactically valid and semantically consistent) continues to classify as
    ``COMPLEX_FORMATION`` -- this is the pre-existing behavior this increment must not
    break."""
    result = parse_topology("A + B -> C")

    assert result.edges[0].interaction_type is NetworkEdgeInteractionType.COMPLEX_FORMATION
    assert result.edges[1].interaction_type is NetworkEdgeInteractionType.UNKNOWN


def test_group_mixing_comma_and_plus_separators_is_malformed() -> None:
    """A group using both ``,`` and ``+`` has no documented KEGG meaning -- never guessed,
    always reported as incomplete."""
    result = parse_topology("(A+B,C) -> D")

    assert result.incomplete
    assert result.edges == ()
    assert "mix" in result.reason.lower()


def test_variant_marker_asterisk_is_part_of_the_term_real_record_n00017() -> None:
    """``*`` is a term character (a trailing variant-marker suffix), never an operator.
    Real record: KEGG N00017, ``DEFINITION  SMO* -> (SUFU+KIF7) ...`` -- before this
    increment's fix, ``"SMO*"`` split into the term ``"SMO"`` plus a spurious one-character
    operator ``"*"``, making the whole expression unparseable."""
    result = parse_topology("SMO* -> GLI")

    assert not result.incomplete
    assert result.edges[0].source_token == "SMO*"


def test_compact_gene_family_slash_is_preserved_as_one_term_real_record_n00151() -> None:
    """A single ``/`` glued between identifier characters (KEGG's own compact gene-family
    shorthand, e.g. ``"TRAF2/5"`` meaning "TRAF2 or TRAF5") stays one term -- real record:
    KEGG N00151, ``(RIPK1+TRADD+TRAF2/5)``."""
    result = parse_topology("(RIPK1+TRADD+TRAF2/5) -> TAK1")

    assert not result.incomplete
    outer_targets = {e.target_token for e in result.edges if e.raw_operator == "->"}
    assert outer_targets == {"TAK1"}
    outer_sources = {e.source_token for e in result.edges if e.raw_operator == "->"}
    assert outer_sources == {"RIPK1", "TRADD", "TRAF2/5"}


def test_doubled_slash_is_still_the_missing_interaction_operator_not_a_term() -> None:
    """A doubled ``//`` is never absorbed into an adjacent term by the same ``/``-in-term
    rule that preserves ``"TRAF2/5"`` -- the lookahead rule distinguishes "next char is
    another identifier character" (stays in the term) from "next char is another ``/``"
    (starts the ``//`` operator)."""
    result = parse_topology("A // B")

    assert not result.incomplete
    assert result.edges[0].raw_operator == "//"
    assert result.edges[0].source_token == "A"
    assert result.edges[0].target_token == "B"


def test_real_record_n00017_full_expression_parses_end_to_end() -> None:
    """The complete real ``DEFINITION`` text of KEGG N00017 ("Mutation-activated SMO to
    Hedgehog signaling pathway") parses fully, with every operator classified per the
    mapping table above and the ``(SUFU+KIF7)`` complex producing its own internal edge."""
    expression = "SMO* -> (SUFU+KIF7) // GLI => (BMP2/4,HHIP,GLI1,PTCH,WNT)"
    result = parse_topology(expression)

    assert not result.incomplete
    by_operator: dict[str, set[tuple[str, str]]] = {}
    for edge in result.edges:
        by_operator.setdefault(edge.raw_operator, set()).add((edge.source_token, edge.target_token))

    assert by_operator["->"] == {("SMO*", "SUFU"), ("SMO*", "KIF7")}
    assert by_operator["//"] == {("SUFU", "GLI"), ("KIF7", "GLI")}
    assert by_operator["=>"] == {
        ("GLI", "BMP2/4"),
        ("GLI", "HHIP"),
        ("GLI", "GLI1"),
        ("GLI", "PTCH"),
        ("GLI", "WNT"),
    }
    assert by_operator["+"] == {("SUFU", "KIF7")}
    assert all(
        e.interaction_type is NetworkEdgeInteractionType.UNKNOWN
        for e in result.edges
        if e.raw_operator in ("->", "//")
    )
    assert all(
        e.interaction_type is NetworkEdgeInteractionType.EXPRESSION
        for e in result.edges
        if e.raw_operator == "=>"
    )
    assert all(
        e.interaction_type is NetworkEdgeInteractionType.COMPLEX_FORMATION
        for e in result.edges
        if e.raw_operator == "+"
    )


def test_malformed_parenthesized_label_is_incomplete_not_guessed() -> None:
    """KEGG metabolic DEFINITION text occasionally uses parentheses as part of a compound's
    own display name (e.g. ``"Moco(sulfide)"``, from real record N00890), which this
    parser's structural grammar cannot distinguish from a genuine grouping construct. This
    is a disclosed, out-of-scope limitation (see docs/28_kegg_network_support.md) -- the
    correct, safe behavior is to report ``incomplete=True``, never to guess."""
    result = parse_topology("Moco -- MOCOS -> Moco(sulfide)")

    assert result.incomplete
    assert result.edges == ()
