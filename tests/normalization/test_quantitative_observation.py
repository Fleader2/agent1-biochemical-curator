"""Tests for ``app.normalization.quantitative_observation`` (Agent 1.x Increment
"Experimental Context and Quantitative Observation Framework").

Pure unit tests: no database, no connector I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.connectors.sgd import SgdProteinAbundance
from app.models.enums import (
    ContextCompatibility,
    QuantitativeEvidenceClass,
    SourceType,
    TimeReferenceBasis,
)
from app.normalization.kinetic_units import UnitConversionStatus
from app.normalization.quantitative_observation import (
    CANONICAL_UNIT_MOLECULES_PER_CELL,
    CANONICAL_UNIT_NM,
    CANONICAL_UNIT_NM_PER_S,
    CANONICAL_UNIT_PER_SEC,
    CANONICAL_UNIT_PL,
    QuantitativeObservationIdentity,
    QuantitativeObservationType,
    classify_context_compatibility,
    convert_quantitative_unit,
    convert_time_to_seconds,
    quantitative_observation_identity_from_sgd_abundance,
)

pytestmark = pytest.mark.unit


# --- convert_quantitative_unit ----------------------------------------------------------------


def test_protein_concentration_um_to_nm():
    result = convert_quantitative_unit(
        QuantitativeObservationType.PROTEIN_CONCENTRATION.value, Decimal("8.3"), "uM"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("8300.0")
    assert result.canonical_unit == CANONICAL_UNIT_NM


def test_metabolite_concentration_mm_to_nm():
    result = convert_quantitative_unit(
        QuantitativeObservationType.METABOLITE_CONCENTRATION.value, Decimal("2"), "mM"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("2000000")
    assert result.canonical_unit == CANONICAL_UNIT_NM


def test_reaction_flux_um_per_s_to_nm_per_s():
    result = convert_quantitative_unit(
        QuantitativeObservationType.REACTION_FLUX.value, Decimal("1"), "uM/s"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("1000")
    assert result.canonical_unit == CANONICAL_UNIT_NM_PER_S


def test_growth_rate_per_hour_to_per_sec():
    """Growth rate's own real-world reported vocabulary (hours) differs from kcat's --
    confirmed recognized here even though kinetic_units' own kcat table never recognizes it."""
    result = convert_quantitative_unit(
        QuantitativeObservationType.GROWTH_RATE.value, Decimal("0.3"), "h-1"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_unit == CANONICAL_UNIT_PER_SEC
    assert result.canonical_value == Decimal("0.3") * (Decimal(1) / Decimal(3600))


def test_protein_abundance_molecules_per_cell_is_already_canonical():
    """Task's own real use case: SGD reference protein abundance in molecules/cell."""
    result = convert_quantitative_unit(
        QuantitativeObservationType.PROTEIN_ABUNDANCE.value, Decimal("5000"), "molecules/cell"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("5000")
    assert result.canonical_unit == CANONICAL_UNIT_MOLECULES_PER_CELL


def test_cell_volume_liters_to_pl():
    """Task's own real use case: a reference cell volume of 0.1 pL."""
    result = convert_quantitative_unit(
        QuantitativeObservationType.CELL_VOLUME.value, Decimal("0.1"), "pL"
    )
    assert result.status is UnitConversionStatus.RESOLVED
    assert result.canonical_value == Decimal("0.1")
    assert result.canonical_unit == CANONICAL_UNIT_PL


def test_unsupported_unit_remains_unresolved_never_guessed():
    """A real-world unrecognized unit (specific-activity-style, mirrors the SABIO-RK VMAX
    case in kinetic_units) must never be silently converted."""
    result = convert_quantitative_unit(
        QuantitativeObservationType.PROTEIN_CONCENTRATION.value, Decimal("3"), "mg/mL"
    )
    assert result.status is UnitConversionStatus.UNRECOGNIZED_UNIT
    assert result.canonical_value is None
    assert result.canonical_unit is None


def test_incompatible_dimension_never_silently_converted():
    """A unit recognized for one family (a volume) is never applied to a different family
    (a concentration) even though both are, in this module, 'known' units somewhere."""
    result = convert_quantitative_unit(
        QuantitativeObservationType.PROTEIN_CONCENTRATION.value, Decimal("1"), "pL"
    )
    assert result.status is UnitConversionStatus.INCOMPATIBLE_DIMENSION
    assert result.canonical_value is None


def test_other_observation_type_has_no_canonical_target():
    result = convert_quantitative_unit(QuantitativeObservationType.OTHER.value, Decimal("1"), "x")
    assert result.status is UnitConversionStatus.NOT_APPLICABLE_PARAMETER_TYPE


# --- convert_time_to_seconds --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("30"), "min", Decimal("1800")),
        (Decimal("2"), "h", Decimal("7200")),
        (Decimal("1"), "day", Decimal("86400")),
        (Decimal("10"), "s", Decimal("10")),
    ],
)
def test_convert_time_to_seconds_recognized(value, unit, expected):
    assert convert_time_to_seconds(value, unit) == expected


def test_convert_time_to_seconds_unrecognized_unit_is_none():
    assert convert_time_to_seconds(Decimal("1"), "fortnight") is None


# --- QuantitativeObservationIdentity -------------------------------------------------------------


def test_identity_computes_normalized_value_and_unit():
    identity = QuantitativeObservationIdentity(
        observation_type=QuantitativeObservationType.CELL_VOLUME.value,
        value=Decimal("100"),
        unit="fL",
        evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
    )
    assert identity.normalized_value == Decimal("0.1")
    assert identity.normalized_unit == CANONICAL_UNIT_PL


def test_identity_unresolved_unit_discloses_in_notes_never_fabricates():
    identity = QuantitativeObservationIdentity(
        observation_type=QuantitativeObservationType.PROTEIN_CONCENTRATION.value,
        value=Decimal("3"),
        unit="mg/mL",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
    )
    assert identity.normalized_value is None
    assert identity.normalized_unit is None
    assert "unresolved" in identity.notes.lower()


def test_identity_computes_time_canonical_seconds():
    identity = QuantitativeObservationIdentity(
        observation_type=QuantitativeObservationType.GROWTH_RATE.value,
        value=Decimal("0.3"),
        unit="h-1",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
        time_reference_basis=TimeReferenceBasis.EXPERIMENT_START,
        time_value=Decimal("30"),
        time_unit="min",
    )
    assert identity.time_canonical_s == Decimal("1800")


def test_identity_perturbation_onset_requires_perturbation_id():
    with pytest.raises(ValueError, match="PERTURBATION_ONSET"):
        QuantitativeObservationIdentity(
            observation_type=QuantitativeObservationType.GROWTH_RATE.value,
            value=Decimal("0.3"),
            unit="h-1",
            evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
            time_reference_basis=TimeReferenceBasis.PERTURBATION_ONSET,
            time_value=Decimal("10"),
            time_unit="min",
        )


def test_identity_time_reference_basis_requires_time_value():
    with pytest.raises(ValueError, match="time_value"):
        QuantitativeObservationIdentity(
            observation_type=QuantitativeObservationType.GROWTH_RATE.value,
            value=Decimal("0.3"),
            unit="h-1",
            evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
            time_reference_basis=TimeReferenceBasis.EXPERIMENT_START,
        )


def test_identity_requires_non_empty_unit():
    with pytest.raises(ValueError, match="unit"):
        QuantitativeObservationIdentity(
            observation_type=QuantitativeObservationType.PROTEIN_ABUNDANCE.value,
            value=Decimal("1"),
            unit="",
            evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
        )


def test_identity_deterministic_repeated_construction():
    """Identical inputs must always produce identical normalized outputs -- no randomness,
    no hidden state."""
    kwargs = {
        "observation_type": QuantitativeObservationType.METABOLITE_CONCENTRATION.value,
        "value": Decimal("2"),
        "unit": "mM",
        "evidence_class": QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
    }
    first = QuantitativeObservationIdentity(**kwargs)
    second = QuantitativeObservationIdentity(**kwargs)
    assert first == second


def test_identity_never_treats_derived_or_predicted_as_direct_measurement():
    """Task Sec 5: evidence_class is preserved verbatim, never reinterpreted."""
    derived = QuantitativeObservationIdentity(
        observation_type=QuantitativeObservationType.PROTEIN_CONCENTRATION.value,
        value=Decimal("8.3"),
        unit="nM",
        evidence_class=QuantitativeEvidenceClass.DERIVED,
    )
    predicted = QuantitativeObservationIdentity(
        observation_type=QuantitativeObservationType.PROTEIN_ABUNDANCE.value,
        value=Decimal("4000"),
        unit="molecules/cell",
        evidence_class=QuantitativeEvidenceClass.MODEL_PREDICTED,
    )
    assert derived.evidence_class is QuantitativeEvidenceClass.DERIVED
    assert predicted.evidence_class is QuantitativeEvidenceClass.MODEL_PREDICTED
    assert derived.evidence_class is not QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC
    assert predicted.evidence_class is not QuantitativeEvidenceClass.REFERENCE_BASELINE


# --- classify_context_compatibility --------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    organism_id: UUID | None
    strain: str | None = None
    medium: str | None = None
    carbon_source: str | None = None
    temperature_c: Decimal | None = None
    ph: Decimal | None = None
    growth_phase: str | None = None
    growth_condition: str | None = None


def test_context_compatibility_unknown_when_either_side_missing():
    organism = uuid4()
    unknown = ContextCompatibility.CONTEXT_UNKNOWN
    assert classify_context_compatibility(None, _Ctx(organism)) is unknown
    assert classify_context_compatibility(_Ctx(organism), None) is unknown


def test_context_compatibility_unknown_when_organism_unresolved():
    a = _Ctx(organism_id=None, strain="BY4741")
    b = _Ctx(organism_id=None, strain="BY4741")
    assert classify_context_compatibility(a, b) is ContextCompatibility.CONTEXT_UNKNOWN


def test_context_compatibility_mismatch_on_different_organism():
    a = _Ctx(organism_id=uuid4())
    b = _Ctx(organism_id=uuid4())
    assert classify_context_compatibility(a, b) is ContextCompatibility.CONTEXT_MISMATCH


def test_context_compatibility_exact_when_every_shared_field_equal():
    organism = uuid4()
    a = _Ctx(organism, strain="BY4741", medium="YPD", temperature_c=Decimal("30"))
    b = _Ctx(organism, strain="BY4741", medium="YPD", temperature_c=Decimal("30"))
    assert classify_context_compatibility(a, b) is ContextCompatibility.EXACT_CONTEXT


def test_context_compatibility_mismatch_on_conflicting_field():
    organism = uuid4()
    a = _Ctx(organism, strain="BY4741", temperature_c=Decimal("30"))
    b = _Ctx(organism, strain="BY4741", temperature_c=Decimal("37"))
    assert classify_context_compatibility(a, b) is ContextCompatibility.CONTEXT_MISMATCH


def test_context_compatibility_reference_when_partial_overlap_only():
    organism = uuid4()
    a = _Ctx(organism, strain="BY4741")
    b = _Ctx(organism, strain="BY4741", medium="YPD")
    assert classify_context_compatibility(a, b) is ContextCompatibility.COMPATIBLE_REFERENCE


def test_context_compatibility_unknown_when_organism_matches_but_nothing_else_comparable():
    organism = uuid4()
    a = _Ctx(organism)
    b = _Ctx(organism)
    assert classify_context_compatibility(a, b) is ContextCompatibility.CONTEXT_UNKNOWN


def test_context_compatibility_never_fuzzy_matches_strings():
    """A plain string mismatch (never resolved to mean the same medium) -- deliberately not
    sophisticated scoring (task Sec 6)."""
    organism = uuid4()
    a = _Ctx(organism, medium="YPD")
    b = _Ctx(organism, medium="yeast peptone dextrose")
    assert classify_context_compatibility(a, b) is ContextCompatibility.CONTEXT_MISMATCH


def test_context_compatibility_is_deterministic():
    organism = uuid4()
    a = _Ctx(organism, strain="BY4741", medium="YPD")
    b = _Ctx(organism, strain="BY4741", medium="YPD")
    first = classify_context_compatibility(a, b)
    second = classify_context_compatibility(a, b)
    assert first == second == ContextCompatibility.EXACT_CONTEXT


# --- quantitative_observation_identity_from_sgd_abundance (Agent 1.x Increment "SGD
# Reference Protein Abundance Integration") -------------------------------------------------


def test_sgd_abundance_identity_uses_real_confirmed_cdc28_values():
    """Real, live-confirmed CDC28 figure: 6670 molecules/cell, MAD 1539."""
    abundance = SgdProteinAbundance(
        value=Decimal("6670"), median_absolute_deviation=Decimal("1539")
    )
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000364"
    )

    assert identity.observation_type == QuantitativeObservationType.PROTEIN_ABUNDANCE.value
    assert identity.value == Decimal("6670")
    assert identity.unit == "molecules/cell"
    assert identity.uncertainty == Decimal("1539")
    assert identity.normalized_value == Decimal("6670")
    assert identity.normalized_unit == CANONICAL_UNIT_MOLECULES_PER_CELL


def test_sgd_abundance_identity_evidence_class_is_always_reference_baseline():
    abundance = SgdProteinAbundance(value=Decimal("1000"), median_absolute_deviation=None)
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000001"
    )

    assert identity.evidence_class is QuantitativeEvidenceClass.REFERENCE_BASELINE


def test_sgd_abundance_identity_source_and_deterministic_source_id():
    abundance = SgdProteinAbundance(value=Decimal("1000"), median_absolute_deviation=None)
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000001"
    )

    assert identity.source is SourceType.SGD
    assert identity.source_id == "sgd-protein-abundance:S000000001"

    repeat = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000001"
    )
    assert repeat.source_id == identity.source_id


def test_sgd_abundance_identity_no_uncertainty_when_sgd_reports_none():
    """A real, disclosed case (SGD's own MAD absent) -- never fabricated as zero."""
    abundance = SgdProteinAbundance(value=Decimal("1000"), median_absolute_deviation=None)
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000001"
    )

    assert identity.uncertainty is None


def test_sgd_abundance_identity_threads_through_already_resolved_identity_links():
    abundance = SgdProteinAbundance(value=Decimal("1000"), median_absolute_deviation=None)
    protein_id, organism_id, context_id, publication_id = uuid4(), uuid4(), uuid4(), uuid4()
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance,
        sgd_id="S000000001",
        protein_id=protein_id,
        organism_id=organism_id,
        experimental_context_id=context_id,
        publication_id=publication_id,
    )

    assert identity.protein_id == protein_id
    assert identity.organism_id == organism_id
    assert identity.experimental_context_id == context_id
    assert identity.publication_id == publication_id


def test_sgd_abundance_identity_never_derives_a_concentration():
    """Task's own explicit exclusion: no nM value, no 0.1 pL assumption anywhere."""
    abundance = SgdProteinAbundance(
        value=Decimal("6670"), median_absolute_deviation=Decimal("1539")
    )
    identity = quantitative_observation_identity_from_sgd_abundance(
        abundance, sgd_id="S000000364"
    )

    assert identity.observation_type != QuantitativeObservationType.PROTEIN_CONCENTRATION.value
    assert identity.unit != CANONICAL_UNIT_NM
    assert "0.1 pL" not in (identity.notes or "")
    assert "pL" not in identity.unit
