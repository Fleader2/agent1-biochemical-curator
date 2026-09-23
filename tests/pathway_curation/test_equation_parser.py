"""Tests for ``app.pathway_curation.equation_parser.parse_kegg_equation`` (Step 34)."""

from __future__ import annotations

from decimal import Decimal

from app.pathway_curation.equation_parser import parse_kegg_equation


def test_single_substrate_and_product():
    result = parse_kegg_equation("C00024 <=> C00332")
    assert [p.kegg_compound_id for p in result.reactants] == ["C00024"]
    assert [p.kegg_compound_id for p in result.products] == ["C00332"]
    assert result.reversible is True
    assert result.unparseable_tokens == ()


def test_multiple_substrates_and_products():
    result = parse_kegg_equation("C00024 + C00083 <=> C00010 + C00332")
    assert [p.kegg_compound_id for p in result.reactants] == ["C00024", "C00083"]
    assert [p.kegg_compound_id for p in result.products] == ["C00010", "C00332"]


def test_omitted_coefficient_defaults_to_one():
    result = parse_kegg_equation("C00024 <=> C00332")
    assert result.reactants[0].coefficient == Decimal(1)


def test_coefficient_greater_than_one_is_preserved():
    result = parse_kegg_equation("2 C00005 + C00006 <=> C00003 + 2 C00080")
    coefficients = {p.kegg_compound_id: p.coefficient for p in result.reactants + result.products}
    assert coefficients["C00005"] == Decimal(2)
    assert coefficients["C00006"] == Decimal(1)
    assert coefficients["C00003"] == Decimal(1)
    assert coefficients["C00080"] == Decimal(2)


def test_reversible_arrow_is_detected():
    result = parse_kegg_equation("C00024 <=> C00332")
    assert result.reversible is True


def test_irreversible_forward_arrow_is_detected():
    result = parse_kegg_equation("C00024 => C00332")
    assert result.reversible is False


def test_malformed_equation_with_no_arrow_is_reported_verbatim():
    result = parse_kegg_equation("this is not a kegg equation")
    assert result.reactants == ()
    assert result.products == ()
    assert result.reversible is None
    assert result.unparseable_tokens == ("this is not a kegg equation",)


def test_unknown_compound_token_is_flagged_not_guessed():
    result = parse_kegg_equation("C00024 + G00123 <=> C00332")
    assert [p.kegg_compound_id for p in result.reactants] == ["C00024"]
    assert result.unparseable_tokens == ("G00123",)


def test_variable_polymer_coefficient_is_flagged_not_guessed():
    result = parse_kegg_equation("2n C00023 <=> C00024")
    assert result.unparseable_tokens == ("2n C00023",)
    assert [p.kegg_compound_id for p in result.products] == ["C00024"]


def test_blank_equation_returns_empty_result():
    result = parse_kegg_equation(None)
    assert result.reactants == ()
    assert result.products == ()
    assert result.reversible is None
    assert result.unparseable_tokens == ()
    assert result.has_any_participant is False


def test_output_ordering_is_deterministic_and_order_preserving():
    first = parse_kegg_equation("C00024 + C00083 <=> C00010 + C00332")
    second = parse_kegg_equation("C00024 + C00083 <=> C00010 + C00332")
    assert first == second


def test_has_any_participant_true_when_at_least_one_side_resolves():
    result = parse_kegg_equation("C00024 + G00123 <=> C00332")
    assert result.has_any_participant is True
