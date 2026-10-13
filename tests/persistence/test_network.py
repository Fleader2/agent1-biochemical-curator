"""Tests for ``app.persistence.network``."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.enums import (
    NetworkEdgeInteractionType,
    NetworkNodeMappingStatus,
    NetworkNodeType,
    NetworkPerturbationType,
    NetworkType,
    SourceType,
)
from app.models.network import Network, NetworkEdge, NetworkNode, NetworkPerturbation
from app.normalization.network import NetworkIdentity, NetworkNodeMappingResult
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
from app.persistence.errors import EntityTypeMismatchError
from app.persistence.network import (
    NetworkEdgeInput,
    NetworkImportInput,
    NetworkNodeInput,
    NetworkPerturbationInput,
    persist_network,
)
from app.persistence.provenance import ExternalRecordProvenance
from app.persistence.types import PersistenceAction


def _identity(**overrides) -> NetworkIdentity:
    merged = {
        "source": SourceType.KEGG,
        "source_identifier": "N00103",
        "name": "test network",
        "network_type": NetworkType.REFERENCE,
    } | overrides
    return NetworkIdentity(**merged)


def _result(status: NormalizationStatus, **overrides) -> NormalizationResult:
    merged = {
        "status": status,
        "source": SourceType.KEGG,
        "source_identifier": "N00103",
        "entity_type": "network",
        "match_method": MatchMethod.EXACT_IDENTIFIER,
    } | overrides
    return NormalizationResult(**merged)


def _mapped(entity_kind: str, entity_id) -> NetworkNodeMappingResult:
    return NetworkNodeMappingResult(
        status=NetworkNodeMappingStatus.MAPPED,
        node_type=NetworkNodeType.GENE,
        **{f"{entity_kind}_id": entity_id},
    )


def _unresolved(node_type: NetworkNodeType = NetworkNodeType.UNKNOWN) -> NetworkNodeMappingResult:
    return NetworkNodeMappingResult(status=NetworkNodeMappingStatus.UNRESOLVED, node_type=node_type)


def _three_node_chain() -> tuple[NetworkNodeInput, ...]:
    return (
        NetworkNodeInput(raw_token="A", node_type=NetworkNodeType.UNKNOWN, mapping=_unresolved()),
        NetworkNodeInput(raw_token="B", node_type=NetworkNodeType.UNKNOWN, mapping=_unresolved()),
        NetworkNodeInput(raw_token="C", node_type=NetworkNodeType.UNKNOWN, mapping=_unresolved()),
    )


def _two_edges() -> tuple[NetworkEdgeInput, ...]:
    return (
        NetworkEdgeInput(
            source_raw_token="A",
            target_raw_token="B",
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            raw_operator="->",
        ),
        NetworkEdgeInput(
            source_raw_token="B",
            target_raw_token="C",
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            raw_operator="->",
        ),
    )


def test_entity_type_mismatch_raises(db_session):
    result = _result(NormalizationStatus.NEW, entity_type="compound", match_method=MatchMethod.NONE)
    import_input = NetworkImportInput(identity=_identity())
    with pytest.raises(EntityTypeMismatchError):
        persist_network(import_input, result, session=db_session)


def test_new_network_creation(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    import_input = NetworkImportInput(identity=_identity())

    outcome = persist_network(import_input, result, session=db_session)

    assert outcome.action is PersistenceAction.CREATED
    assert outcome.entity_id is not None
    row = db_session.get(Network, outcome.entity_id)
    assert row.external_id == "N00103"
    assert row.source_type is SourceType.KEGG
    assert row.network_type is NetworkType.REFERENCE


def test_existing_network_reuse_never_touches_children(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    created = persist_network(
        NetworkImportInput(identity=_identity(), nodes=_three_node_chain(), edges=_two_edges()),
        result,
        session=db_session,
    )

    matched_result = _result(NormalizationStatus.MATCHED, matched_entity_id=created.entity_id)
    reuse_outcome = persist_network(
        NetworkImportInput(identity=_identity()), matched_result, session=db_session
    )

    assert reuse_outcome.action is PersistenceAction.REUSED_EXISTING
    assert reuse_outcome.entity_id == created.entity_id
    nodes = (
        db_session.execute(select(NetworkNode).where(NetworkNode.network_id == created.entity_id))
        .scalars()
        .all()
    )
    assert len(nodes) == 3


def test_nodes_created(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_network(
        NetworkImportInput(identity=_identity(), nodes=_three_node_chain()),
        result,
        session=db_session,
    )

    nodes = (
        db_session.execute(select(NetworkNode).where(NetworkNode.network_id == outcome.entity_id))
        .scalars()
        .all()
    )
    assert {node.raw_token for node in nodes} == {"A", "B", "C"}


def test_edges_created(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    outcome = persist_network(
        NetworkImportInput(identity=_identity(), nodes=_three_node_chain(), edges=_two_edges()),
        result,
        session=db_session,
    )

    edges = (
        db_session.execute(select(NetworkEdge).where(NetworkEdge.network_id == outcome.entity_id))
        .scalars()
        .all()
    )
    assert len(edges) == 2
    nodes_by_id = {
        node.id: node.raw_token
        for node in db_session.execute(
            select(NetworkNode).where(NetworkNode.network_id == outcome.entity_id)
        )
        .scalars()
        .all()
    }
    pairs = {(nodes_by_id[edge.source_node_id], nodes_by_id[edge.target_node_id]) for edge in edges}
    assert pairs == {("A", "B"), ("B", "C")}


def test_unresolved_nodes_still_persisted(db_session):
    """A node that could not be mapped to any entity is never discarded."""
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    node = NetworkNodeInput(
        raw_token="orphan-gene-9999",
        node_type=NetworkNodeType.GENE,
        mapping=_unresolved(NetworkNodeType.GENE),
    )

    outcome = persist_network(
        NetworkImportInput(identity=_identity(), nodes=(node,)), result, session=db_session
    )

    rows = (
        db_session.execute(select(NetworkNode).where(NetworkNode.network_id == outcome.entity_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].mapping_status is NetworkNodeMappingStatus.UNRESOLVED
    assert rows[0].gene_id is None


def test_mapped_node_carries_its_resolved_entity_id(db_session):
    from tests.persistence.conftest import make_gene, make_organism

    organism = make_organism(db_session)
    gene = make_gene(db_session, organism_id=organism.id)

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    node = NetworkNodeInput(
        raw_token="1950", node_type=NetworkNodeType.GENE, mapping=_mapped("gene", gene.id)
    )

    outcome = persist_network(
        NetworkImportInput(identity=_identity(), nodes=(node,)), result, session=db_session
    )

    row = db_session.execute(
        select(NetworkNode).where(NetworkNode.network_id == outcome.entity_id)
    ).scalar_one()
    assert row.mapping_status is NetworkNodeMappingStatus.MAPPED
    assert row.gene_id == gene.id


def test_perturbations_persisted(db_session):
    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    node = NetworkNodeInput(
        raw_token="hsa:1956",
        node_type=NetworkNodeType.GENE,
        mapping=_unresolved(NetworkNodeType.GENE),
    )
    perturbation = NetworkPerturbationInput(
        perturbation_type=NetworkPerturbationType.GENE_VARIANT,
        external_identifier="hsa:1956",
        name="EGFR mutation",
        target_raw_token="hsa:1956",
        effect="activating",
        raw_value="hsa:1956(mutation-activated)",
    )

    outcome = persist_network(
        NetworkImportInput(identity=_identity(), nodes=(node,), perturbations=(perturbation,)),
        result,
        session=db_session,
    )

    rows = (
        db_session.execute(
            select(NetworkPerturbation).where(NetworkPerturbation.network_id == outcome.entity_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].perturbation_type is NetworkPerturbationType.GENE_VARIANT
    assert rows[0].raw_value == "hsa:1956(mutation-activated)"
    node_row = db_session.execute(
        select(NetworkNode).where(NetworkNode.network_id == outcome.entity_id)
    ).scalar_one()
    assert rows[0].target_node_id == node_row.id


def test_provenance_attached(db_session):
    from app.models.external_record import ExternalRecord
    from app.models.source_cross_reference import SourceCrossReference

    result = _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE)
    provenance = ExternalRecordProvenance(
        retrieval_date=__import__("datetime").datetime.now(__import__("datetime").UTC),
        raw_response_hash="deadbeef",
        external_id="N00103",
        raw_response_text="ENTRY N00103 Network\n///\n",
    )

    outcome = persist_network(
        NetworkImportInput(identity=_identity()), result, session=db_session, provenance=provenance
    )

    cross_refs = (
        db_session.execute(
            select(SourceCrossReference).where(
                SourceCrossReference.entity_type == "network",
                SourceCrossReference.entity_id == outcome.entity_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(cross_refs) == 1
    assert cross_refs[0].external_id == "N00103"

    external_records = (
        db_session.execute(select(ExternalRecord).where(ExternalRecord.external_id == "N00103"))
        .scalars()
        .all()
    )
    assert len(external_records) == 1


def test_repeated_import_is_idempotent(db_session):
    """Re-persisting the identical record creates no duplicate topology."""
    nodes = _three_node_chain()
    edges = _two_edges()
    import_input = NetworkImportInput(identity=_identity(), nodes=nodes, edges=edges)

    first = persist_network(
        import_input,
        _result(NormalizationStatus.NEW, match_method=MatchMethod.NONE),
        session=db_session,
    )
    second = persist_network(
        import_input,
        _result(NormalizationStatus.MATCHED, matched_entity_id=first.entity_id),
        session=db_session,
    )

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert first.entity_id == second.entity_id

    networks = (
        db_session.execute(select(Network).where(Network.id == first.entity_id)).scalars().all()
    )
    node_rows = (
        db_session.execute(select(NetworkNode).where(NetworkNode.network_id == first.entity_id))
        .scalars()
        .all()
    )
    edge_rows = (
        db_session.execute(select(NetworkEdge).where(NetworkEdge.network_id == first.entity_id))
        .scalars()
        .all()
    )
    assert len(networks) == 1
    assert len(node_rows) == 3
    assert len(edge_rows) == 2


def test_ambiguous_requires_review(db_session):
    candidate_ids = (uuid4(), uuid4())
    result = _result(
        NormalizationStatus.AMBIGUOUS,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=candidate_ids,
    )

    outcome = persist_network(NetworkImportInput(identity=_identity()), result, session=db_session)

    assert outcome.action is PersistenceAction.REQUIRES_REVIEW
    assert outcome.review_required is True
    assert outcome.entity_id is None


def test_conflicted_requires_review(db_session):
    result = _result(
        NormalizationStatus.CONFLICTED,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        candidate_entity_ids=(uuid4(),),
    )

    outcome = persist_network(NetworkImportInput(identity=_identity()), result, session=db_session)

    assert outcome.action is PersistenceAction.REQUIRES_REVIEW
