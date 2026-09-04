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

from app.connectors.kegg import KeggCompoundRecord, KeggFlatFileRecord
from app.models.enums import SourceType
from app.normalization.compound import (
    CompoundCandidate,
    CompoundIdentity,
    CompoundLookup,
    compound_identity_from_kegg,
    normalize_compound,
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
