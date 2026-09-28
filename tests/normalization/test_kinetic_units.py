"""Tests for canonical kinetic-unit normalization (Agent 1.x Increment C.12).

Pure unit tests: no database, no connector I/O.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.normalization.kinetic_units import (
    CANONICAL_UNIT_NM,
    CANONICAL_UNIT_NM_PER_S,
    CANONICAL_UNIT_PER_NMS,
    CANONICAL_UNIT_PER_SEC,
    UnitConversionStatus,
    convert_to_canonical_unit,
)

pytestmark = pytest.mark.unit


# --- Concentration family: Km/Ki -> nM -----------------------------------------------------


def test_mm_to_nm() -> None:
    result = convert_to_canonical_unit("KM", Decimal("0.5"), "mM")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("500000.0")
    assert result.canonical_unit == CANONICAL_UNIT_NM


def test_um_to_nm() -> None:
    """The real malonyl-CoA case: BRENDA/SABIO-RK's own real reported unit."""
    result = convert_to_canonical_unit("KM", Decimal("18.0"), "uM")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("18000.0")
    assert result.canonical_unit == CANONICAL_UNIT_NM


def test_nm_passthrough() -> None:
    result = convert_to_canonical_unit("KM", Decimal("5"), "nM")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("5")
    assert result.canonical_unit == CANONICAL_UNIT_NM


def test_m_to_nm() -> None:
    result = convert_to_canonical_unit("KI", Decimal("0.001"), "M")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("1000000.0")
    assert result.canonical_unit == CANONICAL_UNIT_NM


# --- First-order-rate family: kcat -> per_sec ----------------------------------------------


def test_s_minus_1_to_per_sec() -> None:
    result = convert_to_canonical_unit("KCAT", Decimal("3.9841"), "s^-1")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("3.9841")
    assert result.canonical_unit == CANONICAL_UNIT_PER_SEC


def test_one_over_s_to_per_sec() -> None:
    result = convert_to_canonical_unit("KCAT", Decimal("12"), "1/s")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("12")
    assert result.canonical_unit == CANONICAL_UNIT_PER_SEC


# --- Bimolecular-rate family: kcat/Km -> per_nMs -------------------------------------------


def test_mm_minus_1_s_minus_1_to_per_nms() -> None:
    result = convert_to_canonical_unit("KCAT_OVER_KM", Decimal("72.14"), "mM^-1 s^-1")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("0.00007214")
    assert result.canonical_unit == CANONICAL_UNIT_PER_NMS


def test_um_minus_1_s_minus_1_to_per_nms() -> None:
    result = convert_to_canonical_unit("KCAT_OVER_KM", Decimal("1"), "uM^-1 s^-1")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("0.001")
    assert result.canonical_unit == CANONICAL_UNIT_PER_NMS


def test_nm_minus_1_s_minus_1_passthrough() -> None:
    result = convert_to_canonical_unit("KCAT_OVER_KM", Decimal("1"), "nM^-1 s^-1")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("1")
    assert result.canonical_unit == CANONICAL_UNIT_PER_NMS


def test_brenda_documented_mm_per_s_idiom_resolves_for_kcat_over_km() -> None:
    """BRENDA's own official documentation (datafields.php, confirmed live): "The unit of
    this value is mM/s" -- meaning per mM per second, for kcat/Km specifically."""
    result = convert_to_canonical_unit("KCAT_OVER_KM", Decimal("1"), "mM/s")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("0.000001")
    assert result.canonical_unit == CANONICAL_UNIT_PER_NMS


# --- Vmax / concentration flux -> nM_per_s -------------------------------------------------


def test_vmax_mm_per_s_to_nm_per_s() -> None:
    result = convert_to_canonical_unit("VMAX", Decimal("2"), "mM/s")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("2000000")
    assert result.canonical_unit == CANONICAL_UNIT_NM_PER_S


def test_vmax_um_per_s_to_nm_per_s() -> None:
    result = convert_to_canonical_unit("VMAX", Decimal("3"), "uM/s")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("3000")
    assert result.canonical_unit == CANONICAL_UNIT_NM_PER_S


def test_vmax_nm_per_s_passthrough() -> None:
    result = convert_to_canonical_unit("VMAX", Decimal("7"), "nM/s")
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("7")
    assert result.canonical_unit == CANONICAL_UNIT_NM_PER_S


# --- Same string, different meaning depending on parameter_type ---------------------------


def test_mm_per_s_means_different_things_for_vmax_vs_kcat_over_km() -> None:
    """The single most important disambiguation this module performs: "mM/s" is a genuine
    concentration flux for Vmax, but BRENDA's own documented per-mM-per-second idiom for
    kcat/Km -- resolved unambiguously by parameter_type alone, never confused."""
    vmax_result = convert_to_canonical_unit("VMAX", Decimal("1"), "mM/s")
    kcat_km_result = convert_to_canonical_unit("KCAT_OVER_KM", Decimal("1"), "mM/s")

    assert vmax_result.canonical_unit == CANONICAL_UNIT_NM_PER_S
    assert kcat_km_result.canonical_unit == CANONICAL_UNIT_PER_NMS
    assert vmax_result.canonical_value != kcat_km_result.canonical_value


# --- Unit spelling variants and Unicode micro symbols --------------------------------------


@pytest.mark.parametrize("spelling", ["uM", "µM", "μM", "UM", " uM ", "um"])
def test_micro_molar_spelling_variants_all_resolve_identically(spelling: str) -> None:
    result = convert_to_canonical_unit("KM", Decimal("18.0"), spelling)
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("18000.0")


@pytest.mark.parametrize(
    "spelling", ["s^-1", "s-1", "S^-1", " s^-1 ", "s⁻¹"]
)
def test_per_second_spelling_variants_all_resolve_identically(spelling: str) -> None:
    result = convert_to_canonical_unit("KCAT", Decimal("5"), spelling)
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("5")
    assert result.canonical_unit == CANONICAL_UNIT_PER_SEC


# --- Unsupported / unrecognized / incompatible units ---------------------------------------


def test_unsupported_specific_activity_unit_remains_unresolved() -> None:
    """Real, live-observed SABIO-RK Vmax unit (Pilot 2 Run 5): a mass-normalized specific
    activity, not a simple concentration/time flux -- never fabricated into one."""
    result = convert_to_canonical_unit("VMAX", Decimal("1"), "nmol/(min*mg)")
    assert result.status is UnitConversionStatus.UNRECOGNIZED_UNIT
    assert result.canonical_value is None
    assert result.canonical_unit is None


def test_incompatible_dimension_rejected_not_silently_converted() -> None:
    """A genuinely recognized unit (a rate) supplied for a concentration parameter type is
    never silently converted across dimensions -- distinguished from a wholly unrecognized
    unit by a more specific status/reason."""
    result = convert_to_canonical_unit("KM", Decimal("1"), "s^-1")
    assert result.status is UnitConversionStatus.INCOMPATIBLE_DIMENSION
    assert result.canonical_value is None
    assert result.canonical_unit is None


@pytest.mark.parametrize(
    "parameter_type", ["PH_OPTIMUM", "TEMPERATURE_OPTIMUM", "SPECIFIC_ACTIVITY", "OTHER"]
)
def test_parameter_types_with_no_canonical_target_are_not_applicable(
    parameter_type: str,
) -> None:
    result = convert_to_canonical_unit(parameter_type, Decimal("1"), "nM")
    assert result.status is UnitConversionStatus.NOT_APPLICABLE_PARAMETER_TYPE
    assert result.canonical_value is None


def test_completely_unrecognized_unit_text_remains_unresolved() -> None:
    result = convert_to_canonical_unit("KM", Decimal("1"), "furlongs per fortnight")
    assert result.status is UnitConversionStatus.UNRECOGNIZED_UNIT
    assert result.canonical_value is None


# --- Determinism / idempotence / Decimal-safety --------------------------------------------


def test_conversion_is_deterministic() -> None:
    first = convert_to_canonical_unit("KM", Decimal("18.0"), "uM")
    second = convert_to_canonical_unit("KM", Decimal("18.0"), "uM")
    assert first == second


def test_conversion_is_idempotent_on_an_already_canonical_value() -> None:
    """Feeding an already-canonical (value, unit) pair back in converts to itself
    unchanged -- every canonical unit's own multiplier is exactly 1."""
    once = convert_to_canonical_unit("KM", Decimal("18000.0"), "uM")
    twice = convert_to_canonical_unit("KM", once.canonical_value, once.canonical_unit)
    assert twice.canonical_value == once.canonical_value
    assert twice.canonical_unit == once.canonical_unit


def test_conversion_never_introduces_binary_float_error() -> None:
    """0.1 mM -> nM must be exactly 100000, never a float-representation artifact like
    99999.99999999999 or 100000.00000000001."""
    result = convert_to_canonical_unit("KM", Decimal("0.1"), "mM")
    assert result.canonical_value == Decimal("100000.0")
    assert str(result.canonical_value) == "100000.0"
