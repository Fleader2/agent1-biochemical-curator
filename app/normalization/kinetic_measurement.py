"""Kinetic measurement identity: pure reshaping, never entity resolution or deduplication.

**This module deliberately has no ``NormalizationResult``/``NormalizationStatus``
flow and no injected ``*Lookup`` protocol**, unlike every other module in
this package (``app.normalization.reaction_enzyme``, ``.compound``,
``.gene``, ...). Those exist because their tables permit at most one
canonical row per identity and a genuine MATCHED/NEW/AMBIGUOUS/CONFLICTED
decision must be made against existing rows. ``KineticMeasurement`` is
different by explicit, long-standing design
(``app/models/kinetic_measurement.py``'s own docstring, predating this
increment): "no natural-key uniqueness or deduplication constraint is
placed on any combination of its columns: two measurements that appear
identical ... must both be able to persist as independent rows." There is
therefore no identity-dedup *decision* for this module to make -- only a
pure, source-native-record -> source-neutral-identity reshaping, exactly
mirroring ``app.normalization.reaction_enzyme.ReactionEnzymeIdentity`` in
shape but with no accompanying ``normalize_*()``/``*Lookup`` pair.

The one exception is *idempotent re-ingestion of the exact same source
record*, which is not an identity-dedup decision (no ambiguity about which
of several existing rows a new one "matches") but a replay-safety
guarantee -- and it is handled entirely by
``app.persistence.kinetic_measurement`` via the database's own partial
unique index on ``(source, source_id)``, never here.

**Never resolves entity identity.** ``reaction_id``/``protein_id``/
``complex_id``/``substrate_id``/``organism_id``/``publication_id`` are
accepted only as already-normalized UUIDs, supplied by the caller (which
itself got them from the appropriate upstream normalizer, e.g.
``app.normalization.reaction``/``.protein``/``.compound``/``.organism``/
``.publication``) -- this module never looks up an entity by EC number,
organism name, substrate name, or publication title itself (Increment A
Steps 18-23). Every one of these fields is optional: a kinetic measurement
whose reaction/protein/organism could not yet be resolved is still valid
and still worth curating (as ``UNRESOLVED``-equivalent, pending
resolution) -- this module never invents or guesses a UUID to fill a gap.

**Decimal only, never float** (Increment A Steps 16-17). ``parse_decimal()``
is the one place a source's reported numeric string becomes a ``Decimal``;
it never rounds, never averages, and never combines a reported range
(``BrendaKineticMeasurement.parameter_value_maximum``,
present when BRENDA reports a min-max range rather than a point value)
into a single mean -- the maximum, when present, is preserved on
``KineticMeasurementIdentity.value_maximum`` as its own independent field,
never blended into ``value``.

**No unit conversion.** ``unit`` is always the source's own reported unit
string, copied verbatim -- Agent 1 has no trusted unit-normalization
framework yet (Increment A Step 17), so nothing here converts, say,
BRENDA's ``"mM"`` and a hypothetical SABIO-RK ``"mol/l"`` reading onto a
common scale. SABIO-RK's own API additionally reports an SI-normalized
pair (``n_start_value``/``unit.n_name``) alongside its as-reported pair;
this module's SABIO-RK adapter (``kinetic_identity_from_sabiork``) still
only ever reads the as-reported pair (``SabioKineticParameter.value``/
``.unit``) into ``value``/``unit`` -- SABIO-RK performed that conversion,
not Agent 1, and Agent 1's own "no unit conversion" policy is about what
*this repository* computes, not about refusing a source's own
self-reported normalized value entirely. Rather than silently accept
SABIO-RK's computed value under an Agent-1-owned field name, this adapter
leaves ``normalized_value``/``normalized_unit`` unset (``None``) for every
source in this increment, full stop -- populating them from a source's own
computation without Agent 1 having verified or endorsed that computation
would misrepresent whose normalization it is. This is a disclosed,
deliberate limitation, not an oversight (see
``docs/24_kinetic_data_curation_and_handoff.md`` §11).

**Deterministic source-record identity, never numeric-equality-based**
(Increment A Step 24). ``source_id`` uniquely identifies *which record a
source reported*, never *what value it reported* -- two independent
experiments that happen to report the identical numeric value for the
identical parameter type must still receive two different ``source_id``
values (when the source itself distinguishes them, e.g. by a distinct
literature reference or a distinct native record ID) and both persist.
SABIO-RK supplies a native stable ``EntryID`` (one entry may report
several parameters; this module's SABIO-RK adapter combines
``entry_id`` with the specific parameter's own identifying fields, since
one entry's several parameters are still independent measurements needing
independent ``source_id`` values). BRENDA and OED supply no native
per-record ID at all, so their adapters compute one deterministically via
SHA-256 of a canonical, sorted-keys JSON encoding of the record's own
already-specific fields (never Python's built-in ``hash()``, which is
process-randomized for strings and not a suitable persistent identifier)
-- mirroring ``app.connectors.open_enzyme_database``'s own identity
computation exactly, so that the identity computed at the connector layer
(where OED's own multi-parameter-per-row splitting already happens) and
the identity recorded here agree.

**Source-lineage preservation, never invention** (Increment A Step 12).
``original_source``/``original_source_identifier`` describe a *reported*
upstream origin, set only when the source itself provided one.  OED's
live API (confirmed this increment, see
``app.connectors.open_enzyme_database``'s module docstring) provides none
-- ``kinetic_identity_from_oed`` therefore always leaves both ``None``,
never inferring that "OED aggregates from BRENDA/SABIO-RK in general"
means "this specific record came from BRENDA/SABIO-RK specifically."
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from app.connectors.brenda import BrendaKineticMeasurement
from app.connectors.open_enzyme_database import OedKineticParameter
from app.connectors.sabiork import SabioKineticParameter, SabioKineticRecord
from app.models.enums import SourceType
from app.normalization.identifiers import require_non_empty


class KineticParameterType(StrEnum):
    """Controlled vocabulary for ``KineticMeasurementIdentity.parameter_type``.

    ``KineticMeasurement.parameter_type`` itself is a deliberately open,
    unconstrained ``VARCHAR`` column (``app/models/enums.py``'s own
    docstring: "must never be backed by one of these closed enums") -- this
    enum exists only at the normalization layer, to give
    ``KineticMeasurementIdentity`` a consistent, typo-proof value to
    persist into that open column, while ``reported_parameter_type``
    (a plain string, see ``KineticMeasurementIdentity``) preserves each
    source's own exact original label untouched. ``OTHER`` is the
    catch-all for any reported parameter type this vocabulary does not yet
    cover -- never a reason to drop a record.
    """

    KM = "KM"
    KI = "KI"
    KCAT = "KCAT"
    KCAT_OVER_KM = "KCAT_OVER_KM"
    VMAX = "VMAX"
    PH_OPTIMUM = "PH_OPTIMUM"
    TEMPERATURE_OPTIMUM = "TEMPERATURE_OPTIMUM"
    SPECIFIC_ACTIVITY = "SPECIFIC_ACTIVITY"
    OTHER = "OTHER"


# Reported-label -> controlled-vocabulary mapping, one entry per label this
# increment's two new connectors plus the existing BRENDA connector are
# confirmed to emit. Matching is case-insensitive and whitespace-trimmed;
# an unrecognized label maps to OTHER, never raises and never drops the
# record (Increment A Step 11: "never silently ... exclude").
_PARAMETER_TYPE_LABELS: dict[str, KineticParameterType] = {
    "km": KineticParameterType.KM,
    "ki": KineticParameterType.KI,
    "kcat": KineticParameterType.KCAT,
    "kcat/km": KineticParameterType.KCAT_OVER_KM,
    "kcat_km": KineticParameterType.KCAT_OVER_KM,
    "vmax": KineticParameterType.VMAX,
    "ph optimum": KineticParameterType.PH_OPTIMUM,
    "temperature optimum": KineticParameterType.TEMPERATURE_OPTIMUM,
    "specific activity": KineticParameterType.SPECIFIC_ACTIVITY,
}


def map_parameter_type(reported_label: str | None) -> KineticParameterType:
    """Map a source's own free-text parameter-type label onto the controlled vocabulary.

    Never raises: an empty/unrecognized label maps to ``OTHER`` rather than
    excluding the record (Increment A Step 11's "excluded or marked
    unresolved, never silently ... treated as" applies to experimental
    status, not to parameter-type labeling, but the same "never drop data
    over an unrecognized label" spirit applies here).
    """
    if reported_label is None:
        return KineticParameterType.OTHER
    return _PARAMETER_TYPE_LABELS.get(reported_label.strip().lower(), KineticParameterType.OTHER)


def parse_decimal(value: str | None) -> Decimal | None:
    """Parse a source-reported numeric string into an exact ``Decimal``.

    Returns ``None`` for ``None``/blank input -- a legitimately absent
    value, not a parse failure. Raises ``ValueError`` for a non-blank
    string that is not a valid decimal literal, rather than silently
    discarding or coercing it through ``float`` first (which would
    introduce binary floating-point representation error before the value
    ever reaches ``Decimal`` -- exactly what Increment A Step 16
    prohibits).
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return Decimal(stripped)
    except InvalidOperation as exc:
        raise ValueError(f"not a valid decimal literal: {stripped!r}") from exc


@dataclass(frozen=True, slots=True)
class KineticMeasurementIdentity:
    """Source-neutral description of one independently-sourced kinetic measurement.

    Every field beyond ``source``/``source_id``/``parameter_type``/
    ``value``/``unit`` is optional -- a measurement whose reaction, protein,
    organism, or publication could not yet be resolved to an existing
    Agent 1 UUID is still a valid, curatable record (Increment A Steps
    18-23: this module never resolves that identity itself, it only
    accepts what the caller already resolved).

    ``value_maximum`` is BRENDA's occasional reported-range upper bound
    (``BrendaKineticMeasurement.parameter_value_maximum``) -- kept fully
    separate from ``value``, never averaged into it (Increment A Step 16).

    ``original_source``/``original_source_identifier`` describe a
    *reported* upstream origin (e.g. an OED record that names its
    underlying BRENDA/SABIO-RK record) -- set only when the source itself
    exposed one; never inferred (Increment A Step 12).
    """

    source: SourceType
    source_id: str
    parameter_type: KineticParameterType
    value: Decimal
    unit: str

    reported_parameter_type: str | None = None
    value_maximum: Decimal | None = None

    reaction_id: UUID | None = None
    protein_id: UUID | None = None
    complex_id: UUID | None = None
    substrate_id: UUID | None = None
    organism_id: UUID | None = None
    publication_id: UUID | None = None

    strain: str | None = None
    temperature_c: Decimal | None = None
    ph: Decimal | None = None

    reported_rate_law: str | None = None

    original_source: SourceType | None = None
    original_source_identifier: str | None = None

    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", require_non_empty(self.source_id, field_name="source_id")
        )
        if not self.unit or not self.unit.strip():
            raise ValueError("KineticMeasurementIdentity requires a non-empty unit")
        if (self.original_source is None) != (self.original_source_identifier is None):
            raise ValueError(
                "KineticMeasurementIdentity requires original_source and "
                "original_source_identifier together, or neither"
            )


def _brenda_source_id(record: BrendaKineticMeasurement) -> str:
    """Deterministic identity for a BRENDA record, which has no native record ID.

    SHA-256 of a canonical, sorted-keys JSON encoding of the record's own
    already-specific fields -- never Python's built-in ``hash()``. Mirrors
    ``app.connectors.open_enzyme_database``'s identical technique.
    """
    fields = {
        "parameter_type": record.parameter_type,
        "ec_number": record.ec_number,
        "organism": record.organism,
        "substrate": record.substrate,
        "inhibitor": record.inhibitor,
        "parameter_value": record.parameter_value,
        "parameter_value_maximum": record.parameter_value_maximum,
        "literature_ids": sorted(record.literature_ids),
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def kinetic_identity_from_brenda(
    record: BrendaKineticMeasurement,
    *,
    reaction_id: UUID | None = None,
    protein_id: UUID | None = None,
    complex_id: UUID | None = None,
    substrate_id: UUID | None = None,
    organism_id: UUID | None = None,
    publication_id: UUID | None = None,
) -> KineticMeasurementIdentity | None:
    """Pure adapter: a BRENDA connector's kinetic record -> a source-neutral identity.

    Returns ``None`` when ``record.parameter_value`` is blank -- BRENDA
    sometimes reports a record with only a maximum (range upper bound) and
    no point value, or (rarely) neither; such a record carries no
    ``value`` this table's ``NOT NULL parameter_value`` column could ever
    hold, so it is not convertible into a persistable identity at all
    (never coerced to ``0`` or silently dropped by the caller without
    knowing why -- returning ``None`` makes the caller's filtering
    decision explicit and inspectable).

    ``unit`` falls back to the literal string ``"dimensionless"`` only for
    BRENDA's ``"pH optimum"`` records (``_METHOD_SPECS["getPhOptimum"]``
    reports ``unit=None`` -- pH genuinely has no physical unit, this is not
    a missing-data gap): a factual description of the quantity, not an
    invented measurement unit, and the one case this connector's
    ``_METHOD_SPECS`` is known to leave blank. ``KineticMeasurement.unit``
    is ``NOT NULL`` in the schema, so *some* string is required here.
    """
    value = parse_decimal(record.parameter_value)
    if value is None:
        return None
    return KineticMeasurementIdentity(
        source=SourceType.BRENDA,
        source_id=_brenda_source_id(record),
        parameter_type=map_parameter_type(record.parameter_type),
        reported_parameter_type=record.parameter_type,
        value=value,
        value_maximum=parse_decimal(record.parameter_value_maximum),
        unit=record.unit or "dimensionless",
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        substrate_id=substrate_id,
        organism_id=organism_id,
        publication_id=publication_id,
        notes=record.commentary,
    )


def kinetic_identity_from_sabiork(
    entry: SabioKineticRecord,
    parameter: SabioKineticParameter,
    *,
    reaction_id: UUID | None = None,
    protein_id: UUID | None = None,
    complex_id: UUID | None = None,
    substrate_id: UUID | None = None,
    organism_id: UUID | None = None,
    publication_id: UUID | None = None,
) -> KineticMeasurementIdentity | None:
    """Pure adapter: one SABIO-RK entry + one of its parameters -> a source-neutral identity.

    ``parameter`` must be one of ``entry.parameters`` (the caller iterates
    ``entry.parameters``, calling this once per parameter -- one SABIO-RK
    entry commonly reports several independent kinetic constants, see
    ``app.connectors.sabiork``'s module docstring). ``source_id`` combines
    ``entry.entry_id`` with the parameter's own name/type, since SABIO-RK's
    native ``EntryID`` alone identifies the *entry*, not the specific
    parameter within it.

    Returns ``None`` when ``parameter.value`` is blank (no reported value
    to persist) -- see ``kinetic_identity_from_brenda`` for why this is a
    return of ``None``, never a coercion or a raise. ``unit`` falls back to
    the literal string ``"unspecified"`` only if SABIO-RK reports a value
    with no accompanying unit at all -- not expected in practice (every
    SABIO-RK kinetic-constant parameter carries a unit), a defensive
    fallback for the schema's ``NOT NULL`` constraint, never an invented
    measurement unit.
    """
    value = parse_decimal(parameter.value)
    if value is None:
        return None
    source_id = f"{entry.entry_id}:{parameter.parameter_type or parameter.name or 'unknown'}"
    temperature = (
        parse_decimal(entry.temperature)
        if entry.temperature_unit in (None, "°C", "C")
        else None
    )
    return KineticMeasurementIdentity(
        source=SourceType.SABIORK,
        source_id=source_id,
        parameter_type=map_parameter_type(parameter.parameter_type),
        reported_parameter_type=parameter.parameter_type,
        value=value,
        unit=parameter.unit or "unspecified",
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        substrate_id=substrate_id,
        organism_id=organism_id,
        publication_id=publication_id,
        strain=entry.strain,
        temperature_c=temperature,
        ph=parse_decimal(entry.ph),
        reported_rate_law=None,
        notes=parameter.comment,
    )


def kinetic_identity_from_oed(
    parameter: OedKineticParameter,
    *,
    reaction_id: UUID | None = None,
    protein_id: UUID | None = None,
    complex_id: UUID | None = None,
    substrate_id: UUID | None = None,
    organism_id: UUID | None = None,
    publication_id: UUID | None = None,
) -> KineticMeasurementIdentity | None:
    """Pure adapter: one Open Enzyme Database parameter -> a source-neutral identity.

    ``parameter.original_source``/``.original_source_identifier`` are
    always ``None`` on the connector's own output (see
    ``app.connectors.open_enzyme_database``'s module docstring: OED's live
    API exposes no source-lineage field) and are passed through unchanged
    here, never invented.

    Returns ``None`` when ``parameter.value`` is blank. ``unit`` falls back
    to the literal string ``"unspecified"`` only if OED reports a value
    with no accompanying unit -- a defensive fallback for the schema's
    ``NOT NULL`` constraint, never an invented measurement unit.
    """
    value = parse_decimal(parameter.value)
    if value is None:
        return None
    return KineticMeasurementIdentity(
        source=SourceType.OED,
        source_id=parameter.source_identifier,
        parameter_type=map_parameter_type(parameter.parameter_type),
        reported_parameter_type=parameter.parameter_type,
        value=value,
        unit=parameter.unit or "unspecified",
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        substrate_id=substrate_id,
        organism_id=organism_id,
        publication_id=publication_id,
        original_source=parameter.original_source,
        original_source_identifier=parameter.original_source_identifier,
    )


__all__ = [
    "KineticMeasurementIdentity",
    "KineticParameterType",
    "kinetic_identity_from_brenda",
    "kinetic_identity_from_oed",
    "kinetic_identity_from_sabiork",
    "map_parameter_type",
    "parse_decimal",
]
