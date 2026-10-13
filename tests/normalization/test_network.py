"""Tests for ``app.normalization.network``.

Pure unit tests: no database, no HTTP, no live KEGG access -- mirrors
``tests/normalization/test_reaction.py``'s own convention of a small,
in-memory ``Fake*Lookup`` per test module.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.enums import NetworkNodeMappingStatus, NetworkNodeType, NetworkType, SourceType
from app.normalization.network import (
    NetworkCandidate,
    NetworkIdentity,
    map_network_node,
    normalize_network,
)
from app.normalization.types import NormalizationStatus


class FakeNetworkLookup:
    def __init__(self, rows: list[NetworkCandidate]) -> None:
        self._rows = rows

    def by_external_id(self, external_id: str) -> list[NetworkCandidate]:
        return [row for row in self._rows if row.external_id == external_id]


def _identity(**overrides: object) -> NetworkIdentity:
    defaults: dict[str, object] = {
        "source": SourceType.KEGG,
        "source_identifier": "N00103",
        "name": "test network",
        "network_type": NetworkType.REFERENCE,
    }
    defaults.update(overrides)
    return NetworkIdentity(**defaults)  # type: ignore[arg-type]


# --- Network identity -----------------------------------------------------------------------


def test_network_identity_requires_non_empty_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier"):
        _identity(source_identifier="")


def test_network_identity_requires_non_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        _identity(name="")


def test_existing_n_number_matches() -> None:
    existing_id = uuid4()
    lookup = FakeNetworkLookup(
        [NetworkCandidate(id=existing_id, source_type=SourceType.KEGG, external_id="N00103")]
    )

    result = normalize_network(_identity(), lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == existing_id


def test_unseen_n_number_becomes_new() -> None:
    result = normalize_network(_identity(), lookup=FakeNetworkLookup([]))

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None


def test_duplicate_names_with_different_n_numbers_remain_distinct() -> None:
    """Network identity is the external id alone -- two different networks
    that happen to share a ``name`` are never merged or confused, because
    name is never consulted for identity at all."""
    existing_id = uuid4()
    lookup = FakeNetworkLookup(
        [NetworkCandidate(id=existing_id, source_type=SourceType.KEGG, external_id="N00103")]
    )

    same_name_different_id = normalize_network(
        _identity(source_identifier="N00999", name="test network"), lookup=lookup
    )

    assert same_name_different_id.status is NormalizationStatus.NEW
    assert same_name_different_id.matched_entity_id is None


def test_conflicting_n_number_data_becomes_conflicted() -> None:
    """The same external id already belongs to a network from a different
    source -- a genuine identity conflict, never silently resolved either
    way."""
    existing_id = uuid4()
    lookup = FakeNetworkLookup(
        [NetworkCandidate(id=existing_id, source_type=SourceType.BRENDA, external_id="N00103")]
    )

    result = normalize_network(_identity(), lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.candidate_entity_ids == (existing_id,)


def test_multiple_existing_rows_sharing_the_same_external_id_is_ambiguous() -> None:
    lookup = FakeNetworkLookup(
        [
            NetworkCandidate(id=uuid4(), source_type=SourceType.KEGG, external_id="N00103"),
            NetworkCandidate(id=uuid4(), source_type=SourceType.KEGG, external_id="N00103"),
        ]
    )

    result = normalize_network(_identity(), lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert len(result.candidate_entity_ids) == 2


# --- Node mapping ----------------------------------------------------------------------------


class FakeGeneLookup:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def by_kegg_gene_id(self, kegg_gene_id: str) -> list:
        return [row for row in self._rows if row["kegg_gene_id"] == kegg_gene_id]


class FakeCompoundLookup:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def by_kegg_compound_id(self, kegg_compound_id: str) -> list:
        return [row for row in self._rows if row["kegg_compound_id"] == kegg_compound_id]


class FakeReactionLookup:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> list:
        return [row for row in self._rows if row["kegg_reaction_id"] == kegg_reaction_id]


class _Row(dict):
    """A dict that also exposes its own ``id``/key attributes, so
    ``unique_by_id`` (which reads ``.id``) and the fake lookups above (which
    read ``row["..."]``) both work against the identical object."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)

    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def test_gene_resolves_uniquely() -> None:
    gene_id = uuid4()
    lookup = FakeGeneLookup([_Row(id=gene_id, kegg_gene_id="1950")])

    result = map_network_node(node_type=NetworkNodeType.GENE, raw_token="1950", gene_lookup=lookup)

    assert result.status is NetworkNodeMappingStatus.MAPPED
    assert result.gene_id == gene_id
    assert result.protein_id is None
    assert result.compound_id is None


def test_compound_resolves_uniquely() -> None:
    compound_id = uuid4()
    lookup = FakeCompoundLookup([_Row(id=compound_id, kegg_compound_id="C00031")])

    result = map_network_node(
        node_type=NetworkNodeType.COMPOUND, raw_token="C00031", compound_lookup=lookup
    )

    assert result.status is NetworkNodeMappingStatus.MAPPED
    assert result.compound_id == compound_id


def test_reaction_resolves_uniquely() -> None:
    reaction_id = uuid4()
    lookup = FakeReactionLookup([_Row(id=reaction_id, kegg_reaction_id="R00001")])

    result = map_network_node(
        node_type=NetworkNodeType.REACTION, raw_token="R00001", reaction_lookup=lookup
    )

    assert result.status is NetworkNodeMappingStatus.MAPPED
    assert result.reaction_id == reaction_id


def test_unresolved_identifier_retained() -> None:
    result = map_network_node(
        node_type=NetworkNodeType.GENE, raw_token="9999999", gene_lookup=FakeGeneLookup([])
    )

    assert result.status is NetworkNodeMappingStatus.UNRESOLVED
    assert result.gene_id is None
    assert "9999999" in result.reason


def test_ambiguous_identifier_retained_for_review() -> None:
    lookup = FakeGeneLookup(
        [
            _Row(id=uuid4(), kegg_gene_id="1950"),
            _Row(id=uuid4(), kegg_gene_id="1950"),
        ]
    )

    result = map_network_node(node_type=NetworkNodeType.GENE, raw_token="1950", gene_lookup=lookup)

    assert result.status is NetworkNodeMappingStatus.AMBIGUOUS
    assert result.gene_id is None


def test_node_type_with_no_lookup_support_is_unresolved() -> None:
    """``PROTEIN``/``COMPLEX`` have no KEGG-id-based lookup in this
    increment -- never fabricated, always left unresolved."""
    result = map_network_node(node_type=NetworkNodeType.COMPLEX, raw_token="whatever")

    assert result.status is NetworkNodeMappingStatus.UNRESOLVED


def test_re_resolution_to_a_different_entity_is_conflicted_not_silently_remapped() -> None:
    new_gene_id = uuid4()
    old_gene_id = uuid4()
    lookup = FakeGeneLookup([_Row(id=new_gene_id, kegg_gene_id="1950")])

    result = map_network_node(
        node_type=NetworkNodeType.GENE,
        raw_token="1950",
        gene_lookup=lookup,
        existing_mapped_entity_id=old_gene_id,
    )

    assert result.status is NetworkNodeMappingStatus.CONFLICTED
    assert result.gene_id is None
