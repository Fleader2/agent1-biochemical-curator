"""The public Entity Mention Resolution API: ``resolve_entity_mention``.

Implements this increment's Core Rule directly: **retrieval proposes
identity candidates, normalization decides identity.** This function
never itself declares two entities identical -- it only calls existing
``app.normalization.*`` normalizers (via ``app.entity_resolution.adapters``)
and reports, via ``app.entity_resolution.ranking.classify_outcome``, what
those normalizers already concluded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.claim_generation.mapping import NormalizationLookups
from app.claim_generation.types import EntityKind
from app.connectors.exceptions import ConnectorError
from app.entity_resolution.adapters import (
    KeggSearchAndFetch,
    PubMedSearchAndFetch,
    SgdSearchAndFetch,
    UniProtSearchAndFetch,
    resolve_compound_via_kegg,
    resolve_gene_via_sgd,
    resolve_protein_via_uniprot,
    resolve_publication_via_pubmed,
    resolve_reaction_via_kegg,
)
from app.entity_resolution.errors import UnsupportedEntityKindError
from app.entity_resolution.ranking import classify_outcome, sort_candidates
from app.entity_resolution.types import (
    EntityMention,
    IdentifierCandidate,
    MentionResolutionResult,
    MentionResolutionStatus,
)
from app.models.enums import SourceType

#: Entity kinds this repository currently has *no* trustworthy enrichment
#: source for at all (Step 1's connector inventory found no NCBI-taxonomy or
#: compartment-ontology connector anywhere; Increment 15 closed the UniProt
#: gap for PROTEIN) -- see this increment's completion report for the full
#: inventory.
_NO_CONNECTOR_KINDS = frozenset(
    {EntityKind.ORGANISM, EntityKind.COMPARTMENT, EntityKind.UNKNOWN}
)


@dataclass(frozen=True, slots=True)
class ConnectorBundle:
    """The connector instances one ``resolve_entity_mention`` call has available, by source.

    Every field is optional: a source with no connector supplied here
    simply cannot be queried at all -- the entity kinds that depend on it
    then return ``UNSUPPORTED_ENTITY_KIND``, never a fabricated fallback
    or a silent skip to a different source.
    """

    sgd: SgdSearchAndFetch | None = None
    kegg: KeggSearchAndFetch | None = None
    pubmed: PubMedSearchAndFetch | None = None
    uniprot: UniProtSearchAndFetch | None = None


def _unsupported(mention: EntityMention, *, reason: str) -> MentionResolutionResult:
    return MentionResolutionResult(
        mention=mention, status=MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND, reason=reason
    )


def _unresolved(mention: EntityMention, *, reason: str) -> MentionResolutionResult:
    return MentionResolutionResult(
        mention=mention, status=MentionResolutionStatus.UNRESOLVED, reason=reason
    )


def _source_failure(
    mention: EntityMention,
    *,
    source: SourceType,
    exc: ConnectorError,
    sources_queried: tuple[SourceType, ...],
) -> MentionResolutionResult:
    return MentionResolutionResult(
        mention=mention,
        status=MentionResolutionStatus.SOURCE_FAILURE,
        sources_queried=sources_queried,
        reason=f"{source.value} connector call failed: {exc}",
        failed_source=source,
        error_category=type(exc).__name__,
    )


def _finalize(
    mention: EntityMention,
    candidates: list[IdentifierCandidate],
    *,
    sources_queried: tuple[SourceType, ...],
) -> MentionResolutionResult:
    ordered = sort_candidates(candidates)
    status, resolved_id, reason = classify_outcome(ordered)
    return MentionResolutionResult(
        mention=mention,
        status=status,
        candidates=ordered,
        resolved_entity_id=resolved_id,
        sources_queried=sources_queried,
        reason=reason,
    )


def resolve_entity_mention(
    mention: EntityMention,
    *,
    connectors: ConnectorBundle | None = None,
    lookups: NormalizationLookups | None = None,
) -> MentionResolutionResult:
    """Resolve one ``EntityMention`` to zero or more verified ``IdentifierCandidate``\\ s.

    ``connectors``/``lookups`` default to empty bundles -- with neither
    supplied, every mention structurally returns
    ``UNSUPPORTED_ENTITY_KIND``, the maximally conservative default
    (mirrors ``app.claim_generation.generate_candidate_claims``'s own
    "no lookups means nothing resolves" default).

    Dispatch is by ``mention.entity_kind`` alone -- never inferred, never
    guessed. Organism-scoped kinds (``GENE``, ``REACTION``, ``PROTEIN``)
    require ``mention.organism_id`` to already be resolved; if it is not,
    this function returns ``UNRESOLVED`` without attempting any connector
    call at all (Step 8/Step 20). A connector failure (any
    ``app.connectors.exceptions.ConnectorError``) is caught per source and
    converted into ``SOURCE_FAILURE`` -- it never propagates as a raw
    exception, and it is never confused with a legitimate zero-candidate
    search.
    """
    if not isinstance(mention, EntityMention):
        raise TypeError(f"resolve_entity_mention requires an EntityMention, got {mention!r}")

    resolved_connectors = connectors if connectors is not None else ConnectorBundle()
    resolved_lookups = lookups if lookups is not None else NormalizationLookups()

    if mention.entity_kind is EntityKind.GENE:
        if resolved_connectors.sgd is None or resolved_lookups.gene is None:
            return _unsupported(mention, reason="no SGD connector/Gene lookup is configured")
        if mention.organism_id is None:
            return _unresolved(mention, reason="Gene resolution requires a resolved organism_id")
        try:
            candidates = resolve_gene_via_sgd(
                query=mention.original_text,
                original_mention=mention.original_text,
                source_identifier=mention.source_context_identifier,
                organism_id=mention.organism_id,
                organism_context_text=mention.organism_context_text,
                connector=resolved_connectors.sgd,
                lookup=resolved_lookups.gene,
            )
        except ConnectorError as exc:
            return _source_failure(
                mention, source=SourceType.SGD, exc=exc, sources_queried=(SourceType.SGD,)
            )
        return _finalize(mention, candidates, sources_queried=(SourceType.SGD,))

    if mention.entity_kind is EntityKind.COMPOUND:
        if resolved_connectors.kegg is None or resolved_lookups.compound is None:
            return _unsupported(mention, reason="no KEGG connector/Compound lookup is configured")
        try:
            candidates = resolve_compound_via_kegg(
                query=mention.original_text,
                original_mention=mention.original_text,
                connector=resolved_connectors.kegg,
                lookup=resolved_lookups.compound,
            )
        except ConnectorError as exc:
            return _source_failure(
                mention, source=SourceType.KEGG, exc=exc, sources_queried=(SourceType.KEGG,)
            )
        return _finalize(mention, candidates, sources_queried=(SourceType.KEGG,))

    if mention.entity_kind is EntityKind.REACTION:
        if resolved_connectors.kegg is None or resolved_lookups.reaction is None:
            return _unsupported(mention, reason="no KEGG connector/Reaction lookup is configured")
        if mention.organism_id is None:
            return _unresolved(
                mention, reason="Reaction resolution requires a resolved organism_id"
            )
        try:
            candidates = resolve_reaction_via_kegg(
                query=mention.original_text,
                original_mention=mention.original_text,
                organism_id=mention.organism_id,
                connector=resolved_connectors.kegg,
                lookup=resolved_lookups.reaction,
            )
        except ConnectorError as exc:
            return _source_failure(
                mention, source=SourceType.KEGG, exc=exc, sources_queried=(SourceType.KEGG,)
            )
        return _finalize(mention, candidates, sources_queried=(SourceType.KEGG,))

    if mention.entity_kind is EntityKind.PUBLICATION:
        if resolved_connectors.pubmed is None or resolved_lookups.publication is None:
            return _unsupported(
                mention, reason="no PubMed connector/Publication lookup is configured"
            )
        try:
            candidates = resolve_publication_via_pubmed(
                query=mention.original_text,
                original_mention=mention.original_text,
                connector=resolved_connectors.pubmed,
                lookup=resolved_lookups.publication,
            )
        except ConnectorError as exc:
            return _source_failure(
                mention, source=SourceType.PUBMED, exc=exc, sources_queried=(SourceType.PUBMED,)
            )
        return _finalize(mention, candidates, sources_queried=(SourceType.PUBMED,))

    if mention.entity_kind is EntityKind.PROTEIN:
        if resolved_connectors.uniprot is None or resolved_lookups.protein is None:
            return _unsupported(
                mention, reason="no UniProt connector/Protein lookup is configured"
            )
        if mention.organism_id is None:
            return _unresolved(
                mention, reason="Protein resolution requires a resolved organism_id"
            )
        try:
            candidates = resolve_protein_via_uniprot(
                query=mention.original_text,
                original_mention=mention.original_text,
                organism_id=mention.organism_id,
                organism_context_text=mention.organism_context_text,
                connector=resolved_connectors.uniprot,
                lookup=resolved_lookups.protein,
            )
        except ConnectorError as exc:
            return _source_failure(
                mention, source=SourceType.UNIPROT, exc=exc, sources_queried=(SourceType.UNIPROT,)
            )
        return _finalize(mention, candidates, sources_queried=(SourceType.UNIPROT,))

    if mention.entity_kind in _NO_CONNECTOR_KINDS:
        return _unsupported(
            mention,
            reason=(
                "no trustworthy enrichment source exists in this repository for "
                f"{mention.entity_kind.value} today"
            ),
        )

    raise UnsupportedEntityKindError(
        f"no resolution dispatch exists for entity kind {mention.entity_kind!r}"
    )


__all__ = ["ConnectorBundle", "resolve_entity_mention"]
