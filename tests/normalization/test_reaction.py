"""Tests for reaction identity normalization (Phase 4, Increment 8).

Pure unit tests: no database, no HTTP, no live KEGG access.
``FakeReactionLookup`` is an in-memory, read-only stand-in for
``app.normalization.reaction.ReactionLookup`` -- there is no SQLAlchemy
adapter in this increment, consistent with ``app.normalization.reaction``'s
own module docstring. It mirrors the real lookup contract exactly:
``by_kegg_reaction_id``/``by_metacyc_reaction_id``/``by_rhea_id`` search
**globally**, while ``by_name`` is **organism-scoped**. There is no
structural/participant lookup method at all -- this repository has no such
lookup capability, a deliberate, reported limitation (see the module
docstring and this increment's completion report), not an oversight in the
tests. Every test uses one of two fixed, distinct organism UUIDs so no test
can pass by accidentally assuming a single/default/global organism.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.connectors.kegg import KeggFlatFileRecord, KeggReactionRecord
from app.models.enums import ReactionParticipantRole, SourceType
from app.normalization.reaction import (
    ReactionCandidate,
    ReactionIdentity,
    ReactionLookup,
    ReactionParticipantIdentity,
    normalize_reaction,
    participants_structurally_equal,
    reaction_identity_from_kegg,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit

ORGANISM_A = UUID("11111111-1111-1111-1111-111111111111")
ORGANISM_B = UUID("22222222-2222-2222-2222-222222222222")

COMPOUND_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
COMPOUND_B = UUID("aaaaaaaa-0000-0000-0000-000000000002")
COMPOUND_C = UUID("aaaaaaaa-0000-0000-0000-000000000003")
COMPARTMENT_CYTOSOL = UUID("cccccccc-0000-0000-0000-000000000001")
COMPARTMENT_MITO = UUID("cccccccc-0000-0000-0000-000000000002")


@dataclass(frozen=True, slots=True)
class FakeReactionLookup:
    """In-memory ``ReactionLookup``: global external-ID lookup, organism-scoped name lookup."""

    reactions: Sequence[ReactionCandidate] = ()

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.kegg_reaction_id == kegg_reaction_id]

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.metacyc_reaction_id == metacyc_reaction_id]

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.rhea_id == rhea_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.organism_id == organism_id and r.name == name]


class LeakyNameReactionLookup:
    """A ``ReactionLookup`` whose ``by_name`` ignores organism scope entirely.

    Used only to prove ``normalize_reaction`` rejects cross-organism leakage
    from ``by_name``, which is contractually required to filter by
    ``organism_id`` itself. Global methods are legitimately unscoped, so
    they are implemented correctly here -- only ``by_name`` is broken.
    """

    def __init__(self, reactions: Sequence[ReactionCandidate]) -> None:
        self.reactions = reactions

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.kegg_reaction_id == kegg_reaction_id]

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.metacyc_reaction_id == metacyc_reaction_id]

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.rhea_id == rhea_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.name == name]


def _participant(
    *,
    compound_id: UUID = COMPOUND_A,
    role: ReactionParticipantRole = ReactionParticipantRole.REACTANT,
    stoichiometry: Decimal = Decimal("1"),
    compartment_id: UUID | None = COMPARTMENT_CYTOSOL,
) -> ReactionParticipantIdentity:
    return ReactionParticipantIdentity(
        compound_id=compound_id,
        role=role,
        stoichiometry=stoichiometry,
        compartment_id=compartment_id,
    )


def _candidate(
    *,
    organism_id: UUID,
    internal_id: str = "FFA_R0001",
    name: str = "Test Reaction",
    kegg_reaction_id: str | None = None,
    metacyc_reaction_id: str | None = None,
    rhea_id: str | None = None,
    reversible: bool | None = None,
    reaction_type: str | None = None,
    ec_number: str | None = None,
    participants: tuple[ReactionParticipantIdentity, ...] = (),
    reaction_id: UUID | None = None,
) -> ReactionCandidate:
    return ReactionCandidate(
        id=reaction_id or uuid4(),
        organism_id=organism_id,
        internal_id=internal_id,
        name=name,
        kegg_reaction_id=kegg_reaction_id,
        metacyc_reaction_id=metacyc_reaction_id,
        rhea_id=rhea_id,
        reversible=reversible,
        reaction_type=reaction_type,
        ec_number=ec_number,
        participants=participants,
    )


def _kegg_reaction_record(
    *,
    entry_id: str = "R00299",
    names: tuple[str, ...] = ("hexokinase reaction",),
    equation: str | None = "C00031 + C00002 <=> C00092 + C00008",
    enzymes: tuple[str, ...] = ("2.7.1.1",),
) -> KeggReactionRecord:
    raw = KeggFlatFileRecord(entry_id=entry_id, entry_type="Reaction", fields={})
    return KeggReactionRecord(
        entry_id=entry_id,
        names=names,
        definition=None,
        equation=equation,
        enzymes=enzymes,
        pathways=(),
        raw=raw,
    )


# --- ReactionParticipantIdentity construction / validation ------------------------


def test_participant_requires_compound_id() -> None:
    with pytest.raises(ValueError, match="requires compound_id"):
        ReactionParticipantIdentity(
            compound_id=None,  # type: ignore[arg-type]
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=Decimal("1"),
        )


def test_participant_stoichiometry_must_be_positive() -> None:
    with pytest.raises(ValueError, match="stoichiometry must be positive"):
        ReactionParticipantIdentity(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=Decimal("0"),
        )


def test_participant_stoichiometry_rejects_negative() -> None:
    with pytest.raises(ValueError, match="stoichiometry must be positive"):
        ReactionParticipantIdentity(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.REACTANT,
            stoichiometry=Decimal("-1"),
        )


def test_participant_rejects_invalid_role() -> None:
    with pytest.raises(ValueError, match="must be a ReactionParticipantRole"):
        ReactionParticipantIdentity(
            compound_id=COMPOUND_A,
            role="REACTANT",
            stoichiometry=Decimal("1"),  # type: ignore[arg-type]
        )


def test_participant_compartment_may_be_none() -> None:
    participant = ReactionParticipantIdentity(
        compound_id=COMPOUND_A,
        role=ReactionParticipantRole.REACTANT,
        stoichiometry=Decimal("1"),
        compartment_id=None,
    )
    assert participant.compartment_id is None


def test_participant_supports_modifier_role() -> None:
    participant = ReactionParticipantIdentity(
        compound_id=COMPOUND_A, role=ReactionParticipantRole.MODIFIER, stoichiometry=Decimal("1")
    )
    assert participant.role is ReactionParticipantRole.MODIFIER


# --- ReactionIdentity construction / validation --------------------------------------


def test_reaction_identity_requires_at_least_one_identity_signal() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ReactionIdentity(source=SourceType.KEGG, source_identifier="req-1")


def test_reaction_identity_ec_reaction_type_reversible_alone_are_insufficient() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ReactionIdentity(
            source=SourceType.OTHER,
            source_identifier="req-2",
            ec_number="1.1.1.1",
            reaction_type="BIOCHEMICAL",
            reversible=True,
        )


def test_reaction_identity_participants_alone_is_sufficient_signal() -> None:
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-3", participants=(_participant(),)
    )
    assert identity.participants


def test_reaction_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        ReactionIdentity(source=SourceType.KEGG, source_identifier="   ", name="X")


def test_reaction_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="  R00299  ",
        name="   ",
    )
    assert identity.kegg_reaction_id == "R00299"
    assert identity.name is None


def test_reaction_identity_has_no_internal_id_field() -> None:
    assert "internal_id" not in inspect.signature(ReactionIdentity).parameters


# --- Lookup API shape ------------------------------------------------------------------


def test_reaction_lookup_has_no_ec_number_method() -> None:
    assert not hasattr(ReactionLookup, "by_ec_number")


def test_reaction_lookup_has_no_reaction_type_method() -> None:
    assert not hasattr(ReactionLookup, "by_reaction_type")


def test_reaction_lookup_has_no_equation_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(ReactionLookup, inspect.isfunction)}
    assert not any("equation" in name for name in method_names)


def test_reaction_lookup_has_no_participant_count_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(ReactionLookup, inspect.isfunction)}
    assert not any("participant" in name or "structure" in name for name in method_names)


def test_reaction_lookup_has_no_enzyme_or_pathway_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(ReactionLookup, inspect.isfunction)}
    assert not any("enzyme" in name or "pathway" in name for name in method_names)


def test_reaction_lookup_has_no_fuzzy_name_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(ReactionLookup, inspect.isfunction)}
    assert not any("fuzzy" in name for name in method_names)


def test_external_id_lookups_do_not_accept_organism_id() -> None:
    for name in ("by_kegg_reaction_id", "by_metacyc_reaction_id", "by_rhea_id"):
        params = list(inspect.signature(getattr(ReactionLookup, name)).parameters)
        assert "organism_id" not in params


def test_name_lookup_requires_organism_id_first() -> None:
    params = list(inspect.signature(ReactionLookup.by_name).parameters)
    assert params[1] == "organism_id"


def test_normalize_reaction_organism_id_is_keyword_only_with_no_default() -> None:
    parameters = inspect.signature(normalize_reaction).parameters
    assert parameters["organism_id"].default is inspect.Parameter.empty
    assert parameters["organism_id"].kind is inspect.Parameter.KEYWORD_ONLY


# --- Exact strong-ID matching ----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kegg_reaction_id", "R00299"),
        ("metacyc_reaction_id", "HEXOKINASE-RXN"),
        ("rhea_id", "12420"),
    ],
)
def test_exact_strong_id_single_candidate_in_requested_organism_matched(
    field: str, value: str
) -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(_candidate(organism_id=ORGANISM_A, reaction_id=reaction_id, **{field: value}),)
    )
    identity = ReactionIdentity(source=SourceType.KEGG, source_identifier=value, **{field: value})

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == reaction_id
    assert result.organism_id == ORGANISM_A


@pytest.mark.parametrize("field", ["kegg_reaction_id", "metacyc_reaction_id", "rhea_id"])
def test_multiple_rows_for_one_external_id_is_ambiguous(field: str) -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, reaction_id=reaction_a, **{field: "DUPID"}),
            _candidate(organism_id=ORGANISM_A, reaction_id=reaction_b, **{field: "DUPID"}),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="DUPID", **{field: "DUPID"}
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {reaction_a, reaction_b}


def test_foreign_organism_candidate_is_conflicted_never_new() -> None:
    foreign = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_B, kegg_reaction_id="R00299", reaction_id=foreign),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="R00299", kegg_reaction_id="R00299"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert foreign in result.candidate_entity_ids
    assert result.organism_id == ORGANISM_A


# --- Cross-identifier conflict --------------------------------------------------------


def test_rhea_resolves_a_kegg_resolves_b_is_conflicted() -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, rhea_id="12420", reaction_id=reaction_a),
            _candidate(organism_id=ORGANISM_A, kegg_reaction_id="R00299", reaction_id=reaction_b),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        rhea_id="12420",
        kegg_reaction_id="R00299",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {reaction_a, reaction_b}


def test_kegg_resolves_a_metacyc_resolves_b_is_conflicted() -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, kegg_reaction_id="R00299", reaction_id=reaction_a),
            _candidate(
                organism_id=ORGANISM_A, metacyc_reaction_id="HEXOKINASE-RXN", reaction_id=reaction_b
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        metacyc_reaction_id="HEXOKINASE-RXN",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert set(result.candidate_entity_ids) == {reaction_a, reaction_b}


# --- Participant canonicalization ---------------------------------------------------


def test_structural_equality_ignores_participant_order() -> None:
    left = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    right = (
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
    )
    assert participants_structurally_equal(left, right)


def test_same_compound_different_roles_remain_distinct() -> None:
    left = (_participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),)
    right = (_participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.PRODUCT),)
    assert not participants_structurally_equal(left, right)


def test_same_compound_different_compartments_remain_distinct() -> None:
    left = (_participant(compound_id=COMPOUND_A, compartment_id=COMPARTMENT_CYTOSOL),)
    right = (_participant(compound_id=COMPOUND_A, compartment_id=COMPARTMENT_MITO),)
    assert not participants_structurally_equal(left, right)


def test_exact_stoichiometry_preserved_in_signature() -> None:
    left = (_participant(stoichiometry=Decimal("1")),)
    right = (_participant(stoichiometry=Decimal("2")),)
    assert not participants_structurally_equal(left, right)


def test_modifier_role_preserved_in_signature() -> None:
    left = (_participant(role=ReactionParticipantRole.MODIFIER),)
    right = (_participant(role=ReactionParticipantRole.REACTANT),)
    assert not participants_structurally_equal(left, right)


def test_duplicate_identical_participants_are_preserved_not_collapsed() -> None:
    """No aggregation policy is documented -- duplicates are preserved exactly,
    so two duplicate rows differ structurally from a single row.
    """
    single = (_participant(compound_id=COMPOUND_A),)
    duplicated = (_participant(compound_id=COMPOUND_A), _participant(compound_id=COMPOUND_A))
    assert not participants_structurally_equal(single, duplicated)


# --- Structural equality (direct) -----------------------------------------------------


def test_identical_participant_sets_are_structurally_equal() -> None:
    participants = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    assert participants_structurally_equal(participants, participants)


def test_different_compound_is_structurally_unequal() -> None:
    left = (_participant(compound_id=COMPOUND_A),)
    right = (_participant(compound_id=COMPOUND_C),)
    assert not participants_structurally_equal(left, right)


# --- Direction safety -------------------------------------------------------------------


def test_forward_direction_does_not_equal_reverse_direction() -> None:
    forward = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    reverse = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.PRODUCT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.REACTANT),
    )
    assert not participants_structurally_equal(forward, reverse)


def test_reversible_flag_does_not_affect_structural_comparison() -> None:
    """reversible is never consulted by participants_structurally_equal at all --
    it isn't even a parameter.
    """
    assert "reversible" not in inspect.signature(participants_structurally_equal).parameters


def test_reaction_identity_preserves_null_reversibility() -> None:
    identity = ReactionIdentity(source=SourceType.OTHER, source_identifier="req-4", name="X")
    assert identity.reversible is None


def test_reversible_true_candidate_does_not_get_participants_silently_reversed() -> None:
    reaction_id = uuid4()
    forward_participants = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                reversible=True,
                participants=forward_participants,
                reaction_id=reaction_id,
            ),
        )
    )
    reversed_participants = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.PRODUCT),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.REACTANT),
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=reversed_participants,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == reaction_id


# --- Compartment safety ----------------------------------------------------------------


def test_transport_reaction_differs_from_same_compartment_reaction() -> None:
    transport = (
        _participant(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.REACTANT,
            compartment_id=COMPARTMENT_CYTOSOL,
        ),
        _participant(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.PRODUCT,
            compartment_id=COMPARTMENT_MITO,
        ),
    )
    same_compartment = (
        _participant(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.REACTANT,
            compartment_id=COMPARTMENT_CYTOSOL,
        ),
        _participant(
            compound_id=COMPOUND_A,
            role=ReactionParticipantRole.PRODUCT,
            compartment_id=COMPARTMENT_CYTOSOL,
        ),
    )
    assert not participants_structurally_equal(transport, same_compartment)


def test_missing_compartment_is_not_silently_filled() -> None:
    with_compartment = (_participant(compound_id=COMPOUND_A, compartment_id=COMPARTMENT_CYTOSOL),)
    without_compartment = (_participant(compound_id=COMPOUND_A, compartment_id=None),)
    assert not participants_structurally_equal(with_compartment, without_compartment)


# --- Strong-ID + structural agreement/disagreement ------------------------------------


def test_external_id_candidate_with_identical_structure_matched() -> None:
    reaction_id = uuid4()
    participants = (
        _participant(compound_id=COMPOUND_A),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                participants=participants,
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=participants,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == reaction_id


def test_same_external_id_different_compound_participants_is_conflicted() -> None:
    reaction_id = uuid4()
    existing = (_participant(compound_id=COMPOUND_A),)
    incoming = (_participant(compound_id=COMPOUND_C),)
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                participants=existing,
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=incoming,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == reaction_id


def test_same_external_id_different_stoichiometry_is_conflicted() -> None:
    reaction_id = uuid4()
    existing = (_participant(compound_id=COMPOUND_A, stoichiometry=Decimal("1")),)
    incoming = (_participant(compound_id=COMPOUND_A, stoichiometry=Decimal("2")),)
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                participants=existing,
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=incoming,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED


def test_same_external_id_different_compartment_is_conflicted() -> None:
    reaction_id = uuid4()
    existing = (_participant(compound_id=COMPOUND_A, compartment_id=COMPARTMENT_CYTOSOL),)
    incoming = (_participant(compound_id=COMPOUND_A, compartment_id=COMPARTMENT_MITO),)
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                participants=existing,
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=incoming,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED


def test_missing_candidate_participants_is_compatible_not_conflict() -> None:
    """Existing candidate has no recorded structure -- supplying one is compatible metadata,
    not a contradiction.
    """
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, kegg_reaction_id="R00299", reaction_id=reaction_id),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        participants=(_participant(),),
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == reaction_id


# --- Structure-only candidate behavior (deferred capability) ---------------------------


def test_structure_only_duplicate_is_currently_undetectable_new_not_ambiguous() -> None:
    """Documented, deliberate limitation: with no structural lookup API, this module
    cannot discover a reaction sharing identical structure but no external ID/name
    match. This test proves and records that gap rather than pretending it is handled.
    """
    participants = (_participant(compound_id=COMPOUND_A),)
    # An existing reaction with identical structure but a different external ID and name.
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R99999",
                name="Some Other Reaction",
                participants=participants,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        name="A Brand New Reaction",
        participants=participants,
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW


# --- Name behavior --------------------------------------------------------------------


def test_one_exact_name_candidate_is_ambiguous_never_matched() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, name="Hexokinase Reaction", reaction_id=reaction_id),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-5", name="Hexokinase Reaction"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.CANDIDATE_SYNONYM
    assert result.candidate_entity_ids == (reaction_id,)


def test_multiple_name_candidates_is_ambiguous() -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, name="Hexokinase Reaction", reaction_id=reaction_a),
            _candidate(organism_id=ORGANISM_A, name="Hexokinase Reaction", reaction_id=reaction_b),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-6", name="Hexokinase Reaction"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert set(result.candidate_entity_ids) == {reaction_a, reaction_b}


def test_name_collision_blocks_new() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(organism_id=ORGANISM_A, name="Hexokinase Reaction", reaction_id=reaction_id),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R09999",
        kegg_reaction_id="R09999",
        name="Hexokinase Reaction",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW


def test_no_fuzzy_name_matching() -> None:
    lookup = FakeReactionLookup(
        reactions=(_candidate(organism_id=ORGANISM_A, name="Hexokinase Reaction I"),)
    )
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-7", name="Hexokinase Reaction"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


def test_cross_organism_leakage_from_name_lookup_is_rejected() -> None:
    other_org = _candidate(organism_id=ORGANISM_B, name="Hexokinase Reaction")
    lookup = LeakyNameReactionLookup(reactions=[other_org])
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-8", name="Hexokinase Reaction"
    )

    with pytest.raises(ValueError, match="ReactionLookup returned candidate"):
        normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)


# --- EC safety ---------------------------------------------------------------------------


def test_ec_number_alone_cannot_construct_an_identity() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ReactionIdentity(source=SourceType.OTHER, source_identifier="req-9", ec_number="2.7.1.1")


def test_reactions_may_share_ec_number_without_being_considered_same() -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                ec_number="2.7.1.1",
                reaction_id=reaction_a,
            ),
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00300",
                ec_number="2.7.1.1",
                reaction_id=reaction_b,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="R00299", kegg_reaction_id="R00299"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == reaction_a


def test_ec_disagreement_on_strong_id_match_is_inert() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                ec_number="2.7.1.1",
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        ec_number="9.9.9.9",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == reaction_id


# --- Reaction-type safety ------------------------------------------------------------


def test_reaction_type_alone_cannot_construct_an_identity() -> None:
    with pytest.raises(ValueError, match="requires at least one identity signal"):
        ReactionIdentity(
            source=SourceType.OTHER, source_identifier="req-10", reaction_type="TRANSPORT"
        )


def test_reaction_type_disagreement_on_strong_id_match_is_inert() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A,
                kegg_reaction_id="R00299",
                reaction_type="BIOCHEMICAL",
                reaction_id=reaction_id,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R00299",
        kegg_reaction_id="R00299",
        reaction_type="TRANSPORT",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED


# --- Generic compound safety --------------------------------------------------------------


def test_reaction_using_generic_compound_id_does_not_equal_specific_compound_id() -> None:
    """compound_id is opaque here -- a 'generic fatty acid' UUID and a 'palmitate' UUID
    are simply different UUIDs, never treated as related.
    """
    generic_fatty_acid = uuid4()
    palmitate = uuid4()
    left = (_participant(compound_id=generic_fatty_acid),)
    right = (_participant(compound_id=palmitate),)
    assert not participants_structurally_equal(left, right)


# --- Proton/water safety ------------------------------------------------------------------


def test_reaction_with_explicit_proton_differs_structurally_from_one_without() -> None:
    proton = uuid4()
    with_proton = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=proton, role=ReactionParticipantRole.PRODUCT),
    )
    without_proton = (_participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),)
    assert not participants_structurally_equal(with_proton, without_proton)


def test_reaction_with_explicit_water_differs_structurally_from_one_without() -> None:
    water = uuid4()
    with_water = (
        _participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),
        _participant(compound_id=water, role=ReactionParticipantRole.REACTANT),
    )
    without_water = (_participant(compound_id=COMPOUND_A, role=ReactionParticipantRole.REACTANT),)
    assert not participants_structurally_equal(with_water, without_water)


# --- NEW / UNRESOLVED ------------------------------------------------------------------


def test_unmatched_strong_id_creation_complete_no_collisions_is_new() -> None:
    lookup = FakeReactionLookup()
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R09999",
        kegg_reaction_id="R09999",
        name="A Brand New Reaction",
        participants=(_participant(),),
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id == ORGANISM_A


def test_name_only_is_not_new() -> None:
    lookup = FakeReactionLookup()
    identity = ReactionIdentity(
        source=SourceType.OTHER, source_identifier="req-11", name="A Brand New Reaction"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_missing_required_name_is_unresolved() -> None:
    lookup = FakeReactionLookup()
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="R09999", kegg_reaction_id="R09999"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


def test_structural_collision_via_name_is_not_new() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_A, name="A Brand New Reaction", reaction_id=reaction_id
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R09999",
        kegg_reaction_id="R09999",
        name="A Brand New Reaction",
        participants=(_participant(),),
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.status is not NormalizationStatus.NEW


def test_foreign_strong_id_conflict_never_becomes_new() -> None:
    foreign = uuid4()
    lookup = FakeReactionLookup(
        reactions=(
            _candidate(
                organism_id=ORGANISM_B,
                kegg_reaction_id="R09999",
                name="A Brand New Reaction",
                reaction_id=foreign,
            ),
        )
    )
    identity = ReactionIdentity(
        source=SourceType.KEGG,
        source_identifier="R09999",
        kegg_reaction_id="R09999",
        name="A Brand New Reaction",
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.status is not NormalizationStatus.NEW


# --- Determinism ------------------------------------------------------------------------


def test_external_candidate_order_does_not_affect_status() -> None:
    reaction_a, reaction_b = uuid4(), uuid4()
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="DUPID", kegg_reaction_id="DUPID"
    )

    forward = normalize_reaction(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeReactionLookup(
            reactions=(
                _candidate(
                    organism_id=ORGANISM_A, kegg_reaction_id="DUPID", reaction_id=reaction_a
                ),
                _candidate(
                    organism_id=ORGANISM_A, kegg_reaction_id="DUPID", reaction_id=reaction_b
                ),
            )
        ),
    )
    backward = normalize_reaction(
        identity,
        organism_id=ORGANISM_A,
        lookup=FakeReactionLookup(
            reactions=(
                _candidate(
                    organism_id=ORGANISM_A, kegg_reaction_id="DUPID", reaction_id=reaction_b
                ),
                _candidate(
                    organism_id=ORGANISM_A, kegg_reaction_id="DUPID", reaction_id=reaction_a
                ),
            )
        ),
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_participant_order_does_not_affect_structure_signature() -> None:
    participants_forward = (
        _participant(compound_id=COMPOUND_A),
        _participant(compound_id=COMPOUND_B, role=ReactionParticipantRole.PRODUCT),
    )
    participants_backward = tuple(reversed(participants_forward))
    assert participants_structurally_equal(participants_forward, participants_backward)


def test_duplicate_candidate_rows_are_deduplicated() -> None:
    reaction_id = uuid4()
    candidate = _candidate(
        organism_id=ORGANISM_A, kegg_reaction_id="R00299", reaction_id=reaction_id
    )
    lookup = FakeReactionLookup(reactions=(candidate, candidate))
    identity = ReactionIdentity(
        source=SourceType.KEGG, source_identifier="R00299", kegg_reaction_id="R00299"
    )

    result = normalize_reaction(identity, organism_id=ORGANISM_A, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == reaction_id


# --- KEGG conversion helper ----------------------------------------------------------------


def test_reaction_identity_from_kegg_preserves_kegg_reaction_id() -> None:
    record = _kegg_reaction_record(entry_id="R00299")

    identity = reaction_identity_from_kegg(record)

    assert identity.source is SourceType.KEGG
    assert identity.source_identifier == "R00299"
    assert identity.kegg_reaction_id == "R00299"


def test_reaction_identity_from_kegg_maps_first_name() -> None:
    record = _kegg_reaction_record(names=("hexokinase reaction", "glucose phosphorylation"))

    identity = reaction_identity_from_kegg(record)

    assert identity.name == "hexokinase reaction"


def test_reaction_identity_from_kegg_maps_first_ec_number() -> None:
    record = _kegg_reaction_record(enzymes=("2.7.1.1", "2.7.1.2"))

    identity = reaction_identity_from_kegg(record)

    assert identity.ec_number == "2.7.1.1"


def test_reaction_identity_from_kegg_never_parses_equation_into_participants() -> None:
    record = _kegg_reaction_record(equation="C00031 + C00002 <=> C00092 + C00008")

    identity = reaction_identity_from_kegg(record)

    assert identity.participants == ()


def test_reaction_identity_from_kegg_handles_empty_names_and_enzymes() -> None:
    record = _kegg_reaction_record(names=(), enzymes=())

    identity = reaction_identity_from_kegg(record)

    assert identity.name is None
    assert identity.ec_number is None
    assert identity.kegg_reaction_id == record.entry_id


def test_reaction_identity_from_kegg_does_not_mutate_original_record() -> None:
    record = _kegg_reaction_record(entry_id="R00299")

    reaction_identity_from_kegg(record)

    assert record.entry_id == "R00299"


# --- Safety -------------------------------------------------------------------------------


def test_no_proportional_stoichiometry_reduction() -> None:
    """A + B -> C is not automatically the same as 2A + 2B -> 2C."""
    single = (
        _participant(compound_id=COMPOUND_A, stoichiometry=Decimal("1")),
        _participant(compound_id=COMPOUND_B, stoichiometry=Decimal("1")),
    )
    doubled = (
        _participant(compound_id=COMPOUND_A, stoichiometry=Decimal("2")),
        _participant(compound_id=COMPOUND_B, stoichiometry=Decimal("2")),
    )
    assert not participants_structurally_equal(single, doubled)
