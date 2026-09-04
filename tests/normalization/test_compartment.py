"""Tests for compartment identity normalization (Phase 4, Increment 7).

Pure unit tests: no database, no HTTP, no live external access.
``FakeCompartmentLookup`` is an in-memory, read-only stand-in for
``app.normalization.compartment.CompartmentLookup`` -- there is no
SQLAlchemy adapter in this increment, consistent with
``app.normalization.compartment``'s own module docstring. It mirrors the
real lookup contract exactly: ``by_ontology_id`` searches **globally**
(``Compartment.ontology_id`` has no uniqueness constraint -- candidates can
legitimately span organisms and include standard/reference rows), while
``by_name``/``by_abbreviation`` are **organism-scoped** -- including
correctly requiring an *exact* match on a requested ``organism_id=None``
(global/reference scope). Every test uses one of two fixed, distinct
organism UUIDs (plus ``None`` for standard/reference scope) so no test can
pass by accidentally assuming a single/default/global organism.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.models.enums import SourceType
from app.normalization.compartment import (
    CompartmentCandidate,
    CompartmentIdentity,
    CompartmentLookup,
    normalize_compartment,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit

ORGANISM_A = UUID("11111111-1111-1111-1111-111111111111")
ORGANISM_B = UUID("22222222-2222-2222-2222-222222222222")


@dataclass(frozen=True, slots=True)
class FakeCompartmentLookup:
    """In-memory ``CompartmentLookup``: global ontology lookup, organism-scoped name/abbrev."""

    compartments: Sequence[CompartmentCandidate] = ()

    def by_ontology_id(self, ontology_id: str) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.ontology_id == ontology_id]

    def by_name(self, organism_id: UUID | None, name: str) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.organism_id == organism_id and c.name == name]

    def by_abbreviation(
        self, organism_id: UUID | None, abbreviation: str
    ) -> Sequence[CompartmentCandidate]:
        return [
            c
            for c in self.compartments
            if c.organism_id == organism_id and c.abbreviation == abbreviation
        ]


class LeakyNameCompartmentLookup:
    """A ``CompartmentLookup`` whose ``by_name`` ignores organism scope entirely.

    Used only to prove ``normalize_compartment`` rejects cross-scope leakage
    from ``by_name``, which is contractually required to filter by the
    exact requested ``organism_id`` (including ``None``) itself.
    ``by_ontology_id`` is legitimately global, so it is implemented
    correctly here -- only ``by_name`` is broken.
    """

    def __init__(self, compartments: Sequence[CompartmentCandidate]) -> None:
        self.compartments = compartments

    def by_ontology_id(self, ontology_id: str) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.ontology_id == ontology_id]

    def by_name(self, organism_id: UUID | None, name: str) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.name == name]

    def by_abbreviation(
        self, organism_id: UUID | None, abbreviation: str
    ) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.abbreviation == abbreviation]


def _candidate(
    *,
    organism_id: UUID | None,
    name: str = "Test Compartment",
    abbreviation: str | None = None,
    ontology_id: str | None = None,
    compartment_id: UUID | None = None,
) -> CompartmentCandidate:
    return CompartmentCandidate(
        id=compartment_id or uuid4(),
        organism_id=organism_id,
        name=name,
        abbreviation=abbreviation,
        ontology_id=ontology_id,
    )


# --- CompartmentIdentity construction / validation ----------------------------------


def test_compartment_identity_requires_at_least_one_identity_signal() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        CompartmentIdentity(source=SourceType.OTHER, source_identifier="req-1")


def test_compartment_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        CompartmentIdentity(source=SourceType.OTHER, source_identifier="   ", name="cytosol")


def test_compartment_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="req-2",
        name="  cytosol  ",
        abbreviation="   ",
    )
    assert identity.name == "cytosol"
    assert identity.abbreviation is None


def test_compartment_identity_does_not_participate_via_notes() -> None:
    """CompartmentIdentity has no notes field at all -- notes are never an identity signal."""
    assert "notes" not in inspect.signature(CompartmentIdentity).parameters


def test_lookup_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-3", name="cytosol"
    )
    with pytest.raises(TypeError):
        normalize_compartment(identity, organism_id=ORGANISM_A)  # type: ignore[call-arg]


def test_organism_id_parameter_has_no_default_but_none_is_a_valid_value() -> None:
    parameters = inspect.signature(normalize_compartment).parameters
    assert parameters["organism_id"].default is inspect.Parameter.empty
    assert parameters["organism_id"].kind is inspect.Parameter.KEYWORD_ONLY

    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-4", name="cytosol"
    )
    result = normalize_compartment(identity, organism_id=None, lookup=FakeCompartmentLookup())
    assert result.organism_id is None


# --- Lookup API shape ------------------------------------------------------------------


def test_compartment_lookup_has_no_notes_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(CompartmentLookup, inspect.isfunction)}
    assert not any("notes" in name for name in method_names)


def test_compartment_lookup_has_no_fuzzy_or_substring_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(CompartmentLookup, inspect.isfunction)}
    assert not any("fuzzy" in name or "substring" in name for name in method_names)


def test_compartment_lookup_has_no_parent_organelle_method() -> None:
    assert not hasattr(CompartmentLookup, "by_parent")
    assert not hasattr(CompartmentLookup, "by_parent_organelle")


def test_compartment_lookup_has_no_reaction_membership_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(CompartmentLookup, inspect.isfunction)}
    assert not any("reaction" in name for name in method_names)


def test_ontology_id_lookup_does_not_accept_organism_id() -> None:
    params = list(inspect.signature(CompartmentLookup.by_ontology_id).parameters)
    assert "organism_id" not in params


def test_name_and_abbreviation_lookups_require_organism_id_first() -> None:
    for name in ("by_name", "by_abbreviation"):
        params = list(inspect.signature(getattr(CompartmentLookup, name)).parameters)
        assert params[1] == "organism_id"


# --- Ontology identity ----------------------------------------------------------------


def test_ontology_id_single_candidate_in_requested_organism_matched() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_A, ontology_id="GO:0005829", compartment_id=compartment_id
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:0005829", ontology_id="GO:0005829"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == compartment_id
    assert result.organism_id == ORGANISM_A


def test_ontology_id_matches_standard_reference_compartment() -> None:
    """A candidate with organism_id=None (a seeded standard compartment) is compatible
    with any requested organism -- per the verified seed-row semantics.
    """
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=None, ontology_id="GO:0005829", compartment_id=compartment_id),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:0005829", ontology_id="GO:0005829"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compartment_id
    assert result.organism_id == ORGANISM_A


def test_same_ontology_id_on_two_rows_in_requested_organism_is_ambiguous() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_A, ontology_id="GO:9999999", compartment_id=compartment_a
            ),
            _candidate(
                organism_id=ORGANISM_A, ontology_id="GO:9999999", compartment_id=compartment_b
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:9999999", ontology_id="GO:9999999"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {compartment_a, compartment_b}


def test_ontology_id_candidate_in_different_organism_is_conflicted_never_new() -> None:
    foreign_compartment = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_B, ontology_id="GO:0005829", compartment_id=foreign_compartment
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:0005829", ontology_id="GO:0005829"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert foreign_compartment in result.candidate_entity_ids
    assert result.organism_id == ORGANISM_A


def test_ontology_id_candidates_spanning_requested_and_foreign_organism_is_conflicted() -> None:
    same_org, foreign_org = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, ontology_id="GO:1111111", compartment_id=same_org),
            _candidate(
                organism_id=ORGANISM_B, ontology_id="GO:1111111", compartment_id=foreign_org
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:1111111", ontology_id="GO:1111111"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert set(result.candidate_entity_ids) == {same_org, foreign_org}


def test_ontology_id_candidates_spanning_requested_and_global_is_ambiguous_not_conflicted() -> None:
    """Requested organism + a standard/reference (None-organism) row sharing the same
    ontology_id are both compatible -- AMBIGUOUS (never pick one), not CONFLICTED.
    """
    same_org, global_row = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, ontology_id="GO:2222222", compartment_id=same_org),
            _candidate(organism_id=None, ontology_id="GO:2222222", compartment_id=global_row),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:2222222", ontology_id="GO:2222222"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {same_org, global_row}


def test_ontology_id_remains_literal_no_prefix_stripping() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_A, ontology_id="GO:0005829", compartment_id=compartment_id
            ),
        )
    )
    # A bare numeric ID, without the "GO:" prefix, must NOT match the prefixed one.
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="0005829", ontology_id="0005829"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is not NormalizationStatus.MATCHED
    assert result.matched_entity_id is None


# --- Name behavior ----------------------------------------------------------------------


def test_exact_same_organism_name_single_candidate_is_ambiguous_never_matched() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, name="cytosol", compartment_id=compartment_id),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-5", name="cytosol"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (compartment_id,)
    assert result.matched_entity_id is None


def test_same_name_in_another_organism_does_not_silently_match() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, name="cytosol", compartment_id=compartment_a),
            _candidate(organism_id=ORGANISM_B, name="cytosol", compartment_id=compartment_b),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-6", name="cytosol"
    )

    result_a = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)
    result_b = normalize_compartment(identity, organism_id=ORGANISM_B, lookup=lookup)

    assert result_a.candidate_entity_ids == (compartment_a,)
    assert result_b.candidate_entity_ids == (compartment_b,)


def test_same_name_across_multiple_same_organism_rows_is_ambiguous() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, name="cytosol", compartment_id=compartment_a),
            _candidate(organism_id=ORGANISM_A, name="cytosol", compartment_id=compartment_b),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-7", name="cytosol"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {compartment_a, compartment_b}


def test_no_fuzzy_name_matching() -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name="cytosolic region"),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-8", name="cytosol"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_cytosol_does_not_match_cytoplasm() -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name="cytoplasm"),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-9", name="cytosol"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None


def test_mitochondrion_does_not_match_mitochondrial_matrix() -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name="mitochondrial matrix"),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-10", name="mitochondrion"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None


# --- Abbreviation behavior ---------------------------------------------------------------


def test_abbreviation_only_one_candidate_is_ambiguous() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, abbreviation="cyt", compartment_id=compartment_id),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-11", abbreviation="cyt"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.candidate_entity_ids == (compartment_id,)


def test_multiple_abbreviation_candidates_is_ambiguous() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, abbreviation="mito", compartment_id=compartment_a),
            _candidate(organism_id=ORGANISM_A, abbreviation="mito", compartment_id=compartment_b),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-12", abbreviation="mito"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {compartment_a, compartment_b}


def test_abbreviation_collision_prevents_new() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, abbreviation="ER", compartment_id=compartment_id),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:0005783",
        ontology_id="GO:0005783",
        abbreviation="ER",
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.candidate_entity_ids == (compartment_id,)


def test_er_is_not_automatically_rewritten_to_endoplasmic_reticulum() -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name="endoplasmic reticulum"),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-13", abbreviation="ER"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


# --- Cross-signal reconciliation -----------------------------------------------------------


def test_ontology_match_stands_despite_differing_name() -> None:
    """Ontology wins: once MATCHED via ontology_id, a differing supplied name does not
    block MATCHED or turn it into CONFLICTED -- name is not identity-capable here
    (open policy question, documented in the module docstring).
    """
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_A,
                name="cytosol",
                ontology_id="GO:0005829",
                compartment_id=compartment_id,
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:0005829",
        ontology_id="GO:0005829",
        name="cytoplasm",
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compartment_id


def test_unmatched_ontology_id_with_weak_collision_does_not_silently_produce_new() -> None:
    compartment_id = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(organism_id=ORGANISM_A, name="cytosol", compartment_id=compartment_id),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:9999998",
        ontology_id="GO:9999998",
        name="cytosol",
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW
    assert result.candidate_entity_ids == (compartment_id,)


def test_candidate_order_does_not_affect_outcome() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:3333333", ontology_id="GO:3333333"
    )

    forward = normalize_compartment(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeCompartmentLookup(
            compartments=(
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:3333333", compartment_id=compartment_a
                ),
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:3333333", compartment_id=compartment_b
                ),
            )
        ),
    )
    backward = normalize_compartment(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeCompartmentLookup(
            compartments=(
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:3333333", compartment_id=compartment_b
                ),
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:3333333", compartment_id=compartment_a
                ),
            )
        ),
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


# --- Organism scoping -----------------------------------------------------------------------


def test_requested_organism_preserved_in_every_status() -> None:
    empty_lookup = FakeCompartmentLookup()

    new_identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:1", ontology_id="GO:1", name="X"
    )
    unresolved_identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-14", name="X"
    )

    new_result = normalize_compartment(new_identity, organism_id=ORGANISM_B, lookup=empty_lookup)
    unresolved_result = normalize_compartment(
        unresolved_identity, organism_id=ORGANISM_B, lookup=empty_lookup
    )

    assert new_result.status is NormalizationStatus.NEW
    assert new_result.organism_id == ORGANISM_B
    assert unresolved_result.status is NormalizationStatus.UNRESOLVED
    assert unresolved_result.organism_id == ORGANISM_B


def test_candidate_from_wrong_organism_never_silently_matches_via_weak_path() -> None:
    foreign = _candidate(organism_id=ORGANISM_B, name="cytosol")
    lookup = FakeCompartmentLookup(compartments=(foreign,))
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-15", name="cytosol"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None


def test_cross_organism_leakage_from_name_lookup_is_rejected() -> None:
    other_org_compartment = _candidate(organism_id=ORGANISM_B, name="cytosol")
    lookup = LeakyNameCompartmentLookup(compartments=[other_org_compartment])
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-16", name="cytosol"
    )

    with pytest.raises(ValueError, match="CompartmentLookup returned candidate"):
        normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)


def test_cross_organism_behavior_is_deterministic() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_A, ontology_id="GO:4444444", compartment_id=compartment_a
            ),
            _candidate(
                organism_id=ORGANISM_B, ontology_id="GO:4444444", compartment_id=compartment_b
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:4444444", ontology_id="GO:4444444"
    )

    first = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)
    second = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert first.status == second.status == NormalizationStatus.CONFLICTED
    assert first.candidate_entity_ids == second.candidate_entity_ids


# --- Semantic safety -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("incoming_name", "existing_name"),
    [
        ("mitochondrial matrix", "mitochondrial intermembrane space"),
        ("mitochondrial inner membrane", "mitochondrial outer membrane"),
        ("cytosol", "extracellular"),
        ("endoplasmic reticulum", "Golgi"),
        ("peroxisome", "mitochondrion"),
    ],
)
def test_semantically_related_compartments_are_never_automatically_merged(
    incoming_name: str, existing_name: str
) -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name=existing_name),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-17", name=incoming_name
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None


# --- NEW / UNRESOLVED -------------------------------------------------------------------------


def test_no_collisions_creation_complete_identity_is_new() -> None:
    lookup = FakeCompartmentLookup()
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:5555555",
        ontology_id="GO:5555555",
        name="vacuole",
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


def test_missing_required_name_is_unresolved() -> None:
    lookup = FakeCompartmentLookup()
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:5555556", ontology_id="GO:5555556"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_weak_only_name_is_unresolved_never_new() -> None:
    lookup = FakeCompartmentLookup()
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-18", name="vacuole"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_foreign_strong_id_collision_never_becomes_new() -> None:
    foreign_compartment = uuid4()
    lookup = FakeCompartmentLookup(
        compartments=(
            _candidate(
                organism_id=ORGANISM_B,
                ontology_id="GO:6666666",
                name="vacuole",
                compartment_id=foreign_compartment,
            ),
        )
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:6666666",
        ontology_id="GO:6666666",
        name="vacuole",
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW


def test_no_synthetic_name_generation_from_ontology_id_alone() -> None:
    lookup = FakeCompartmentLookup()
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:7777777", ontology_id="GO:7777777"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_global_scope_request_can_become_new() -> None:
    """organism_id=None is a legitimate scope, not 'insufficient' -- NEW is reachable there too."""
    lookup = FakeCompartmentLookup()
    identity = CompartmentIdentity(
        source=SourceType.OTHER,
        source_identifier="GO:8888888",
        ontology_id="GO:8888888",
        name="cytosol",
    )

    result = normalize_compartment(identity, organism_id=None, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.organism_id is None


# --- Determinism -------------------------------------------------------------------------------


def test_duplicate_lookup_rows_are_deduplicated() -> None:
    compartment_id = uuid4()
    candidate = _candidate(
        organism_id=ORGANISM_A, ontology_id="GO:9990001", compartment_id=compartment_id
    )
    lookup = FakeCompartmentLookup(compartments=(candidate, candidate))
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="GO:9990001", ontology_id="GO:9990001"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == compartment_id


def test_conflicting_candidate_id_order_is_deterministic() -> None:
    compartment_a, compartment_b = uuid4(), uuid4()

    forward = normalize_compartment(
        CompartmentIdentity(
            source=SourceType.OTHER, source_identifier="GO:9990002", ontology_id="GO:9990002"
        ),
        organism_id=ORGANISM_A,
        lookup=FakeCompartmentLookup(
            compartments=(
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:9990002", compartment_id=compartment_a
                ),
                _candidate(
                    organism_id=ORGANISM_B, ontology_id="GO:9990002", compartment_id=compartment_b
                ),
            )
        ),
    )
    backward = normalize_compartment(
        CompartmentIdentity(
            source=SourceType.OTHER, source_identifier="GO:9990002", ontology_id="GO:9990002"
        ),
        organism_id=ORGANISM_A,
        lookup=FakeCompartmentLookup(
            compartments=(
                _candidate(
                    organism_id=ORGANISM_B, ontology_id="GO:9990002", compartment_id=compartment_b
                ),
                _candidate(
                    organism_id=ORGANISM_A, ontology_id="GO:9990002", compartment_id=compartment_a
                ),
            )
        ),
    )

    assert (
        forward.candidate_entity_ids
        == backward.candidate_entity_ids
        == tuple(sorted((compartment_a, compartment_b)))
    )


# --- Safety --------------------------------------------------------------------------------------


def test_no_case_folding_of_name() -> None:
    lookup = FakeCompartmentLookup(compartments=(_candidate(organism_id=ORGANISM_A, name="Golgi"),))
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-19", name="golgi"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_no_abbreviation_expansion_prevents_spurious_match() -> None:
    lookup = FakeCompartmentLookup(
        compartments=(_candidate(organism_id=ORGANISM_A, name="mitochondrion"),)
    )
    identity = CompartmentIdentity(
        source=SourceType.OTHER, source_identifier="req-20", abbreviation="mito"
    )

    result = normalize_compartment(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
