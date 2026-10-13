"""Network identity and node-mapping normalization (KEGG NETWORK support
increment).

Two separate, deliberately distinct normalization questions live in this
module:

1. **Network identity** (``NetworkIdentity``/``normalize_network``): does an
   incoming KEGG NETWORK record (identified solely by its own external id,
   e.g. ``"N00103"``) already exist? This follows the same
   ``app.normalization.types.NormalizationResult`` shape every other entity
   normalizer uses. Network's own identity hierarchy (task's own words) is
   deliberately the simplest in this repository: "1. KEGG NETWORK external
   ID (Nxxxxx); 2. no fallback identity based only on name" -- there is no
   Level-3 name/synonym lookup here at all, unlike Compound/Gene/Protein.

2. **Node mapping** (``NetworkNodeMappingResult``/``map_network_node``):
   does one node *inside* an already-identified network link to an
   existing, normalized Agent 1 entity (``Gene``/``Compound``/``Reaction``
   -- the only three with an existing, global, exact-match Lookup method
   this increment can reuse; see module docstring note under
   ``map_network_node``)? This is a *different* question from Network
   identity, uses a different, smaller status vocabulary
   (``app.models.enums.NetworkNodeMappingStatus``: no ``NEW`` member -- a
   node mapping never creates a new biological entity, only links to one
   that already exists), and never reuses ``NormalizationResult``.

No database access, no I/O: both functions take already-injected,
read-only ``*Lookup`` protocol implementations, exactly like every other
``app.normalization.*`` module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import NetworkNodeMappingStatus, NetworkNodeType, NetworkType, SourceType
from app.normalization.compound import CompoundLookup
from app.normalization.gene import GeneLookup
from app.normalization.identifiers import CandidateSetState, classify_candidates, unique_by_id
from app.normalization.reaction import ReactionLookup
from app.normalization.types import MatchMethod, NormalizationResult, NormalizationStatus

_ENTITY_TYPE = "network"


@dataclass(frozen=True, slots=True)
class NetworkIdentity:
    """One incoming KEGG NETWORK record's own identity-relevant fields.

    ``source_identifier`` is the KEGG N-number (e.g. ``"N00103"``) -- the
    *only* identity field (see module docstring). Every other field here is
    corroborating metadata, never consulted for identity matching itself.
    """

    source: SourceType
    source_identifier: str
    name: str
    network_type: NetworkType
    definition: str | None = None
    expanded_definition: str | None = None
    organism_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.source_identifier.strip():
            raise ValueError("NetworkIdentity requires a non-empty source_identifier")
        if not self.name.strip():
            raise ValueError("NetworkIdentity requires a non-empty name")


@dataclass(frozen=True, slots=True)
class NetworkCandidate:
    """A read-only snapshot of one existing ``Network`` row, for identity
    comparison only."""

    id: UUID
    source_type: SourceType
    external_id: str


@runtime_checkable
class NetworkLookup(Protocol):
    """Read-only candidate lookup, injected so ``normalize_network`` never
    touches SQLAlchemy.

    Global (no organism parameter): a network's own identity is its
    external id alone, regardless of any ``organism_id`` it may separately
    carry as metadata.
    """

    def by_external_id(self, external_id: str) -> Sequence[NetworkCandidate]:
        """Every existing network with this exact ``external_id``, from any
        source (0, 1, or more) -- never pre-filtered by the caller's own
        ``source``, so a same-id conflict across sources is detectable
        rather than silently invisible."""
        ...


def normalize_network(identity: NetworkIdentity, *, lookup: NetworkLookup) -> NormalizationResult:
    """Resolve one ``NetworkIdentity`` against existing ``Network`` rows.

    Network's identity model has no Level-3 fallback, so ``UNRESOLVED`` is
    not reachable from this function itself: an identity with no usable
    ``source_identifier`` cannot even be constructed (``NetworkIdentity``'s
    own ``__post_init__`` raises first) -- the same "invalid input is
    rejected at construction, not returned as a status" convention every
    other ``*Identity`` type in this package already follows (e.g.
    ``ReactionIdentity``).
    """
    candidates = unique_by_id(lookup.by_external_id(identity.source_identifier))
    state = classify_candidates(tuple(candidate.id for candidate in candidates))

    if state is CandidateSetState.NO_MATCH:
        return NormalizationResult(
            status=NormalizationStatus.NEW,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.NONE,
            organism_id=identity.organism_id,
            reason="no existing network shares this external_id",
        )

    if state is CandidateSetState.AMBIGUOUS:
        return NormalizationResult(
            status=NormalizationStatus.AMBIGUOUS,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=identity.organism_id,
            candidate_entity_ids=tuple(candidate.id for candidate in candidates),
            reason="multiple existing networks already share this external_id",
        )

    # SINGLE_MATCH.
    candidate = candidates[0]
    if candidate.source_type != identity.source:
        return NormalizationResult(
            status=NormalizationStatus.CONFLICTED,
            source=identity.source,
            source_identifier=identity.source_identifier,
            entity_type=_ENTITY_TYPE,
            match_method=MatchMethod.EXACT_IDENTIFIER,
            organism_id=identity.organism_id,
            candidate_entity_ids=(candidate.id,),
            reason=(
                f"external_id {identity.source_identifier!r} already belongs to an "
                f"existing network from a different source ({candidate.source_type})"
            ),
        )

    return NormalizationResult(
        status=NormalizationStatus.MATCHED,
        source=identity.source,
        source_identifier=identity.source_identifier,
        entity_type=_ENTITY_TYPE,
        match_method=MatchMethod.EXACT_IDENTIFIER,
        organism_id=identity.organism_id,
        matched_entity_id=candidate.id,
        reason="matched existing network by external_id",
    )


@dataclass(frozen=True, slots=True)
class NetworkNodeMappingResult:
    """The outcome of attempting to map one network node's own raw token to
    an existing, normalized Agent 1 entity.

    At most one of ``gene_id``/``protein_id``/``complex_id``/
    ``compound_id``/``reaction_id`` is ever set, mirroring
    ``network_node``'s own "at most one" application-level rule.
    """

    status: NetworkNodeMappingStatus
    node_type: NetworkNodeType
    gene_id: UUID | None = None
    protein_id: UUID | None = None
    complex_id: UUID | None = None
    compound_id: UUID | None = None
    reaction_id: UUID | None = None
    reason: str = ""


def map_network_node(
    *,
    node_type: NetworkNodeType,
    raw_token: str,
    gene_lookup: GeneLookup | None = None,
    compound_lookup: CompoundLookup | None = None,
    reaction_lookup: ReactionLookup | None = None,
    existing_mapped_entity_id: UUID | None = None,
) -> NetworkNodeMappingResult:
    """Attempt to map one node to an existing entity, by its own raw KEGG
    token -- never by name, never by inference.

    Only ``GENE`` (via ``gene_lookup.by_kegg_gene_id``), ``COMPOUND`` (via
    ``compound_lookup.by_kegg_compound_id``), and ``REACTION`` (via
    ``reaction_lookup.by_kegg_reaction_id``) can currently resolve: these
    are the only three entity types with an existing, global, exact-match
    KEGG-id lookup method already committed in this repository.
    ``PROTEIN``/``COMPLEX`` (and any other ``NetworkNodeType``) always
    return ``UNRESOLVED`` in this increment -- ``Protein`` has no
    ``kegg_gene_id``-equivalent column, and ``EnzymeComplex`` has no KEGG
    identifier column at all, so there is no deterministic lookup to
    perform without fabricating one; leaving these unresolved is exactly
    what the task's own "the first implementation may import network
    topology while leaving some biological node mappings unresolved" scope
    allows.

    ``existing_mapped_entity_id``, when given, is whatever entity id an
    earlier import of the identical ``raw_token`` within the identical
    network already persisted (see ``app.persistence.network``). If this
    resolution now finds a *different* single match than that, the result
    is ``CONFLICTED`` rather than silently re-``MAPPED`` -- a mapping, once
    established, is never silently remapped to a different entity.
    """
    candidates: list[UUID] = []
    lookup_description = ""

    if node_type is NetworkNodeType.GENE and gene_lookup is not None:
        matches = unique_by_id(gene_lookup.by_kegg_gene_id(raw_token))
        candidates = [match.id for match in matches]
        lookup_description = "gene"
    elif node_type is NetworkNodeType.COMPOUND and compound_lookup is not None:
        matches = unique_by_id(compound_lookup.by_kegg_compound_id(raw_token))
        candidates = [match.id for match in matches]
        lookup_description = "compound"
    elif node_type is NetworkNodeType.REACTION and reaction_lookup is not None:
        matches = unique_by_id(reaction_lookup.by_kegg_reaction_id(raw_token))
        candidates = [match.id for match in matches]
        lookup_description = "reaction"
    else:
        return NetworkNodeMappingResult(
            status=NetworkNodeMappingStatus.UNRESOLVED,
            node_type=node_type,
            reason=f"no deterministic lookup available for node_type={node_type.value}",
        )

    state = classify_candidates(tuple(candidates))

    if state is CandidateSetState.NO_MATCH:
        return NetworkNodeMappingResult(
            status=NetworkNodeMappingStatus.UNRESOLVED,
            node_type=node_type,
            reason=f"no existing {lookup_description} with KEGG id {raw_token!r}",
        )

    if state is CandidateSetState.AMBIGUOUS:
        return NetworkNodeMappingResult(
            status=NetworkNodeMappingStatus.AMBIGUOUS,
            node_type=node_type,
            reason=(
                f"{len(candidates)} existing {lookup_description} rows share KEGG id {raw_token!r}"
            ),
        )

    # SINGLE_MATCH.
    matched_id = candidates[0]
    if existing_mapped_entity_id is not None and existing_mapped_entity_id != matched_id:
        return NetworkNodeMappingResult(
            status=NetworkNodeMappingStatus.CONFLICTED,
            node_type=node_type,
            reason=(
                f"re-resolving KEGG id {raw_token!r} now matches a different {lookup_description} "
                f"than the one already persisted for this node"
            ),
        )

    kwargs = {f"{lookup_description}_id": matched_id}
    return NetworkNodeMappingResult(
        status=NetworkNodeMappingStatus.MAPPED,
        node_type=node_type,
        reason=f"matched existing {lookup_description} by KEGG id",
        **kwargs,
    )


__all__ = [
    "NetworkCandidate",
    "NetworkIdentity",
    "NetworkLookup",
    "NetworkNodeMappingResult",
    "map_network_node",
    "normalize_network",
]
