"""Applying one Network normalization decision to the database (KEGG NETWORK
support increment).

Follows this package's own established ``persist_*`` shape exactly (see
``app.persistence.compound``/``app.persistence.reaction``): ``MATCHED`` ->
``REUSED_EXISTING``, ``NEW`` -> ``CREATED`` (or ``FAILED`` on a database
integrity violation), ``AMBIGUOUS``/``CONFLICTED`` -> ``REQUIRES_REVIEW``,
``UNRESOLVED`` -> ``NO_ACTION``.

**On ``MATCHED`` (reuse), nodes/edges/perturbations/pathways/diseases/
publications are never touched.** This mirrors ``persist_reaction``'s own
documented policy exactly (confirmed by
``tests/persistence/test_reaction.py::
test_matched_reuses_existing_row_and_never_touches_participants``): once a
network is recognized as already existing, re-importing it is a safe
no-op at the topology level too, never a silent overwrite or a duplicate
insert. This is also exactly what makes repeated import of the identical
record idempotent (task's own requirement): the first import (``NEW``)
creates the full topology once; every subsequent import of the identical
record is ``MATCHED`` and adds nothing. Reconciling a network whose own
upstream KEGG record has genuinely changed since it was first imported is
out of scope for this increment (the task's own "do not overcomplicate the
initial implementation").

Network creation is one atomic unit: the network row, every node, every
edge, every perturbation, and every pathway/disease/publication
association are created inside one ``SAVEPOINT``
(``session.begin_nested()``), exactly mirroring ``persist_reaction``'s own
reaction-plus-participants atomicity -- a database integrity violation
partway through rolls back the whole network, never leaving a partially-
imported one behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    NetworkEdgeInteractionType,
    NetworkNodeType,
    NetworkPerturbationType,
)
from app.models.network import (
    Network,
    NetworkDisease,
    NetworkEdge,
    NetworkNode,
    NetworkPathway,
    NetworkPerturbation,
    NetworkPublication,
)
from app.normalization.network import NetworkIdentity, NetworkNodeMappingResult
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.provenance import (
    ExternalRecordProvenance,
    attach_source_cross_reference,
    record_external_record,
)
from app.persistence.types import PersistenceAction, PersistenceResult

_ENTITY_TYPE = "network"


@dataclass(frozen=True, slots=True)
class NetworkNodeInput:
    """One node to persist, with its already-computed mapping decision.

    ``mapping`` is computed beforehand by
    ``app.normalization.network.map_network_node`` -- this module never
    itself decides whether a node maps to an existing entity, only applies
    a decision already made.
    """

    raw_token: str | None
    node_type: NetworkNodeType
    mapping: NetworkNodeMappingResult
    external_identifier: str | None = None
    display_name: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkEdgeInput:
    """One edge to persist, referencing its endpoints by the same
    ``raw_token`` the corresponding ``NetworkNodeInput`` carries."""

    source_raw_token: str
    target_raw_token: str
    interaction_type: NetworkEdgeInteractionType
    raw_operator: str | None = None
    reversible: bool | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkPerturbationInput:
    """One perturbation to persist. ``target_raw_token``, when given, must
    match a ``NetworkNodeInput.raw_token`` in the same import."""

    perturbation_type: NetworkPerturbationType
    external_identifier: str | None = None
    name: str | None = None
    target_raw_token: str | None = None
    effect: str | None = None
    description: str | None = None
    raw_value: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkPathwayInput:
    kegg_pathway_id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkDiseaseInput:
    kegg_disease_id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkPublicationInput:
    """``publication_id``, when given, is whatever an earlier call into the
    existing ``app.normalization.publication``/``app.persistence.publication``
    pipeline already resolved for this reference's own PMID -- this module
    never resolves a publication itself (task's own "do not create
    redundant publication/provenance infrastructure")."""

    raw_reference: str
    pmid: str | None = None
    publication_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NetworkImportInput:
    """Everything needed to persist one complete network import."""

    identity: NetworkIdentity
    nodes: tuple[NetworkNodeInput, ...] = ()
    edges: tuple[NetworkEdgeInput, ...] = ()
    perturbations: tuple[NetworkPerturbationInput, ...] = ()
    pathways: tuple[NetworkPathwayInput, ...] = ()
    diseases: tuple[NetworkDiseaseInput, ...] = ()
    publications: tuple[NetworkPublicationInput, ...] = ()


def persist_network(
    import_input: NetworkImportInput,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None = None,
) -> PersistenceResult:
    """Apply one Network normalization decision to the database."""
    if result.entity_type != _ENTITY_TYPE:
        raise EntityTypeMismatchError(
            f"persist_network received a result for entity_type={result.entity_type!r}, "
            f"expected {_ENTITY_TYPE!r}"
        )

    if result.status is NormalizationStatus.MATCHED:
        assert result.matched_entity_id is not None
        return _reuse(import_input, result, session=session, provenance=provenance)

    if result.status is NormalizationStatus.NEW:
        return _create(import_input, result, session=session, provenance=provenance)

    if result.status in (NormalizationStatus.AMBIGUOUS, NormalizationStatus.CONFLICTED):
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.REQUIRES_REVIEW,
            entity_type=_ENTITY_TYPE,
            review_required=True,
            reason=result.reason or f"{result.status} network identity requires review",
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.NO_ACTION,
        entity_type=_ENTITY_TYPE,
        reason=result.reason or "unresolved network identity: no action taken",
    )


def _reuse(
    import_input: NetworkImportInput,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    identity = import_input.identity
    entity_id = result.matched_entity_id
    assert entity_id is not None
    cross_reference_id = attach_source_cross_reference(
        session,
        entity_type=_ENTITY_TYPE,
        entity_id=entity_id,
        source=identity.source,
        external_id=identity.source_identifier,
    )
    external_record_id = (
        record_external_record(session, source=identity.source, provenance=provenance)
        if provenance is not None
        else None
    )
    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.REUSED_EXISTING,
        entity_type=_ENTITY_TYPE,
        entity_id=entity_id,
        reused=True,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="reused existing matched network; topology/children left unchanged",
    )


def _node_fk_kwargs(mapping: NetworkNodeMappingResult) -> dict[str, UUID | None]:
    return {
        "gene_id": mapping.gene_id,
        "protein_id": mapping.protein_id,
        "complex_id": mapping.complex_id,
        "compound_id": mapping.compound_id,
        "reaction_id": mapping.reaction_id,
    }


def _create(
    import_input: NetworkImportInput,
    result: NormalizationResult,
    *,
    session: Session,
    provenance: ExternalRecordProvenance | None,
) -> PersistenceResult:
    identity = import_input.identity

    try:
        with session.begin_nested():
            network_row = Network(
                source_type=identity.source,
                external_id=identity.source_identifier,
                name=identity.name,
                network_type=identity.network_type,
                definition=identity.definition,
                expanded_definition=identity.expanded_definition,
                organism_id=identity.organism_id,
            )
            session.add(network_row)
            session.flush()

            node_id_by_raw_token: dict[str, UUID] = {}
            for node_input in import_input.nodes:
                node_row = NetworkNode(
                    network_id=network_row.id,
                    external_identifier=node_input.external_identifier,
                    display_name=node_input.display_name,
                    node_type=node_input.node_type,
                    mapping_status=node_input.mapping.status,
                    raw_token=node_input.raw_token,
                    notes=node_input.notes,
                    **_node_fk_kwargs(node_input.mapping),
                )
                session.add(node_row)
                session.flush()
                if node_input.raw_token is not None:
                    node_id_by_raw_token[node_input.raw_token] = node_row.id

            for edge_input in import_input.edges:
                source_id = node_id_by_raw_token.get(edge_input.source_raw_token)
                target_id = node_id_by_raw_token.get(edge_input.target_raw_token)
                if source_id is None or target_id is None:
                    raise ValueError(
                        "edge references a raw_token with no corresponding node in this "
                        f"import: {edge_input.source_raw_token!r} -> "
                        f"{edge_input.target_raw_token!r}"
                    )
                session.add(
                    NetworkEdge(
                        network_id=network_row.id,
                        source_node_id=source_id,
                        target_node_id=target_id,
                        interaction_type=edge_input.interaction_type,
                        raw_operator=edge_input.raw_operator,
                        reversible=edge_input.reversible,
                        notes=edge_input.notes,
                    )
                )
            if import_input.edges:
                session.flush()

            for perturbation_input in import_input.perturbations:
                target_node_id = (
                    node_id_by_raw_token.get(perturbation_input.target_raw_token)
                    if perturbation_input.target_raw_token is not None
                    else None
                )
                session.add(
                    NetworkPerturbation(
                        network_id=network_row.id,
                        perturbation_type=perturbation_input.perturbation_type,
                        external_identifier=perturbation_input.external_identifier,
                        name=perturbation_input.name,
                        target_node_id=target_node_id,
                        effect=perturbation_input.effect,
                        description=perturbation_input.description,
                        raw_value=perturbation_input.raw_value,
                        notes=perturbation_input.notes,
                    )
                )

            for pathway_input in import_input.pathways:
                session.add(
                    NetworkPathway(
                        network_id=network_row.id,
                        kegg_pathway_id=pathway_input.kegg_pathway_id,
                        name=pathway_input.name,
                    )
                )

            for disease_input in import_input.diseases:
                session.add(
                    NetworkDisease(
                        network_id=network_row.id,
                        kegg_disease_id=disease_input.kegg_disease_id,
                        name=disease_input.name,
                    )
                )

            for publication_input in import_input.publications:
                session.add(
                    NetworkPublication(
                        network_id=network_row.id,
                        publication_id=publication_input.publication_id,
                        raw_reference=publication_input.raw_reference,
                        pmid=publication_input.pmid,
                    )
                )

            if (
                import_input.perturbations
                or import_input.pathways
                or import_input.diseases
                or import_input.publications
            ):
                session.flush()

            cross_reference_id = attach_source_cross_reference(
                session,
                entity_type=_ENTITY_TYPE,
                entity_id=network_row.id,
                source=identity.source,
                external_id=identity.source_identifier,
            )
            external_record_id = (
                record_external_record(session, source=identity.source, provenance=provenance)
                if provenance is not None
                else None
            )
    except IntegrityError as exc:
        return PersistenceResult(
            normalization_status=result.status,
            action=PersistenceAction.FAILED,
            entity_type=_ENTITY_TYPE,
            reason=(
                "network creation rolled back as a unit due to a database integrity "
                f"violation: {exc.orig}"
            ),
        )

    return PersistenceResult(
        normalization_status=result.status,
        action=PersistenceAction.CREATED,
        entity_type=_ENTITY_TYPE,
        entity_id=network_row.id,
        created=True,
        source_cross_reference_id=cross_reference_id,
        external_record_id=external_record_id,
        reason="created new network with its topology",
    )


__all__ = [
    "NetworkDiseaseInput",
    "NetworkEdgeInput",
    "NetworkImportInput",
    "NetworkNodeInput",
    "NetworkPathwayInput",
    "NetworkPerturbationInput",
    "NetworkPublicationInput",
    "persist_network",
]
