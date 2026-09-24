"""Agent 2 structural-readiness assessment (Increment C pre-commit revision).

Answers a question distinct from ``CompletionStatus``
(``app.pathway_curation.policy.assess_completion``): completion asks
*"did Agent 1 finish the requested curation policy?"*; this module asks
*"is the resulting structural handoff internally complete enough for
Agent 2 to consume it and construct a biochemical network without manual
repair?"* A run can be ``COMPLETE_WITH_GAPS`` (missing kinetics, missing
regulation) and still be Agent-2-ready; a run can also, in principle,
report ``COMPLETE`` under a policy that never asked for participant
resolution at all and still be *not* ready. The two are related, never
conflated, and both are always reported on ``PathwayCurationResult``.

**Read-only, and never constructs an Agent 2 object.** ``validate_agent2_
readiness`` only reads the two container types Agent 1's own existing
export layer already produces
(``app.agent1.types.Agent1CuratedKnowledgeView``/``Agent1KnowledgePackage``)
-- it builds no Antimony, no model species, no kinetic-law assignment, no
parameter declaration. It never mutates either container.

**Why both container types are read.** ``Agent1CuratedKnowledgeView`` is
what is literally handed to Agent 2, but it carries no ``proteins``/
``genes`` tuple of its own (verified directly against
``app.agent1.types``) -- so a ``ReactionEnzyme.protein_id`` reference
cannot be checked for dangling-ness against the view alone.
``Agent1KnowledgePackage`` (the broader, Agent-1-internal container) does
carry ``proteins``, and is already computed by
``execute_pathway_curation`` immediately before the view is derived from
it, so passing both costs nothing extra and lets this module verify what
is actually verifiable. Neither container exposes an ``EnzymeComplex``
list at all (a pre-existing gap, not introduced by this module) --
``complex_id`` references are therefore never checked here and are
disclosed as unverifiable, not silently assumed valid.

**Blocking vs. nonblocking, and why.** A reaction with zero participants,
a participant whose ``compound_id`` does not resolve, or a dangling
``ReactionEnzyme``/enzyme-state reference are genuine structural defects
Agent 2 could not route around without inventing biology of its own --
these are always blocking. A participant with no compartment is
*disclosed but not blocking*: ``ReactionParticipant.compartment_id`` is a
nullable column (verified directly against ``app/models/reaction.py``),
so the existing handoff contract does not itself require one -- this
module never invents a cytosol default to paper over that, it only
reports the gap. Missing kinetic measurements are never blocking (Agent 2
may declare a placeholder/tentative kinetic law); a *dangling* kinetic
reference is likewise reported nonblocking, symmetric with that same
policy. Missing/unsupported regulation is disclosed, never blocking --
except that a persisted enzyme-state-family row (``EnzymeState``/
``EnzymeModification``/``AllostericInteraction``/``EnzymeStateTransition``)
whose own reference is dangling *is* blocking: an object that exists in
the export but cannot be resolved is a contract-integrity defect, not a
disclosed scientific gap, and the "missing regulation is fine" policy
was never meant to cover a broken reference to something that does
exist.

**Increment C.1, F6: readiness is never vacuously true.** Pilot 1 Run 1
demonstrated that a completely empty export (zero reactions, because
structural discovery itself failed) reported ``is_ready=True`` -- there
was nothing to find a defect *in*, so no check above ever fired. That is
a real gap in this contract's own interpretability, not a correct reading
of "ready": ``is_ready`` must mean "Agent 2 can build something," never
merely "nothing found was broken." ``validate_agent2_readiness`` therefore
also computes a **modelable-reaction count**, using exactly the structural
invariants ``_check_participants`` already enforces per reaction (it
exists, it has at least one participant, every participant's compound
resolves, every non-``None`` compartment resolves, every role/stoichiometry
is valid) -- deliberately excluding catalyst/kinetics/regulation
completeness, none of which this contract has ever required for
readiness (§24 of the Increment C.1 instructions: "Do not make kinetics
mandatory"). Zero modelable reactions is always its own additional
blocking issue (``NO_MODELABLE_REACTIONS``), regardless of what else is or
is not present in the export -- an empty export, an organism-only export,
and an export whose only reactions all lack participants are now all
``is_ready=False`` for this reason alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.agent1.types import Agent1CuratedKnowledgeView, Agent1KnowledgePackage


class Agent2ReadinessIssueCode(StrEnum):
    """The controlled vocabulary of structural-readiness defects this module can detect.

    Every member maps to a concrete, checkable condition below -- never an
    invented category with no corresponding check.
    """

    REACTION_WITHOUT_PARTICIPANTS = "REACTION_WITHOUT_PARTICIPANTS"
    PARTICIPANT_COMPOUND_MISSING = "PARTICIPANT_COMPOUND_MISSING"
    PARTICIPANT_COMPARTMENT_MISSING = "PARTICIPANT_COMPARTMENT_MISSING"
    PARTICIPANT_ROLE_INVALID = "PARTICIPANT_ROLE_INVALID"
    PARTICIPANT_STOICHIOMETRY_INVALID = "PARTICIPANT_STOICHIOMETRY_INVALID"
    REACTION_ENZYME_REACTION_MISSING = "REACTION_ENZYME_REACTION_MISSING"
    REACTION_ENZYME_CATALYST_MISSING = "REACTION_ENZYME_CATALYST_MISSING"
    KINETIC_REFERENCE_MISSING = "KINETIC_REFERENCE_MISSING"
    ENZYME_STATE_REFERENCE_MISSING = "ENZYME_STATE_REFERENCE_MISSING"
    #: Zero reactions in the export satisfy the modelable-reaction definition (module
    #: docstring's "F6" section) -- an empty export, an organism-only export, or an
    #: export whose only reactions all lack usable participants. Added in Increment C.1
    #: specifically because Pilot 1 Run 1 showed this exact situation previously
    #: reported ``is_ready=True``.
    NO_MODELABLE_REACTIONS = "NO_MODELABLE_REACTIONS"


@dataclass(frozen=True, slots=True)
class Agent2ReadinessIssue:
    """One structural-readiness finding, always tied to the specific entity it concerns."""

    code: Agent2ReadinessIssueCode
    blocking: bool
    message: str
    entity_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Agent2ReadinessAssessment:
    """The complete, deterministic structural-readiness outcome for one exported handoff.

    ``is_ready`` is ``True`` iff ``blocking_issues`` is empty -- a
    nonblocking issue never affects it. Every count is a plain ``len()``
    over the already-loaded export tuples, no new query logic.
    """

    is_ready: bool
    blocking_issues: tuple[Agent2ReadinessIssue, ...]
    nonblocking_issues: tuple[Agent2ReadinessIssue, ...]

    reaction_count: int
    #: Reactions satisfying the structural "modelable" definition (module docstring's
    #: F6 section): exists, has at least one participant, every participant's compound
    #: resolves, every non-``None`` compartment resolves, and every role/stoichiometry
    #: is valid. Never requires a resolved catalyst, kinetics, or regulation. May be
    #: strictly less than ``reaction_count``.
    modelable_reaction_count: int
    participant_count: int
    compound_count: int
    compartment_count: int
    reaction_enzyme_count: int
    kinetic_measurement_count: int
    enzyme_state_count: int


def _check_participants(
    view: Agent1CuratedKnowledgeView,
    compound_ids: frozenset[UUID],
    compartment_ids: frozenset[UUID],
) -> tuple[list[Agent2ReadinessIssue], list[Agent2ReadinessIssue], frozenset[UUID]]:
    """Per-reaction structural checks. Returns ``(blocking, nonblocking,
    modelable_reaction_ids)`` -- the third element is exactly the set of reactions that
    triggered no blocking issue here (module docstring's F6 "modelable reaction"
    definition): it never requires a resolved catalyst/kinetics/regulation, only what
    this function itself already checks.
    """
    from app.models.enums import ReactionParticipantRole

    blocking: list[Agent2ReadinessIssue] = []
    nonblocking: list[Agent2ReadinessIssue] = []
    modelable_reaction_ids: set[UUID] = set()

    participants_by_reaction: dict[UUID, list] = {}
    for participant in view.reaction_participants:
        participants_by_reaction.setdefault(participant.reaction_id, []).append(participant)

    for reaction in view.reactions:
        participants = participants_by_reaction.get(reaction.id, [])
        if not participants:
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.REACTION_WITHOUT_PARTICIPANTS,
                    blocking=True,
                    entity_id=reaction.id,
                    message=(
                        f"reaction {reaction.id} ({reaction.name!r}) has no exported "
                        "ReactionParticipant rows"
                    ),
                )
            )
            continue

        reaction_is_modelable = True
        for participant in participants:
            if participant.compound_id not in compound_ids:
                blocking.append(
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.PARTICIPANT_COMPOUND_MISSING,
                        blocking=True,
                        entity_id=participant.id,
                        message=(
                            f"participant {participant.id} on reaction {reaction.id} "
                            f"references compound {participant.compound_id}, which is not "
                            "in the exported compound set"
                        ),
                    )
                )
                reaction_is_modelable = False

            if participant.compartment_id is None:
                nonblocking.append(
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.PARTICIPANT_COMPARTMENT_MISSING,
                        blocking=False,
                        entity_id=participant.id,
                        message=(
                            f"participant {participant.id} on reaction {reaction.id} has no "
                            "resolved compartment -- ReactionParticipant.compartment_id is "
                            "nullable, so this is a disclosed gap, never a blocker"
                        ),
                    )
                )
            elif participant.compartment_id not in compartment_ids:
                blocking.append(
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.PARTICIPANT_COMPARTMENT_MISSING,
                        blocking=True,
                        entity_id=participant.id,
                        message=(
                            f"participant {participant.id} on reaction {reaction.id} "
                            f"references compartment {participant.compartment_id}, which is "
                            "not in the exported compartment set"
                        ),
                    )
                )
                reaction_is_modelable = False

            if not isinstance(participant.role, ReactionParticipantRole):
                blocking.append(  # pragma: no cover -- defensive; the DB enum guarantees this
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.PARTICIPANT_ROLE_INVALID,
                        blocking=True,
                        entity_id=participant.id,
                        message=f"participant {participant.id} has an invalid role",
                    )
                )
                reaction_is_modelable = False
            if participant.stoichiometry is None or participant.stoichiometry <= 0:
                blocking.append(  # pragma: no cover -- defensive; the DB CHECK guarantees this
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.PARTICIPANT_STOICHIOMETRY_INVALID,
                        blocking=True,
                        entity_id=participant.id,
                        message=(
                            f"participant {participant.id} has non-positive stoichiometry "
                            f"{participant.stoichiometry!r}"
                        ),
                    )
                )
                reaction_is_modelable = False

        if reaction_is_modelable:
            modelable_reaction_ids.add(reaction.id)

    return blocking, nonblocking, frozenset(modelable_reaction_ids)


def _check_reaction_enzymes(
    view: Agent1CuratedKnowledgeView,
    reaction_ids: frozenset[UUID],
    protein_ids: frozenset[UUID],
    enzyme_state_ids: frozenset[UUID],
) -> list[Agent2ReadinessIssue]:
    blocking: list[Agent2ReadinessIssue] = []
    for association in view.reaction_enzyme_associations:
        if association.reaction_id not in reaction_ids:
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.REACTION_ENZYME_REACTION_MISSING,
                    blocking=True,
                    entity_id=association.id,
                    message=(
                        f"reaction/enzyme association {association.id} references reaction "
                        f"{association.reaction_id}, which is not in the exported reaction set"
                    ),
                )
            )
        if association.protein_id is not None and association.protein_id not in protein_ids:
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.REACTION_ENZYME_CATALYST_MISSING,
                    blocking=True,
                    entity_id=association.id,
                    message=(
                        f"reaction/enzyme association {association.id} references protein "
                        f"{association.protein_id}, which is not in the exported protein set"
                    ),
                )
            )
        if (
            association.enzyme_state_id is not None
            and association.enzyme_state_id not in enzyme_state_ids
        ):
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.REACTION_ENZYME_CATALYST_MISSING,
                    blocking=True,
                    entity_id=association.id,
                    message=(
                        f"reaction/enzyme association {association.id} references enzyme "
                        f"state {association.enzyme_state_id}, which is not in the exported "
                        "enzyme-state set"
                    ),
                )
            )
        # association.complex_id is intentionally never checked: neither container
        # type exports an EnzymeComplex list (see module docstring) -- a
        # complex-targeted association is therefore always structurally
        # unverifiable here, disclosed, never assumed valid or invalid.
    return blocking


def _check_kinetic_measurements(
    view: Agent1CuratedKnowledgeView,
    reaction_ids: frozenset[UUID],
    protein_ids: frozenset[UUID],
) -> list[Agent2ReadinessIssue]:
    nonblocking: list[Agent2ReadinessIssue] = []
    for measurement in view.kinetic_measurements:
        if measurement.reaction_id is not None and measurement.reaction_id not in reaction_ids:
            nonblocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.KINETIC_REFERENCE_MISSING,
                    blocking=False,
                    entity_id=measurement.kinetic_measurement_id,
                    message=(
                        f"kinetic measurement {measurement.kinetic_measurement_id} references "
                        f"reaction {measurement.reaction_id}, which is not in the exported "
                        "reaction set -- disclosed, never blocking (missing/dangling kinetics "
                        "never blocks structural modeling)"
                    ),
                )
            )
        if measurement.protein_id is not None and measurement.protein_id not in protein_ids:
            nonblocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.KINETIC_REFERENCE_MISSING,
                    blocking=False,
                    entity_id=measurement.kinetic_measurement_id,
                    message=(
                        f"kinetic measurement {measurement.kinetic_measurement_id} references "
                        f"protein {measurement.protein_id}, which is not in the exported "
                        "protein set -- disclosed, never blocking"
                    ),
                )
            )
    return nonblocking


def _check_enzyme_state_family(
    view: Agent1CuratedKnowledgeView, enzyme_state_ids: frozenset[UUID]
) -> list[Agent2ReadinessIssue]:
    """Every exported ``EnzymeModification``/``AllostericInteraction``/``EnzymeStateTransition``
    references an exported ``EnzymeState`` -- blocking when it does not (module docstring's
    "contract-integrity" exception to the general "regulation gaps are nonblocking" rule)."""
    blocking: list[Agent2ReadinessIssue] = []

    for modification in view.enzyme_modifications:
        if modification.enzyme_state_id not in enzyme_state_ids:
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.ENZYME_STATE_REFERENCE_MISSING,
                    blocking=True,
                    entity_id=modification.enzyme_modification_id,
                    message=(
                        f"enzyme modification {modification.enzyme_modification_id} "
                        f"references enzyme state {modification.enzyme_state_id}, which is "
                        "not in the exported enzyme-state set"
                    ),
                )
            )

    for interaction in view.allosteric_interactions:
        if interaction.enzyme_state_id not in enzyme_state_ids:
            blocking.append(
                Agent2ReadinessIssue(
                    code=Agent2ReadinessIssueCode.ENZYME_STATE_REFERENCE_MISSING,
                    blocking=True,
                    entity_id=interaction.allosteric_interaction_id,
                    message=(
                        f"allosteric interaction {interaction.allosteric_interaction_id} "
                        f"references enzyme state {interaction.enzyme_state_id}, which is "
                        "not in the exported enzyme-state set"
                    ),
                )
            )

    for transition in view.enzyme_state_transitions:
        for field_name, state_id in (
            ("from_state_id", transition.from_state_id),
            ("to_state_id", transition.to_state_id),
        ):
            if state_id not in enzyme_state_ids:
                blocking.append(
                    Agent2ReadinessIssue(
                        code=Agent2ReadinessIssueCode.ENZYME_STATE_REFERENCE_MISSING,
                        blocking=True,
                        entity_id=transition.enzyme_state_transition_id,
                        message=(
                            f"enzyme state transition "
                            f"{transition.enzyme_state_transition_id}.{field_name} references "
                            f"enzyme state {state_id}, which is not in the exported "
                            "enzyme-state set"
                        ),
                    )
                )

    return blocking


def validate_agent2_readiness(
    view: Agent1CuratedKnowledgeView, package: Agent1KnowledgePackage
) -> Agent2ReadinessAssessment:
    """Deterministically assess whether ``view`` is structurally ready for Agent 2.

    Read-only over both already-computed containers (see module
    docstring). Never raises for expected incomplete biology: every
    defect this function can detect is reported as a structured
    ``Agent2ReadinessIssue``, not an exception.
    """
    compound_ids = frozenset(compound.id for compound in view.compounds)
    compartment_ids = frozenset(compartment.id for compartment in view.compartments)
    reaction_ids = frozenset(reaction.id for reaction in view.reactions)
    protein_ids = frozenset(protein.id for protein in package.proteins)
    enzyme_state_ids = frozenset(state.enzyme_state_id for state in view.enzyme_states)

    participant_blocking, participant_nonblocking, modelable_reaction_ids = _check_participants(
        view, compound_ids, compartment_ids
    )
    reaction_enzyme_blocking = _check_reaction_enzymes(
        view, reaction_ids, protein_ids, enzyme_state_ids
    )
    kinetic_nonblocking = _check_kinetic_measurements(view, reaction_ids, protein_ids)
    enzyme_state_blocking = _check_enzyme_state_family(view, enzyme_state_ids)

    blocking = list(participant_blocking + reaction_enzyme_blocking + enzyme_state_blocking)
    if not modelable_reaction_ids:
        blocking.append(
            Agent2ReadinessIssue(
                code=Agent2ReadinessIssueCode.NO_MODELABLE_REACTIONS,
                blocking=True,
                entity_id=None,
                message=(
                    f"0 of {len(view.reactions)} exported reaction(s) satisfy the "
                    "modelable-reaction definition (participants present, every "
                    "participant's compound/compartment reference resolves, role and "
                    "stoichiometry valid) -- an export with no modelable reaction is "
                    "never Agent-2-ready, regardless of what else it contains"
                ),
            )
        )
    nonblocking = tuple(participant_nonblocking + kinetic_nonblocking)

    return Agent2ReadinessAssessment(
        is_ready=not blocking,
        blocking_issues=tuple(blocking),
        nonblocking_issues=nonblocking,
        reaction_count=len(view.reactions),
        modelable_reaction_count=len(modelable_reaction_ids),
        participant_count=len(view.reaction_participants),
        compound_count=len(view.compounds),
        compartment_count=len(view.compartments),
        reaction_enzyme_count=len(view.reaction_enzyme_associations),
        kinetic_measurement_count=len(view.kinetic_measurements),
        enzyme_state_count=len(view.enzyme_states),
    )


__all__ = [
    "Agent2ReadinessAssessment",
    "Agent2ReadinessIssue",
    "Agent2ReadinessIssueCode",
    "validate_agent2_readiness",
]
