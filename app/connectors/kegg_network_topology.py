"""KEGG NETWORK topology parsing: ``DEFINITION``/``EXPANDED`` expressions.

A pure, deterministic parser from one KEGG topology expression string (e.g.
``"(hsa:1950,hsa:1956) -> hsa:5290 -> (hsa:207,hsa:208)"``) to an explicit,
flat tuple of directed edges between raw tokens. No I/O, no database access,
no biological interpretation of *what* a token is (that is
``app.normalization.network``'s job) -- this module only answers "which raw
tokens does this expression connect, via which raw operator."

**Grammar.**

```text
expr      := term (operator term)*
term      := TOKEN | '(' term (separator term)* ')'
separator := ',' | '+'
operator  := any maximal run of characters that are not whitespace, not a
             term character, and not a structural character ('(', ')', ',')
```

A term character is ``[A-Za-z0-9:_./]`` -- the character classes KEGG's own
identifiers actually use (``"hsa:1950"``, ``"cpd:C00031"``, bare gene
symbols). Every character outside that set, outside whitespace, and outside
the three structural characters is therefore unambiguously part of an
operator token, including KEGG operator variants this module does not
specifically recognize -- the "unsupported operator preserved safely"
requirement: an unrecognized operator is never accidentally split, merged
with a term, or dropped.

**Grouping semantics -- two distinct separators, confirmed by real KEGG
NETWORK records (KEGG NETWORK operator-semantics increment).** A
parenthesized group's members are joined by exactly one separator
throughout -- mixing ``,`` and ``+`` in the same group is rejected as
malformed, never guessed:

* ``,`` -- an OR-set: every leaf token inside it participates independently
  in whatever edge the group as a whole forms (e.g. ``(C,D)`` -- "either C
  or D"). This is the only grouping KEGG's own two worked examples in the
  original task used, and remains unchanged from the prior increment.
* ``+`` -- an AND-set: the members together form one combined complex,
  confirmed directly from real NETWORK records, e.g. KEGG entry ``N00270``'s
  own ``Core -| (STAT1+STAT2+IRF9)`` (the real ISGF3 complex) and
  ``N00017``'s own ``(SUFU+KIF7)``. A ``+``-group's own leaf tokens still
  participate in the *outer* edge exactly like a ``,``-group's would (the
  whole complex is what the outer edge actually connects to), but
  additionally produces internal ``COMPLEX_FORMATION`` edges between its own
  adjacent members (see ``_collect_internal_edges``) -- without this, the
  ``+`` operator's own classification would never actually appear in a
  parsed real record, since real records always write it *inside* a group,
  never as a bare top-level chain operator.

A nested group's own members are, in turn, flattened into the same leaf set
for *outer*-edge purposes regardless of which separator produced them (an OR
of ORs, or a group nested inside an AND-set's own member, is still just a
union of leaves one level up) -- internal ``+``-group edges are collected
separately, at every nesting depth, and never affect this outer flattening.

**Operator interpretation: evidence-backed, from KEGG's own documentation.**
See ``docs/28_kegg_network_support.md`` ("Operator semantics") for the full
citation trail (authoritative source, real NETWORK records examined, and
the complete decision table). Summary:

* Promoted (KEGG's own documented NETWORK notation,
  https://www.genome.jp/kegg/document/help_network.html, each corroborated
  by at least one real NETWORK record): ``-|`` -> ``INHIBITION``, ``=>`` ->
  ``EXPRESSION``, ``--`` -> ``BINDING``, ``+``/``==`` -> ``COMPLEX_FORMATION``.
  ``=|`` -> ``REPRESSION`` is also promoted on the strength of that same
  primary KEGG documentation, though no real record using it was found in
  this increment's own sampling -- disclosed, not hidden (see the design
  doc).
* Deliberately **not** promoted despite being KEGG-documented: ``->`` is
  genuinely context-dependent *in KEGG's own documentation itself* --
  ``"Activation"`` in a signaling network, but ``"Enzyme-product
  relation"`` in a metabolic network, with no way to tell which from the
  operator string alone (this module has no access to the owning NETWORK
  entry's own type). This is exactly the task's own "context-dependent...
  must remain UNKNOWN" case, not an oversight. ``//`` ("missing
  interaction/reaction"), ``-^`` ("gain of function"/"enhanced reaction"),
  and ``>>`` ("enzyme-enzyme relation of successive reactions") are all
  real, KEGG-documented, unambiguous notations -- but none of them is a
  defensible fit for any existing ``NetworkEdgeInteractionType`` member
  (``ACTIVATION``/``INHIBITION``/``EXPRESSION``/``REPRESSION``/``BINDING``/
  ``COMPLEX_FORMATION``/``REACTION``/``TRANSPORT``), so none is force-fit
  into one; they remain ``UNKNOWN`` with their own raw text preserved,
  exactly like any unrecognized operator.
* Every other operator (anything not listed above) is preserved verbatim on
  ``TopologyEdge.raw_operator`` and classified ``UNKNOWN`` -- never dropped,
  never guessed.

**Malformed syntax never raises out of ``parse_topology``.** Unbalanced
parentheses, an empty group, a dangling/doubled operator, a group that mixes
``,`` and ``+`` separators, or any other structurally invalid expression is
caught internally and reported as ``TopologyParseResult.incomplete=True``
with a human-readable ``reason`` -- ``edges`` is always ``()`` in that case.
Nothing here invents a speculative edge to paper over syntax it cannot
parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import NetworkEdgeInteractionType

#: ``*`` is a core term character, not an operator character: real NETWORK
#: records use it as a trailing variant-marker suffix directly on a gene
#: symbol (e.g. ``"SMO*"``, ``"BRAF*"``, ``"RB1*"`` -- confirmed live
#: against KEGG entries N00017/N00013/N00075, each pairing the marked symbol
#: with its own ``VARIANT`` field, e.g. ``VARIANT 6608v1 (SMO*) SMO
#: mutation``), never as a connector between two different identifiers.
#: Before this was added, ``"SMO*"`` tokenized as the term ``"SMO"``
#: immediately followed by a spurious one-character operator ``"*"``,
#: breaking the very next token boundary and making the whole expression
#: unparseable (``incomplete=True``) -- a real gap this increment's own
#: real-record testing surfaced, not a hypothetical.
#:
#: ``/`` is deliberately **not** in this set -- see ``_slash_is_inside_term``
#: immediately below for why a single ``/`` (compact gene-family shorthand
#: like ``"BMP2/4"``/``"TRAF2/5"``) and a doubled ``//`` (the real,
#: KEGG-documented "missing interaction/reaction" operator) need different
#: tokenizing treatment despite sharing a character.
_CORE_TERM_CHAR_RE = re.compile(r"[A-Za-z0-9:_.*]")


def _slash_is_inside_term(expression: str, pos: int) -> bool:
    """``True`` when the ``/`` at ``pos`` is part of a compact gene-family
    identifier (``"BMP2/4"``, ``"TRAF2/5"``, ``"TAB1/2/3"``), never when it
    is the start of the doubled ``//`` operator or at a term's own boundary.

    A single ``/`` glued directly between two identifier-like characters is
    KEGG's own compact shorthand for "either of these" (confirmed live: KEGG
    entry N00151's ``GENE`` field resolves ``"TAB1/2/3"`` to three separate
    gene ids). The real ``//`` operator, in every real record this
    increment examined, always appears as its own whitespace-bounded token
    (e.g. ``"RB1* // E2F"``) -- so the deciding test is simply whether the
    *next* character is itself a core term character: if it is another
    ``/``, whitespace, or anything else that is not part of an identifier,
    this ``/`` is not inside a term.
    """
    next_pos = pos + 1
    return next_pos < len(expression) and bool(_CORE_TERM_CHAR_RE.match(expression[next_pos]))


def _is_term_char(expression: str, pos: int) -> bool:
    ch = expression[pos]
    if _CORE_TERM_CHAR_RE.match(ch):
        return True
    return ch == "/" and _slash_is_inside_term(expression, pos)


_STRUCTURAL_CHARS = frozenset("(),")

#: KEGG NETWORK operator-semantics increment: the full, evidence-backed
#: operator-to-interaction-type table. See module docstring and
#: ``docs/28_kegg_network_support.md`` ("Operator semantics") for the
#: authoritative source, every real NETWORK record examined, and the
#: reasoning behind each entry -- including the operators deliberately kept
#: at ``UNKNOWN`` despite being real, KEGG-documented notation.
_KNOWN_OPERATOR_INTERACTION_TYPES: dict[str, NetworkEdgeInteractionType] = {
    # Genuinely context-dependent in KEGG's own documentation (Activation in
    # a signaling network; Enzyme-product relation in a metabolic network) --
    # never promoted. See module docstring.
    "->": NetworkEdgeInteractionType.UNKNOWN,
    # Evidence-backed promotions.
    "-|": NetworkEdgeInteractionType.INHIBITION,
    "=>": NetworkEdgeInteractionType.EXPRESSION,
    "=|": NetworkEdgeInteractionType.REPRESSION,
    "--": NetworkEdgeInteractionType.BINDING,
    "+": NetworkEdgeInteractionType.COMPLEX_FORMATION,
    "==": NetworkEdgeInteractionType.COMPLEX_FORMATION,
    # Real, KEGG-documented, unambiguous notation -- but none is a
    # defensible fit for any existing NetworkEdgeInteractionType member, so
    # none is force-fit into one. Listed explicitly (rather than silently
    # falling through to the default) so a future reader knows these were
    # considered and deliberately not promoted, not overlooked.
    "//": NetworkEdgeInteractionType.UNKNOWN,  # "missing interaction/reaction"
    "-^": NetworkEdgeInteractionType.UNKNOWN,  # "gain of function"/"enhanced reaction"
    ">>": NetworkEdgeInteractionType.UNKNOWN,  # "enzyme-enzyme relation of successive reactions"
}


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """One directed edge between two raw tokens, as literally written in the
    source expression -- no node-type classification, no entity mapping."""

    source_token: str
    target_token: str
    raw_operator: str
    interaction_type: NetworkEdgeInteractionType


@dataclass(frozen=True, slots=True)
class TopologyParseResult:
    """The result of parsing one topology expression.

    ``incomplete=True`` means the expression could not be fully parsed --
    ``edges`` is always ``()`` in that case, and ``reason`` explains why.
    ``incomplete=False`` means the topology was fully determined (every
    edge in ``edges`` is real); an unrecognized *operator* never sets
    ``incomplete`` by itself -- it only affects that edge's own
    ``interaction_type`` (``UNKNOWN``), never whether parsing succeeded.
    """

    raw_expression: str
    edges: tuple[TopologyEdge, ...]
    incomplete: bool
    reason: str = ""


class _TopologySyntaxError(Exception):
    """Internal only -- always caught inside ``parse_topology`` and turned
    into an ``incomplete`` result, never raised to a caller."""


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "TERM" | "OPERATOR" | "LPAREN" | "RPAREN" | "COMMA"
    text: str


@dataclass(frozen=True, slots=True)
class _Atom:
    token: str


@dataclass(frozen=True, slots=True)
class _Group:
    members: tuple[_Atom | _Group, ...]
    #: ``","`` (OR-set) or ``"+"`` (AND-set / complex) -- see module
    #: docstring, "Grouping semantics." Meaningless (but harmlessly set to
    #: ``","``) for a singleton group, which has no separator to speak of.
    separator: str


def _leaf_tokens(node: _Atom | _Group) -> tuple[str, ...]:
    """Flatten a (possibly nested) group down to its leaf tokens, in order --
    see module docstring, "Grouping semantics." Identical treatment for a
    ``,``-group and a ``+``-group: both contribute their full leaf set to
    whatever *outer* edge the group as a whole participates in."""
    if isinstance(node, _Atom):
        return (node.token,)
    leaves: list[str] = []
    for member in node.members:
        leaves.extend(_leaf_tokens(member))
    return tuple(leaves)


def _collect_internal_edges(node: _Atom | _Group) -> list[TopologyEdge]:
    """Recursively find every ``+``-group anywhere inside ``node`` (at any
    nesting depth) and emit a ``COMPLEX_FORMATION`` edge between each pair
    of its own *adjacent* members -- the internal structure a ``+``-group
    describes, distinct from, and in addition to, whatever outer edge the
    group as a whole also participates in (via ``_leaf_tokens``). See module
    docstring, "Grouping semantics," for why this is necessary for the
    ``+`` operator's own classification to ever actually appear in a parsed
    real record.
    """
    if isinstance(node, _Atom):
        return []

    edges: list[TopologyEdge] = []
    for member in node.members:
        edges.extend(_collect_internal_edges(member))

    if node.separator == "+" and len(node.members) >= 2:
        interaction_type = _KNOWN_OPERATOR_INTERACTION_TYPES["+"]
        for left, right in zip(node.members, node.members[1:], strict=False):
            for source_token in _leaf_tokens(left):
                for target_token in _leaf_tokens(right):
                    edges.append(
                        TopologyEdge(
                            source_token=source_token,
                            target_token=target_token,
                            raw_operator="+",
                            interaction_type=interaction_type,
                        )
                    )
    return edges


def _tokenize(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(expression)
    while i < n:
        ch = expression[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token("LPAREN", ch))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token("RPAREN", ch))
            i += 1
            continue
        if ch == ",":
            tokens.append(_Token("COMMA", ch))
            i += 1
            continue
        if _is_term_char(expression, i):
            j = i
            while j < n and _is_term_char(expression, j):
                j += 1
            tokens.append(_Token("TERM", expression[i:j]))
            i = j
            continue
        # Everything else (not whitespace, not structural, not a term
        # character) is an operator -- including any KEGG operator variant
        # this module does not specifically recognize. See module docstring.
        j = i
        while (
            j < n
            and not expression[j].isspace()
            and expression[j] not in _STRUCTURAL_CHARS
            and not _is_term_char(expression, j)
        ):
            j += 1
        tokens.append(_Token("OPERATOR", expression[i:j]))
        i = j
    return tokens


def _parse_term(tokens: list[_Token], pos: int) -> tuple[_Atom | _Group, int]:
    if pos >= len(tokens):
        raise _TopologySyntaxError("expected a term, found end of expression")
    tok = tokens[pos]
    if tok.kind == "TERM":
        return _Atom(tok.text), pos + 1
    if tok.kind == "LPAREN":
        members: list[_Atom | _Group] = []
        pos += 1
        node, pos = _parse_term(tokens, pos)
        members.append(node)

        separator: str | None = None
        while pos < len(tokens):
            next_tok = tokens[pos]
            if next_tok.kind == "COMMA":
                candidate_separator = ","
            elif next_tok.kind == "OPERATOR" and next_tok.text == "+":
                candidate_separator = "+"
            else:
                break
            if separator is None:
                separator = candidate_separator
            elif separator != candidate_separator:
                raise _TopologySyntaxError(
                    "a group cannot mix ',' and '+' separators "
                    f"(already using {separator!r}, found {candidate_separator!r})"
                )
            pos += 1
            node, pos = _parse_term(tokens, pos)
            members.append(node)

        if pos >= len(tokens) or tokens[pos].kind != "RPAREN":
            raise _TopologySyntaxError("unbalanced parentheses: missing ')'")
        pos += 1
        return _Group(tuple(members), separator=separator or ","), pos
    raise _TopologySyntaxError(f"expected a term, found {tok.kind.lower()} {tok.text!r}")


def _parse_chain(
    tokens: list[_Token],
) -> tuple[list[_Atom | _Group], list[str]]:
    pos = 0
    steps: list[_Atom | _Group] = []
    operators: list[str] = []

    node, pos = _parse_term(tokens, pos)
    steps.append(node)
    while pos < len(tokens):
        tok = tokens[pos]
        if tok.kind != "OPERATOR":
            raise _TopologySyntaxError(
                f"expected an operator, found {tok.kind.lower()} {tok.text!r}"
            )
        operators.append(tok.text)
        pos += 1
        node, pos = _parse_term(tokens, pos)
        steps.append(node)

    return steps, operators


def parse_topology(expression: str) -> TopologyParseResult:
    """Parse one KEGG ``DEFINITION``/``EXPANDED`` topology expression.

    Never raises -- a structurally invalid expression is reported as
    ``incomplete=True`` with ``edges=()``. See module docstring for the
    grammar, grouping semantics, and operator-interpretation policy.
    """
    stripped = expression.strip()
    if not stripped:
        return TopologyParseResult(
            raw_expression=expression, edges=(), incomplete=True, reason="empty expression"
        )

    try:
        tokens = _tokenize(stripped)
        if not tokens:
            return TopologyParseResult(
                raw_expression=expression, edges=(), incomplete=True, reason="no tokens found"
            )
        steps, operators = _parse_chain(tokens)
    except _TopologySyntaxError as exc:
        return TopologyParseResult(
            raw_expression=expression, edges=(), incomplete=True, reason=str(exc)
        )

    edges: list[TopologyEdge] = []
    for index, raw_operator in enumerate(operators):
        source_leaves = _leaf_tokens(steps[index])
        target_leaves = _leaf_tokens(steps[index + 1])
        interaction_type = _KNOWN_OPERATOR_INTERACTION_TYPES.get(
            raw_operator, NetworkEdgeInteractionType.UNKNOWN
        )
        for source_token in source_leaves:
            for target_token in target_leaves:
                edges.append(
                    TopologyEdge(
                        source_token=source_token,
                        target_token=target_token,
                        raw_operator=raw_operator,
                        interaction_type=interaction_type,
                    )
                )

    for step in steps:
        edges.extend(_collect_internal_edges(step))

    return TopologyParseResult(raw_expression=expression, edges=tuple(edges), incomplete=False)


__all__ = ["TopologyEdge", "TopologyParseResult", "parse_topology"]
