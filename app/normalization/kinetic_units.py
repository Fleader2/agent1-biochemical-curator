"""Canonical kinetic-unit normalization (Agent 1.x Increment C.12).

Converts a source-reported ``(parameter_type, value, unit)`` triple into one of four
canonical units -- never inventing a value, never guessing an unrecognized or
dimensionally incompatible unit, and never touching the original reported figure (that
remains on ``KineticMeasurement.parameter_value``/``.unit``; this module only ever
populates the pre-existing, previously-always-``NULL``
``normalized_value``/``normalized_unit`` columns, see
``app.normalization.kinetic_measurement.KineticMeasurementIdentity``).

**Canonical units, exactly as specified for this increment** (named to match the literal
Antimony/SBML-style unit definitions a future model-generation increment may one day emit
verbatim -- this module only ever stores the plain label string, e.g. ``"nM"``, never the
unit-definition syntax itself, which is out of Agent 1's own scope):

* ``substance = 1e-9 mole``, ``time_unit = second`` -- base units, never a target label.
* ``nM = 1e-9 mole / litre`` -- Km, Ki (Kd, when a source ever reports one -- no currently
  integrated source does, so no code path targets it yet).
* ``per_sec = 1 / second`` -- kcat and other first-order rate constants.
* ``nM_per_s = 1e-9 mole / (litre * second)`` -- Vmax / concentration flux.
* ``per_nMs = litre / (1e-9 mole * second)`` -- kcat/Km and other bimolecular rate
  constants.
* ``per_nM = litre / 1e-9 mole`` -- defined for completeness (this increment's own
  specification), but no currently-integrated parameter type targets it; never used as a
  conversion target here.

**Closed, explicit recognized-unit vocabulary only** -- deliberately not a general unit
parser. Every spelling below is either directly confirmed live/from a source's own
official documentation (see each connector's own module docstring:
``app.connectors.sabiork``, ``app.connectors.brenda``, ``app.connectors.gotenzymes``) or a
mechanical, lossless normalization of one (case, whitespace, Unicode micro-symbol variant,
caret-exponent notation). A unit this module has never been shown real evidence for is
left **unresolved**, never guessed (Increment C.12 instructions, §5) -- this includes, for
real, live-observed SABIO-RK data, specific-activity-style units such as
``"nmol/(min*mg)"`` (confirmed live, Pilot 2 Run 5's own real Vmax measurements), which are
mass-normalized, not simple concentration/time, and are never treated as convertible.

**The one deliberate, documented cross-source ambiguity**: BRENDA's own official
documentation (https://www.brenda-enzymes.org/datafields.php, confirmed live) states,
verbatim, "The unit of this [kcat/Km] value is mM/s" -- a real, officially-documented
enzymology idiom meaning "per mM per second" (a bimolecular rate constant), not a literal
concentration-per-time flux, despite reading exactly like one. This module recognizes
``"mM/s"`` under the ``per_nMs`` (kcat/Km) family specifically -- resolved unambiguously by
``parameter_type`` alone, since each of the four canonical families below is looked up
independently; there is no runtime ambiguity, only an unusual source convention. GotEnzymes2
never reports its own kcat/Km this way (its real underlying model, UniKP, reports
"mM⁻¹s⁻¹"/"s⁻¹·mM⁻¹" -- confirmed via UniKP's own publication, since
GotEnzymes2's live API exposes no unit metadata at all); ``app.connectors.gotenzymes`` was
corrected this increment to report the unambiguous ``"mM^-1 s^-1"`` spelling instead of
BRENDA's own borrowed (and, outside BRENDA's own documented context, misleading) ``"mM/s"``
idiom.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

CANONICAL_UNIT_NM = "nM"
CANONICAL_UNIT_PER_SEC = "per_sec"
CANONICAL_UNIT_NM_PER_S = "nM_per_s"
CANONICAL_UNIT_PER_NMS = "per_nMs"
# Defined by this increment's own canonical-unit specification; no currently-integrated
# parameter type targets it (see module docstring).
CANONICAL_UNIT_PER_NM = "per_nM"


class UnitConversionStatus(StrEnum):
    """The outcome of one canonical-unit conversion attempt."""

    RESOLVED = "RESOLVED"
    UNRECOGNIZED_UNIT = "UNRECOGNIZED_UNIT"
    INCOMPATIBLE_DIMENSION = "INCOMPATIBLE_DIMENSION"
    NOT_APPLICABLE_PARAMETER_TYPE = "NOT_APPLICABLE_PARAMETER_TYPE"


@dataclass(frozen=True, slots=True)
class UnitConversionResult:
    """The result of ``convert_to_canonical_unit``. ``canonical_value``/``canonical_unit``
    are both ``None`` unless ``status is UnitConversionStatus.RESOLVED`` -- never a
    fabricated value for any other status (Increment C.12 instructions, §5)."""

    status: UnitConversionStatus
    canonical_value: Decimal | None
    canonical_unit: str | None
    reason: str


_MICRO_VARIANTS = re.compile("[µμ]")  # MICRO SIGN, GREEK SMALL LETTER MU
# NFKC itself already decomposes superscript digits (e.g. superscript "1") to their plain
# ASCII form, but decomposes superscript minus (U+207B) to the Unicode MINUS SIGN
# (U+2212), not the ASCII hyphen-minus (U+002D) -- confirmed empirically, not
# assumed. This substitution runs *after* NFKC for exactly that reason.
_MINUS_SIGN = re.compile("\u2212")
_CARET_EXPONENT = re.compile(r"\^(-?\d+)")
_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_unit_text(unit: str) -> str:
    """Conservative, mechanical, lossless normalization only -- never a guess at meaning.

    In order: Unicode NFKC normalization (also folds a superscript digit like superscript
    "1" to plain ASCII "1"), micro-sign variants (µ/μ) to ASCII ``u``, the Unicode
    MINUS SIGN NFKC leaves behind for a superscript minus to ASCII ``-``, ``^-1``-style
    caret exponents to a bare ``-1`` (dropping only the caret, never the sign or digit),
    whitespace collapsed to a single space and stripped, then case-folded. Never strips
    punctuation generally, never reorders tokens -- this is normalization, not parsing.
    """
    text = unicodedata.normalize("NFKC", unit)
    text = _MICRO_VARIANTS.sub("u", text)
    text = _MINUS_SIGN.sub("-", text)
    text = _CARET_EXPONENT.sub(r"\1", text)
    text = _WHITESPACE_RUN.sub(" ", text.strip())
    return text.casefold()


# --- Concentration family: Km, Ki -> nM ---------------------------------------------------------

_CONCENTRATION_TO_NM: dict[str, Decimal] = {
    "nm": Decimal(1),
    "um": Decimal(1000),  # 1e3
    "mm": Decimal(1_000_000),  # 1e6
    "m": Decimal(1_000_000_000),  # 1e9
}

# --- First-order-rate family: kcat -> per_sec ---------------------------------------------------

_RATE_TO_PER_SEC: dict[str, Decimal] = {
    "s-1": Decimal(1),
    "1/s": Decimal(1),
    "/s": Decimal(1),
}

# --- Concentration-flux family: Vmax -> nM_per_s ------------------------------------------------

_FLUX_TO_NM_PER_S: dict[str, Decimal] = {
    "nm/s": Decimal(1),
    "nm s-1": Decimal(1),
    "um/s": Decimal(1000),
    "um s-1": Decimal(1000),
    "mm/s": Decimal(1_000_000),
    "mm s-1": Decimal(1_000_000),
    "m/s": Decimal(1_000_000_000),
    "m s-1": Decimal(1_000_000_000),
}

# --- Bimolecular-rate family: kcat/Km -> per_nMs ------------------------------------------------

_BIMOLECULAR_TO_PER_NMS: dict[str, Decimal] = {
    "nm-1 s-1": Decimal(1),
    "um-1 s-1": Decimal("0.001"),  # 1e-3
    "mm-1 s-1": Decimal("0.000001"),  # 1e-6
    "m-1 s-1": Decimal("0.000000001"),  # 1e-9
    # BRENDA's own official, documented idiom for this field specifically -- see module
    # docstring's "one deliberate, documented cross-source ambiguity."
    "mm/s": Decimal("0.000001"),
}

#: Keyed by the plain string value of ``app.normalization.kinetic_measurement
#: .KineticParameterType`` (``"KM"``, ``"KI"``, ...) rather than the enum itself, so this
#: module has no import dependency on ``kinetic_measurement`` at all (which itself calls
#: into this module) -- avoids a circular import, and keeps this module a genuinely
#: lower-level, standalone primitive, consistent with ``app.normalization.identifiers``'s
#: own "knows nothing about any particular entity type" convention.
_PARAMETER_TYPE_FAMILIES: dict[str, tuple[str, dict[str, Decimal]]] = {
    "KM": (CANONICAL_UNIT_NM, _CONCENTRATION_TO_NM),
    "KI": (CANONICAL_UNIT_NM, _CONCENTRATION_TO_NM),
    "KCAT": (CANONICAL_UNIT_PER_SEC, _RATE_TO_PER_SEC),
    "VMAX": (CANONICAL_UNIT_NM_PER_S, _FLUX_TO_NM_PER_S),
    "KCAT_OVER_KM": (CANONICAL_UNIT_PER_NMS, _BIMOLECULAR_TO_PER_NMS),
}

_ALL_FAMILIES: tuple[dict[str, Decimal], ...] = (
    _CONCENTRATION_TO_NM,
    _RATE_TO_PER_SEC,
    _FLUX_TO_NM_PER_S,
    _BIMOLECULAR_TO_PER_NMS,
)


def convert_to_canonical_unit(
    parameter_type: str, value: Decimal, unit: str
) -> UnitConversionResult:
    """Convert one reported ``(parameter_type, value, unit)`` triple to its canonical unit.

    ``parameter_type`` is the plain string value of
    ``app.normalization.kinetic_measurement.KineticParameterType`` (e.g. ``"KM"``) -- see
    this module's own docstring for why this module never imports that enum directly.

    Deterministic, Decimal-safe (every multiplier is an exact ``Decimal`` literal, never a
    binary-float intermediate), and idempotent (an already-canonical ``(value, unit)`` pair
    -- e.g. ``("KM", Decimal("5"), "nM")`` -- converts to itself unchanged, since every
    canonical unit's own multiplier is exactly ``1``).

    ``parameter_type`` values with no canonical target at all (``PH_OPTIMUM``,
    ``TEMPERATURE_OPTIMUM``, ``SPECIFIC_ACTIVITY``, ``OTHER``) always return
    ``NOT_APPLICABLE_PARAMETER_TYPE`` -- never attempted, never a fabricated value
    (Increment C.12 instructions, §3: canonical targets are Km/Ki/Kd, kcat, Vmax,
    kcat/Km only).
    """
    family = _PARAMETER_TYPE_FAMILIES.get(parameter_type)
    if family is None:
        return UnitConversionResult(
            status=UnitConversionStatus.NOT_APPLICABLE_PARAMETER_TYPE,
            canonical_value=None,
            canonical_unit=None,
            reason=f"{parameter_type} has no canonical unit target in this increment",
        )
    canonical_unit, table = family

    normalized = _normalize_unit_text(unit)
    multiplier = table.get(normalized)
    if multiplier is not None:
        return UnitConversionResult(
            status=UnitConversionStatus.RESOLVED,
            canonical_value=value * multiplier,
            canonical_unit=canonical_unit,
            reason=f"{unit!r} recognized as {canonical_unit} (x{multiplier})",
        )

    if any(normalized in other_table for other_table in _ALL_FAMILIES if other_table is not table):
        return UnitConversionResult(
            status=UnitConversionStatus.INCOMPATIBLE_DIMENSION,
            canonical_value=None,
            canonical_unit=None,
            reason=(
                f"{unit!r} is a recognized unit, but not one this repository has evidence "
                f"of {parameter_type} ever being reported in -- never silently "
                "converted across dimensions"
            ),
        )

    return UnitConversionResult(
        status=UnitConversionStatus.UNRECOGNIZED_UNIT,
        canonical_value=None,
        canonical_unit=None,
        reason=f"{unit!r} is not a recognized unit for {parameter_type}",
    )


__all__ = [
    "CANONICAL_UNIT_NM",
    "CANONICAL_UNIT_NM_PER_S",
    "CANONICAL_UNIT_PER_NM",
    "CANONICAL_UNIT_PER_NMS",
    "CANONICAL_UNIT_PER_SEC",
    "UnitConversionResult",
    "UnitConversionStatus",
    "convert_to_canonical_unit",
]
