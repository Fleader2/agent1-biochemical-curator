"""Quantitative observation identity, canonical-unit normalization, and context
compatibility (Agent 1.x Increment "Experimental Context and Quantitative
Observation Framework").

Mirrors ``app.normalization.kinetic_measurement``/``.kinetic_units`` in
shape and policy, deliberately: this module never resolves entity identity
(``protein_id``/``compound_id``/``reaction_id``/``organism_id`` are accepted
only as already-normalized UUIDs the caller supplies), never guesses an
unrecognized unit, and never performs a scientific derivation -- it is a
pure, source-native-record -> source-neutral-identity reshaping, plus a
closed, explicit unit-conversion policy, exactly like its kinetic
counterpart.

See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import (
    ContextCompatibility,
    QuantitativeEvidenceClass,
    SourceType,
    TimeReferenceBasis,
)
from app.normalization.kinetic_units import (
    UnitConversionResult,
    UnitConversionStatus,
)

# --- Canonical units -----------------------------------------------------------------------

#: Reused verbatim from app.normalization.kinetic_units -- protein/metabolite concentration
#: observations share exactly the same canonical target (nM) and recognized-unit vocabulary
#: as a kinetic Km/Ki measurement; reaction flux shares Vmax's nM_per_s; growth rate shares
#: kcat's dimension (per_sec), though its own real-world reported-unit vocabulary differs
#: (hours are common for growth rate, never observed for kcat), so growth rate gets its own
#: recognized-unit table below rather than importing kinetic_units' private one.
CANONICAL_UNIT_NM = "nM"
CANONICAL_UNIT_NM_PER_S = "nM_per_s"
CANONICAL_UNIT_PER_SEC = "per_sec"
#: New canonical units this increment introduces -- neither concentration/rate/flux dimension
#: existed in app.normalization.kinetic_units, which only ever targeted the four Agent
#: 2-facing kinetic units. Never conflated with any of those four.
CANONICAL_UNIT_MOLECULES_PER_CELL = "molecules_per_cell"
CANONICAL_UNIT_PL = "pL"


class QuantitativeObservationType(StrEnum):
    """Controlled, normalization-layer vocabulary for
    ``QuantitativeObservation.observation_type``.

    That column itself is a deliberately open, unconstrained ``VARCHAR``
    (mirrors ``KineticMeasurement.parameter_type``'s own "never a closed
    enum" policy, ``app/models/enums.py``'s own module docstring) -- this
    enum exists only to give calling code a consistent, typo-proof value
    to persist into that open column. ``OTHER`` is the catch-all for any
    future observation type this vocabulary does not yet cover -- never a
    reason to drop a record (task Sec 1: "future observation types can be
    added without major schema redesign").
    """

    PROTEIN_ABUNDANCE = "PROTEIN_ABUNDANCE"
    PROTEIN_CONCENTRATION = "PROTEIN_CONCENTRATION"
    METABOLITE_CONCENTRATION = "METABOLITE_CONCENTRATION"
    REACTION_FLUX = "REACTION_FLUX"
    CELL_VOLUME = "CELL_VOLUME"
    GROWTH_RATE = "GROWTH_RATE"
    OTHER = "OTHER"


class PerturbationCategory(StrEnum):
    """Controlled, normalization-layer vocabulary for
    ``Perturbation.perturbation_type`` -- mirrors
    ``QuantitativeObservationType``'s own relationship to its open column
    exactly. Task Sec 1: "support genetic, chemical, nutrient/
    environmental, induction/repression, temperature/pH, and similar
    perturbations without hard-coding every possible subtype" -- ``OTHER``
    is the catch-all that makes that literally true.
    """

    GENETIC = "GENETIC"
    CHEMICAL = "CHEMICAL"
    NUTRIENT_ENVIRONMENTAL = "NUTRIENT_ENVIRONMENTAL"
    INDUCTION_REPRESSION = "INDUCTION_REPRESSION"
    TEMPERATURE_PH = "TEMPERATURE_PH"
    OTHER = "OTHER"


_MICRO_VARIANTS = re.compile("[µμ]")
_MINUS_SIGN = re.compile("\u2212")
_CARET_EXPONENT = re.compile(r"\^(-?\d+)")
_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_unit_text(unit: str) -> str:
    """Identical, conservative, mechanical normalization to
    ``app.normalization.kinetic_units._normalize_unit_text`` -- duplicated rather than
    imported since that function is module-private (a deliberate, narrow primitive, not a
    shared public utility) and this module has no other dependency on kinetic_units beyond
    the public ``UnitConversionResult``/``UnitConversionStatus`` shapes."""
    text = unicodedata.normalize("NFKC", unit)
    text = _MICRO_VARIANTS.sub("u", text)
    text = _MINUS_SIGN.sub("-", text)
    text = _CARET_EXPONENT.sub(r"\1", text)
    text = _WHITESPACE_RUN.sub(" ", text.strip())
    return text.casefold()


# --- Concentration family: PROTEIN_CONCENTRATION, METABOLITE_CONCENTRATION -> nM -----------

_CONCENTRATION_TO_NM: dict[str, Decimal] = {
    "nm": Decimal(1),
    "um": Decimal(1000),
    "mm": Decimal(1_000_000),
    "m": Decimal(1_000_000_000),
}

# --- Flux family: REACTION_FLUX -> nM_per_s -------------------------------------------------

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

# --- Rate family: GROWTH_RATE -> per_sec (own vocabulary -- hours are real-world-common) ---

_RATE_TO_PER_SEC: dict[str, Decimal] = {
    "s-1": Decimal(1),
    "1/s": Decimal(1),
    "/s": Decimal(1),
    "h-1": Decimal(1) / Decimal(3600),
    "1/h": Decimal(1) / Decimal(3600),
    "/h": Decimal(1) / Decimal(3600),
    "hr-1": Decimal(1) / Decimal(3600),
}

# --- Abundance family: PROTEIN_ABUNDANCE -> molecules_per_cell ------------------------------

_ABUNDANCE_TO_MOLECULES_PER_CELL: dict[str, Decimal] = {
    "molecules/cell": Decimal(1),
    "molecules per cell": Decimal(1),
    "copies/cell": Decimal(1),
    "copies per cell": Decimal(1),
}

# --- Volume family: CELL_VOLUME -> pL -------------------------------------------------------

_VOLUME_TO_PL: dict[str, Decimal] = {
    "pl": Decimal(1),
    "fl": Decimal("0.001"),
    "nl": Decimal(1000),
    "ul": Decimal(1_000_000),
    "ml": Decimal(1_000_000_000),
    "l": Decimal(1_000_000_000_000),
}

_OBSERVATION_TYPE_FAMILIES: dict[str, tuple[str, dict[str, Decimal]]] = {
    QuantitativeObservationType.PROTEIN_CONCENTRATION.value: (
        CANONICAL_UNIT_NM,
        _CONCENTRATION_TO_NM,
    ),
    QuantitativeObservationType.METABOLITE_CONCENTRATION.value: (
        CANONICAL_UNIT_NM,
        _CONCENTRATION_TO_NM,
    ),
    QuantitativeObservationType.REACTION_FLUX.value: (CANONICAL_UNIT_NM_PER_S, _FLUX_TO_NM_PER_S),
    QuantitativeObservationType.GROWTH_RATE.value: (CANONICAL_UNIT_PER_SEC, _RATE_TO_PER_SEC),
    QuantitativeObservationType.PROTEIN_ABUNDANCE.value: (
        CANONICAL_UNIT_MOLECULES_PER_CELL,
        _ABUNDANCE_TO_MOLECULES_PER_CELL,
    ),
    QuantitativeObservationType.CELL_VOLUME.value: (CANONICAL_UNIT_PL, _VOLUME_TO_PL),
}

_ALL_FAMILIES: tuple[dict[str, Decimal], ...] = (
    _CONCENTRATION_TO_NM,
    _FLUX_TO_NM_PER_S,
    _RATE_TO_PER_SEC,
    _ABUNDANCE_TO_MOLECULES_PER_CELL,
    _VOLUME_TO_PL,
)


def convert_quantitative_unit(
    observation_type: str, value: Decimal, unit: str
) -> UnitConversionResult:
    """Convert one reported ``(observation_type, value, unit)`` triple to its canonical unit.

    Deterministic, Decimal-safe, idempotent -- mirrors
    ``app.normalization.kinetic_units.convert_to_canonical_unit`` exactly in policy and
    return shape (reusing that module's own ``UnitConversionResult``/``UnitConversionStatus``,
    task's own "reuse existing canonical-unit infrastructure where appropriate"). An
    ``observation_type`` this increment does not name a canonical target for (e.g.
    ``"OTHER"``) always returns ``NOT_APPLICABLE_PARAMETER_TYPE`` -- never attempted, never a
    fabricated value.
    """
    family = _OBSERVATION_TYPE_FAMILIES.get(observation_type)
    if family is None:
        return UnitConversionResult(
            status=UnitConversionStatus.NOT_APPLICABLE_PARAMETER_TYPE,
            canonical_value=None,
            canonical_unit=None,
            reason=f"{observation_type} has no canonical unit target in this increment",
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
                f"of {observation_type} ever being reported in -- never silently converted "
                "across dimensions"
            ),
        )

    return UnitConversionResult(
        status=UnitConversionStatus.UNRECOGNIZED_UNIT,
        canonical_value=None,
        canonical_unit=None,
        reason=f"{unit!r} is not a recognized unit for {observation_type}",
    )


# --- Time-to-canonical-seconds conversion (task Sec 2) --------------------------------------

_TIME_TO_SECONDS: dict[str, Decimal] = {
    "s": Decimal(1),
    "sec": Decimal(1),
    "secs": Decimal(1),
    "second": Decimal(1),
    "seconds": Decimal(1),
    "min": Decimal(60),
    "mins": Decimal(60),
    "minute": Decimal(60),
    "minutes": Decimal(60),
    "h": Decimal(3600),
    "hr": Decimal(3600),
    "hrs": Decimal(3600),
    "hour": Decimal(3600),
    "hours": Decimal(3600),
    "d": Decimal(86400),
    "day": Decimal(86400),
    "days": Decimal(86400),
}


def convert_time_to_seconds(value: Decimal, unit: str) -> Decimal | None:
    """Convert one reported time ``(value, unit)`` to canonical seconds, or ``None`` when the
    unit is not recognized -- never guessed (task Sec 2: "Do not encode time only inside
    free-text metadata," and this repository's own established "never fabricate a
    conversion" policy for every other unit family). A closed, explicit vocabulary only,
    exactly like ``convert_quantitative_unit``/``convert_to_canonical_unit`` -- not a general
    time-string parser.
    """
    normalized = _normalize_unit_text(unit)
    multiplier = _TIME_TO_SECONDS.get(normalized)
    if multiplier is None:
        return None
    return value * multiplier


# --- Identity ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuantitativeObservationIdentity:
    """Source-neutral description of one independently-sourced quantitative observation.

    Mirrors ``app.normalization.kinetic_measurement.KineticMeasurementIdentity`` exactly in
    shape and policy. Every field beyond ``observation_type``/``value``/``unit``/
    ``evidence_class`` is optional -- an observation whose protein/compound/reaction/
    organism/context/perturbation could not yet be resolved to an existing Agent 1 UUID (or
    was never reported at all) is still a valid, curatable record; this module never
    resolves that identity itself, it only accepts what the caller already resolved (task
    Sec 3: "never infer an identity from fuzzy text... allow unresolved source identity to
    remain explicit").
    """

    observation_type: str
    value: Decimal
    unit: str
    evidence_class: QuantitativeEvidenceClass

    reported_observation_type: str | None = None

    uncertainty: Decimal | None = None
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    measurement_method: str | None = None

    time_reference_basis: TimeReferenceBasis | None = None
    time_value: Decimal | None = None
    time_unit: str | None = None

    experimental_context_id: UUID | None = None
    perturbation_id: UUID | None = None

    biological_replicate_id: str | None = None
    technical_replicate_id: str | None = None

    protein_id: UUID | None = None
    compound_id: UUID | None = None
    reaction_id: UUID | None = None
    organism_id: UUID | None = None
    unresolved_identity_kind: str | None = None
    unresolved_identity_text: str | None = None

    source: SourceType | None = None
    source_id: str | None = None
    publication_id: UUID | None = None
    dataset_id: str | None = None

    notes: str | None = None

    # Always computed in __post_init__ -- never accepted as a meaningful caller-supplied
    # value. Defaulted to None only so the constructor signature stays keyword-friendly.
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None
    time_canonical_s: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.unit or not self.unit.strip():
            raise ValueError("QuantitativeObservationIdentity requires a non-empty unit")
        if (
            self.time_reference_basis is TimeReferenceBasis.PERTURBATION_ONSET
            and self.perturbation_id is None
        ):
            raise ValueError(
                "QuantitativeObservationIdentity with time_reference_basis="
                "PERTURBATION_ONSET requires perturbation_id"
            )
        if self.time_reference_basis is not None and self.time_value is None:
            raise ValueError(
                "QuantitativeObservationIdentity with a time_reference_basis requires "
                "time_value (a stated basis with no actual time value is meaningless)"
            )

        conversion = convert_quantitative_unit(self.observation_type, self.value, self.unit)
        object.__setattr__(self, "normalized_value", conversion.canonical_value)
        object.__setattr__(self, "normalized_unit", conversion.canonical_unit)
        if conversion.status in (
            UnitConversionStatus.UNRECOGNIZED_UNIT,
            UnitConversionStatus.INCOMPATIBLE_DIMENSION,
        ):
            disclosure = f"Canonical unit conversion unresolved: {conversion.reason}."
            object.__setattr__(
                self, "notes", f"{self.notes} | {disclosure}" if self.notes else disclosure
            )

        if self.time_value is not None and self.time_unit is not None:
            object.__setattr__(
                self, "time_canonical_s", convert_time_to_seconds(self.time_value, self.time_unit)
            )


# --- Context compatibility (task Sec 6) -----------------------------------------------------


@runtime_checkable
class ExperimentalContextLike(Protocol):
    """Structural shape ``classify_context_compatibility`` needs -- deliberately a Protocol,
    not an import of ``app.models.experimental_context.ExperimentalContext``, so this
    normalization module has no dependency on the persistence layer (mirrors this package's
    own established "knows nothing about any particular entity type" convention,
    ``app.normalization.identifiers``)."""

    organism_id: UUID | None
    strain: str | None
    medium: str | None
    carbon_source: str | None
    temperature_c: Decimal | None
    ph: Decimal | None
    growth_phase: str | None
    growth_condition: str | None


_DETAIL_FIELDS: tuple[str, ...] = (
    "strain",
    "medium",
    "carbon_source",
    "temperature_c",
    "ph",
    "growth_phase",
    "growth_condition",
)


def classify_context_compatibility(
    a: ExperimentalContextLike | None, b: ExperimentalContextLike | None
) -> ContextCompatibility:
    """A minimal, wholly deterministic comparison of two experimental contexts (task Sec 6).

    **Never a numeric or weighted score** -- exactly field-subset equality, nothing else:

    * either side missing, or organism unknown on either side -> ``CONTEXT_UNKNOWN``;
    * organisms known and different -> ``CONTEXT_MISMATCH``;
    * organisms match, and any detail field both sides report disagrees ->
      ``CONTEXT_MISMATCH``;
    * organisms match, no field disagrees, but neither side reports any detail field at
      all (nothing was actually compared) -> ``CONTEXT_UNKNOWN``;
    * organisms match, no field disagrees, and every detail field *one* side reports is
      also reported (and equal) on the other -> ``EXACT_CONTEXT``;
    * organisms match, no field disagrees, but at least one side reports a detail field the
      other leaves unset -> ``COMPATIBLE_REFERENCE`` (a real, if partial, match -- never
      silently promoted to ``EXACT_CONTEXT``).

    Never fuzzy-matches strings (e.g. ``"YPD"`` vs ``"yeast peptone dextrose"`` are treated
    as a plain, disagreeing mismatch, not resolved) -- a future increment could add that;
    this one deliberately does not (task's own "do not implement sophisticated scoring
    yet").
    """
    if a is None or b is None:
        return ContextCompatibility.CONTEXT_UNKNOWN
    if a.organism_id is None or b.organism_id is None:
        return ContextCompatibility.CONTEXT_UNKNOWN
    if a.organism_id != b.organism_id:
        return ContextCompatibility.CONTEXT_MISMATCH

    compared_any = False
    only_a_has_extra = False
    only_b_has_extra = False
    for field_name in _DETAIL_FIELDS:
        value_a = getattr(a, field_name)
        value_b = getattr(b, field_name)
        if value_a is not None and value_b is not None:
            compared_any = True
            if value_a != value_b:
                return ContextCompatibility.CONTEXT_MISMATCH
        elif value_a is not None:
            only_a_has_extra = True
        elif value_b is not None:
            only_b_has_extra = True

    if not compared_any and not only_a_has_extra and not only_b_has_extra:
        return ContextCompatibility.CONTEXT_UNKNOWN
    if only_a_has_extra or only_b_has_extra:
        return ContextCompatibility.COMPATIBLE_REFERENCE
    return ContextCompatibility.EXACT_CONTEXT


__all__ = [
    "CANONICAL_UNIT_MOLECULES_PER_CELL",
    "CANONICAL_UNIT_NM",
    "CANONICAL_UNIT_NM_PER_S",
    "CANONICAL_UNIT_PER_SEC",
    "CANONICAL_UNIT_PL",
    "ExperimentalContextLike",
    "PerturbationCategory",
    "QuantitativeObservationIdentity",
    "QuantitativeObservationType",
    "classify_context_compatibility",
    "convert_quantitative_unit",
    "convert_time_to_seconds",
]
