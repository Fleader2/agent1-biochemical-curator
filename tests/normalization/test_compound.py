"""Tests for compound identity normalization (Phase 4, Increment 6).

Pure unit tests: no database, no HTTP, no live KEGG/ChEBI/PubChem access.
``FakeCompoundLookup`` is an in-memory, read-only stand-in for
``app.normalization.compound.CompoundLookup`` -- there is no SQLAlchemy
adapter in this increment, consistent with ``app.normalization.compound``'s
own module docstring. Unlike Gene/Protein, Compound has no organism scope at
all, so no lookup method here takes an ``organism_id`` and there is nothing
to leak across.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.connectors.brenda import BrendaKineticMeasurement
from app.connectors.kegg import KeggCompoundRecord, KeggFlatFileRecord
from app.connectors.sabiork import (
    SabioCompoundExternalIdentity,
    SabioKineticParameter,
    SabioKineticRecord,
    SabioReactionSpecies,
)
from app.models.enums import SourceType
from app.normalization.compound import (
    BrendaLigandMatchMethod,
    BrendaLigandResolutionStatus,
    CompoundCandidate,
    CompoundIdentity,
    CompoundLookup,
    brenda_ligand_text,
    compound_identity_from_kegg,
    compound_identity_from_sabiork,
    normalize_compound,
    resolve_brenda_ligand_to_compound,
    structured_compound_identity_from_brenda_ligand,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class FakeCompoundLookup:
    """In-memory ``CompoundLookup``: exact-match filtering over a fixed candidate list."""

    compounds: Sequence[CompoundCandidate] = ()

    def by_chebi_id(self, chebi_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.chebi_id == chebi_id]

    def by_kegg_compound_id(self, kegg_compound_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.kegg_compound_id == kegg_compound_id]

    def by_pubchem_cid(self, pubchem_cid: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.pubchem_cid == pubchem_cid]

    def by_metacyc_id(self, metacyc_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.metacyc_id == metacyc_id]

    def by_inchikey(self, inchikey: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.inchikey == inchikey]

    def by_canonical_name(self, canonical_name: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.canonical_name == canonical_name]

    def by_synonym(self, synonym: str) -> Sequence[CompoundCandidate]:
        # FakeCompoundLookup has no synonym table -- tests that need synonym
        # lookups use SynonymAwareFakeCompoundLookup instead.
        return []


def _candidate(
    *,
    canonical_name: str = "Test Compound",
    chebi_id: str | None = None,
    kegg_compound_id: str | None = None,
    pubchem_cid: str | None = None,
    metacyc_id: str | None = None,
    inchikey: str | None = None,
    inchi: str | None = None,
    formula: str | None = None,
    charge: int | None = None,
    is_generic: bool = False,
    compound_id: UUID | None = None,
) -> CompoundCandidate:
    return CompoundCandidate(
        id=compound_id or uuid4(),
        canonical_name=canonical_name,
        chebi_id=chebi_id,
        kegg_compound_id=kegg_compound_id,
        pubchem_cid=pubchem_cid,
        metacyc_id=metacyc_id,
        inchikey=inchikey,
        inchi=inchi,
        formula=formula,
        charge=charge,
        is_generic=is_generic,
    )


class SynonymAwareFakeCompoundLookup:
    """A ``CompoundLookup`` that actually supports ``by_synonym``, via a separate synonym map."""

    def __init__(
        self, compounds: Sequence[CompoundCandidate] = (), synonyms: dict[str, UUID] | None = None
    ) -> None:
        self.compounds = list(compounds)
        self._by_id = {c.id: c for c in self.compounds}
        self._synonyms = synonyms or {}

    def by_chebi_id(self, chebi_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.chebi_id == chebi_id]

    def by_kegg_compound_id(self, kegg_compound_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.kegg_compound_id == kegg_compound_id]

    def by_pubchem_cid(self, pubchem_cid: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.pubchem_cid == pubchem_cid]

    def by_metacyc_id(self, metacyc_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.metacyc_id == metacyc_id]

    def by_inchikey(self, inchikey: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.inchikey == inchikey]

    def by_canonical_name(self, canonical_name: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.canonical_name == canonical_name]

    def by_synonym(self, synonym: str) -> Sequence[CompoundCandidate]:
        compound_id = self._synonyms.get(synonym)
        if compound_id is None:
            return []
        candidate = self._by_id.get(compound_id)
        return [candidate] if candidate is not None else []


def _kegg_record(
    *,
    entry_id: str = "C00031",
    names: tuple[str, ...] = ("D-Glucose", "Grape sugar", "Dextrose"),
    formula: str | None = "C6H12O6",
) -> KeggCompoundRecord:
    raw = KeggFlatFileRecord(entry_id=entry_id, entry_type="Compound", fields={})
    return KeggCompoundRecord(
        entry_id=entry_id,
        names=names,
        formula=formula,
        exact_mass="180.0634",
        mol_weight="180.16",
        pathways=(),
        raw=raw,
    )


# --- CompoundIdentity construction / validation ------------------------------------


def test_compound_identity_requires_at_least_one_identity_signal() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.KEGG, source_identifier="req-1")


def test_compound_identity_formula_alone_is_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.OTHER, source_identifier="req-2", formula="C6H12O6")


def test_compound_identity_charge_alone_is_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.OTHER, source_identifier="req-3", charge=-1)


def test_compound_identity_inchi_alone_is_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(
            source=SourceType.OTHER, source_identifier="req-4", inchi="InChI=1S/C6H12O6/..."
        )


def test_compound_identity_is_generic_alone_is_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.OTHER, source_identifier="req-5", is_generic=True)


def test_compound_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        CompoundIdentity(source=SourceType.KEGG, source_identifier="   ", chebi_id="CHEBI:17234")


def test_compound_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = CompoundIdentity(
        source=SourceType.KEGG,
        source_identifier="C00031",
        chebi_id="  CHEBI:17234  ",
        canonical_name="   ",
    )
    assert identity.chebi_id == "CHEBI:17234"
    assert identity.canonical_name is None


def test_compound_identity_drops_blank_synonyms_and_dedupes_exact_repeats() -> None:
    identity = CompoundIdentity(
        source=SourceType.KEGG,
        source_identifier="C00031",
        canonical_name="D-Glucose",
        synonyms=("  Dextrose  ", "", "Dextrose", "  ", "Grape sugar"),
    )
    assert identity.synonyms == ("Dextrose", "Grape sugar")


def test_compound_identity_does_not_strip_isoform_like_or_charge_notation() -> None:
    """Whitespace-only cleaning: inchikey/formula content is never rewritten."""
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="req-6",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
        formula="C6H12O6",
        charge=-2,
    )
    assert identity.inchikey == "WQZGKKKJIJFFOK-GASJEMHNSA-N"
    assert identity.formula == "C6H12O6"
    assert identity.charge == -2


# --- Lookup API shape ----------------------------------------------------------------


def test_compound_lookup_has_no_by_formula_method() -> None:
    assert not hasattr(CompoundLookup, "by_formula")


def test_compound_lookup_has_no_by_charge_method() -> None:
    assert not hasattr(CompoundLookup, "by_charge")


def test_compound_lookup_has_no_by_molecular_weight_method() -> None:
    assert not hasattr(CompoundLookup, "by_molecular_weight")


def test_compound_lookup_has_no_by_ec_number_method() -> None:
    assert not hasattr(CompoundLookup, "by_ec_number")


def test_compound_lookup_has_no_fuzzy_name_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(CompoundLookup, inspect.isfunction)}
    assert not any("fuzzy" in name for name in method_names)


def test_compound_lookup_methods_take_no_organism_id() -> None:
    """Compound is organism-agnostic -- no lookup method should take organism_id."""
    for name, method in inspect.getmembers(CompoundLookup, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert "organism_id" not in inspect.signature(method).parameters, name


def test_normalize_compound_has_no_organism_parameter() -> None:
    assert "organism_id" not in inspect.signature(normalize_compound).parameters


# --- Exact strong identifier matching ------------------------------------------------


def test_chebi_id_single_candidate_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(chebi_id="CHEBI:17234", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI, source_identifier="CHEBI:17234", chebi_id="CHEBI:17234"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == compound_id
    assert result.organism_id is None


def test_inchikey_single_candidate_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="WQZGKKKJIJFFOK-GASJEMHNSA-N",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


def test_kegg_compound_id_single_candidate_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(kegg_compound_id="C00031", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.KEGG, source_identifier="C00031", kegg_compound_id="C00031"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


def test_pubchem_cid_single_candidate_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(pubchem_cid="5793", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="5793", pubchem_cid="5793"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


def test_metacyc_id_single_candidate_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(metacyc_id="Glucopyranose", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.METACYC, source_identifier="Glucopyranose", metacyc_id="Glucopyranose"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


def test_all_supplied_strong_ids_resolving_same_compound_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(
                chebi_id="CHEBI:17234",
                kegg_compound_id="C00031",
                inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
                compound_id=compound_id,
            ),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:17234",
        chebi_id="CHEBI:17234",
        kegg_compound_id="C00031",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id
    for name in ("chebi_id", "kegg_compound_id", "inchikey"):
        assert name in result.reason


# --- Compatible missing metadata -----------------------------------------------------


def test_chebi_resolves_a_incoming_inchikey_missing_on_a_is_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(chebi_id="CHEBI:17234", inchikey=None, compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:17234",
        chebi_id="CHEBI:17234",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


# --- Strong identifier ambiguity ------------------------------------------------------


def test_same_strong_identifier_on_two_rows_is_ambiguous() -> None:
    compound_a, compound_b = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(chebi_id="CHEBI:99999", compound_id=compound_a),
            _candidate(chebi_id="CHEBI:99999", compound_id=compound_b),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI, source_identifier="CHEBI:99999", chebi_id="CHEBI:99999"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {compound_a, compound_b}


def test_duplicate_candidate_ids_do_not_manufacture_ambiguity() -> None:
    compound_id = uuid4()
    candidate = _candidate(chebi_id="CHEBI:17234", compound_id=compound_id)
    lookup = FakeCompoundLookup(compounds=(candidate, candidate))
    identity = CompoundIdentity(
        source=SourceType.CHEBI, source_identifier="CHEBI:17234", chebi_id="CHEBI:17234"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


# --- Cross-identifier conflict --------------------------------------------------------


def test_chebi_resolves_a_inchikey_resolves_b_is_conflicted() -> None:
    compound_a, compound_b = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(chebi_id="CHEBI:17234", compound_id=compound_a),
            _candidate(inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N", compound_id=compound_b),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:17234",
        chebi_id="CHEBI:17234",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {compound_a, compound_b}


def test_chebi_resolves_a_candidate_has_different_inchikey_is_conflicted() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(
                chebi_id="CHEBI:17234",
                inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
                compound_id=compound_id,
            ),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:17234",
        chebi_id="CHEBI:17234",
        inchikey="DIFFERENTKEYXX-UHFFFAOYSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == compound_id
    assert "inchikey" in result.reason


# --- Structure safety: no stereochemistry/protonation collapse -----------------------


def test_different_inchikeys_do_not_match() -> None:
    """WQZGKKKJIJFFOK-GASJEMHNSA-N (D-glucose) vs a differently-stereo InChIKey."""
    lookup = FakeCompoundLookup(compounds=(_candidate(inchikey="WQZGKKKJIJFFOK-VFUOTHLCSA-N"),))
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="req-7",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is not NormalizationStatus.MATCHED
    assert result.matched_entity_id is None


def test_stereochemical_difference_is_preserved_not_stripped() -> None:
    """Two InChIKeys differing only in the stereochemistry layer remain distinct."""
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(inchikey="WQZGKKKJIJFFOK-VFUOTHLCSA-N", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="req-8",
        inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.matched_entity_id != compound_id
    assert result.status is not NormalizationStatus.MATCHED


def test_no_protonation_normalization_distinct_charge_states_stay_distinct() -> None:
    """A neutral and deprotonated form, represented as distinct rows with distinct
    ChEBI IDs, must not be merged just because the incoming record also supplies a
    shared canonical_name.
    """
    neutral_id, anion_id = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(
                canonical_name="Phosphate",
                chebi_id="CHEBI:18367",
                charge=0,
                compound_id=neutral_id,
            ),
            _candidate(
                canonical_name="Phosphate",
                chebi_id="CHEBI:43474",
                charge=-2,
                compound_id=anion_id,
            ),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:43474",
        chebi_id="CHEBI:43474",
        charge=-2,
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == anion_id
    assert result.matched_entity_id != neutral_id


# --- Formula safety --------------------------------------------------------------------


def test_same_formula_on_two_compounds_does_not_make_them_identical() -> None:
    """Constitutional isomers sharing a formula (e.g. glucose vs fructose, both
    C6H12O6) must remain distinct -- formula is never queried as an identifier.
    """
    glucose_id, fructose_id = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(canonical_name="D-Glucose", formula="C6H12O6", compound_id=glucose_id),
            _candidate(canonical_name="D-Fructose", formula="C6H12O6", compound_id=fructose_id),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-9", canonical_name="D-Glucose"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (glucose_id,)


def test_formula_only_identity_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.OTHER, source_identifier="req-10", formula="C6H12O6")


def test_formula_disagreement_on_matched_candidate_does_not_prevent_matched() -> None:
    """Open policy question (see module docstring): formula is Level 2, inert for
    conflict purposes in this increment -- a strong-ID match stands even if formula
    differs (e.g. a database representation difference), rather than inventing a
    conflict rule not justified by current specifications.
    """
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(chebi_id="CHEBI:17234", formula="C6H12O6", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:17234",
        chebi_id="CHEBI:17234",
        formula="C6H10O5",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


# --- Charge safety -----------------------------------------------------------------------


def test_charge_only_identity_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompoundIdentity(source=SourceType.OTHER, source_identifier="req-11", charge=-1)


def test_same_name_with_distinct_charge_states_does_not_silently_match() -> None:
    """Two rows sharing a canonical_name but distinct charges must not be conflated --
    the weak-name path only ever produces AMBIGUOUS, and charge is never consulted
    to break the tie.
    """
    neutral_id, anion_id = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(canonical_name="Phosphate", charge=0, compound_id=neutral_id),
            _candidate(canonical_name="Phosphate", charge=-2, compound_id=anion_id),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-12", canonical_name="Phosphate", charge=-2
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {neutral_id, anion_id}


# --- Generic compound safety --------------------------------------------------------------


def test_is_generic_is_preserved_on_candidate() -> None:
    compound_id = uuid4()
    candidate = _candidate(is_generic=True, compound_id=compound_id)
    assert candidate.is_generic is True


def test_generic_and_specific_are_not_silently_merged_via_weak_name() -> None:
    """A generic class entry ('fatty acid') and a specific compound must not collide
    just because a source supplies the same text as both name and incoming claim --
    the weak path is AMBIGUOUS, never an automatic MATCHED, regardless of genericness.
    """
    generic_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(canonical_name="fatty acid", is_generic=True, compound_id=generic_id),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="req-13",
        canonical_name="fatty acid",
        is_generic=False,
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.candidate_entity_ids == (generic_id,)


def test_is_generic_disagreement_does_not_block_strong_id_match() -> None:
    """Open policy question (see module docstring): is_generic is inert for conflict
    purposes in this increment.
    """
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(chebi_id="CHEBI:99999", is_generic=True, compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:99999",
        chebi_id="CHEBI:99999",
        is_generic=False,
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compound_id


# --- Weak candidate generation -----------------------------------------------------------


def test_canonical_name_only_one_candidate_is_ambiguous_not_matched() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(canonical_name="D-Glucose", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-14", canonical_name="D-Glucose"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (compound_id,)
    assert result.matched_entity_id is None


def test_multiple_canonical_name_candidates_is_ambiguous_with_all_ids() -> None:
    compound_a, compound_b = uuid4(), uuid4()
    lookup = FakeCompoundLookup(
        compounds=(
            _candidate(canonical_name="D-Glucose", compound_id=compound_a),
            _candidate(canonical_name="D-Glucose", compound_id=compound_b),
        )
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-15", canonical_name="D-Glucose"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {compound_a, compound_b}


def test_exact_synonym_candidate_is_ambiguous() -> None:
    compound_id = uuid4()
    candidate = _candidate(canonical_name="D-Glucose", compound_id=compound_id)
    lookup = SynonymAwareFakeCompoundLookup(
        compounds=(candidate,), synonyms={"Dextrose": compound_id}
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-16", synonyms=("Dextrose",)
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (compound_id,)


def test_no_fuzzy_name_matching() -> None:
    """A near-miss name must not match -- exact string comparison only."""
    lookup = FakeCompoundLookup(compounds=(_candidate(canonical_name="D-Glucose monohydrate"),))
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-17", canonical_name="D-Glucose"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_multiple_weak_signals_pointing_to_same_candidate_remain_ambiguous_not_inflated() -> None:
    compound_id = uuid4()
    candidate = _candidate(canonical_name="D-Glucose", compound_id=compound_id)
    lookup = SynonymAwareFakeCompoundLookup(
        compounds=(candidate,), synonyms={"Dextrose": compound_id}
    )
    identity = CompoundIdentity(
        source=SourceType.OTHER,
        source_identifier="req-18",
        canonical_name="D-Glucose",
        synonyms=("Dextrose",),
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.candidate_entity_ids == (compound_id,)


# --- NEW / UNRESOLVED ------------------------------------------------------------------


def test_unmatched_strong_id_with_canonical_name_no_weak_collision_is_new() -> None:
    lookup = FakeCompoundLookup(compounds=())
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:99999",
        chebi_id="CHEBI:99999",
        canonical_name="A Brand New Compound",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_unmatched_strong_id_with_exact_name_collision_is_ambiguous_not_new() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=(_candidate(canonical_name="A Brand New Compound", compound_id=compound_id),)
    )
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:99999",
        chebi_id="CHEBI:99999",
        canonical_name="A Brand New Compound",
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == (compound_id,)


def test_unmatched_strong_id_without_canonical_name_is_unresolved() -> None:
    lookup = FakeCompoundLookup(compounds=())
    identity = CompoundIdentity(
        source=SourceType.CHEBI, source_identifier="CHEBI:99999", chebi_id="CHEBI:99999"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_canonical_name_only_unmatched_is_unresolved_never_new() -> None:
    lookup = FakeCompoundLookup(compounds=())
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-19", canonical_name="A Brand New Compound"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_no_canonical_name_never_becomes_new_even_with_strong_id() -> None:
    """No canonical name is ever synthesized from an external identifier."""
    lookup = FakeCompoundLookup(compounds=())
    identity = CompoundIdentity(
        source=SourceType.OTHER, source_identifier="req-20", inchikey="ABCDEFGHIJKLMN-UHFFFAOYSA-N"
    )

    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


# --- Determinism ---------------------------------------------------------------------------


def test_same_candidate_set_different_lookup_order_same_result() -> None:
    compound_a, compound_b = uuid4(), uuid4()
    candidate_a = _candidate(chebi_id="CHEBI:1", compound_id=compound_a)
    candidate_b = _candidate(chebi_id="CHEBI:1", compound_id=compound_b)
    identity = CompoundIdentity(
        source=SourceType.CHEBI, source_identifier="CHEBI:1", chebi_id="CHEBI:1"
    )

    forward = normalize_compound(
        identity, lookup=FakeCompoundLookup(compounds=(candidate_a, candidate_b))
    )
    backward = normalize_compound(
        identity, lookup=FakeCompoundLookup(compounds=(candidate_b, candidate_a))
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_conflicting_candidate_id_order_is_deterministic() -> None:
    compound_a, compound_b = uuid4(), uuid4()
    identity = CompoundIdentity(
        source=SourceType.CHEBI,
        source_identifier="CHEBI:1",
        chebi_id="CHEBI:1",
        inchikey="ABCDEFGHIJKLMN-UHFFFAOYSA-N",
    )

    forward = normalize_compound(
        identity,
        lookup=FakeCompoundLookup(
            compounds=(
                _candidate(chebi_id="CHEBI:1", compound_id=compound_a),
                _candidate(inchikey="ABCDEFGHIJKLMN-UHFFFAOYSA-N", compound_id=compound_b),
            )
        ),
    )
    backward = normalize_compound(
        identity,
        lookup=FakeCompoundLookup(
            compounds=(
                _candidate(inchikey="ABCDEFGHIJKLMN-UHFFFAOYSA-N", compound_id=compound_b),
                _candidate(chebi_id="CHEBI:1", compound_id=compound_a),
            )
        ),
    )

    assert (
        forward.candidate_entity_ids
        == backward.candidate_entity_ids
        == tuple(sorted((compound_a, compound_b)))
    )


# --- KEGG conversion helper ----------------------------------------------------------------


def test_compound_identity_from_kegg_preserves_kegg_compound_id() -> None:
    record = _kegg_record(entry_id="C00031")

    identity = compound_identity_from_kegg(record)

    assert identity.source is SourceType.KEGG
    assert identity.source_identifier == "C00031"
    assert identity.kegg_compound_id == "C00031"


def test_compound_identity_from_kegg_maps_first_name_to_canonical_name() -> None:
    record = _kegg_record(names=("D-Glucose", "Grape sugar", "Dextrose"))

    identity = compound_identity_from_kegg(record)

    assert identity.canonical_name == "D-Glucose"
    assert identity.synonyms == ("Grape sugar", "Dextrose")


def test_compound_identity_from_kegg_preserves_formula() -> None:
    record = _kegg_record(formula="C6H12O6")

    identity = compound_identity_from_kegg(record)

    assert identity.formula == "C6H12O6"


def test_compound_identity_from_kegg_handles_empty_names() -> None:
    record = _kegg_record(names=())

    identity = compound_identity_from_kegg(record)

    assert identity.canonical_name is None
    assert identity.synonyms == ()
    assert identity.kegg_compound_id == record.entry_id


def test_compound_identity_from_kegg_does_not_mutate_original_record() -> None:
    record = _kegg_record(entry_id="C00031")

    compound_identity_from_kegg(record)

    assert record.entry_id == "C00031"


def test_compound_identity_from_kegg_does_not_copy_molecular_weight() -> None:
    record = _kegg_record()

    identity = compound_identity_from_kegg(record)

    assert not hasattr(identity, "molecular_weight")
    assert not hasattr(identity, "mol_weight")


# --- compound_identity_from_sabiork (Agent 1.x Increment C.7) ------------------------------------


def _sabio_record(
    *,
    entry_id: str = "18229",
    reaction_species: tuple[SabioReactionSpecies, ...] = (),
    compound_external_identities: tuple[SabioCompoundExternalIdentity, ...] = (),
) -> SabioKineticRecord:
    return SabioKineticRecord(
        entry_id=entry_id,
        parameters=(),
        reaction_equation=None,
        ec_number="2.3.1.86",
        enzyme_name="fatty-acyl-CoA synthase",
        uniprot_ids=(),
        is_wildtype=None,
        is_recombinant=None,
        organism="Saccharomyces cerevisiae",
        ncbi_taxonomy_id=None,
        strain=None,
        tissue=None,
        buffer=None,
        ph=None,
        temperature=None,
        temperature_unit=None,
        pubmed_id=None,
        publication_title=None,
        reaction_species=reaction_species,
        compound_external_identities=compound_external_identities,
        raw={},
    )


def _sabio_parameter(*, species_label: str | None) -> SabioKineticParameter:
    return SabioKineticParameter(
        name="Km",
        parameter_type="Km",
        value="18.0",
        unit="µM",
        species_label=species_label,
        comment="apparent",
    )


def _malonyl_coa_record() -> SabioKineticRecord:
    """Shaped exactly after the real, live SABIO-RK entry 18229 (Agent 1.x Increment C.7
    inspection): Malonyl-CoA has one unique KeggCompoundID (C00083) but SEVEN distinct
    ChebiID values (real ChEBI protonation-state granularity) -- chebi_id must stay
    unresolved while kegg_compound_id still resolves."""
    return _sabio_record(
        reaction_species=(
            SabioReactionSpecies(internal_id=1930, name="Malonyl-CoA", role="Substrate"),
        ),
        compound_external_identities=(
            SabioCompoundExternalIdentity(
                internal_id=1930,
                chebi_id=None,  # 7 distinct real ChEBI ids -> never a single value
                kegg_compound_id="C00083",
                pubchem_cid="644066",
                metacyc_id="MALONYL-COA",
                inchikey=None,
            ),
        ),
    )


def test_compound_identity_from_sabiork_resolves_via_unique_kegg_id() -> None:
    record = _malonyl_coa_record()
    parameter = _sabio_parameter(species_label="n | Malonyl-CoA | Substrate")

    identity = compound_identity_from_sabiork(record, parameter)

    assert identity is not None
    assert identity.kegg_compound_id == "C00083"
    assert identity.pubchem_cid == "644066"
    assert identity.metacyc_id == "MALONYL-COA"
    assert identity.chebi_id is None
    assert identity.canonical_name == "Malonyl-CoA"
    assert identity.source == SourceType.SABIORK


def test_compound_identity_from_sabiork_none_when_no_species_label() -> None:
    """Every real Vmax observed this increment carries no species_label at all."""
    record = _malonyl_coa_record()
    parameter = _sabio_parameter(species_label=None)

    assert compound_identity_from_sabiork(record, parameter) is None


def test_compound_identity_from_sabiork_none_when_label_shape_unrecognized() -> None:
    record = _malonyl_coa_record()
    parameter = _sabio_parameter(species_label="not the expected shape")

    assert compound_identity_from_sabiork(record, parameter) is None


def test_compound_identity_from_sabiork_none_when_name_matches_no_reaction_species() -> None:
    record = _sabio_record(reaction_species=(), compound_external_identities=())
    parameter = _sabio_parameter(species_label="1 | Propionyl-CoA | Substrate")

    assert compound_identity_from_sabiork(record, parameter) is None


def test_compound_identity_from_sabiork_none_when_name_matches_two_internal_ids() -> None:
    """Never guesses among ambiguous internal SABIO-RK ids sharing one name."""
    record = _sabio_record(
        reaction_species=(
            SabioReactionSpecies(internal_id=1, name="Ambiguous", role="Substrate"),
            SabioReactionSpecies(internal_id=2, name="Ambiguous", role="Product"),
        ),
        compound_external_identities=(),
    )
    parameter = _sabio_parameter(species_label="1 | Ambiguous | Substrate")

    assert compound_identity_from_sabiork(record, parameter) is None


def test_compound_identity_from_sabiork_none_when_no_external_identifiers_at_all() -> None:
    record = _sabio_record(
        reaction_species=(SabioReactionSpecies(internal_id=1, name="Enzyme", role="Catalyst"),),
        compound_external_identities=(
            SabioCompoundExternalIdentity(
                internal_id=1,
                chebi_id=None,
                kegg_compound_id=None,
                pubchem_cid=None,
                metacyc_id=None,
                inchikey=None,
            ),
        ),
    )
    parameter = _sabio_parameter(species_label="1 | Enzyme | Catalyst")

    assert compound_identity_from_sabiork(record, parameter) is None


def test_compound_identity_from_sabiork_never_guesses_a_single_chebi_from_several() -> None:
    """No fuzzy/arbitrary selection: 7 distinct ChEBI ids for one internal compound (real,
    live-confirmed shape) must never collapse into "the first one"."""
    record = _malonyl_coa_record()
    parameter = _sabio_parameter(species_label="n | Malonyl-CoA | Substrate")

    identity = compound_identity_from_sabiork(record, parameter)

    assert identity is not None
    assert identity.chebi_id is None


def test_compound_identity_from_sabiork_end_to_end_matches_existing_compound() -> None:
    """Full, real-shaped resolution: an existing curated Compound with kegg_compound_id
    matching SABIO-RK's own reported KeggCompoundID resolves to MATCHED -- the exact real
    outcome confirmed live for Malonyl-CoA/Acetyl-CoA/NADPH (Agent 1.x Increment C.7)."""
    existing = CompoundCandidate(
        id=uuid4(), canonical_name="Malonyl-CoA", kegg_compound_id="C00083"
    )
    lookup = FakeCompoundLookup(compounds=(existing,))
    record = _malonyl_coa_record()
    parameter = _sabio_parameter(species_label="n | Malonyl-CoA | Substrate")

    identity = compound_identity_from_sabiork(record, parameter)
    assert identity is not None
    result = normalize_compound(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == existing.id


def test_compound_identity_from_sabiork_absent_compound_does_not_match() -> None:
    """Real, live-confirmed outcome for Propionyl-/Butanoyl-/Hexanoyl-/Octanoyl-CoA: a
    compound with no curated Compound row at all never resolves, and this module never
    creates one -- normalize_compound alone (never called with a creating caller) already
    makes that safe, but confirm the identity itself carries no anchor an empty lookup could
    spuriously match."""
    lookup = FakeCompoundLookup(compounds=())
    record = _sabio_record(
        reaction_species=(
            SabioReactionSpecies(internal_id=1, name="Propionyl-CoA", role="Substrate"),
        ),
        compound_external_identities=(
            SabioCompoundExternalIdentity(
                internal_id=1,
                chebi_id=None,
                kegg_compound_id="C00100",
                pubchem_cid=None,
                metacyc_id=None,
                inchikey=None,
            ),
        ),
    )
    parameter = _sabio_parameter(species_label="1 | Propionyl-CoA | Substrate")

    identity = compound_identity_from_sabiork(record, parameter)
    assert identity is not None
    result = normalize_compound(identity, lookup=lookup)

    assert result.status is not NormalizationStatus.MATCHED


# --- BRENDA ligand-to-compound resolution (Agent 1.x Increment C.10) ---------------------------


@dataclass(frozen=True, slots=True)
class FakeCompoundNameIndexLookup:
    """In-memory ``CompoundNameIndexLookup``: fixed ``(id, name)`` pairs, no database."""

    canonical_names: Sequence[tuple[UUID, str]] = ()
    synonyms: Sequence[tuple[UUID, str]] = ()

    def all_canonical_names(self) -> Sequence[tuple[UUID, str]]:
        return self.canonical_names

    def all_synonyms(self) -> Sequence[tuple[UUID, str]]:
        return self.synonyms


def test_brenda_ligand_exact_canonical_name_match_resolves() -> None:
    atp_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(canonical_names=((atp_id, "ATP"),))

    resolution = resolve_brenda_ligand_to_compound("ATP", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.RESOLVED
    assert resolution.matched_compound_id == atp_id
    assert resolution.match_method is BrendaLigandMatchMethod.CANONICAL_NAME


def test_brenda_ligand_case_and_whitespace_normalization() -> None:
    """Real, live data: BRENDA reports 'malonyl-CoA' (lowercase m); the real curated compound
    is 'Malonyl-CoA'. Surrounding whitespace is also conservatively normalized."""
    compound_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(canonical_names=((compound_id, "Malonyl-CoA"),))

    resolution = resolve_brenda_ligand_to_compound("  malonyl-coa  ", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.RESOLVED
    assert resolution.matched_compound_id == compound_id


def test_brenda_ligand_safe_hyphen_variant_normalization() -> None:
    """A Unicode en-dash/minus-sign variant of the same hyphenated name still matches --
    never a different chemical claim, purely a character-encoding difference."""
    compound_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(canonical_names=((compound_id, "Palmitoyl-CoA"),))

    en_dash = chr(0x2013)
    resolution = resolve_brenda_ligand_to_compound(f"Palmitoyl{en_dash}CoA", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.RESOLVED
    assert resolution.matched_compound_id == compound_id


def test_brenda_ligand_exact_synonym_match_resolves() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(
        canonical_names=((compound_id, "Coenzyme A"),),
        synonyms=((compound_id, "CoA"),),
    )

    resolution = resolve_brenda_ligand_to_compound("CoA", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.RESOLVED
    assert resolution.match_method is BrendaLigandMatchMethod.SYNONYM
    assert resolution.matched_compound_id == compound_id


def test_brenda_ligand_never_matches_by_substring_containment() -> None:
    """The single most important guarantee this increment adds: 'CoA' (a real reactant of
    several real pathway reactions) must never match 'octanoyl-CoA'/'acetyl-CoA' merely
    because the shorter string is a literal substring of the longer one."""
    coa_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(
        canonical_names=(
            (coa_id, "CoA"),
            (uuid4(), "octanoyl-CoA"),
            (uuid4(), "acetyl-CoA"),
            (uuid4(), "palmitoyl-CoA"),
        )
    )

    resolution = resolve_brenda_ligand_to_compound("CoA", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.RESOLVED
    assert resolution.matched_compound_id == coa_id  # never one of the longer names


def test_brenda_ligand_reverse_substring_direction_also_never_matches() -> None:
    """The converse direction: a BRENDA ligand name that happens to *contain* an existing
    compound's shorter name must not match that shorter compound either."""
    lookup = FakeCompoundNameIndexLookup(canonical_names=((uuid4(), "CoA"),))

    resolution = resolve_brenda_ligand_to_compound("octanoyl-CoA", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.UNRESOLVED
    assert resolution.matched_compound_id is None


def test_brenda_ligand_zero_matches_is_unresolved() -> None:
    lookup = FakeCompoundNameIndexLookup(canonical_names=((uuid4(), "Something Else"),))

    resolution = resolve_brenda_ligand_to_compound("oleate", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.UNRESOLVED
    assert resolution.matched_compound_id is None


def test_brenda_ligand_multiple_canonical_name_matches_is_ambiguous_never_first_picked() -> None:
    first_id, second_id = uuid4(), uuid4()
    lookup = FakeCompoundNameIndexLookup(
        canonical_names=((first_id, "Generic Substrate"), (second_id, "Generic Substrate"))
    )

    resolution = resolve_brenda_ligand_to_compound("generic substrate", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.AMBIGUOUS
    assert resolution.matched_compound_id is None
    assert set(resolution.candidate_compound_ids) == {first_id, second_id}


def test_brenda_ligand_ambiguous_name_never_falls_through_to_synonym() -> None:
    """An ambiguous canonical-name result stops the hierarchy -- it never also tries
    synonyms, even if a synonym match would otherwise have been unique."""
    first_id, second_id, synonym_id = uuid4(), uuid4(), uuid4()
    lookup = FakeCompoundNameIndexLookup(
        canonical_names=((first_id, "X"), (second_id, "X"), (synonym_id, "Y")),
        synonyms=((synonym_id, "X"),),
    )

    resolution = resolve_brenda_ligand_to_compound("X", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.AMBIGUOUS
    assert set(resolution.candidate_compound_ids) == {first_id, second_id}


def test_brenda_ligand_multiple_synonym_matches_is_ambiguous() -> None:
    first_id, second_id = uuid4(), uuid4()
    lookup = FakeCompoundNameIndexLookup(
        synonyms=((first_id, "Shared Alias"), (second_id, "Shared Alias"))
    )

    resolution = resolve_brenda_ligand_to_compound("shared alias", lookup=lookup)

    assert resolution.status is BrendaLigandResolutionStatus.AMBIGUOUS
    assert set(resolution.candidate_compound_ids) == {first_id, second_id}


def test_brenda_ligand_resolution_never_creates_only_reuses_existing() -> None:
    """BrendaLigandResolution has no notion of creation at all -- confirmed structurally:
    a resolved outcome's matched_compound_id is always one of the lookup's own existing ids,
    never a freshly-minted one."""
    existing_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(canonical_names=((existing_id, "ATP"),))

    resolution = resolve_brenda_ligand_to_compound("ATP", lookup=lookup)

    assert resolution.matched_compound_id == existing_id
    assert not hasattr(resolution, "created")


def test_brenda_ligand_blank_or_none_ligand_text_returns_none_not_unresolved() -> None:
    lookup = FakeCompoundNameIndexLookup(canonical_names=((uuid4(), "ATP"),))

    assert resolve_brenda_ligand_to_compound(None, lookup=lookup) is None
    assert resolve_brenda_ligand_to_compound("   ", lookup=lookup) is None


def test_brenda_ligand_raw_text_preserved_regardless_of_outcome() -> None:
    lookup = FakeCompoundNameIndexLookup()

    resolution = resolve_brenda_ligand_to_compound("  Some-Ligand  ", lookup=lookup)

    assert resolution.raw_ligand_text == "  Some-Ligand  "


def test_structured_compound_identity_from_brenda_ligand_always_none() -> None:
    """No crosswalk exists in this repository between BRENDA's internal ligandStructureId
    and any of Agent 1's own Level 1 compound identifiers -- confirmed, never guessed."""
    assert structured_compound_identity_from_brenda_ligand("7") is None
    assert structured_compound_identity_from_brenda_ligand(None) is None


def _brenda_measurement(*, substrate: str | None = None, inhibitor: str | None = None):
    return BrendaKineticMeasurement(
        parameter_type="Km",
        parameter_value="0.5",
        parameter_value_maximum=None,
        unit="mM",
        ec_number="1.1.1.1",
        organism="Saccharomyces cerevisiae",
        substrate=substrate,
        inhibitor=inhibitor,
        commentary=None,
        literature_ids=(),
        raw={},
    )


def test_brenda_ligand_text_prefers_substrate_never_both() -> None:
    km_record = _brenda_measurement(substrate="ATP", inhibitor=None)
    ki_record = _brenda_measurement(substrate=None, inhibitor="pyrazole")
    ph_record = _brenda_measurement(substrate=None, inhibitor=None)

    assert brenda_ligand_text(km_record) == "ATP"
    assert brenda_ligand_text(ki_record) == "pyrazole"
    assert brenda_ligand_text(ph_record) is None


def test_brenda_ligand_resolution_is_deterministic() -> None:
    """Same input, same lookup -> identical resolution every time (idempotent decision-making,
    mirrors persist_kinetic_measurement's own idempotent-by-source-id persistence)."""
    compound_id = uuid4()
    lookup = FakeCompoundNameIndexLookup(canonical_names=((compound_id, "ATP"),))

    first = resolve_brenda_ligand_to_compound("ATP", lookup=lookup)
    second = resolve_brenda_ligand_to_compound("ATP", lookup=lookup)

    assert first == second
