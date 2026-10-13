"""KEGG NETWORK topology parsing: ``DEFINITION``/``EXPANDED`` expressions.

A pure, deterministic parser from one KEGG topology expression string (e.g.
``"(hsa:1950,hsa:1956) -> hsa:5290 -> (hsa:207,hsa:208)"``) to an explicit,
flat tuple of directed edges between raw tokens. No I/O, no database access,
no biological interpretation of *what* a token is (that is
``app.normalization.network``'s job) -- this module only answers "which raw
tokens does this expression connect, via which raw operator."

**Grammar.** An expression is a chain of *terms* joined by *operators*:

```text
expr      := term (operator term)*
term      := TOKEN | '(' term (',' term)* ')'
operator  := any maximal run of characters that are not whitespace, not a
             term character, and not a structural character ('(', ')', ',')
```

A term character is ``[A-Za-z0-9:_./]`` -- the character classes KEGG's own
identifiers actually use (``"hsa:1950"``, ``"cpd:C00031"``, bare gene
symbols, dotted/sub-identifiers). Every character outside that set, outside
whitespace, and outside the three structural characters is therefore
unambiguously part of an operator token, including KEGG operator variants
this module does not specifically recognize -- exactly the "unsupported
operator preserved safely" requirement: an unrecognized operator is never
accidentally split, merged with a term, or dropped.

**Grouping semantics.** A parenthesized group is an explicit OR-set: every
leaf token inside it participates independently in whatever edge the group
as a whole forms. A *nested* group's own members are, in turn, flattened
into that same leaf set -- a deliberate, documented design choice for this
increment (nested parentheses are nested OR-grouping; an OR of ORs is just
one OR over the union of leaves), not a KEGG-documented requirement this
module could instead cite verbatim. An edge between two terms -- one or
both of which may be a group -- becomes one edge for every (source leaf,
target leaf) pair: this is exactly what reproduces the task's own three
worked examples (linear chain, branch, grouped upstream element).

**Operator interpretation is deliberately minimal.** Only two operators are
mapped to a specific ``NetworkEdgeInteractionType`` in this increment:

* ``->`` -- KEGG's own generic topology connector. It is mapped to
  ``UNKNOWN``, never guessed as ``ACTIVATION``/``REACTION``/anything more
  specific: KEGG's plain ``->`` does not, by itself, state a molecular
  mechanism, and this module never invents one without real operator-level
  evidence (the same "never silently invent topology" principle the task
  itself states for malformed syntax, applied here to operator semantics).
* ``+`` -- directly justified by notation: two terms joined by ``+`` are
  combining into one entity, i.e. ``COMPLEX_FORMATION``.

Every other operator (including any KEGG might use for activation/
inhibition/expression/etc. that this increment has no confirmed mapping
for) is preserved verbatim on ``TopologyEdge.raw_operator`` and classified
``UNKNOWN`` -- never dropped, never guessed. A future increment with real,
confirmed operator-to-semantics evidence can extend
``_KNOWN_OPERATOR_INTERACTION_TYPES`` without changing this module's own
parsing logic.

**Malformed syntax never raises out of ``parse_topology``.** Unbalanced
parentheses, an empty group, a dangling/doubled operator, or any other
structurally invalid expression is caught internally and reported as
``TopologyParseResult.incomplete=True`` with a human-readable ``reason`` --
``edges`` is always ``()`` in that case. Nothing here invents a speculative
edge to paper over syntax it cannot parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import NetworkEdgeInteractionType

_TERM_CHAR_RE = re.compile(r"[A-Za-z0-9:_./]")
_STRUCTURAL_CHARS = frozenset("(),")

#: The only operator-to-interaction-type mappings this increment asserts --
#: see the module docstring for why each one is justified and why no others
#: are included.
_KNOWN_OPERATOR_INTERACTION_TYPES: dict[str, NetworkEdgeInteractionType] = {
    "->": NetworkEdgeInteractionType.UNKNOWN,
    "+": NetworkEdgeInteractionType.COMPLEX_FORMATION,
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


def _leaf_tokens(node: _Atom | _Group) -> tuple[str, ...]:
    """Flatten a (possibly nested) group down to its leaf tokens, in order --
    see module docstring, "Grouping semantics."""
    if isinstance(node, _Atom):
        return (node.token,)
    leaves: list[str] = []
    for member in node.members:
        leaves.extend(_leaf_tokens(member))
    return tuple(leaves)


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
        if _TERM_CHAR_RE.match(ch):
            j = i
            while j < n and _TERM_CHAR_RE.match(expression[j]):
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
            and not _TERM_CHAR_RE.match(expression[j])
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
        while pos < len(tokens) and tokens[pos].kind == "COMMA":
            pos += 1
            node, pos = _parse_term(tokens, pos)
            members.append(node)
        if pos >= len(tokens) or tokens[pos].kind != "RPAREN":
            raise _TopologySyntaxError("unbalanced parentheses: missing ')'")
        pos += 1
        return _Group(tuple(members)), pos
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

    return TopologyParseResult(raw_expression=expression, edges=tuple(edges), incomplete=False)


__all__ = ["TopologyEdge", "TopologyParseResult", "parse_topology"]
