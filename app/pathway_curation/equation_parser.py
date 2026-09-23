"""Deterministic parsing of a KEGG reaction's raw ``EQUATION`` text.

``app.normalization.reaction.reaction_identity_from_kegg`` deliberately
never parses ``KeggReactionRecord.equation`` -- that module's own
docstring states the raw text is "unstructured" and that parsing it is
out of that increment's scope. It is not, in fact, unstructured: KEGG's
own reaction-equation grammar is a small, long-stable, fully documented
format (``https://www.kegg.jp/kegg/rest/keggapi.html``) -- a fixed arrow
token separating two ``" + "``-joined sides, each term an optional integer
coefficient (default 1) followed by a bare KEGG compound id
(``C\\d{5}``). This module adds exactly that minimal, deterministic
parser at the orchestration layer, without touching
``app.normalization.reaction`` at all -- the existing adapter's
``participants=()`` output is unchanged; ``app.pathway_curation.executor``
separately calls this parser and attaches a resolved ``participants``
tuple to the identity it already built, via ``dataclasses.replace``.

**Never guesses.** A term whose leading token is not a plain, positive
integer (KEGG's own polymer/variable-coefficient notation, e.g. ``"2n
C00023"`` or ``"(n+1) C00023"``) is reported verbatim in
``unparseable_tokens``, never coerced to a guessed integer. A term whose
compound token is not exactly ``C`` followed by five digits (a glycan id
like ``G00123``, a KCF-only entry, or any other non-compound token) is
likewise reported verbatim, never silently dropped and never treated as
a plain compound. An equation with no recognizable arrow at all reports
its full original text as the single unparseable token, empty sides, and
``reversible=None`` -- never an exception raised out of this module: the
caller (``executor.py``) decides how to turn that into a frontier item.

**No unit conversion, no mass/charge balancing, no polymer expansion**
-- this module only tokenizes and reads coefficients; it never verifies
that an equation is chemically balanced (that is a separate, deterministic
validation concern this increment does not implement, per
``app/models/reaction.py``'s own ``balanced_mass``/``balanced_charge``
columns existing precisely so a *later* pass can compute that) and never
expands a variable-length polymer -- see "Never guesses" above.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Longest/most-specific arrow first: "<=>" must never be matched as "=>"
# with a stray leading "<" left over. ``reversible`` states this module's
# entire, structural knowledge of direction from the arrow token alone --
# never a re-derivation of ``Reaction.reversible`` (which remains its own,
# separately-supplied field; see ``docs/03_agent_behavior.md``'s
# "Reversibility Behavior": reversibility must never be inferred from
# arrow notation. This module reports what the arrow token *literally is*,
# for the caller to decide what -- if anything -- to do with it; it is not
# itself the source of truth for ``Reaction.reversible``).
_ARROWS: tuple[tuple[str, bool, bool], ...] = (
    ("<=>", True, False),
    ("=>", False, False),
    ("->", False, False),
    ("<-", False, True),
)


@dataclass(frozen=True, slots=True)
class EquationParticipant:
    """One successfully parsed term: a bare KEGG compound id and its positive coefficient."""

    kegg_compound_id: str
    coefficient: Decimal

    def __post_init__(self) -> None:
        if self.coefficient <= 0:
            raise ValueError(
                f"EquationParticipant.coefficient must be positive, got {self.coefficient!r}"
            )


@dataclass(frozen=True, slots=True)
class ParsedKeggEquation:
    """The deterministic result of parsing one KEGG ``EQUATION`` string.

    ``unparseable_tokens`` holds every term (on either side) this parser
    could not safely resolve to a bare compound id and a plain integer
    coefficient, verbatim as it appeared in the source text -- never
    dropped silently. An equation is considered usable
    (``has_any_participant``) as soon as at least one side produced at
    least one participant, even if the other side (or a term within a
    side) could not be parsed -- partial structural knowledge is still
    worth persisting and is never withheld merely because one term was
    unparseable (the caller reports each such term as its own frontier
    item instead).
    """

    reactants: tuple[EquationParticipant, ...]
    products: tuple[EquationParticipant, ...]
    reversible: bool | None
    unparseable_tokens: tuple[str, ...]

    @property
    def has_any_participant(self) -> bool:
        return bool(self.reactants or self.products)


def _is_plain_positive_integer(token: str) -> bool:
    """A bare, non-negative-sign, digits-only coefficient token -- never ``"2n"``/``"(n+1)"``."""
    return token.isdigit()


def _is_kegg_compound_id(token: str) -> bool:
    return len(token) == 6 and token[0] == "C" and token[1:].isdigit()


def _parse_term(term: str) -> EquationParticipant | None:
    """Parse one ``+``-separated term. Returns ``None`` (never raises) when unsafe to resolve."""
    parts = term.split(None, 1)
    if len(parts) == 2 and _is_plain_positive_integer(parts[0]):
        coefficient_text, compound_token = parts
    else:
        coefficient_text, compound_token = "1", term
    if not _is_kegg_compound_id(compound_token):
        return None
    try:
        coefficient = Decimal(coefficient_text)
    except InvalidOperation:  # pragma: no cover -- _is_plain_positive_integer already guards this
        return None
    return EquationParticipant(kegg_compound_id=compound_token, coefficient=coefficient)


def _parse_side(side_text: str, unparseable: list[str]) -> tuple[EquationParticipant, ...]:
    participants: list[EquationParticipant] = []
    for raw_term in side_text.split("+"):
        term = raw_term.strip()
        if not term:
            continue
        parsed = _parse_term(term)
        if parsed is None:
            unparseable.append(term)
        else:
            participants.append(parsed)
    return tuple(participants)


def parse_kegg_equation(equation: str | None) -> ParsedKeggEquation:
    """Deterministically parse one KEGG reaction's raw ``EQUATION`` text.

    Never raises: a blank/``None`` equation returns an empty, fully
    unresolved result; an equation with no recognizable arrow reports its
    entire text as one unparseable token. Both are legitimate,
    inspectable outcomes for the caller to turn into a frontier item --
    never an exception escaping this module.
    """
    if equation is None or not equation.strip():
        return ParsedKeggEquation(reactants=(), products=(), reversible=None, unparseable_tokens=())

    text = equation.strip()
    best: tuple[int, str, bool, bool] | None = None
    for arrow, reversible, swapped in _ARROWS:
        index = text.find(arrow)
        if index != -1 and (best is None or index < best[0]):
            best = (index, arrow, reversible, swapped)

    if best is None:
        return ParsedKeggEquation(
            reactants=(), products=(), reversible=None, unparseable_tokens=(text,)
        )

    index, arrow, reversible, swapped = best
    left = text[:index]
    right = text[index + len(arrow) :]
    if swapped:
        left, right = right, left

    unparseable: list[str] = []
    reactants = _parse_side(left, unparseable)
    products = _parse_side(right, unparseable)
    return ParsedKeggEquation(
        reactants=reactants,
        products=products,
        reversible=reversible,
        unparseable_tokens=tuple(unparseable),
    )


__all__ = ["EquationParticipant", "ParsedKeggEquation", "parse_kegg_equation"]
