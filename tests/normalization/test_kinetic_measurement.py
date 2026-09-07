"""Tests for kinetic measurement identity: pure reshaping, no entity resolution.

No database, no HTTP -- pure functions and dataclasses only, exactly like
``tests/normalization/test_reaction_enzyme.py``.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.connectors.brenda import BrendaKineticMeasurement
from app.connectors.open_enzyme_database import OedKineticParameter
from app.connectors.sabiork import parse_kinetic_law_json
from app.models.enums import SourceType
from app.normalization.kinetic_measurement import (
    KineticMeasurementIdentity,
    KineticParameterType,
    kinetic_identity_from_brenda,
    kinetic_identity_from_oed,
    kinetic_identity_from_sabiork,
    map_parameter_type,
    parse_decimal,
)

pytestmark = pytest.mark.unit


# --- parse_decimal --------------------------------------------------------------


def test_parse_decimal_none_is_none() -> None:
    assert parse_decimal(None) is None


def test_parse_decimal_blank_is_none() -> None:
    assert parse_decimal("   ") is None


def test_parse_decimal_exact_value_no_float_rounding() -> None:
    # 0.1 cannot be represented exactly in binary float; Decimal must be exact.
    assert parse_decimal("0.1") == Decimal("0.1")
    assert str(parse_decimal("0.1")) == "0.1"


def test_parse_decimal_rejects_invalid_literal() -> None:
    with pytest.raises(ValueError):
        parse_decimal("not-a-number")


# --- map_parameter_type ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Km", KineticParameterType.KM),
        ("km", KineticParameterType.KM),
        ("Ki", KineticParameterType.KI),
        ("kcat", KineticParameterType.KCAT),
        ("kcat/Km", KineticParameterType.KCAT_OVER_KM),
        ("kcat_km", KineticParameterType.KCAT_OVER_KM),
        ("pH optimum", KineticParameterType.PH_OPTIMUM),
        ("temperature optimum", KineticParameterType.TEMPERATURE_OPTIMUM),
        ("specific activity", KineticParameterType.SPECIFIC_ACTIVITY),
    ],
)
def test_map_parameter_type_known_labels(label: str, expected: KineticParameterType) -> None:
    assert map_parameter_type(label) is expected


def test_map_parameter_type_unrecognized_label_is_other_not_dropped() -> None:
    assert map_parameter_type("something novel") is KineticParameterType.OTHER


def test_map_parameter_type_none_is_other() -> None:
    assert map_parameter_type(None) is KineticParameterType.OTHER


# --- KineticMeasurementIdentity validation ---------------------------------------


def test_identity_requires_non_empty_source_id() -> None:
    with pytest.raises(ValueError):
        KineticMeasurementIdentity(
            source=SourceType.BRENDA,
            source_id="   ",
            parameter_type=KineticParameterType.KM,
            value=Decimal("1.0"),
            unit="mM",
        )


def test_identity_requires_non_empty_unit() -> None:
    with pytest.raises(ValueError):
        KineticMeasurementIdentity(
            source=SourceType.BRENDA,
            source_id="x",
            parameter_type=KineticParameterType.KM,
            value=Decimal("1.0"),
            unit="",
        )


def test_identity_original_source_requires_identifier_too() -> None:
    with pytest.raises(ValueError):
        KineticMeasurementIdentity(
            source=SourceType.OED,
            source_id="x",
            parameter_type=KineticParameterType.KM,
            value=Decimal("1.0"),
            unit="mM",
            original_source=SourceType.BRENDA,
            original_source_identifier=None,
        )


def test_identity_original_source_identifier_requires_source_too() -> None:
    with pytest.raises(ValueError):
        KineticMeasurementIdentity(
            source=SourceType.OED,
            source_id="x",
            parameter_type=KineticParameterType.KM,
            value=Decimal("1.0"),
            unit="mM",
            original_source=None,
            original_source_identifier="some-id",
        )


def test_identity_never_resolves_entity_identity_itself() -> None:
    """Every entity reference is optional and accepted as-given -- never required or inferred."""
    identity = KineticMeasurementIdentity(
        source=SourceType.BRENDA,
        source_id="x",
        parameter_type=KineticParameterType.KM,
        value=Decimal("1.0"),
        unit="mM",
    )
    assert identity.reaction_id is None
    assert identity.protein_id is None
    assert identity.organism_id is None
    assert identity.publication_id is None


# --- kinetic_identity_from_brenda ------------------------------------------------


def _brenda_record(**overrides: object) -> BrendaKineticMeasurement:
    base = {
        "parameter_type": "Km",
        "parameter_value": "0.33",
        "parameter_value_maximum": None,
        "unit": "mM",
        "ec_number": "1.1.1.1",
        "organism": "Escherichia coli",
        "substrate": "glucose",
        "inhibitor": None,
        "commentary": "note",
        "literature_ids": ("123", "456"),
        "raw": {},
    }
    base.update(overrides)
    return BrendaKineticMeasurement(**base)


def test_brenda_adapter_maps_fields() -> None:
    identity = kinetic_identity_from_brenda(_brenda_record())
    assert identity is not None
    assert identity.source == SourceType.BRENDA
    assert identity.value == Decimal("0.33")
    assert identity.unit == "mM"
    assert identity.parameter_type == KineticParameterType.KM
    assert identity.reported_parameter_type == "Km"
    assert identity.notes == "note"


def test_brenda_adapter_returns_none_for_blank_value() -> None:
    assert kinetic_identity_from_brenda(_brenda_record(parameter_value=None)) is None


def test_brenda_adapter_ph_optimum_gets_dimensionless_unit() -> None:
    record = _brenda_record(parameter_type="pH optimum", parameter_value="7.2", unit=None)
    identity = kinetic_identity_from_brenda(record)
    assert identity is not None
    assert identity.unit == "dimensionless"


def test_brenda_adapter_range_maximum_kept_separate_never_averaged() -> None:
    record = _brenda_record(parameter_value="0.3", parameter_value_maximum="0.5")
    identity = kinetic_identity_from_brenda(record)
    assert identity is not None
    assert identity.value == Decimal("0.3")
    assert identity.value_maximum == Decimal("0.5")


def test_brenda_adapter_deterministic_identity_no_native_id() -> None:
    record = _brenda_record()
    first = kinetic_identity_from_brenda(record)
    second = kinetic_identity_from_brenda(record)
    assert first is not None and second is not None
    assert first.source_id == second.source_id


def test_brenda_adapter_distinct_records_get_distinct_ids_even_if_value_matches() -> None:
    record_a = _brenda_record(literature_ids=("111",))
    record_b = _brenda_record(literature_ids=("222",))
    identity_a = kinetic_identity_from_brenda(record_a)
    identity_b = kinetic_identity_from_brenda(record_b)
    assert identity_a is not None and identity_b is not None
    assert identity_a.source_id != identity_b.source_id


def test_brenda_adapter_passes_through_resolved_uuids() -> None:
    reaction_id = uuid4()
    identity = kinetic_identity_from_brenda(_brenda_record(), reaction_id=reaction_id)
    assert identity is not None
    assert identity.reaction_id == reaction_id


# --- kinetic_identity_from_sabiork ------------------------------------------------


def _sabiork_entry_json() -> dict:
    return {
        "kineticlaw": {
            "parameter": [
                {
                    "name": "Km",
                    "role": "Constant",
                    "parameter_type": {"name": "Km"},
                    "start_value": "0.5",
                    "unit": {"name": "mM"},
                }
            ]
        },
        "general": {"organism": {"name": "E. coli"}, "strain": "K12"},
        "experimental_conditions": {
            "envvar_ph": {"start_value": "7.0"},
            "envvar_temperature": {"start_value": "25", "unit": {"name": "°C"}},
        },
        "publication": {"pubmed_id": "999"},
    }


def test_sabiork_adapter_maps_fields() -> None:
    import json

    record = parse_kinetic_law_json("42", json.dumps(_sabiork_entry_json()))
    identity = kinetic_identity_from_sabiork(record, record.parameters[0])
    assert identity is not None
    assert identity.source == SourceType.SABIORK
    assert identity.source_id == "42:Km"
    assert identity.value == Decimal("0.5")
    assert identity.unit == "mM"
    assert identity.strain == "K12"
    assert identity.ph == Decimal("7.0")
    assert identity.temperature_c == Decimal("25")


def test_sabiork_adapter_never_populates_normalized_fields() -> None:
    """No unit conversion in this increment -- see module docstring."""
    import json

    record = parse_kinetic_law_json("42", json.dumps(_sabiork_entry_json()))
    identity = kinetic_identity_from_sabiork(record, record.parameters[0])
    assert identity is not None
    # KineticMeasurementIdentity has no normalized_value/normalized_unit at all
    # -- persistence is responsible for leaving those columns NULL.
    assert not hasattr(identity, "normalized_value")


def test_sabiork_adapter_one_entry_two_parameters_yields_independent_identities() -> None:
    import json

    entry_json = _sabiork_entry_json()
    entry_json["kineticlaw"]["parameter"].append(
        {
            "name": "kcat",
            "role": "Constant",
            "parameter_type": {"name": "kcat"},
            "start_value": "10.0",
            "unit": {"name": "1/s"},
        }
    )
    record = parse_kinetic_law_json("42", json.dumps(entry_json))
    identities = [
        kinetic_identity_from_sabiork(record, parameter) for parameter in record.parameters
    ]
    ids = {identity.source_id for identity in identities if identity is not None}
    assert len(ids) == 2


# --- kinetic_identity_from_oed ------------------------------------------------------


def _oed_parameter(**overrides: object) -> OedKineticParameter:
    base = {
        "source_identifier": "digest-abc",
        "parameter_type": "kcat",
        "value": "12.3",
        "unit": "1/s",
        "ec_number": "1.1.1.1",
        "substrate": "ethanol",
        "organism": "Homo sapiens",
        "uniprot_id": "P00325",
        "enzyme_type": "wildtype",
        "pubmed_id": "999",
        "original_source": None,
        "original_source_identifier": None,
        "raw": {},
    }
    base.update(overrides)
    return OedKineticParameter(**base)


def test_oed_adapter_maps_fields() -> None:
    identity = kinetic_identity_from_oed(_oed_parameter())
    assert identity is not None
    assert identity.source == SourceType.OED
    assert identity.source_id == "digest-abc"
    assert identity.value == Decimal("12.3")
    assert identity.unit == "1/s"


def test_oed_adapter_returns_none_for_blank_value() -> None:
    assert kinetic_identity_from_oed(_oed_parameter(value=None)) is None


def test_oed_adapter_never_invents_lineage() -> None:
    identity = kinetic_identity_from_oed(_oed_parameter())
    assert identity is not None
    assert identity.original_source is None
    assert identity.original_source_identifier is None


def test_oed_adapter_passes_through_explicit_lineage_if_ever_present() -> None:
    """If a future OED payload does carry lineage, this adapter must not discard it."""
    parameter = _oed_parameter(
        original_source=SourceType.SABIORK, original_source_identifier="99:Km"
    )
    identity = kinetic_identity_from_oed(parameter)
    assert identity is not None
    assert identity.original_source == SourceType.SABIORK
    assert identity.original_source_identifier == "99:Km"
