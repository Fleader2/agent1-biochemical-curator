"""In-memory fake connectors for ``tests/entity_resolution``.

Mirrors ``tests/claim_generation/fakes.py``'s role for normalization
``Lookup`` protocols: a small, deterministic, in-memory stand-in for each
real connector's ``search()``/``fetch()``/``normalize()`` trio (see
``app.entity_resolution.adapters``'s own ``SgdSearchAndFetch``/
``KeggSearchAndFetch``/``PubMedSearchAndFetch`` protocols), so these tests
never make a network call. Normalization ``Lookup`` fakes themselves are
reused directly from ``tests.claim_generation.fakes`` rather than
duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggFlatFileRecord,
    KeggReactionRecord,
    KeggSearchHit,
)
from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord, PubMedSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit


@dataclass(frozen=True, slots=True)
class FakeSgdConnector:
    """Keyed by exact query string (search) and SGD id (fetch/normalize)."""

    hits_by_query: dict[str, list[SgdSearchHit]] = field(default_factory=dict)
    locus_by_sgd_id: dict[str, SgdLocusRecord] = field(default_factory=dict)
    normalized_by_sgd_id: dict[str, SgdNormalizedRecord] = field(default_factory=dict)

    def search(self, query: str) -> list[SgdSearchHit]:
        return self.hits_by_query.get(query, [])

    def fetch(self, external_id: str) -> SgdLocusRecord | None:
        return self.locus_by_sgd_id.get(external_id)

    def normalize(self, raw: SgdLocusRecord) -> SgdNormalizedRecord:
        return self.normalized_by_sgd_id[raw.sgd_id]


@dataclass(frozen=True, slots=True)
class FakeKeggConnector:
    """Keyed by ``(query, database)`` (search) and entry id (fetch/normalize)."""

    hits_by_query: dict[tuple[str, str], list[KeggSearchHit]] = field(default_factory=dict)
    normalized_by_entry_id: dict[str, KeggCompoundRecord | KeggReactionRecord] = field(
        default_factory=dict
    )
    entry_type_by_entry_id: dict[str, str] = field(default_factory=dict)

    def search(self, query: str, *, database: str) -> list[KeggSearchHit]:
        return self.hits_by_query.get((query, database), [])

    def fetch(self, external_id: str) -> KeggFlatFileRecord | None:
        if external_id not in self.normalized_by_entry_id:
            return None
        return KeggFlatFileRecord(
            entry_id=external_id,
            entry_type=self.entry_type_by_entry_id.get(external_id),
            fields={},
        )

    def normalize(
        self, raw: KeggFlatFileRecord
    ) -> KeggCompoundRecord | KeggReactionRecord | KeggFlatFileRecord:
        return self.normalized_by_entry_id.get(raw.entry_id, raw)


@dataclass(frozen=True, slots=True)
class FakePubMedConnector:
    """Keyed by exact query string (search) and PMID (fetch/normalize)."""

    hits_by_query: dict[str, list[PubMedSearchHit]] = field(default_factory=dict)
    article_by_pmid: dict[str, PubMedArticleRecord] = field(default_factory=dict)
    normalized_by_pmid: dict[str, PubMedNormalizedRecord] = field(default_factory=dict)

    def search(self, query: str) -> list[PubMedSearchHit]:
        return self.hits_by_query.get(query, [])

    def fetch(self, external_id: str) -> PubMedArticleRecord | None:
        return self.article_by_pmid.get(external_id)

    def normalize(self, raw: PubMedArticleRecord) -> PubMedNormalizedRecord:
        return self.normalized_by_pmid[raw.pmid]


@dataclass(frozen=True, slots=True)
class FakeUniProtConnector:
    """Keyed by exact query string (search) and accession (fetch/normalize)."""

    hits_by_query: dict[str, list[UniProtSearchHit]] = field(default_factory=dict)
    entry_by_accession: dict[str, UniProtEntryRecord] = field(default_factory=dict)
    normalized_by_accession: dict[str, UniProtProteinRecord] = field(default_factory=dict)

    def search(
        self, query: str, *, organism_taxonomy_id: int | None = None, limit: int = 25
    ) -> list[UniProtSearchHit]:
        return self.hits_by_query.get(query, [])

    def fetch(self, accession: str) -> UniProtEntryRecord | None:
        return self.entry_by_accession.get(accession)

    def normalize(self, raw: UniProtEntryRecord) -> UniProtProteinRecord:
        return self.normalized_by_accession[raw.primary_accession]
