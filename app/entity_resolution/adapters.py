"""Per-(entity kind, source) retrieval: search a trusted connector, adapt, normalize.

Every function here follows the same five steps (this increment's Step 5):
search the connector with the mention's exact text, fetch each hit's full
record, adapt it into an existing ``app.normalization.*`` ``*Identity``
using an *existing* adapter (never a new one invented here), call the
existing normalizer, and wrap the result as an ``IdentifierCandidate``.
Connector I/O errors (``app.connectors.exceptions.ConnectorError``) are
deliberately **not** caught here -- `app.entity_resolution.resolver` is
what converts them into a structured ``SOURCE_FAILURE`` result, keeping
"how do we call this connector" separate from "how do we report that it
failed."

**Reused adapters, not duplicated** (Step 16): ``gene_identity_from_sgd``,
``compound_identity_from_kegg``, ``reaction_identity_from_kegg``,
``publication_identity_from_pubmed`` -- all four already existed in
``app.normalization.*`` before this increment. Nothing here reimplements
any of them.

**Connector protocols.** Each function depends on a narrow, structural
``Protocol`` covering only the handful of methods it actually calls --
never the concrete connector class -- mirroring
``app.normalization.*``'s own ``*Lookup`` protocol pattern. The real
``SgdConnector``/``KeggConnector``/``PubMedConnector`` classes already
satisfy these structurally; tests supply lightweight fakes instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.claim_generation.types import EntityKind
from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggFlatFileRecord,
    KeggReactionRecord,
    KeggSearchHit,
)
from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord, PubMedSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit
from app.entity_resolution.types import IdentifierCandidate
from app.normalization.compound import (
    CompoundLookup,
    compound_identity_from_kegg,
    normalize_compound,
)
from app.normalization.gene import GeneLookup, gene_identity_from_sgd, normalize_gene
from app.normalization.protein import (
    ProteinLookup,
    normalize_protein,
    protein_identity_from_uniprot,
)
from app.normalization.publication import (
    PublicationLookup,
    normalize_publication,
    publication_identity_from_pubmed,
)
from app.normalization.reaction import (
    ReactionLookup,
    normalize_reaction,
    reaction_identity_from_kegg,
)


@runtime_checkable
class SgdSearchAndFetch(Protocol):
    """The three ``SgdConnector`` methods Gene resolution actually calls."""

    def search(self, query: str) -> Sequence[SgdSearchHit]: ...

    def fetch(self, external_id: str) -> SgdLocusRecord | None: ...

    def normalize(self, raw: SgdLocusRecord) -> SgdNormalizedRecord: ...


@runtime_checkable
class KeggSearchAndFetch(Protocol):
    """The three ``KeggConnector`` methods Compound/Reaction resolution actually call."""

    def search(self, query: str, *, database: str) -> Sequence[KeggSearchHit]: ...

    def fetch(self, external_id: str) -> KeggFlatFileRecord | None: ...

    def normalize(
        self, raw: KeggFlatFileRecord
    ) -> KeggCompoundRecord | KeggReactionRecord | KeggFlatFileRecord: ...


@runtime_checkable
class PubMedSearchAndFetch(Protocol):
    """The three ``PubMedConnector`` methods Publication resolution actually calls."""

    def search(self, query: str) -> Sequence[PubMedSearchHit]: ...

    def fetch(self, external_id: str) -> PubMedArticleRecord | None: ...

    def normalize(self, raw: PubMedArticleRecord) -> PubMedNormalizedRecord: ...


@runtime_checkable
class UniProtSearchAndFetch(Protocol):
    """The three ``UniProtConnector`` methods Protein resolution actually calls."""

    def search(
        self, query: str, *, organism_taxonomy_id: int | None = None, limit: int = 25
    ) -> Sequence[UniProtSearchHit]: ...

    def fetch(self, accession: str) -> UniProtEntryRecord | None: ...

    def normalize(self, raw: UniProtEntryRecord) -> UniProtProteinRecord: ...


def resolve_gene_via_sgd(
    *,
    query: str,
    original_mention: str,
    source_identifier: str,
    organism_id: UUID,
    organism_context_text: str | None,
    connector: SgdSearchAndFetch,
    lookup: GeneLookup,
) -> list[IdentifierCandidate]:
    """Search SGD by exact text, fetch each hit's full locus, normalize as a Gene.

    Every candidate this returns carries a real ``sgd_id`` (SGD's own
    stable identifier, always present on a fetched locus record) -- a
    genuine Level 1 strong identifier, never a bare symbol alone (Step 17:
    "Do not allow bare symbol alone to become MATCHED inside this new
    layer."). A search hit whose full record could not be fetched (a
    since-deleted locus, for instance) is silently skipped, not treated as
    a failure -- ``fetch()`` returning ``None`` is itself SGD's own
    legitimate "no such record" outcome.
    """
    hits = connector.search(query)
    candidates: list[IdentifierCandidate] = []
    for hit in hits:
        locus = connector.fetch(hit.sgd_id)
        if locus is None:
            continue
        normalized_record = connector.normalize(locus)
        identity = gene_identity_from_sgd(normalized_record)
        result = normalize_gene(identity, organism_id=organism_id, lookup=lookup)
        candidates.append(
            IdentifierCandidate(
                entity_kind=EntityKind.GENE,
                source=identity.source,
                source_identifier=identity.source_identifier,
                original_mention=original_mention,
                search_term=query,
                source_record_identifier=normalized_record.sgd_id,
                normalization_input=identity,
                normalization_result=result,
                display_name=normalized_record.standard_name or normalized_record.systematic_name,
                organism_context_text=organism_context_text,
                organism_id=organism_id,
                retrieved_identifiers=tuple(
                    (name, value)
                    for name, value in (
                        ("sgd_id", normalized_record.sgd_id),
                        ("systematic_name", normalized_record.systematic_name),
                        ("standard_name", normalized_record.standard_name),
                    )
                    if value is not None
                ),
            )
        )
    return candidates


def resolve_compound_via_kegg(
    *,
    query: str,
    original_mention: str,
    connector: KeggSearchAndFetch,
    lookup: CompoundLookup,
) -> list[IdentifierCandidate]:
    """Search KEGG's ``compound`` database by exact text, fetch, normalize.

    Every candidate carries a real ``kegg_compound_id`` (KEGG's own entry
    id). KEGG's connector exposes no ChEBI/PubChem/MetaCyc/InChIKey
    cross-reference on a compound entry (verified directly against
    ``KeggCompoundRecord`` -- see this increment's completion report), so
    only ``kegg_compound_id`` is ever enriched here; the other four
    Compound identifiers remain unenrichable until a connector for one of
    those sources exists. Protonation state, stereochemistry, and generic
    vs. specific distinctions are never collapsed -- each KEGG entry
    becomes its own independent candidate, and ``normalize_compound``
    remains the sole authority on whether any of them match an existing
    row.
    """
    hits = connector.search(query, database="compound")
    candidates: list[IdentifierCandidate] = []
    for hit in hits:
        flat_record = connector.fetch(hit.entry_id)
        if flat_record is None:
            continue
        normalized_record = connector.normalize(flat_record)
        if not isinstance(normalized_record, KeggCompoundRecord):
            continue  # not actually a Compound entry -- skip rather than guess
        identity = compound_identity_from_kegg(normalized_record)
        result = normalize_compound(identity, lookup=lookup)
        candidates.append(
            IdentifierCandidate(
                entity_kind=EntityKind.COMPOUND,
                source=identity.source,
                source_identifier=identity.source_identifier,
                original_mention=original_mention,
                search_term=query,
                source_record_identifier=normalized_record.entry_id,
                normalization_input=identity,
                normalization_result=result,
                display_name=identity.canonical_name,
                retrieved_identifiers=(("kegg_compound_id", normalized_record.entry_id),),
            )
        )
    return candidates


def resolve_reaction_via_kegg(
    *,
    query: str,
    original_mention: str,
    organism_id: UUID,
    connector: KeggSearchAndFetch,
    lookup: ReactionLookup,
) -> list[IdentifierCandidate]:
    """Search KEGG's ``reaction`` database by exact text, fetch, normalize.

    Every candidate carries a real ``kegg_reaction_id``. The raw
    ``equation`` field is never parsed (Step 21/Step 6 of this increment's
    instructions) -- ``reaction_identity_from_kegg`` already leaves
    ``participants`` empty for exactly this reason (see that adapter's own
    docstring), and this function does not add participant inference on
    top of it. Rhea/MetaCyc ids are never inferred -- KEGG's connector
    does not return them, so they are simply absent from the resulting
    ``ReactionIdentity``, never guessed.
    """
    hits = connector.search(query, database="reaction")
    candidates: list[IdentifierCandidate] = []
    for hit in hits:
        flat_record = connector.fetch(hit.entry_id)
        if flat_record is None:
            continue
        normalized_record = connector.normalize(flat_record)
        if not isinstance(normalized_record, KeggReactionRecord):
            continue
        identity = reaction_identity_from_kegg(normalized_record)
        result = normalize_reaction(identity, organism_id=organism_id, lookup=lookup)
        candidates.append(
            IdentifierCandidate(
                entity_kind=EntityKind.REACTION,
                source=identity.source,
                source_identifier=identity.source_identifier,
                original_mention=original_mention,
                search_term=query,
                source_record_identifier=normalized_record.entry_id,
                normalization_input=identity,
                normalization_result=result,
                display_name=identity.name,
                organism_id=organism_id,
                retrieved_identifiers=(("kegg_reaction_id", normalized_record.entry_id),),
            )
        )
    return candidates


def resolve_publication_via_pubmed(
    *,
    query: str,
    original_mention: str,
    connector: PubMedSearchAndFetch,
    lookup: PublicationLookup,
) -> list[IdentifierCandidate]:
    """Search PubMed by exact text, fetch each PMID's full article, normalize.

    A bare title-like mention never becomes a strong identifier on its
    own: PubMed's ``search()`` returns only PMIDs (``PubMedSearchHit``
    carries no title/DOI), so every candidate here is only ever produced
    after ``fetch()`` has retrieved and verified the article's real
    metadata (Step 22: "Textual publication titles alone should not
    become strong identity unless an existing PubMed search result
    returns verified PMID/DOI metadata"). ``PublicationIdentity`` itself
    still enforces that at least one of pmid/pmcid/doi is present --
    ``publication_identity_from_pubmed`` always supplies the verified
    PMID, so this can never fail that check.
    """
    hits = connector.search(query)
    candidates: list[IdentifierCandidate] = []
    for hit in hits:
        article = connector.fetch(hit.pmid)
        if article is None:
            continue
        normalized_record = connector.normalize(article)
        identity = publication_identity_from_pubmed(normalized_record)
        result = normalize_publication(identity, lookup=lookup)
        candidates.append(
            IdentifierCandidate(
                entity_kind=EntityKind.PUBLICATION,
                source=identity.source,
                source_identifier=identity.source_identifier,
                original_mention=original_mention,
                search_term=query,
                source_record_identifier=normalized_record.pmid,
                normalization_input=identity,
                normalization_result=result,
                display_name=identity.title,
                retrieved_identifiers=tuple(
                    (name, value)
                    for name, value in (
                        ("pmid", normalized_record.pmid),
                        ("pmcid", normalized_record.pmcid),
                        ("doi", normalized_record.doi),
                    )
                    if value is not None
                ),
            )
        )
    return candidates


def resolve_protein_via_uniprot(
    *,
    query: str,
    original_mention: str,
    organism_id: UUID,
    organism_context_text: str | None,
    connector: UniProtSearchAndFetch,
    lookup: ProteinLookup,
) -> list[IdentifierCandidate]:
    """Search UniProtKB by exact text (plus organism name, when known), fetch, normalize.

    ``organism_id`` (an already-resolved Agent 1 organism UUID) is required
    to call ``normalize_protein`` at all -- exactly like Gene and Reaction,
    ``app.entity_resolution.resolver`` never calls this function without
    one already resolved.

    ``organism_context_text`` (free text, e.g. ``"Saccharomyces
    cerevisiae"``), when present, is folded into the UniProt query itself
    as an ``organism_name`` filter. This repository has no mapping from an
    Agent 1 organism UUID to an NCBI taxonomy id, and inventing one was
    explicitly out of scope (Increment 15 instructions, Step 7) -- this is
    the "final approach" this increment's completion report describes, not
    a placeholder for one. When ``organism_context_text`` is absent, the
    search proceeds unfiltered by organism, and ``normalize_protein``'s own
    organism-scoped ``by_name`` lookup remains the actual safeguard against
    a foreign-organism match -- the same guarantee already relied on for
    every other candidate this module produces.

    Every candidate carries a real, verified UniProt primary accession --
    a search hit alone is never used to construct a ``ProteinIdentity``
    (Step 24): each hit's full entry is re-fetched by accession first.
    ``record.gene_names``/cross-references are never read here --
    ``protein_identity_from_uniprot`` itself already guarantees ``gene_id``
    is always ``None`` (Step 9), and no Gene or Reaction normalizer is
    imported anywhere in this function (Steps 25-26).
    """
    search_term = query
    if organism_context_text:
        search_term = f'{query} AND organism_name:"{organism_context_text}"'

    hits = connector.search(search_term)
    candidates: list[IdentifierCandidate] = []
    for hit in hits:
        entry = connector.fetch(hit.primary_accession)
        if entry is None:
            continue
        normalized_record = connector.normalize(entry)
        identity = protein_identity_from_uniprot(normalized_record)
        result = normalize_protein(identity, organism_id=organism_id, lookup=lookup)
        candidates.append(
            IdentifierCandidate(
                entity_kind=EntityKind.PROTEIN,
                source=identity.source,
                source_identifier=identity.source_identifier,
                original_mention=original_mention,
                search_term=search_term,
                source_record_identifier=normalized_record.primary_accession,
                normalization_input=identity,
                normalization_result=result,
                display_name=normalized_record.protein_name,
                organism_context_text=organism_context_text,
                organism_id=organism_id,
                retrieved_identifiers=tuple(
                    (name, value)
                    for name, value in (
                        ("uniprot_id", normalized_record.primary_accession),
                        (
                            "reviewed",
                            str(normalized_record.reviewed)
                            if normalized_record.reviewed is not None
                            else None,
                        ),
                        ("organism_name", normalized_record.organism_name),
                    )
                    if value is not None
                ),
            )
        )
    return candidates


__all__ = [
    "KeggSearchAndFetch",
    "PubMedSearchAndFetch",
    "SgdSearchAndFetch",
    "UniProtSearchAndFetch",
    "resolve_compound_via_kegg",
    "resolve_gene_via_sgd",
    "resolve_protein_via_uniprot",
    "resolve_publication_via_pubmed",
    "resolve_reaction_via_kegg",
]
