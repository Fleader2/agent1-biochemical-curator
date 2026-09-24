"""The public execution API: ``execute_pathway_curation``.

Runs the bounded, auditable loop described in
``docs/26_autonomous_pathway_curation_planner.md``: resolve the organism,
discover a seed pathway and its reactions (KEGG), resolve every
explicitly seeded gene/protein (SGD/UniProt), optionally enrich with
literature (PubMed) and kinetics (SABIO-RK/Open Enzyme Database), run the
existing deterministic knowledge-gap analysis, and assemble the final
``Agent1CuratedKnowledgeView`` -- never inventing a fact none of those
existing capabilities actually supplied.

**Connectors are always an explicit parameter, never a global** (Step 8).
**This function never commits or rolls back ``session``** -- exactly the
transaction-ownership convention every ``app.persistence.*`` module
already follows; the caller decides when (or whether) to commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent1.export import get_agent1_curated_knowledge_view
from app.agent1.service import get_agent1_knowledge_package
from app.claim_generation.types import EntityKind
from app.connectors.exceptions import ConnectorError
from app.entity_resolution.adapters import (
    PubMedSearchAndFetch,
    SgdSearchAndFetch,
    UniProtSearchAndFetch,
)
from app.knowledge_gaps.analysis import analyze_knowledge_gaps
from app.models.enums import ReactionParticipantRole, SourceType
from app.normalization.reaction import ReactionParticipantIdentity
from app.pathway_curation import strategies
from app.pathway_curation.equation_parser import parse_kegg_equation
from app.pathway_curation.errors import CurationExecutionError
from app.pathway_curation.lookups import (
    SqlAlchemyCompartmentLookup,
    SqlAlchemyCompoundLookup,
    SqlAlchemyGeneLookup,
    SqlAlchemyOrganismLookup,
    SqlAlchemyProteinLookup,
    SqlAlchemyPublicationLookup,
    SqlAlchemyReactionEnzymeLookup,
    SqlAlchemyReactionLookup,
)
from app.pathway_curation.planner import plan_pathway_curation
from app.pathway_curation.policy import (
    apply_mode_defaults,
    assess_completion,
    build_frontier_id,
    query_identity,
)
from app.pathway_curation.readiness import validate_agent2_readiness
from app.pathway_curation.state import CurationRunState
from app.pathway_curation.types import (
    CurationFrontierItem,
    CurationIterationRecord,
    FrontierReason,
    PathwayCurationRequest,
    PathwayCurationResult,
)
from app.pathway_curation.validation import require_valid_request
from app.persistence.knowledge_gap import persist_knowledge_gap_analysis


@dataclass(frozen=True, slots=True)
class PathwayConnectorBundle:
    """Every connector one ``execute_pathway_curation`` call has available, by source.

    Every field is optional: a source with no connector supplied here
    simply cannot be queried -- the plan steps that depend on it are then
    recorded as an unresolved frontier item (``NO_CONNECTOR_AVAILABLE``),
    never a fabricated fallback. Mirrors
    ``app.entity_resolution.resolver.ConnectorBundle``'s identical
    "every field optional, no global default" convention, extended with
    the two kinetics sources that resolver has no use for.
    """

    kegg: strategies.KeggPathwayCurationConnector | None = None
    sgd: SgdSearchAndFetch | None = None
    uniprot: UniProtSearchAndFetch | None = None
    pubmed: PubMedSearchAndFetch | None = None
    sabiork: object | None = None
    oed: object | None = None


def execute_pathway_curation(
    request: PathwayCurationRequest,
    *,
    session: Session,
    connectors: PathwayConnectorBundle,
) -> PathwayCurationResult:
    """Plan and execute one bounded, auditable pathway-curation run. See module docstring."""
    require_valid_request(request)
    effective_request = apply_mode_defaults(request)
    plan = plan_pathway_curation(effective_request)

    state = CurationRunState()
    organism_lookup = SqlAlchemyOrganismLookup(session)
    reaction_lookup = SqlAlchemyReactionLookup(session)
    gene_lookup = SqlAlchemyGeneLookup(session)
    protein_lookup = SqlAlchemyProteinLookup(session)
    publication_lookup = SqlAlchemyPublicationLookup(session)
    compound_lookup = SqlAlchemyCompoundLookup(session)
    compartment_lookup = SqlAlchemyCompartmentLookup(session)
    reaction_enzyme_lookup = SqlAlchemyReactionEnzymeLookup(session)

    # --- Organism resolution (Step 11: always first, never a connector call) -----------------
    organism_outcome = strategies.resolve_organism(
        scientific_name=effective_request.organism_text,
        strain=effective_request.strain_text,
        ncbi_taxonomy_id=effective_request.organism_ncbi_taxonomy_id,
        lookup=organism_lookup,
        session=session,
    )
    if organism_outcome.entity_id is None:
        return _build_result(
            request=effective_request,
            plan=plan,
            state=state,
            iterations=(),
            organism_id=None,
            gap_ids=(),
            hard_blocker=(
                f"organism resolution for {effective_request.organism_text!r} did not "
                f"resolve: {organism_outcome.notes}"
            ),
            session=session,
        )
    organism_id = organism_outcome.entity_id
    state.record_entity(organism_id)

    # --- Compartment scope assumption (Increment C pre-commit revision, Step 13: an explicit,
    # caller-asserted scope assumption, never an inference this package makes on its own) -----
    reference_compartment_id: UUID | None = None
    if effective_request.default_compartment_text is not None:
        reference_compartment_id = strategies.resolve_reference_compartment_by_name(
            effective_request.default_compartment_text, lookup=compartment_lookup
        )
        if reference_compartment_id is None:
            state.warn(
                f"default_compartment_text {effective_request.default_compartment_text!r} "
                "did not resolve to exactly one existing reference compartment -- "
                "participants will be persisted with no compartment rather than a guess"
            )

    iterations: list[CurationIterationRecord] = []
    pending_reaction_ids: list[str] = []
    resolved_protein_ec_numbers: list[tuple[UUID, str]] = []
    compound_cache: dict[str, UUID | None] = {}
    pathway_discovered = False

    for iteration_number in range(1, effective_request.max_iterations + 1):
        if state.connector_calls_made >= effective_request.max_connector_calls:
            break

        frontier_before = state.snapshot_frontier()
        new_entities_before = len(state.discovered_entity_ids)
        new_reactions_before = len(state.discovered_reaction_ids)
        new_publications_before = len(state.discovered_publication_ids)
        steps_executed: list[str] = []

        if iteration_number == 1:
            _discover_pathway_and_reactions(
                effective_request,
                connectors=connectors,
                state=state,
                pending_reaction_ids=pending_reaction_ids,
            )
            steps_executed.append("discover-pathway")
            steps_executed.append("discover-reactions")
            pathway_discovered = True

            if effective_request.include_regulation or effective_request.include_enzyme_states:
                _record_regulation_not_supported(effective_request, state=state)
                steps_executed.append("regulation-not-supported")

            if effective_request.seed_entity_texts:
                _resolve_seeded_genes_and_proteins(
                    effective_request,
                    connectors=connectors,
                    organism_id=organism_id,
                    gene_lookup=gene_lookup,
                    protein_lookup=protein_lookup,
                    session=session,
                    state=state,
                    resolved_protein_ec_numbers=resolved_protein_ec_numbers,
                )
                steps_executed.append("discover-enzymes")

        _resolve_pending_reactions(
            connectors=connectors,
            organism_id=organism_id,
            reaction_lookup=reaction_lookup,
            compound_lookup=compound_lookup,
            session=session,
            state=state,
            pending_reaction_ids=pending_reaction_ids,
            max_connector_calls=effective_request.max_connector_calls,
            reference_compartment_id=reference_compartment_id,
            compound_cache=compound_cache,
        )
        if pending_reaction_ids or (iteration_number == 1 and pathway_discovered):
            steps_executed.append("resolve-reactions")

        if connectors.uniprot is not None and state.discovered_reaction_ids:
            _discover_catalysts_from_reactions(
                connectors=connectors,
                organism_id=organism_id,
                organism_text=effective_request.organism_text,
                protein_lookup=protein_lookup,
                session=session,
                state=state,
                discovered_reaction_ids=state.discovered_reaction_ids,
                resolved_protein_ec_numbers=resolved_protein_ec_numbers,
            )
            steps_executed.append("discover-catalysts-from-reactions")

        if resolved_protein_ec_numbers and state.discovered_reaction_ids:
            _associate_catalysts(
                session=session,
                state=state,
                resolved_protein_ec_numbers=resolved_protein_ec_numbers,
                discovered_reaction_ids=state.discovered_reaction_ids,
                reaction_enzyme_lookup=reaction_enzyme_lookup,
            )
            steps_executed.append("associate-catalysts")

        if effective_request.include_publications and iteration_number == 1:
            _discover_publications(
                effective_request,
                connectors=connectors,
                publication_lookup=publication_lookup,
                session=session,
                state=state,
            )
            steps_executed.append("discover-publications")

        if effective_request.include_kinetics and iteration_number == 1:
            if resolved_protein_ec_numbers:
                _discover_kinetics(
                    connectors=connectors,
                    organism_text=effective_request.organism_text,
                    organism_id=organism_id,
                    resolved_protein_ec_numbers=resolved_protein_ec_numbers,
                    session=session,
                    state=state,
                )
                steps_executed.append("discover-kinetics")
            else:
                _record_kinetics_not_attempted(state=state)
                steps_executed.append("kinetics-not-attempted")

        frontier_after = state.snapshot_frontier()
        made_progress = (
            len(state.discovered_entity_ids) > new_entities_before
            or len(state.discovered_reaction_ids) > new_reactions_before
            or len(state.discovered_publication_ids) > new_publications_before
            or frontier_before != frontier_after
        )
        no_progress = not made_progress and iteration_number > 1

        iterations.append(
            CurationIterationRecord(
                iteration_number=iteration_number,
                frontier_before=frontier_before,
                plan_steps_executed=tuple(steps_executed),
                new_entity_ids=tuple(state.discovered_entity_ids[new_entities_before:]),
                new_reaction_ids=tuple(state.discovered_reaction_ids[new_reactions_before:]),
                new_publication_ids=tuple(
                    state.discovered_publication_ids[new_publications_before:]
                ),
                frontier_after=frontier_after,
                connector_calls_made=state.connector_calls_made,
                no_progress=no_progress,
            )
        )

        if no_progress:
            break
        if not pending_reaction_ids and pathway_discovered and iteration_number > 1:
            break

    # --- Gap analysis (Step 30: reuse the existing detector, never a second one) --------------
    gap_result = analyze_knowledge_gaps(session)
    gap_persistence_results = persist_knowledge_gap_analysis(gap_result, session=session)
    gap_ids = tuple(
        result.knowledge_gap_id
        for result in gap_persistence_results
        if result.knowledge_gap_id is not None
    )

    return _build_result(
        request=effective_request,
        plan=plan,
        state=state,
        iterations=tuple(iterations),
        organism_id=organism_id,
        gap_ids=gap_ids,
        hard_blocker=None,
        session=session,
    )


def _discover_pathway_and_reactions(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    state: CurationRunState,
    pending_reaction_ids: list[str],
) -> None:
    """Resolve one pathway and its reaction membership (Increment C.1 rewrite).

    **F4 precedence** (audited via ``queries_executed``/frontier, Step 13/20 of the
    Increment C.1 instructions): ``request.source_pathway_id``, when supplied, is
    used directly -- no free-text KEGG pathway search is ever attempted in that case.
    Only when it is ``None`` does this fall back to the pre-existing free-text
    ``biological_process`` search.

    **F1 correction**: reaction membership always comes from
    ``strategies.discover_reactions_in_pathway`` (KEGG's own pathway<->reaction
    ``link`` operation, and -- Increment C.1's organism-specific pathway-resolution
    completion -- that pathway id's own KGML diagram when one exists) -- never from
    parsing the pathway's own ``/get/`` record, which does not reliably carry a
    ``REACTION`` field on live KEGG.

    **F9 correction**: a pathway that resolves but links to zero reactions now always
    produces a ``PATHWAY_REACTION_MEMBERSHIP_EMPTY`` frontier item -- Pilot 1 Run 1's
    exact silent-failure condition can no longer occur.
    """
    if connectors.kegg is None:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                    anchor=request.biological_process,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                priority=1,
                entity_text=request.biological_process,
                notes="no KEGG connector configured -- pathway discovery cannot be attempted",
            )
        )
        return

    pathway_id = _resolve_pathway_identity(request, connectors=connectors, state=state)
    if pathway_id is None:
        return  # a frontier item explaining why was already recorded

    link_identity = query_identity(
        connector=SourceType.KEGG, action="discover_reactions", pathway_id=pathway_id
    )
    if state.has_run_query(link_identity):
        return
    try:
        reaction_ids = strategies.discover_reactions_in_pathway(connectors.kegg, pathway_id)
    except ConnectorError as exc:
        state.warn(f"KEGG pathway reaction discovery failed for {pathway_id}: {exc}")
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.SOURCE_FAILURE,
                    anchor=pathway_id,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.SOURCE_FAILURE,
                priority=1,
                entity_text=pathway_id,
                attempted_sources=(SourceType.KEGG,),
                notes=str(exc),
            )
        )
        state.record_connector_call()
        state.record_query(
            link_identity, display_text=f"KEGG pathway reaction discovery: {pathway_id}"
        )
        return
    state.record_connector_call()
    state.record_query(link_identity, display_text=f"KEGG pathway reaction discovery: {pathway_id}")

    if not reaction_ids:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY,
                    anchor=pathway_id,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY,
                priority=1,
                entity_text=pathway_id,
                attempted_sources=(SourceType.KEGG,),
                notes=(
                    f"pathway {pathway_id!r} resolved, but neither its own KGML diagram nor "
                    "KEGG's pathway->reaction link operation yielded any reaction ids -- a "
                    "legitimate empty-membership outcome, never a connector failure, and never "
                    "silently treated as success"
                ),
            )
        )
        return

    excluded = set(request.exclusions)
    for reaction_id in reaction_ids:
        if reaction_id in excluded:
            continue
        pending_reaction_ids.append(reaction_id)


def _resolve_pathway_identity(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    state: CurationRunState,
) -> str | None:
    """F4: an explicit ``request.source_pathway_id`` always takes deterministic
    precedence over free-text discovery -- returns it directly (after confirming it
    actually resolves on KEGG) without ever calling ``strategies.discover_pathway``.
    Falls back to the pre-existing free-text search only when no structured id was
    supplied. Returns ``None`` when neither path resolves a pathway; the caller can
    assume a frontier item explaining why has already been recorded in that case.
    """
    if request.source_pathway_id is not None:
        return _resolve_structured_pathway_id(request, connectors=connectors, state=state)
    return _discover_pathway_by_text(request, connectors=connectors, state=state)


def _resolve_structured_pathway_id(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    state: CurationRunState,
) -> str | None:
    pathway_id = request.source_pathway_id
    assert pathway_id is not None  # guaranteed by the caller
    fetch_identity = query_identity(
        connector=SourceType.KEGG, action="fetch_pathway_metadata", pathway_id=pathway_id
    )
    try:
        record = strategies.fetch_kegg_pathway_metadata(connectors.kegg, pathway_id)
    except ConnectorError as exc:
        state.warn(f"KEGG pathway fetch failed for structured id {pathway_id}: {exc}")
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.SOURCE_FAILURE,
                    anchor=pathway_id,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.SOURCE_FAILURE,
                priority=1,
                entity_text=pathway_id,
                attempted_sources=(SourceType.KEGG,),
                notes=str(exc),
            )
        )
        state.record_connector_call()
        state.record_query(fetch_identity, display_text=f"KEGG pathway fetch: {pathway_id}")
        return None
    state.record_connector_call()
    state.record_query(fetch_identity, display_text=f"KEGG pathway fetch: {pathway_id}")

    if record is None:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                    anchor=pathway_id,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                priority=1,
                entity_text=pathway_id,
                attempted_sources=(SourceType.KEGG,),
                notes=(
                    f"structured pathway id {pathway_id!r} (request.source_pathway_id) was "
                    "not found on KEGG"
                ),
            )
        )
        return None
    return pathway_id


def _discover_pathway_by_text(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    state: CurationRunState,
) -> str | None:
    identity = query_identity(
        connector=SourceType.KEGG, action="discover_pathway", query=request.biological_process
    )
    try:
        pathway_ids = strategies.discover_pathway(connectors.kegg, request.biological_process)
    except ConnectorError as exc:
        state.warn(f"KEGG pathway discovery failed: {exc}")
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.SOURCE_FAILURE,
                    anchor=request.biological_process,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.SOURCE_FAILURE,
                priority=1,
                entity_text=request.biological_process,
                attempted_sources=(SourceType.KEGG,),
                notes=str(exc),
            )
        )
        state.record_connector_call()
        state.record_query(
            identity, display_text=f"KEGG pathway search: {request.biological_process!r}"
        )
        return None
    state.record_connector_call()
    state.record_query(
        identity, display_text=f"KEGG pathway search: {request.biological_process!r}"
    )

    if not pathway_ids:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.UNKNOWN,
                    reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                    anchor=request.biological_process,
                ),
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                priority=1,
                entity_text=request.biological_process,
                attempted_sources=(SourceType.KEGG,),
                notes="no KEGG pathway matched this biological_process",
            )
        )
        return None
    return pathway_ids[0]


def _resolve_pending_reactions(
    *,
    connectors: PathwayConnectorBundle,
    organism_id: UUID,
    reaction_lookup,
    compound_lookup,
    session: Session,
    state: CurationRunState,
    pending_reaction_ids: list[str],
    max_connector_calls: int,
    reference_compartment_id: UUID | None,
    compound_cache: dict[str, UUID | None],
) -> None:
    """Resolve every pending KEGG reaction id: structural identity, then (Increment C
    pre-commit revision) its participants, from the same fetched record -- never a second
    fetch of the reaction itself (see ``strategies.fetch_kegg_reaction_record``)."""
    if connectors.kegg is None:
        return
    while pending_reaction_ids and state.connector_calls_made < max_connector_calls:
        kegg_reaction_id = pending_reaction_ids.pop(0)
        fetch_identity = query_identity(
            connector=SourceType.KEGG, action="fetch_reaction", kegg_reaction_id=kegg_reaction_id
        )
        if state.has_run_query(fetch_identity):
            continue

        try:
            record = strategies.fetch_kegg_reaction_record(connectors.kegg, kegg_reaction_id)
        except ConnectorError as exc:
            state.warn(f"KEGG reaction fetch failed for {kegg_reaction_id}: {exc}")
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=build_frontier_id(
                        entity_kind=EntityKind.REACTION,
                        reason=FrontierReason.SOURCE_FAILURE,
                        anchor=kegg_reaction_id,
                    ),
                    entity_kind=EntityKind.REACTION,
                    reason=FrontierReason.SOURCE_FAILURE,
                    priority=1,
                    entity_text=kegg_reaction_id,
                    attempted_sources=(SourceType.KEGG,),
                    notes=str(exc),
                )
            )
            state.record_connector_call()
            state.record_query(
                fetch_identity, display_text=f"KEGG reaction fetch: {kegg_reaction_id}"
            )
            continue
        state.record_connector_call()
        state.record_query(fetch_identity, display_text=f"KEGG reaction fetch: {kegg_reaction_id}")

        if record is None:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=build_frontier_id(
                        entity_kind=EntityKind.REACTION,
                        reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                        anchor=kegg_reaction_id,
                    ),
                    entity_kind=EntityKind.REACTION,
                    reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                    priority=1,
                    entity_text=kegg_reaction_id,
                    attempted_sources=(SourceType.KEGG,),
                    notes=(
                        "KEGG reaction id not found (fetch returned None) or not a reaction record"
                    ),
                )
            )
            continue

        participants = _resolve_reaction_participants(
            equation=record.equation,
            kegg_reaction_id=kegg_reaction_id,
            connectors=connectors,
            compound_lookup=compound_lookup,
            session=session,
            state=state,
            max_connector_calls=max_connector_calls,
            reference_compartment_id=reference_compartment_id,
            compound_cache=compound_cache,
        )

        outcome = strategies.resolve_reaction_by_kegg_id(
            connectors.kegg,
            kegg_reaction_id,
            organism_id=organism_id,
            lookup=reaction_lookup,
            session=session,
            participants=participants,
            prefetched_record=record,
        )

        identity_frontier_id = build_frontier_id(
            entity_kind=EntityKind.REACTION,
            reason=outcome.frontier_reason or FrontierReason.UNRESOLVED_REACTION_IDENTITY,
            anchor=kegg_reaction_id,
        )
        participants_frontier_id = build_frontier_id(
            entity_kind=EntityKind.REACTION,
            reason=FrontierReason.REACTION_MISSING_PARTICIPANTS,
            anchor=kegg_reaction_id,
        )
        if outcome.entity_id is not None:
            state.record_reaction(outcome.entity_id)
            state.resolve_frontier(identity_frontier_id)
            if participants:
                state.resolve_frontier(participants_frontier_id)
            else:
                state.add_frontier(
                    CurationFrontierItem(
                        frontier_id=participants_frontier_id,
                        entity_kind=EntityKind.REACTION,
                        reason=FrontierReason.REACTION_MISSING_PARTICIPANTS,
                        priority=1,
                        entity_id=outcome.entity_id,
                        entity_text=kegg_reaction_id,
                        notes=(
                            "reaction resolved, but its KEGG equation yielded no resolvable "
                            "participants (blank/unparseable equation, or every participant "
                            "token failed to resolve)"
                        ),
                    )
                )
        else:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=identity_frontier_id,
                    entity_kind=EntityKind.REACTION,
                    reason=outcome.frontier_reason,
                    priority=1,
                    entity_text=kegg_reaction_id,
                    attempted_sources=(SourceType.KEGG,),
                    notes=outcome.notes,
                )
            )


def _resolve_reaction_participants(
    *,
    equation: str | None,
    kegg_reaction_id: str,
    connectors: PathwayConnectorBundle,
    compound_lookup,
    session: Session,
    state: CurationRunState,
    max_connector_calls: int,
    reference_compartment_id: UUID | None,
    compound_cache: dict[str, UUID | None],
) -> tuple[ReactionParticipantIdentity, ...]:
    """Parse one KEGG reaction's raw equation and resolve every participant token it names.

    Every unparseable term (``app.pathway_curation.equation_parser``'s own, never-guessing
    output) becomes its own ``UNRESOLVED_REACTION_PARTICIPANT`` frontier item, verbatim.
    Partial success is preserved: a reaction with 3 of 4 participants resolved still gets
    those 3 attached, never withheld merely because the 4th could not be resolved.
    """
    parsed = parse_kegg_equation(equation)

    for token in parsed.unparseable_tokens:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.COMPOUND,
                    reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
                    anchor=f"{kegg_reaction_id}:{token}",
                ),
                entity_kind=EntityKind.COMPOUND,
                reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
                priority=1,
                entity_text=token,
                notes=(
                    f"equation term {token!r} in KEGG reaction {kegg_reaction_id} could not "
                    "be parsed into a bare KEGG compound id with a plain integer coefficient"
                ),
            )
        )

    if connectors.kegg is None:
        return ()

    participants: list[ReactionParticipantIdentity] = []
    sides = (
        (parsed.reactants, ReactionParticipantRole.REACTANT),
        (parsed.products, ReactionParticipantRole.PRODUCT),
    )
    for terms, role in sides:
        for term in terms:
            compound_id = _resolve_one_participant_compound(
                kegg_compound_id=term.kegg_compound_id,
                kegg_reaction_id=kegg_reaction_id,
                connectors=connectors,
                compound_lookup=compound_lookup,
                session=session,
                state=state,
                max_connector_calls=max_connector_calls,
                compound_cache=compound_cache,
            )
            if compound_id is not None:
                participants.append(
                    ReactionParticipantIdentity(
                        compound_id=compound_id,
                        role=role,
                        stoichiometry=term.coefficient,
                        compartment_id=reference_compartment_id,
                    )
                )
    return tuple(participants)


def _resolve_one_participant_compound(
    *,
    kegg_compound_id: str,
    kegg_reaction_id: str,
    connectors: PathwayConnectorBundle,
    compound_lookup,
    session: Session,
    state: CurationRunState,
    max_connector_calls: int,
    compound_cache: dict[str, UUID | None],
) -> UUID | None:
    """Resolve one KEGG compound id referenced by a reaction's equation, reusing whatever
    an earlier reaction in this same run already found or failed to find for that exact id
    (``compound_cache``) -- fatty-acid-biosynthesis-shaped pathways reuse a handful of
    compounds (acetyl-CoA, NADPH, ...) across many reactions, and this avoids re-fetching
    the identical KEGG compound record once per reaction that names it."""
    frontier_id = build_frontier_id(
        entity_kind=EntityKind.COMPOUND,
        reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
        anchor=f"{kegg_reaction_id}:{kegg_compound_id}",
    )
    if kegg_compound_id in compound_cache:
        cached = compound_cache[kegg_compound_id]
        if cached is None:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=frontier_id,
                    entity_kind=EntityKind.COMPOUND,
                    reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
                    priority=1,
                    entity_text=kegg_compound_id,
                    attempted_sources=(SourceType.KEGG,),
                    notes=(
                        f"KEGG compound {kegg_compound_id} already failed to resolve "
                        "earlier this run"
                    ),
                )
            )
        else:
            state.resolve_frontier(frontier_id)
        return cached

    if state.connector_calls_made >= max_connector_calls:
        compound_cache[kegg_compound_id] = None
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=frontier_id,
                entity_kind=EntityKind.COMPOUND,
                reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
                priority=1,
                entity_text=kegg_compound_id,
                attempted_sources=(SourceType.KEGG,),
                notes="connector-call budget exhausted before this participant could be resolved",
            )
        )
        return None

    query = query_identity(
        connector=SourceType.KEGG, action="fetch_compound", kegg_compound_id=kegg_compound_id
    )
    try:
        outcome = strategies.resolve_participant_compound(
            connectors.kegg, kegg_compound_id, lookup=compound_lookup, session=session
        )
    except ConnectorError as exc:
        state.warn(f"KEGG compound fetch failed for {kegg_compound_id}: {exc}")
        compound_cache[kegg_compound_id] = None
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=frontier_id,
                entity_kind=EntityKind.COMPOUND,
                reason=FrontierReason.SOURCE_FAILURE,
                priority=1,
                entity_text=kegg_compound_id,
                attempted_sources=(SourceType.KEGG,),
                notes=str(exc),
            )
        )
        state.record_connector_call()
        state.record_query(query, display_text=f"KEGG compound fetch: {kegg_compound_id}")
        return None
    state.record_connector_call()
    state.record_query(query, display_text=f"KEGG compound fetch: {kegg_compound_id}")

    if outcome.entity_id is not None:
        compound_cache[kegg_compound_id] = outcome.entity_id
        state.record_entity(outcome.entity_id)
        state.resolve_frontier(frontier_id)
        return outcome.entity_id

    compound_cache[kegg_compound_id] = None
    state.add_frontier(
        CurationFrontierItem(
            frontier_id=frontier_id,
            entity_kind=EntityKind.COMPOUND,
            reason=outcome.frontier_reason,
            priority=1,
            entity_text=kegg_compound_id,
            attempted_sources=(SourceType.KEGG,),
            notes=outcome.notes,
        )
    )
    return None


def _discover_catalysts_from_reactions(
    *,
    connectors: PathwayConnectorBundle,
    organism_id: UUID,
    organism_text: str,
    protein_lookup,
    session: Session,
    state: CurationRunState,
    discovered_reaction_ids: list[UUID],
    resolved_protein_ec_numbers: list[tuple[UUID, str]],
) -> None:
    """Autonomous catalyst-candidate discovery from already-resolved reaction evidence
    (Increment C.1, F2) -- runs whenever UniProt is configured and at least one
    reaction has been discovered, **regardless of whether ``request.seed_entity_texts``
    was ever supplied**. This is the correction for Pilot 1 Run 1's finding that
    catalyst/gene/protein discovery previously had no path independent of an explicit
    seed list.

    For every discovered reaction's own already-persisted ``ec_number`` (KEGG's
    ``ENZYME`` annotation, via ``reaction_identity_from_kegg`` -- never guessed), this
    searches UniProt, organism-scoped, for candidate proteins
    (``strategies.discover_catalyst_candidates_by_ec_number``). A resolved candidate is
    appended to ``resolved_protein_ec_numbers`` exactly like a seed-resolved one --
    ``_associate_catalysts``/``_discover_kinetics`` require no changes at all to pick
    it up, since both already iterate that same list. An EC number that resolves to
    more than one distinct candidate (a real isozyme pair, confirmed live during this
    increment's own implementation -- e.g. yeast's cytosolic ACC1 vs. mitochondrial
    HFA1, both genuinely EC 6.4.1.2) is never resolved arbitrarily: it becomes a
    ``REACTION_CATALYST_UNRESOLVED`` frontier item, exactly like an EC number with no
    candidate at all -- discovery, never invented evidence (Increment C.1
    instructions, Step 16).
    """
    from app.models.reaction import Reaction as ReactionModel

    seen_ec_numbers: set[str] = set()
    for reaction_id in discovered_reaction_ids:
        reaction_row = session.get(ReactionModel, reaction_id)
        if reaction_row is None or not reaction_row.ec_number:
            continue
        ec_number = reaction_row.ec_number
        if ec_number in seen_ec_numbers:
            continue
        seen_ec_numbers.add(ec_number)

        query_id = query_identity(
            connector=SourceType.UNIPROT,
            action="discover_catalyst_by_ec",
            ec_number=ec_number,
            organism_id=str(organism_id),
        )
        if state.has_run_query(query_id):
            continue

        frontier_id = build_frontier_id(
            entity_kind=EntityKind.PROTEIN,
            reason=FrontierReason.REACTION_CATALYST_UNRESOLVED,
            anchor=f"{reaction_id}:{ec_number}",
        )
        try:
            outcome = strategies.discover_catalyst_candidates_by_ec_number(
                connectors.uniprot,
                ec_number,
                organism_id=organism_id,
                organism_context_text=organism_text,
                lookup=protein_lookup,
                session=session,
            )
        except ConnectorError as exc:
            state.warn(f"UniProt catalyst discovery failed for EC {ec_number}: {exc}")
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=frontier_id,
                    entity_kind=EntityKind.PROTEIN,
                    reason=FrontierReason.SOURCE_FAILURE,
                    priority=3,
                    entity_text=ec_number,
                    attempted_sources=(SourceType.UNIPROT,),
                    notes=str(exc),
                )
            )
            state.record_connector_call()
            state.record_query(query_id, display_text=f"UniProt catalyst discovery: EC {ec_number}")
            continue
        state.record_connector_call()
        state.record_query(query_id, display_text=f"UniProt catalyst discovery: EC {ec_number}")

        if outcome.entity_id is not None:
            state.record_entity(outcome.entity_id)
            resolved_protein_ec_numbers.append((outcome.entity_id, ec_number))
            state.resolve_frontier(frontier_id)
        else:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=frontier_id,
                    entity_kind=EntityKind.PROTEIN,
                    reason=FrontierReason.REACTION_CATALYST_UNRESOLVED,
                    priority=3,
                    entity_text=ec_number,
                    attempted_sources=(SourceType.UNIPROT,),
                    notes=outcome.notes,
                )
            )


def _associate_catalysts(
    *,
    session: Session,
    state: CurationRunState,
    resolved_protein_ec_numbers: list[tuple[UUID, str]],
    discovered_reaction_ids: list[UUID],
    reaction_enzyme_lookup,
) -> None:
    """Create/reuse a ``ReactionEnzyme`` association only where the run's own conservative
    evidence hierarchy actually supports one (Step 17): the catalyst was explicitly named in
    the request's own ``seed_entity_texts`` (a request-scoped human assertion -- the
    strongest evidence this executor has access to; no connector in this repository exposes
    a structured, organism-specific reaction<->gene mapping, see
    ``app.normalization.reaction_enzyme``'s own module docstring), *and* that protein's own
    already-persisted EC number exactly matches this specific reaction's own EC number.

    EC-number equality alone, for a protein the caller never seeded, is never sufficient by
    itself and never used here -- two proteins sharing one EC number are never both
    associated with a reaction merely because their EC numbers match; only the one the
    caller actually named is ever considered (see
    ``tests/pathway_curation/test_executor.py
    ::test_catalyst_association_is_conservative_about_shared_ec_numbers``).
    """
    from app.models.reaction import Reaction as ReactionModel

    for reaction_id in discovered_reaction_ids:
        reaction_row = session.get(ReactionModel, reaction_id)
        if reaction_row is None or not reaction_row.ec_number:
            continue
        for protein_id, ec_number in sorted(
            set(resolved_protein_ec_numbers), key=lambda pair: (pair[1], str(pair[0]))
        ):
            if ec_number != reaction_row.ec_number:
                continue
            identity = query_identity(
                connector=SourceType.OTHER,
                action="associate_catalyst",
                reaction_id=str(reaction_id),
                protein_id=str(protein_id),
            )
            if state.has_run_query(identity):
                continue

            outcome = strategies.associate_catalyst(
                reaction_id=reaction_id,
                protein_id=protein_id,
                relationship="CATALYZES",
                session=session,
                lookup=reaction_enzyme_lookup,
            )
            state.record_query(
                identity,
                display_text=(
                    f"associate catalyst: reaction {reaction_id} <- protein {protein_id} "
                    f"(EC {ec_number})"
                ),
            )

            frontier_id = build_frontier_id(
                entity_kind=EntityKind.PROTEIN,
                reason=outcome.frontier_reason or FrontierReason.REACTION_ENZYME_PERSISTENCE_FAILED,
                anchor=f"{reaction_id}:{protein_id}",
            )
            if outcome.entity_id is not None:
                state.resolve_frontier(frontier_id)
            else:
                state.add_frontier(
                    CurationFrontierItem(
                        frontier_id=frontier_id,
                        entity_kind=EntityKind.PROTEIN,
                        reason=outcome.frontier_reason,
                        priority=1,
                        entity_id=protein_id,
                        entity_text=str(protein_id),
                        notes=outcome.notes,
                    )
                )


def _record_regulation_not_supported(
    request: PathwayCurationRequest, *, state: CurationRunState
) -> None:
    """Step 23: ``include_regulation=True``/``include_enzyme_states=True`` is a valid,
    non-rejected request field, but this executor has no discovery route for either in this
    increment -- this frontier item makes that explicit rather than letting the flag
    misleadingly imply comprehensive regulatory curation was attempted."""
    state.add_frontier(
        CurationFrontierItem(
            frontier_id=build_frontier_id(
                entity_kind=EntityKind.UNKNOWN,
                reason=FrontierReason.REGULATION_REQUESTED_NOT_SUPPORTED,
                anchor=request.biological_process,
            ),
            entity_kind=EntityKind.UNKNOWN,
            reason=FrontierReason.REGULATION_REQUESTED_NOT_SUPPORTED,
            priority=1,
            entity_text=request.biological_process,
            notes=(
                "include_regulation/include_enzyme_states was requested, but this executor "
                "has no regulation/enzyme-state discovery route in this increment "
                "(see docs/26_autonomous_pathway_curation_planner.md §25)"
            ),
        )
    )


def _record_kinetics_not_attempted(*, state: CurationRunState) -> None:
    """``include_kinetics=True`` was requested, but no seeded protein carried a resolvable
    EC number -- there is nothing to search SABIO-RK/OED against, so kinetics enrichment is
    never even attempted, distinct from ``MISSING_KINETICS`` (attempted, nothing found)."""
    state.add_frontier(
        CurationFrontierItem(
            frontier_id=build_frontier_id(
                entity_kind=EntityKind.PROTEIN,
                reason=FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED,
                anchor="kinetics",
            ),
            entity_kind=EntityKind.PROTEIN,
            reason=FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED,
            priority=1,
            entity_text="kinetics",
            notes=(
                "include_kinetics was requested, but no seeded protein carried a resolvable "
                "EC number to search kinetics sources against -- kinetics enrichment was "
                "never attempted"
            ),
        )
    )


def _resolve_seeded_genes_and_proteins(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    organism_id: UUID,
    gene_lookup,
    protein_lookup,
    session: Session,
    state: CurationRunState,
    resolved_protein_ec_numbers: list[tuple[UUID, str]],
) -> None:
    for gene_text in request.seed_entity_texts:
        if gene_text in request.exclusions:
            continue

        if connectors.sgd is None:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=build_frontier_id(
                        entity_kind=EntityKind.GENE,
                        reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                        anchor=gene_text,
                    ),
                    entity_kind=EntityKind.GENE,
                    reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                    priority=3,
                    entity_text=gene_text,
                    notes="no SGD connector configured",
                )
            )
            continue

        gene_query_identity = query_identity(
            connector=SourceType.SGD,
            action="resolve_gene",
            query=gene_text,
            organism_id=str(organism_id),
        )
        gene_frontier_id = build_frontier_id(
            entity_kind=EntityKind.GENE, reason=FrontierReason.UNRESOLVED_CATALYST, anchor=gene_text
        )
        gene_entity_id: UUID | None = None
        if not state.has_run_query(gene_query_identity):
            try:
                gene_outcome = strategies.resolve_gene_by_text(
                    connectors.sgd,
                    gene_text,
                    organism_id=organism_id,
                    organism_context_text=request.organism_text,
                    lookup=gene_lookup,
                    session=session,
                )
            except ConnectorError as exc:
                state.warn(f"SGD gene resolution failed for {gene_text}: {exc}")
                state.add_frontier(
                    CurationFrontierItem(
                        frontier_id=gene_frontier_id,
                        entity_kind=EntityKind.GENE,
                        reason=FrontierReason.SOURCE_FAILURE,
                        priority=3,
                        entity_text=gene_text,
                        attempted_sources=(SourceType.SGD,),
                        notes=str(exc),
                    )
                )
                state.record_connector_call()
                state.record_query(
                    gene_query_identity, display_text=f"SGD gene search: {gene_text}"
                )
                continue
            state.record_connector_call()
            state.record_query(gene_query_identity, display_text=f"SGD gene search: {gene_text}")

            if gene_outcome.entity_id is not None:
                gene_entity_id = gene_outcome.entity_id
                state.record_entity(gene_entity_id)
                state.resolve_frontier(gene_frontier_id)
            else:
                state.add_frontier(
                    CurationFrontierItem(
                        frontier_id=gene_frontier_id,
                        entity_kind=EntityKind.GENE,
                        reason=gene_outcome.frontier_reason,
                        priority=3,
                        entity_text=gene_text,
                        attempted_sources=(SourceType.SGD,),
                        notes=gene_outcome.notes,
                    )
                )

        if connectors.uniprot is None:
            continue

        protein_query_identity = query_identity(
            connector=SourceType.UNIPROT,
            action="resolve_protein",
            query=gene_text,
            organism_id=str(organism_id),
        )
        if state.has_run_query(protein_query_identity):
            continue
        protein_frontier_id = build_frontier_id(
            entity_kind=EntityKind.PROTEIN,
            reason=FrontierReason.UNRESOLVED_CATALYST,
            anchor=gene_text,
        )
        try:
            protein_outcome = strategies.resolve_protein_by_text(
                connectors.uniprot,
                gene_text,
                organism_id=organism_id,
                organism_context_text=request.organism_text,
                lookup=protein_lookup,
                session=session,
            )
        except ConnectorError as exc:
            state.warn(f"UniProt protein resolution failed for {gene_text}: {exc}")
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=protein_frontier_id,
                    entity_kind=EntityKind.PROTEIN,
                    reason=FrontierReason.SOURCE_FAILURE,
                    priority=3,
                    entity_text=gene_text,
                    attempted_sources=(SourceType.UNIPROT,),
                    notes=str(exc),
                )
            )
            state.record_connector_call()
            state.record_query(
                protein_query_identity, display_text=f"UniProt protein search: {gene_text}"
            )
            continue
        state.record_connector_call()
        state.record_query(
            protein_query_identity, display_text=f"UniProt protein search: {gene_text}"
        )

        if protein_outcome.entity_id is not None:
            state.record_entity(protein_outcome.entity_id)
            state.resolve_frontier(protein_frontier_id)
            for ec_number in _ec_numbers_for_protein(session, protein_outcome.entity_id):
                resolved_protein_ec_numbers.append((protein_outcome.entity_id, ec_number))
        else:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=protein_frontier_id,
                    entity_kind=EntityKind.PROTEIN,
                    reason=protein_outcome.frontier_reason,
                    priority=3,
                    entity_text=gene_text,
                    attempted_sources=(SourceType.UNIPROT,),
                    notes=protein_outcome.notes,
                )
            )


def _discover_publications(
    request: PathwayCurationRequest,
    *,
    connectors: PathwayConnectorBundle,
    publication_lookup,
    session: Session,
    state: CurationRunState,
) -> None:
    if connectors.pubmed is None:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.PUBLICATION,
                    reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                    anchor=request.biological_process,
                ),
                entity_kind=EntityKind.PUBLICATION,
                reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                priority=5,
                entity_text=request.biological_process,
                notes="no PubMed connector configured",
            )
        )
        return

    query = f"{request.organism_text} {request.biological_process}"
    identity = query_identity(
        connector=SourceType.PUBMED, action="discover_publications", query=query
    )
    if state.has_run_query(identity):
        return
    try:
        pmids = strategies.discover_publications(
            connectors.pubmed, query, max_results=request.max_publications
        )
    except ConnectorError as exc:
        state.warn(f"PubMed discovery failed: {exc}")
        state.record_connector_call()
        state.record_query(identity, display_text=f"PubMed search: {query!r}")
        return
    state.record_connector_call()
    state.record_query(identity, display_text=f"PubMed search: {query!r}")

    for pmid in pmids:
        if len(state.discovered_publication_ids) >= request.max_publications:
            break
        fetch_identity = query_identity(
            connector=SourceType.PUBMED, action="fetch_publication", pmid=pmid
        )
        if state.has_run_query(fetch_identity):
            continue
        outcome = strategies.resolve_publication_by_pmid(
            connectors.pubmed, pmid, lookup=publication_lookup, session=session
        )
        state.record_connector_call()
        state.record_query(fetch_identity, display_text=f"PubMed fetch: {pmid}")
        frontier_id = build_frontier_id(
            entity_kind=EntityKind.PUBLICATION,
            reason=outcome.frontier_reason or FrontierReason.MISSING_PUBLICATION,
            anchor=pmid,
        )
        if outcome.entity_id is not None:
            state.record_publication(outcome.entity_id)
            state.resolve_frontier(frontier_id)
        else:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=frontier_id,
                    entity_kind=EntityKind.PUBLICATION,
                    reason=outcome.frontier_reason,
                    priority=5,
                    entity_text=pmid,
                    attempted_sources=(SourceType.PUBMED,),
                    notes=outcome.notes,
                )
            )

    if not pmids:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.PUBLICATION,
                    reason=FrontierReason.MISSING_PUBLICATION,
                    anchor=query,
                ),
                entity_kind=EntityKind.PUBLICATION,
                reason=FrontierReason.MISSING_PUBLICATION,
                priority=5,
                entity_text=query,
                attempted_sources=(SourceType.PUBMED,),
                notes="no PubMed hits for this query",
            )
        )


def _ec_numbers_for_protein(session: Session, protein_id: UUID) -> tuple[str, ...]:
    """The EC number(s) already recorded on one just-resolved ``Protein`` row, if any.

    A plain ``session.get`` read -- the row is queryable even before the
    caller's own transaction commits, since every ``persist_X`` in this
    codebase flushes (Increment C never introduces a second commit
    convention). Never guesses an EC number that was not already stored.
    """
    from app.models.protein import Protein

    protein = session.get(Protein, protein_id)
    if protein is None or not protein.ec_number:
        return ()
    return (protein.ec_number,)


def _discover_kinetics(
    *,
    connectors: PathwayConnectorBundle,
    organism_text: str,
    organism_id: UUID,
    resolved_protein_ec_numbers: list[tuple[UUID, str]],
    session: Session,
    state: CurationRunState,
) -> None:
    """Discover kinetics for every (protein, EC number) pair already resolved this run
    (never a bare EC number guessed from a reaction or gene symbol alone -- see
    ``_ec_numbers_for_protein``). ``protein_id``/``organism_id`` are always threaded into the
    persisted measurement so it is actually linked, never an orphaned row invisible to
    ``app.agent1.service.get_agent1_knowledge_package``'s own organism scoping.

    **SABIO-RK and Open Enzyme Database are both tried, independently** (Increment C
    pre-commit revision -- previously OED was built but never wired in here). A configured
    connector's own failure never destroys structural pathway curation and never blocks the
    other kinetics source: each is attempted, and each failure is recorded as its own
    warning, never raised. A completely unconfigured connector (either or both fields left
    ``None`` on ``PathwayConnectorBundle``) is likewise never an error -- it simply cannot
    contribute, and this function still runs whichever source(s) *are* configured.
    """
    if connectors.sabiork is None and connectors.oed is None:
        state.add_frontier(
            CurationFrontierItem(
                frontier_id=build_frontier_id(
                    entity_kind=EntityKind.PROTEIN,
                    reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                    anchor="kinetics",
                ),
                entity_kind=EntityKind.PROTEIN,
                reason=FrontierReason.NO_CONNECTOR_AVAILABLE,
                priority=6,
                entity_text="kinetics",
                notes="no SABIO-RK or Open Enzyme Database connector configured",
            )
        )
        return

    from app.persistence.kinetic_measurement import persist_kinetic_measurement

    for protein_id, ec_number in sorted(
        set(resolved_protein_ec_numbers), key=lambda pair: (pair[1], str(pair[0]))
    ):
        found_any = False
        attempted_sources: list[SourceType] = []

        if connectors.sabiork is not None:
            identity = query_identity(
                connector=SourceType.SABIORK,
                action="discover_kinetics",
                ec_number=ec_number,
                protein_id=str(protein_id),
            )
            if not state.has_run_query(identity):
                attempted_sources.append(SourceType.SABIORK)
                try:
                    identities = strategies.discover_kinetics_sabiork(
                        connectors.sabiork,
                        ec_number,
                        organism=organism_text,
                        protein_id=protein_id,
                        organism_id=organism_id,
                    )
                except ConnectorError as exc:
                    state.warn(f"SABIO-RK kinetics discovery failed for EC {ec_number}: {exc}")
                    identities = ()
                state.record_connector_call()
                state.record_query(identity, display_text=f"SABIO-RK search: EC {ec_number}")
                found_any = found_any or bool(identities)
                for kinetic_identity in identities:
                    try:
                        persist_kinetic_measurement(kinetic_identity, session=session)
                    except (TypeError, ValueError) as exc:  # pragma: no cover -- defensive
                        raise CurationExecutionError(
                            f"failed to persist kinetic measurement for EC {ec_number}: {exc}"
                        ) from exc

        if connectors.oed is not None:
            identity = query_identity(
                connector=SourceType.OED,
                action="discover_kinetics",
                ec_number=ec_number,
                protein_id=str(protein_id),
            )
            if not state.has_run_query(identity):
                attempted_sources.append(SourceType.OED)
                try:
                    identities = strategies.discover_kinetics_oed(
                        connectors.oed,
                        ec_number,
                        organism=organism_text,
                        protein_id=protein_id,
                        organism_id=organism_id,
                    )
                except ConnectorError as exc:
                    state.warn(f"Open Enzyme Database discovery failed for EC {ec_number}: {exc}")
                    identities = ()
                state.record_connector_call()
                state.record_query(identity, display_text=f"OED search: EC {ec_number}")
                found_any = found_any or bool(identities)
                for kinetic_identity in identities:
                    try:
                        persist_kinetic_measurement(kinetic_identity, session=session)
                    except (TypeError, ValueError) as exc:  # pragma: no cover -- defensive
                        raise CurationExecutionError(
                            f"failed to persist kinetic measurement for EC {ec_number}: {exc}"
                        ) from exc

        if attempted_sources and not found_any:
            state.add_frontier(
                CurationFrontierItem(
                    frontier_id=build_frontier_id(
                        entity_kind=EntityKind.PROTEIN,
                        reason=FrontierReason.MISSING_KINETICS,
                        anchor=ec_number,
                    ),
                    entity_kind=EntityKind.PROTEIN,
                    reason=FrontierReason.MISSING_KINETICS,
                    priority=6,
                    entity_text=ec_number,
                    attempted_sources=tuple(attempted_sources),
                    notes="no kinetic measurements found from any configured source",
                )
            )


def _build_result(
    *,
    request: PathwayCurationRequest,
    plan,
    state: CurationRunState,
    iterations: tuple[CurationIterationRecord, ...],
    organism_id: UUID | None,
    gap_ids: tuple[UUID, ...],
    hard_blocker: str | None,
    session: Session,
) -> PathwayCurationResult:
    """Assemble the terminal ``PathwayCurationResult``, always via the real Agent 1 export
    chain (Step 41: Agent 1's own output remains ``Agent1CuratedKnowledgeView`` regardless of
    how far the run progressed)."""
    frontier = state.snapshot_frontier()
    last_iteration_no_progress = bool(iterations) and iterations[-1].no_progress
    completion_status, completion_reasons = assess_completion(
        request=request,
        frontier=frontier,
        connector_calls_made=state.connector_calls_made,
        iterations_completed=len(iterations),
        hard_blocker=hard_blocker,
        no_progress=last_iteration_no_progress,
    )

    package = get_agent1_knowledge_package(session, organism_id=organism_id)
    view = get_agent1_curated_knowledge_view(package)
    agent2_readiness = validate_agent2_readiness(view, package)

    return PathwayCurationResult(
        request=request,
        final_plan=plan,
        iterations=iterations,
        iterations_completed=len(iterations),
        connector_calls_made=state.connector_calls_made,
        queries_executed=tuple(state.queries_executed),
        organism_id=organism_id,
        discovered_entity_ids=tuple(state.discovered_entity_ids),
        discovered_reaction_ids=tuple(state.discovered_reaction_ids),
        discovered_publication_ids=tuple(state.discovered_publication_ids),
        unresolved_frontier=frontier,
        knowledge_gap_ids=gap_ids,
        completion_status=completion_status,
        completion_reasons=completion_reasons,
        warnings=tuple(state.warnings),
        agent1_knowledge_package=package,
        curated_knowledge_view=view,
        agent2_readiness=agent2_readiness,
    )


__all__ = ["PathwayConnectorBundle", "execute_pathway_curation"]
