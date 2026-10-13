"""Database tests for Group J: KEGG NETWORK support (migration
``0019_kegg_network``).

See ``docs/02_database_schema.md``: "Table: network", "Table: network_node",
"Table: network_edge", "Table: network_perturbation", "Table:
network_pathway", "Table: network_disease", "Table: network_publication".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.compound import Compound
from app.models.enums import (
    NetworkEdgeInteractionType,
    NetworkNodeMappingStatus,
    NetworkNodeType,
    NetworkType,
    SourceType,
)
from app.models.network import Network, NetworkEdge, NetworkNode
from app.models.reaction import Reaction

pytestmark = pytest.mark.database


def _make_network(db_session, **kwargs) -> Network:
    kwargs.setdefault("source_type", SourceType.KEGG)
    kwargs.setdefault("external_id", f"N{uuid.uuid4().hex[:8]}")
    kwargs.setdefault("name", "test-only network")
    kwargs.setdefault("network_type", NetworkType.REFERENCE)
    network = Network(**kwargs)
    db_session.add(network)
    db_session.flush()
    return network


def _make_node(db_session, *, network_id, **kwargs) -> NetworkNode:
    kwargs.setdefault("node_type", NetworkNodeType.UNKNOWN)
    kwargs.setdefault("mapping_status", NetworkNodeMappingStatus.UNRESOLVED)
    node = NetworkNode(network_id=network_id, **kwargs)
    db_session.add(node)
    db_session.flush()
    return node


def test_create_network(db_session):
    network = _make_network(db_session, name="EGF-EGFR-ERBB signaling pathway")

    fetched = db_session.get(Network, network.id)
    assert fetched is not None
    assert fetched.curation_state is not None
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_network_source_type_external_id_must_be_unique(db_session):
    _make_network(db_session, external_id="N00103")
    with pytest.raises(IntegrityError):
        _make_network(db_session, external_id="N00103")


def test_deleting_a_network_cascades_to_its_nodes_and_edges(db_session):
    network = _make_network(db_session)
    node_a = _make_node(db_session, network_id=network.id, raw_token="A")
    node_b = _make_node(db_session, network_id=network.id, raw_token="B")
    db_session.add(
        NetworkEdge(
            network_id=network.id,
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            raw_operator="->",
        )
    )
    db_session.flush()
    network_id, node_a_id = network.id, node_a.id

    db_session.delete(db_session.get(Network, network_id))
    db_session.flush()
    db_session.expunge_all()

    # ``passive_deletes=True`` means SQLAlchemy never loads/deletes these
    # children itself -- it relies entirely on the database's own ``ON
    # DELETE CASCADE``. Expunging (rather than refreshing) the stale,
    # now-deleted in-memory objects and querying fresh confirms the
    # database itself actually removed them.
    assert db_session.query(NetworkNode).filter(NetworkNode.id == node_a_id).first() is None
    assert (
        db_session.query(NetworkEdge).filter(NetworkEdge.network_id == network_id).first() is None
    )


def test_duplicate_edge_rejected_on_reimport_with_operator(db_session):
    """No duplicate edges after re-import -- the partial unique index catches
    a duplicate attempt even when ``raw_operator`` is set."""
    network = _make_network(db_session)
    node_a = _make_node(db_session, network_id=network.id, raw_token="A")
    node_b = _make_node(db_session, network_id=network.id, raw_token="B")
    db_session.add(
        NetworkEdge(
            network_id=network.id,
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            raw_operator="->",
        )
    )
    db_session.flush()

    db_session.add(
        NetworkEdge(
            network_id=network.id,
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            raw_operator="->",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_edge_rejected_on_reimport_without_operator(db_session):
    """The same guard also holds when ``raw_operator`` is null on both rows
    -- the second, operator-less partial unique index."""
    network = _make_network(db_session)
    node_a = _make_node(db_session, network_id=network.id, raw_token="A")
    node_b = _make_node(db_session, network_id=network.id, raw_token="B")
    db_session.add(
        NetworkEdge(
            network_id=network.id,
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
        )
    )
    db_session.flush()

    db_session.add(
        NetworkEdge(
            network_id=network.id,
            source_node_id=node_a.id,
            target_node_id=node_b.id,
            interaction_type=NetworkEdgeInteractionType.UNKNOWN,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_edges_cannot_be_constructed_across_networks_by_the_persistence_layer(db_session):
    """``app.persistence.network._create`` only ever resolves an edge's own
    endpoints from the identical import's own node set. A schema-level
    composite-FK guarantee that both endpoints of one ``network_edge``
    belong to its own ``network_id`` does not exist (``docs/
    02_database_schema.md``, "Table: network_edge") -- this invariant is
    instead enforced at the application level, by always resolving an
    edge's endpoints only from the token map the *same* import just built,
    and raising immediately (never silently dropping the edge or guessing
    a node) when an edge names a token with no corresponding node."""
    from app.normalization.network import NetworkIdentity
    from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus
    from app.persistence.network import NetworkEdgeInput, NetworkImportInput, persist_network

    identity = NetworkIdentity(
        source=SourceType.KEGG,
        source_identifier=f"N{uuid.uuid4().hex[:8]}",
        name="test",
        network_type=NetworkType.REFERENCE,
    )
    result = NormalizationResult(
        status=NormalizationStatus.NEW,
        source=SourceType.KEGG,
        source_identifier=identity.source_identifier,
        entity_type="network",
        match_method=MatchMethod.NONE,
    )
    # An edge naming a token with no corresponding node in this same import.
    import_input = NetworkImportInput(
        identity=identity,
        edges=(
            NetworkEdgeInput(
                source_raw_token="A",
                target_raw_token="not-a-real-node",
                interaction_type=NetworkEdgeInteractionType.UNKNOWN,
            ),
        ),
    )

    with pytest.raises(ValueError, match="no corresponding node"):
        persist_network(import_input, result, session=db_session)

    assert (
        db_session.query(Network).filter(Network.external_id == identity.source_identifier).first()
        is None
    )


def test_deleting_a_reaction_referenced_by_a_network_node_is_restricted(db_session):
    """Network import never deletes or modifies an unrelated ``Reaction``
    row -- and the schema itself protects one still referenced by a
    ``NetworkNode`` from being deleted at all (``ON DELETE RESTRICT``)."""
    reaction = Reaction(internal_id=f"TEST_NET_R{uuid.uuid4().hex[:8]}", name="test-only reaction")
    db_session.add(reaction)
    db_session.flush()
    network = _make_network(db_session)
    _make_node(
        db_session,
        network_id=network.id,
        node_type=NetworkNodeType.REACTION,
        mapping_status=NetworkNodeMappingStatus.MAPPED,
        reaction_id=reaction.id,
        raw_token="R00001",
    )

    db_session.delete(db_session.get(Reaction, reaction.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_mapping_a_compound_node_does_not_modify_the_compound_row(db_session):
    """Node mapping is read-only with respect to the entity it links to."""
    compound = Compound(canonical_name="test-only compound", kegg_compound_id="C00031")
    db_session.add(compound)
    db_session.flush()
    original_name = compound.canonical_name

    network = _make_network(db_session)
    _make_node(
        db_session,
        network_id=network.id,
        node_type=NetworkNodeType.COMPOUND,
        mapping_status=NetworkNodeMappingStatus.MAPPED,
        compound_id=compound.id,
        raw_token="C00031",
    )

    refreshed = db_session.get(Compound, compound.id)
    assert refreshed.canonical_name == original_name
