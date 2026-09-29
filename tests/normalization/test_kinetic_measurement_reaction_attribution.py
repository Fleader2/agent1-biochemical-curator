"""Tests for Evidence-Based Kinetic Measurement -> Reaction Attribution.

Pure functions and dataclasses only, no database, no HTTP -- a fake, in-memory
``KineticMeasurementReactionAttributionLookup`` stands in for
``app.pathway_curation.lookups.SqlAlchemyKineticMeasurementReactionAttributionLookup``,
exactly mirroring every other normalization module's own test convention
(``tests/normalization/test_reaction.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.enums import ReactionParticipantRole
from app.normalization.kinetic_measurement import KineticParameterType
from app.normalization.kinetic_measurement_reaction_attribution import (
    KineticMeasurementAttributionContext,
    KineticMeasurementReactionAttributionReason,
    attribute_kinetic_measurement_to_reaction,
    sabiork_signature_role,
)
from app.normalization.reaction import ReactionCandidate, ReactionParticipantIdentity

pytestmark = pytest.mark.unit


@dataclass
class FakeAttributionLookup:
    """In-memory fake implementing ``KineticMeasurementReactionAttributionLookup``."""

    by_kegg: dict[str, list[ReactionCandidate]] = field(default_factory=dict)
    by_metacyc: dict[str, list[ReactionCandidate]] = field(default_factory=dict)
    by_rhea: dict[str, list[ReactionCandidate]] = field(default_factory=dict)
    catalyzed_by_protein: dict[UUID, list[ReactionCandidate]] = field(default_factory=dict)
    catalyzed_by_complex: dict[UUID, list[ReactionCandidate]] = field(default_factory=dict)
    catalyzed_by_enzyme_state: dict[UUID, list[ReactionCandidate]] = field(default_factory=dict)

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        return self.by_kegg.get(kegg_reaction_id, [])

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        return self.by_metacyc.get(metacyc_reaction_id, [])

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        return self.by_rhea.get(rhea_id, [])

    def reactions_catalyzed_by_protein(self, protein_id: UUID) -> Sequence[ReactionCandidate]:
        return self.catalyzed_by_protein.get(protein_id, [])

    def reactions_catalyzed_by_complex(self, complex_id: UUID) -> Sequence[ReactionCandidate]:
        return self.catalyzed_by_complex.get(complex_id, [])

    def reactions_catalyzed_by_enzyme_state(
        self, enzyme_state_id: UUID
    ) -> Sequence[ReactionCandidate]:
        return self.catalyzed_by_enzyme_state.get(enzyme_state_id, [])


def _candidate(**overrides) -> ReactionCandidate:
    merged = {
        "id": uuid4(),
        "organism_id": None,
        "internal_id": "R-internal",
        "name": "test reaction",
    } | overrides
    return ReactionCandidate(**merged)


def _participant(**overrides) -> ReactionParticipantIdentity:
    merged = {
        "compound_id": uuid4(),
        "role": ReactionParticipantRole.REACTANT,
        "stoichiometry": Decimal("1"),
    } | overrides
    return ReactionParticipantIdentity(**merged)


def _context(**overrides) -> KineticMeasurementAttributionContext:
    merged = {"parameter_type": KineticParameterType.KM} | overrides
    return KineticMeasurementAttributionContext(**merged)


# --- Tier 1: direct reaction identifier --------------------------------------------------


def test_direct_kegg_reaction_id_matches_exactly_one_reaction():
    reaction = _candidate()
    lookup = FakeAttributionLookup(by_kegg={"R00742": [reaction]})
    context = _context(parameter_type=KineticParameterType.KCAT, kegg_reaction_id="R00742")

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == reaction.id
    assert result.reason is KineticMeasurementReactionAttributionReason.DIRECT_REACTION_IDENTIFIER


def test_direct_identifier_matching_two_reactions_is_immediately_ambiguous():
    """A direct-identifier ambiguity is never rescued by catalyst-based narrowing --
    it is a final, non-fallthrough result (module docstring, tier 1)."""
    r1, r2 = _candidate(), _candidate()
    protein_id = uuid4()
    lookup = FakeAttributionLookup(
        by_kegg={"R00742": [r1, r2]},
        catalyzed_by_protein={protein_id: [r1]},  # would otherwise uniquely resolve
    )
    context = _context(
        parameter_type=KineticParameterType.KCAT,
        kegg_reaction_id="R00742",
        protein_ids=(protein_id,),
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS
    assert set(result.candidate_reaction_ids) == {r1.id, r2.id}


def test_direct_identifier_no_match_falls_through_to_catalyst_tier():
    reaction = _candidate()
    protein_id = uuid4()
    lookup = FakeAttributionLookup(
        by_kegg={},  # no reaction has this kegg_reaction_id (not yet curated)
        catalyzed_by_protein={protein_id: [reaction]},
    )
    context = _context(
        parameter_type=KineticParameterType.KCAT,
        kegg_reaction_id="R99999",
        protein_ids=(protein_id,),
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == reaction.id
    assert result.reason is KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE


# --- Tier 2: catalyst alone, then catalyst + compound ------------------------------------


def test_catalyst_alone_unique_needs_no_compound_anchor():
    """kcat is not compound-anchor-eligible, but a catalyst curated as catalyzing exactly
    one reaction is sufficient on its own -- the task's own explicit parameter-type
    policy (catalyst identity is always usable, compound anchoring is Km/Ki-only)."""
    reaction = _candidate()
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [reaction]})
    context = _context(parameter_type=KineticParameterType.KCAT, protein_ids=(protein_id,))

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == reaction.id
    assert result.reason is KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE


def test_ambiguous_catalyst_with_two_reactions_and_no_other_evidence():
    r1, r2 = _candidate(), _candidate()
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(parameter_type=KineticParameterType.KCAT, protein_ids=(protein_id,))

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS
    assert set(result.candidate_reaction_ids) == {r1.id, r2.id}


def test_km_compound_anchor_narrows_ambiguous_catalyst_to_one():
    substrate_id = uuid4()
    r1 = _candidate(participants=(_participant(compound_id=substrate_id),))
    r2 = _candidate(participants=(_participant(compound_id=uuid4()),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KM,
        protein_ids=(protein_id,),
        substrate_id=substrate_id,
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == r1.id
    assert result.reason is KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE


def test_ki_compound_anchor_is_also_eligible():
    substrate_id = uuid4()
    r1 = _candidate(participants=(_participant(compound_id=substrate_id),))
    r2 = _candidate(participants=(_participant(compound_id=uuid4()),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KI,
        protein_ids=(protein_id,),
        substrate_id=substrate_id,
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == r1.id


@pytest.mark.parametrize(
    "parameter_type",
    [
        KineticParameterType.KCAT,
        KineticParameterType.VMAX,
        KineticParameterType.KCAT_OVER_KM,
        KineticParameterType.SPECIFIC_ACTIVITY,
        KineticParameterType.PH_OPTIMUM,
        KineticParameterType.TEMPERATURE_OPTIMUM,
        KineticParameterType.OTHER,
    ],
)
def test_non_compound_specific_parameter_types_never_use_compound_anchor(parameter_type):
    """A source-associated compound happening to match one of two ambiguous candidates
    must never attribute kcat/Vmax/etc -- only Km/Ki may use a compound anchor (task's
    own explicit parameter-type policy)."""
    substrate_id = uuid4()
    r1 = _candidate(participants=(_participant(compound_id=substrate_id),))
    r2 = _candidate(participants=(_participant(compound_id=uuid4()),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=parameter_type, protein_ids=(protein_id,), substrate_id=substrate_id
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS


# --- Tier 3: + EC number -------------------------------------------------------------------


def test_ec_number_narrows_two_compound_matching_candidates_to_one():
    """Two candidates both have the same substrate as a participant (e.g. a shared
    cofactor) -- EC number is the deciding narrowing constraint."""
    substrate_id = uuid4()
    r1 = _candidate(ec_number="2.3.1.86", participants=(_participant(compound_id=substrate_id),))
    r2 = _candidate(ec_number="1.1.1.1", participants=(_participant(compound_id=substrate_id),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KM,
        protein_ids=(protein_id,),
        substrate_id=substrate_id,
        ec_number="2.3.1.86",
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == r1.id
    assert (
        result.reason is KineticMeasurementReactionAttributionReason.CATALYST_EC_AND_COMPOUND_UNIQUE
    )


def test_ec_number_alone_never_independently_narrows_without_catalyst_context():
    """EC number is only ever an AND-ed narrowing constraint on top of catalyst
    identity, never an independent discovery mechanism (module docstring, tier 3) --
    with no catalyst identity at all, this is INSUFFICIENT_CONTEXT regardless of EC."""
    context = _context(
        parameter_type=KineticParameterType.KM, ec_number="2.3.1.86", substrate_id=uuid4()
    )
    lookup = FakeAttributionLookup()

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.INSUFFICIENT_CONTEXT


# --- Tier 4: reaction signature -------------------------------------------------------------


def test_reaction_signature_narrows_two_ec_matching_candidates_to_one():
    compound_a, compound_b = uuid4(), uuid4()
    r1 = _candidate(
        ec_number="2.3.1.86",
        participants=(
            _participant(compound_id=compound_a, role=ReactionParticipantRole.REACTANT),
            _participant(compound_id=compound_b, role=ReactionParticipantRole.REACTANT),
        ),
    )
    r2 = _candidate(
        ec_number="2.3.1.86",
        participants=(_participant(compound_id=compound_a, role=ReactionParticipantRole.REACTANT),),
    )
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KCAT,  # not compound-eligible; signature still applies
        protein_ids=(protein_id,),
        ec_number="2.3.1.86",
        reaction_participants=(
            _participant(compound_id=compound_a, role=ReactionParticipantRole.REACTANT),
            _participant(compound_id=compound_b, role=ReactionParticipantRole.REACTANT),
        ),
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == r1.id
    assert (
        result.reason
        is KineticMeasurementReactionAttributionReason.CATALYST_AND_REACTION_SIGNATURE_UNIQUE
    )


def test_reaction_signature_never_compares_stoichiometry():
    """The signature comparison is compound_id+role only -- a candidate with different
    stoichiometry for the identical (compound, role) pair still matches (module
    docstring: SABIO-RK's own connector model exposes no stoichiometry at all)."""
    compound_id = uuid4()
    r1 = _candidate(
        participants=(
            _participant(
                compound_id=compound_id,
                role=ReactionParticipantRole.REACTANT,
                stoichiometry=Decimal("2"),
            ),
        )
    )
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1]})
    context = _context(
        parameter_type=KineticParameterType.KCAT,
        protein_ids=(protein_id,),
        reaction_participants=(
            _participant(
                compound_id=compound_id,
                role=ReactionParticipantRole.REACTANT,
                stoichiometry=Decimal("1"),
            ),
        ),
    )

    # Catalyst alone already resolves it (only one candidate) -- included to document that
    # signature filtering, when it *does* run, ignores stoichiometry entirely.
    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)
    assert result.resolved


def test_sabiork_signature_role_maps_only_substrate_and_product():
    assert sabiork_signature_role("Substrate") is ReactionParticipantRole.REACTANT
    assert sabiork_signature_role("Product") is ReactionParticipantRole.PRODUCT
    assert sabiork_signature_role("substrate") is ReactionParticipantRole.REACTANT
    assert sabiork_signature_role("Inhibitor") is None
    assert sabiork_signature_role("Activator") is None
    assert sabiork_signature_role("Modifier") is None
    assert sabiork_signature_role(None) is None


# --- Pathway membership is never, itself, a narrowing tier -------------------------------


def test_pathway_membership_never_resolves_an_otherwise_ambiguous_pair():
    """This module's own KineticMeasurementAttributionContext carries no pathway field
    at all, and no tier ever consults one -- two catalyst-supported candidates that
    happen to both belong to the same curated pathway (the real, universal case for
    single-pathway curation runs) remain AMBIGUOUS, never silently resolved by an
    implicit pathway assumption (module docstring: "never assign a measurement to a
    reaction merely because both occur in the same pathway")."""
    r1, r2 = _candidate(), _candidate()  # both, structurally, "in the same pathway"
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(parameter_type=KineticParameterType.KM, protein_ids=(protein_id,))

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS


# --- Plural protein context -----------------------------------------------------------------


def test_plural_protein_ids_union_candidates_across_both_proteins():
    """A measurement legitimately applicable to two proteins (isozymes) unions their own
    catalyzed reactions -- never collapsed to a single 'winning' protein."""
    reaction = _candidate()
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeAttributionLookup(
        catalyzed_by_protein={protein_a: [reaction], protein_b: [reaction]}
    )
    context = _context(
        parameter_type=KineticParameterType.KCAT, protein_ids=(protein_a, protein_b)
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == reaction.id


def test_plural_protein_ids_each_catalyzing_a_different_reaction_is_ambiguous():
    """Two isozymes, each independently catalyzing its OWN distinct reaction --
    never guessed among; the combined evidence does not uniquely support one."""
    r1, r2 = _candidate(), _candidate()
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_a: [r1], protein_b: [r2]})
    context = _context(
        parameter_type=KineticParameterType.KCAT, protein_ids=(protein_a, protein_b)
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS


# --- Isozyme-specific evidence still resolves cleanly -------------------------------------


def test_isozyme_specific_context_still_resolves_when_that_isozyme_is_unique():
    """One of two isozymes (protein_ids plural on the shared measurement) independently
    catalyzes exactly one reaction the other never touches -- still resolves correctly."""
    reaction = _candidate()
    protein_a, protein_b = uuid4(), uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_a: [reaction], protein_b: []})
    context = _context(
        parameter_type=KineticParameterType.KCAT, protein_ids=(protein_a, protein_b)
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == reaction.id


# --- Conflicting evidence: compound narrows to zero, EC still resolves --------------------


def test_conflicting_compound_context_falls_back_to_ec_narrowing_on_full_catalyst_set():
    """A resolved substrate that matches NEITHER catalyst candidate (a real data
    inconsistency, or simply an unrelated cofactor) never itself blocks EC-based
    narrowing on the original, unfiltered catalyst set."""
    r1 = _candidate(ec_number="2.3.1.86", participants=(_participant(compound_id=uuid4()),))
    r2 = _candidate(ec_number="1.1.1.1", participants=(_participant(compound_id=uuid4()),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KM,
        protein_ids=(protein_id,),
        substrate_id=uuid4(),  # matches neither candidate's own participants
        ec_number="2.3.1.86",
    )

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.resolved
    assert result.reaction_id == r1.id
    assert (
        result.reason is KineticMeasurementReactionAttributionReason.CATALYST_EC_AND_COMPOUND_UNIQUE
    )


# --- No-candidate / insufficient-context ---------------------------------------------------


def test_known_catalyst_with_no_curated_reaction_is_no_candidate():
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: []})
    context = _context(parameter_type=KineticParameterType.KCAT, protein_ids=(protein_id,))

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.NO_CANDIDATE


def test_no_catalyst_and_no_direct_identifier_is_insufficient_context():
    context = _context(parameter_type=KineticParameterType.KCAT)
    lookup = FakeAttributionLookup()

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert not result.resolved
    assert result.reason is KineticMeasurementReactionAttributionReason.INSUFFICIENT_CONTEXT


# --- No new entity creation ------------------------------------------------------------------


def test_attribution_never_creates_a_reaction_it_only_returns_ids_from_the_lookup():
    """Match-only: every candidate this module ever returns as ``reaction_id`` came
    directly from the injected lookup -- nothing here constructs a new ``Reaction``,
    ``Compound``, or cross-reference (there is no persistence import in this module at
    all, verified structurally by this test never needing a session/engine)."""
    reaction = _candidate()
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [reaction]})
    context = _context(parameter_type=KineticParameterType.KCAT, protein_ids=(protein_id,))

    result = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert result.reaction_id == reaction.id
    assert result.reaction_id in {c.id for c in lookup.catalyzed_by_protein[protein_id]}


# --- Determinism / idempotency --------------------------------------------------------------


def test_attribution_is_deterministic_given_identical_inputs():
    substrate_id = uuid4()
    r1 = _candidate(participants=(_participant(compound_id=substrate_id),))
    r2 = _candidate(participants=(_participant(compound_id=uuid4()),))
    protein_id = uuid4()
    lookup = FakeAttributionLookup(catalyzed_by_protein={protein_id: [r1, r2]})
    context = _context(
        parameter_type=KineticParameterType.KM,
        protein_ids=(protein_id,),
        substrate_id=substrate_id,
    )

    first = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)
    second = attribute_kinetic_measurement_to_reaction(context, lookup=lookup)

    assert first == second


# --- Type safety / invariants ---------------------------------------------------------------


def test_context_rejects_non_kinetic_parameter_type():
    with pytest.raises(TypeError):
        KineticMeasurementAttributionContext(parameter_type="KM")  # type: ignore[arg-type]


def test_attribute_rejects_non_context_input():
    with pytest.raises(TypeError):
        attribute_kinetic_measurement_to_reaction(
            "not a context", lookup=FakeAttributionLookup()  # type: ignore[arg-type]
        )
