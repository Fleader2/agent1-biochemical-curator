"""Tests for ``app.pathway_curation.readiness.validate_agent2_readiness``.

Pure, in-memory tests: every ORM row is constructed directly (no database
session, no flush) -- SQLAlchemy declarative classes are plain Python
objects until flushed, so this is a legitimate, fast way to exercise a
read-only validator that only ever reads attributes off already-loaded
rows.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.agent1.types import (
    AGENT1_CONTRACT_VERSION,
    Agent1CuratedKnowledgeView,
    Agent1KnowledgePackage,
    CuratedEnzymeState,
    CuratedKineticMeasurement,
    ProvenanceSummary,
)
from app.models.compartment import Compartment
from app.models.compound import Compound
from app.models.enums import EnzymeStateType, ReactionParticipantRole
from app.models.protein import Protein
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.pathway_curation.readiness import Agent2ReadinessIssueCode, validate_agent2_readiness


def _reaction(**overrides) -> Reaction:
    defaults = {"id": uuid4(), "internal_id": f"R-{uuid4()}", "name": "fake reaction"}
    defaults.update(overrides)
    return Reaction(**defaults)


def _participant(**overrides) -> ReactionParticipant:
    defaults = {
        "id": uuid4(),
        "role": ReactionParticipantRole.REACTANT,
        "stoichiometry": Decimal(1),
        "compartment_id": None,
    }
    defaults.update(overrides)
    return ReactionParticipant(**defaults)


def _compound(**overrides) -> Compound:
    defaults = {"id": uuid4(), "canonical_name": "fake compound"}
    defaults.update(overrides)
    return Compound(**defaults)


def _compartment(**overrides) -> Compartment:
    defaults = {"id": uuid4(), "name": "cytosol"}
    defaults.update(overrides)
    return Compartment(**defaults)


def _protein(**overrides) -> Protein:
    defaults = {"id": uuid4(), "organism_id": uuid4(), "name": "fake protein"}
    defaults.update(overrides)
    return Protein(**defaults)


def _reaction_enzyme(**overrides) -> ReactionEnzyme:
    defaults = {"id": uuid4(), "relationship": "CATALYZES"}
    defaults.update(overrides)
    return ReactionEnzyme(**defaults)


def _provenance_summary() -> ProvenanceSummary:
    return ProvenanceSummary(
        total_claims=0,
        claims_with_evidence=0,
        claims_without_evidence=0,
        total_evidence=0,
        evidence_with_publication=0,
        evidence_with_quoted_support=0,
        publications_referenced=0,
    )


def _view(**overrides) -> Agent1CuratedKnowledgeView:
    defaults = {
        "contract_version": AGENT1_CONTRACT_VERSION,
        "organism_id": None,
        "compartments": (),
        "compounds": (),
        "reactions": (),
        "reaction_participants": (),
        "reaction_enzyme_associations": (),
        "regulatory_interactions": (),
        "kinetic_measurements": (),
        "enzyme_states": (),
        "enzyme_modifications": (),
        "allosteric_interactions": (),
        "enzyme_state_transitions": (),
        "claims": (),
        "evidence": (),
        "confidence_summaries": (),
    }
    defaults.update(overrides)
    return Agent1CuratedKnowledgeView(**defaults)


def _package(**overrides) -> Agent1KnowledgePackage:
    defaults = {
        "contract_version": AGENT1_CONTRACT_VERSION,
        "organism_id": None,
        "organisms": (),
        "genes": (),
        "proteins": (),
        "compounds": (),
        "compartments": (),
        "reactions": (),
        "reaction_participants": (),
        "reaction_enzyme_associations": (),
        "regulatory_interactions": (),
        "publications": (),
        "kinetic_measurements": (),
        "enzyme_states": (),
        "enzyme_modifications": (),
        "allosteric_interactions": (),
        "enzyme_state_transitions": (),
        "claims": (),
        "evidence": (),
        "confidence_summaries": (),
        "review_states": (),
        "knowledge_gaps": (),
        "experiment_recommendations": (),
        "experiment_executions": (),
        "experiment_results": (),
        "provenance_summary": _provenance_summary(),
        "limitations": (),
    }
    defaults.update(overrides)
    return Agent1KnowledgePackage(**defaults)


def _curated_kinetic_measurement(**overrides) -> CuratedKineticMeasurement:
    defaults = {
        "kinetic_measurement_id": uuid4(),
        "reaction_id": None,
        "protein_id": None,
        "complex_id": None,
        "substrate_id": None,
        "organism_id": None,
        "publication_id": None,
        "parameter_type": "KM",
        "reported_parameter_type": "Km",
        "value": Decimal("0.5"),
        "unit": "mM",
        "normalized_value": None,
        "normalized_unit": None,
        "strain": None,
        "temperature_c": None,
        "ph": None,
        "reported_rate_law": None,
        "source": None,
        "source_id": None,
        "confidence_score": None,
        "confidence_class": None,
        "notes": None,
    }
    defaults.update(overrides)
    return CuratedKineticMeasurement(**defaults)


def _curated_enzyme_state(**overrides) -> CuratedEnzymeState:
    defaults = {
        "enzyme_state_id": uuid4(),
        "protein_id": uuid4(),
        "complex_id": None,
        "state_type": EnzymeStateType.BASE,
        "state_label": None,
        "compartment_id": None,
        "active_state": None,
        "source": None,
        "source_id": None,
        "notes": None,
    }
    defaults.update(overrides)
    return CuratedEnzymeState(**defaults)


# --- A structurally complete, ready pathway -----------------------------------------------------


def test_a_fully_resolved_reaction_is_ready():
    compound_a, compound_b = _compound(), _compound()
    reaction = _reaction()
    participants = (
        _participant(reaction_id=reaction.id, compound_id=compound_a.id, compartment_id=None),
        _participant(reaction_id=reaction.id, compound_id=compound_b.id, compartment_id=None),
    )
    view = _view(
        reactions=(reaction,),
        compounds=(compound_a, compound_b),
        reaction_participants=participants,
    )
    package = _package(reactions=(reaction,), compounds=(compound_a, compound_b))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    assert assessment.blocking_issues == ()
    assert assessment.reaction_count == 1
    assert assessment.participant_count == 2


def test_a_resolved_compartment_produces_no_issue_at_all():
    compound = _compound()
    compartment = _compartment()
    reaction = _reaction()
    participant = _participant(
        reaction_id=reaction.id, compound_id=compound.id, compartment_id=compartment.id
    )
    view = _view(
        reactions=(reaction,),
        compounds=(compound,),
        compartments=(compartment,),
        reaction_participants=(participant,),
    )
    package = _package(reactions=(reaction,), compounds=(compound,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    assert assessment.nonblocking_issues == ()


# --- Blocking cases -------------------------------------------------------------------------------


def test_reaction_with_no_participants_is_not_ready():
    reaction = _reaction()
    view = _view(reactions=(reaction,))
    package = _package(reactions=(reaction,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    assert len(assessment.blocking_issues) == 1
    assert (
        assessment.blocking_issues[0].code is Agent2ReadinessIssueCode.REACTION_WITHOUT_PARTICIPANTS
    )


def test_participant_with_dangling_compound_reference_is_not_ready():
    reaction = _reaction()
    participant = _participant(reaction_id=reaction.id, compound_id=uuid4())
    view = _view(reactions=(reaction,), reaction_participants=(participant,))
    package = _package(reactions=(reaction,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    codes = {issue.code for issue in assessment.blocking_issues}
    assert Agent2ReadinessIssueCode.PARTICIPANT_COMPOUND_MISSING in codes


def test_participant_with_dangling_compartment_reference_is_not_ready():
    compound = _compound()
    reaction = _reaction()
    participant = _participant(
        reaction_id=reaction.id, compound_id=compound.id, compartment_id=uuid4()
    )
    view = _view(reactions=(reaction,), compounds=(compound,), reaction_participants=(participant,))
    package = _package(reactions=(reaction,), compounds=(compound,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    codes = {issue.code for issue in assessment.blocking_issues}
    assert Agent2ReadinessIssueCode.PARTICIPANT_COMPARTMENT_MISSING in codes


def test_dangling_reaction_enzyme_reaction_reference_is_not_ready():
    reaction = _reaction()
    association = _reaction_enzyme(reaction_id=uuid4())
    view = _view(reactions=(reaction,), reaction_enzyme_associations=(association,))
    package = _package(reactions=(reaction,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    codes = {issue.code for issue in assessment.blocking_issues}
    assert Agent2ReadinessIssueCode.REACTION_ENZYME_REACTION_MISSING in codes


def test_dangling_reaction_enzyme_catalyst_reference_is_not_ready():
    reaction = _reaction()
    association = _reaction_enzyme(reaction_id=reaction.id, protein_id=uuid4())
    view = _view(reactions=(reaction,), reaction_enzyme_associations=(association,))
    package = _package(reactions=(reaction,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    codes = {issue.code for issue in assessment.blocking_issues}
    assert Agent2ReadinessIssueCode.REACTION_ENZYME_CATALYST_MISSING in codes


def test_resolved_reaction_enzyme_catalyst_produces_no_issue():
    reaction = _reaction()
    compound = _compound()
    participant = _participant(reaction_id=reaction.id, compound_id=compound.id)
    protein = _protein()
    association = _reaction_enzyme(reaction_id=reaction.id, protein_id=protein.id)
    view = _view(
        reactions=(reaction,),
        compounds=(compound,),
        reaction_participants=(participant,),
        reaction_enzyme_associations=(association,),
    )
    package = _package(reactions=(reaction,), compounds=(compound,), proteins=(protein,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True


def test_reaction_enzyme_complex_reference_is_unverifiable_and_never_flagged():
    """No exported container carries an EnzymeComplex list -- a complex-targeted
    association is disclosed as unverifiable, never assumed valid or flagged invalid."""
    reaction = _reaction()
    compound = _compound()
    participant = _participant(reaction_id=reaction.id, compound_id=compound.id)
    association = _reaction_enzyme(reaction_id=reaction.id, complex_id=uuid4())
    view = _view(
        reactions=(reaction,),
        compounds=(compound,),
        reaction_participants=(participant,),
        reaction_enzyme_associations=(association,),
    )
    package = _package(reactions=(reaction,), compounds=(compound,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    assert assessment.blocking_issues == ()


def test_dangling_enzyme_modification_reference_is_a_contract_integrity_blocker():
    from app.agent1.types import CuratedEnzymeModification
    from app.models.enums import ModificationType

    modification = CuratedEnzymeModification(
        enzyme_modification_id=uuid4(),
        enzyme_state_id=uuid4(),
        modification_type=ModificationType.PHOSPHORYLATION,
        residue=None,
        residue_position=None,
        site_label=None,
        modifying_compound_id=None,
        stoichiometry=None,
        source=None,
        source_id=None,
        notes=None,
    )
    view = _view(enzyme_modifications=(modification,))
    package = _package()

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is False
    codes = {issue.code for issue in assessment.blocking_issues}
    assert Agent2ReadinessIssueCode.ENZYME_STATE_REFERENCE_MISSING in codes


# --- Nonblocking cases ---------------------------------------------------------------------------


def test_missing_participant_compartment_is_disclosed_but_ready():
    compound = _compound()
    reaction = _reaction()
    participant = _participant(
        reaction_id=reaction.id, compound_id=compound.id, compartment_id=None
    )
    view = _view(reactions=(reaction,), compounds=(compound,), reaction_participants=(participant,))
    package = _package(reactions=(reaction,), compounds=(compound,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    codes = {issue.code for issue in assessment.nonblocking_issues}
    assert Agent2ReadinessIssueCode.PARTICIPANT_COMPARTMENT_MISSING in codes


def test_dangling_kinetic_reaction_reference_is_disclosed_but_ready():
    measurement = _curated_kinetic_measurement(reaction_id=uuid4())
    view = _view(kinetic_measurements=(measurement,))
    package = _package()

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    codes = {issue.code for issue in assessment.nonblocking_issues}
    assert Agent2ReadinessIssueCode.KINETIC_REFERENCE_MISSING in codes


def test_missing_kinetics_altogether_is_ready():
    reaction = _reaction()
    compound = _compound()
    participant = _participant(reaction_id=reaction.id, compound_id=compound.id)
    view = _view(reactions=(reaction,), compounds=(compound,), reaction_participants=(participant,))
    package = _package(reactions=(reaction,), compounds=(compound,))

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    assert assessment.kinetic_measurement_count == 0


def test_resolved_enzyme_state_is_counted_and_ready():
    state = _curated_enzyme_state()
    view = _view(enzyme_states=(state,))
    package = _package()

    assessment = validate_agent2_readiness(view, package)

    assert assessment.is_ready is True
    assert assessment.enzyme_state_count == 1
