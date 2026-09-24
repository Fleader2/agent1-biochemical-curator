"""Deterministic fake connectors for Pathway Curation Planner tests (Step 38).

Every fake here is a plain, in-memory, deterministic stand-in satisfying
the exact same structural connector shape the real connectors satisfy
(``app.entity_resolution.adapters``'s ``KeggSearchAndFetch``/
``SgdSearchAndFetch``/``PubMedSearchAndFetch``/``UniProtSearchAndFetch``,
plus the same ``search``/``fetch``/``normalize`` shape SABIO-RK uses) --
never the real ``app.connectors.http.ConnectorHttpClient``. No network
access anywhere in this module; every call is recorded in ``self.calls``
for tests that need to assert on what was actually queried.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggFlatFileRecord,
    KeggLinkEntry,
    KeggReactionRecord,
    KeggSearchHit,
)
from app.connectors.open_enzyme_database import OedDataRow
from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord, PubMedSearchHit
from app.connectors.sabiork import SabioKineticParameter, SabioKineticRecord, SabioSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit


@dataclass
class FakeKeggConnector:
    """Deterministic fake for ``app.pathway_curation.strategies.KeggPathwayCurationConnector``
    (``search``/``fetch``/``normalize``, plus ``link`` -- Increment C.1).

    Shaped after the real live KEGG API's actual behavior, confirmed during Increment
    C.1's own implementation: a pathway's own ``fetch()`` record carries **no**
    ``REACTION`` field (only ``NAME``/``ENZYME``-shaped metadata, mirroring a real
    KEGG pathway ``/get/`` response) -- ``pathway_reactions`` is exposed exclusively
    through ``link()``, exactly like the real pathway<->reaction ``link`` operation.
    """

    pathways: dict[str, str] = field(default_factory=dict)
    pathway_reactions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reactions: dict[str, KeggReactionRecord] = field(default_factory=dict)
    compounds: dict[str, KeggCompoundRecord] = field(default_factory=dict)
    kgml_reactions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Increment C.1 (organism-specific pathway-resolution completion): pathway ids
    keyed here have a fake KGML diagram declaring exactly these reaction ids --
    ``discover_reactions_in_pathway`` tries this before ``pathway_reactions``/``link``.
    A pathway id absent from this dict has no fake KGML document at all (mirrors a
    live 404, i.e. a generic "map"-prefixed pathway), so ``get_kgml`` returns
    ``None`` and callers fall through to ``link()`` exactly as before this dict
    existed -- every pre-existing test that never sets this field is unaffected.
    """
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(self, query: str, *, database: str) -> list[KeggSearchHit]:
        self.calls.append(("search", (query, database)))
        needle = query.lower()
        if database == "pathway":
            return [
                KeggSearchHit(entry_id=pid, description=desc)
                for pid, desc in self.pathways.items()
                if needle in desc.lower()
            ]
        if database == "reaction":
            return [
                KeggSearchHit(entry_id=rid, description=" ".join(rec.names) or rid)
                for rid, rec in self.reactions.items()
                if needle in " ".join(rec.names).lower()
            ]
        if database == "compound":
            return [
                KeggSearchHit(entry_id=cid, description=" ".join(rec.names) or cid)
                for cid, rec in self.compounds.items()
                if needle in " ".join(rec.names).lower()
            ]
        return []

    def fetch(self, external_id: str) -> KeggFlatFileRecord | None:
        self.calls.append(("fetch", (external_id,)))
        if external_id in self.reactions:
            return self.reactions[external_id].raw
        if external_id in self.compounds:
            return self.compounds[external_id].raw
        if external_id in self.pathway_reactions:
            # A real KEGG pathway record: metadata only, never a REACTION field.
            description = self.pathways.get(external_id, external_id)
            return KeggFlatFileRecord(
                entry_id=external_id,
                entry_type="pathway",
                fields={"NAME": (description,)},
            )
        return None

    def normalize(
        self, raw: KeggFlatFileRecord
    ) -> KeggCompoundRecord | KeggReactionRecord | KeggFlatFileRecord:
        self.calls.append(("normalize", (raw.entry_id,)))
        for record in self.reactions.values():
            if record.raw is raw:
                return record
        for record in self.compounds.values():
            if record.raw is raw:
                return record
        return raw

    def link(self, target_db: str, dbentries: str) -> list[KeggLinkEntry]:
        self.calls.append(("link", (target_db, dbentries)))
        if target_db != "reaction":
            return []
        reaction_ids = self.pathway_reactions.get(dbentries, ())
        return [
            KeggLinkEntry(source_id=dbentries, target_id=reaction_id)
            for reaction_id in reaction_ids
        ]

    def get_kgml(self, pathway_id: str) -> str | None:
        """Fake KGML retrieval (Increment C.1 organism-specific completion): a real,
        parseable-by-the-real-parser XML document listing exactly ``kgml_reactions``'
        reaction ids for this pathway id, or ``None`` (mirroring a live 404) when this
        pathway id has no entry there at all -- never a separate hand-rolled parse
        path, so tests exercise ``parse_kgml_reaction_ids`` for real.
        """
        self.calls.append(("get_kgml", (pathway_id,)))
        if pathway_id not in self.kgml_reactions:
            return None
        reaction_elements = "".join(
            f'<reaction id="{index}" name="rn:{reaction_id}" type="irreversible"/>'
            for index, reaction_id in enumerate(self.kgml_reactions[pathway_id], start=1)
        )
        return f'<pathway name="path:{pathway_id}">{reaction_elements}</pathway>'


def make_kegg_reaction(
    entry_id: str,
    *,
    name: str,
    enzymes: tuple[str, ...] = (),
    equation: str | None = None,
) -> KeggReactionRecord:
    """Build one deterministic, fully-linked ``KeggReactionRecord`` (with its own ``raw``).

    ``equation``, when supplied, is KEGG's own raw ``EQUATION`` text (e.g.
    ``"C00024 + C00083 <=> C00010 + C00332"``) -- parsed by
    ``app.pathway_curation.equation_parser`` at the orchestration layer, never by this
    fixture or by ``app.normalization.reaction`` itself.
    """
    fields: dict[str, tuple[str, ...]] = {"NAME": (name,), "ENZYME": enzymes}
    if equation is not None:
        fields["EQUATION"] = (equation,)
    raw = KeggFlatFileRecord(entry_id=entry_id, entry_type="reaction", fields=fields)
    return KeggReactionRecord(
        entry_id=entry_id,
        names=(name,),
        definition=None,
        equation=equation,
        enzymes=enzymes,
        pathways=(),
        raw=raw,
    )


def make_kegg_compound(entry_id: str, *, name: str) -> KeggCompoundRecord:
    raw = KeggFlatFileRecord(entry_id=entry_id, entry_type="compound", fields={"NAME": (name,)})
    return KeggCompoundRecord(
        entry_id=entry_id,
        names=(name,),
        formula=None,
        exact_mass=None,
        mol_weight=None,
        pathways=(),
        raw=raw,
    )


@dataclass
class FakeSgdConnector:
    """Deterministic fake for ``app.entity_resolution.adapters.SgdSearchAndFetch``.

    ``loci`` is keyed by the gene symbol/systematic name a test query is
    expected to find it by.
    """

    loci: dict[str, SgdLocusRecord] = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(self, query: str) -> list[SgdSearchHit]:
        self.calls.append(("search", (query,)))
        hits = []
        for key, record in self.loci.items():
            if query.upper() == key.upper():
                hits.append(
                    SgdSearchHit(
                        sgd_id=record.sgd_id,
                        systematic_name=record.systematic_name,
                        standard_name=record.standard_name,
                        description=record.description,
                        aliases=(),
                    )
                )
        return hits

    def fetch(self, external_id: str) -> SgdLocusRecord | None:
        self.calls.append(("fetch", (external_id,)))
        for record in self.loci.values():
            if record.sgd_id == external_id:
                return record
        return None

    def normalize(self, raw: SgdLocusRecord) -> SgdNormalizedRecord:
        self.calls.append(("normalize", (raw.sgd_id,)))
        return SgdNormalizedRecord(
            sgd_id=raw.sgd_id,
            systematic_name=raw.systematic_name,
            standard_name=raw.standard_name,
            description=raw.description,
            aliases=tuple(alias.display_name for alias in raw.aliases),
            uniprot_id=raw.uniprot_id,
            external_links=raw.external_links,
            raw=raw,
        )


def make_sgd_locus(
    *, sgd_id: str, systematic_name: str, standard_name: str, description: str | None = None
) -> SgdLocusRecord:
    return SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name=systematic_name,
        standard_name=standard_name,
        locus_type="ORF",
        description=description,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw={"sgd_id": sgd_id},
    )


@dataclass
class FakeUniProtConnector:
    """Deterministic fake for ``app.entity_resolution.adapters.UniProtSearchAndFetch``.

    ``entries`` is keyed by the gene-symbol query text a test expects to
    find each accession by.
    """

    entries: dict[str, UniProtEntryRecord] = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(
        self, query: str, *, organism_taxonomy_id: int | None = None, limit: int = 25
    ) -> list[UniProtSearchHit]:
        self.calls.append(("search", (query, organism_taxonomy_id, limit)))
        hits = []
        for key, record in self.entries.items():
            if key.upper() in query.upper():
                hits.append(
                    UniProtSearchHit(
                        primary_accession=record.primary_accession,
                        entry_name=record.entry_name,
                        reviewed=True,
                    )
                )
        return hits

    def fetch(self, accession: str) -> UniProtEntryRecord | None:
        self.calls.append(("fetch", (accession,)))
        for record in self.entries.values():
            if record.primary_accession == accession:
                return record
        return None

    def normalize(self, raw: UniProtEntryRecord) -> UniProtProteinRecord:
        self.calls.append(("normalize", (raw.primary_accession,)))
        return UniProtProteinRecord(
            primary_accession=raw.primary_accession,
            entry_name=raw.entry_name,
            reviewed=True,
            protein_name=raw.recommended_name,
            gene_names=raw.gene_names,
            organism_name=raw.organism_name,
            organism_taxonomy_id=raw.organism_taxonomy_id,
            ec_numbers=raw.ec_numbers,
            sequence_length=raw.sequence_length,
            secondary_accessions=raw.secondary_accessions,
            cross_references=raw.cross_references,
            raw=raw,
        )


def make_uniprot_entry(
    *,
    accession: str,
    recommended_name: str,
    gene_names: tuple[str, ...],
    organism_name: str,
    ec_numbers: tuple[str, ...] = (),
) -> UniProtEntryRecord:
    return UniProtEntryRecord(
        primary_accession=accession,
        entry_name=None,
        entry_type="Swiss-Prot",
        secondary_accessions=(),
        recommended_name=recommended_name,
        submitted_names=(),
        gene_names=gene_names,
        organism_name=organism_name,
        organism_taxonomy_id=None,
        ec_numbers=ec_numbers,
        sequence_length=None,
        cross_references=(),
        raw={"accession": accession},
    )


@dataclass
class FakePubMedConnector:
    """Deterministic fake for ``app.entity_resolution.adapters.PubMedSearchAndFetch``."""

    articles: dict[str, PubMedArticleRecord] = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(self, query: str) -> list[PubMedSearchHit]:
        self.calls.append(("search", (query,)))
        needle = query.lower()
        return [
            PubMedSearchHit(pmid=pmid)
            for pmid, record in self.articles.items()
            if needle in (record.title or "").lower()
        ]

    def fetch(self, external_id: str) -> PubMedArticleRecord | None:
        self.calls.append(("fetch", (external_id,)))
        return self.articles.get(external_id)

    def normalize(self, raw: PubMedArticleRecord) -> PubMedNormalizedRecord:
        self.calls.append(("normalize", (raw.pmid,)))
        return PubMedNormalizedRecord(
            pmid=raw.pmid,
            title=raw.title,
            abstract=None,
            journal=raw.journal_title,
            year=int(raw.year) if raw.year and raw.year.isdigit() else None,
            authors=raw.authors,
            doi=None,
            pmcid=None,
            raw=raw,
        )


def make_pubmed_article(
    *, pmid: str, title: str, journal: str = "Fake Journal"
) -> PubMedArticleRecord:
    return PubMedArticleRecord(
        pmid=pmid,
        title=title,
        abstract_sections=(),
        journal_title=journal,
        year="2020",
        authors=("Fake A", "Fake B"),
        article_ids=(),
        publication_types=("Journal Article",),
    )


@dataclass
class FakeSabiorkConnector:
    """Deterministic fake matching the SABIO-RK ``search``/``fetch``/``normalize`` shape."""

    records: dict[str, SabioKineticRecord] = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(self, query: str, *, organism: str | None = None) -> list[SabioSearchHit]:
        self.calls.append(("search", (query, organism)))
        return [
            SabioSearchHit(entry_id=entry_id, ec_numbers=(query,), raw={})
            for entry_id, record in self.records.items()
            if record.ec_number == query
        ]

    def fetch(self, external_id: str) -> SabioKineticRecord | None:
        self.calls.append(("fetch", (external_id,)))
        return self.records.get(external_id)

    def normalize(self, raw: SabioKineticRecord) -> tuple[SabioKineticParameter, ...]:
        self.calls.append(("normalize", (raw.entry_id,)))
        return raw.parameters


def make_sabio_record(
    *, entry_id: str, ec_number: str, parameter_type: str, value: str, unit: str
) -> SabioKineticRecord:
    parameter = SabioKineticParameter(
        name=parameter_type,
        parameter_type=parameter_type,
        value=value,
        unit=unit,
        species_label=None,
        comment=None,
    )
    return SabioKineticRecord(
        entry_id=entry_id,
        parameters=(parameter,),
        reaction_equation=None,
        ec_number=ec_number,
        enzyme_name=None,
        uniprot_ids=(),
        is_wildtype=True,
        is_recombinant=False,
        organism="Saccharomyces cerevisiae",
        ncbi_taxonomy_id=None,
        strain=None,
        tissue=None,
        buffer=None,
        ph=None,
        temperature=None,
        temperature_unit=None,
        pubmed_id=None,
        publication_title=None,
        raw={},
    )


@dataclass
class FakeOedConnector:
    """Deterministic fake matching Open Enzyme Database's ``search``/``normalize`` shape.

    OED's real connector has no ``fetch()`` -- ``search()`` rows are already complete
    records (see ``app.connectors.open_enzyme_database``'s own module docstring); this fake
    keeps that same two-method shape.
    """

    rows: dict[str, OedDataRow] = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def search(
        self,
        *,
        ec_number: str | None = None,
        organism: str | None = None,
        uniprot_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[OedDataRow]:
        self.calls.append(("search", (ec_number, organism, uniprot_id)))
        if not (ec_number or organism or uniprot_id):
            raise ValueError("at least one of ec_number, organism, uniprot_id must be given")
        return [
            row for row in self.rows.values() if ec_number is None or row.ec_number == ec_number
        ]

    def normalize(self, raw: OedDataRow):
        from app.connectors.open_enzyme_database import OedKineticParameter

        self.calls.append(("normalize", (raw.ec_number,)))
        results = []
        for parameter_type, value, unit in (
            ("kcat", raw.kcat, raw.unit_kcat),
            ("km", raw.km, raw.unit_km),
            ("kcat_km", raw.kcat_km, raw.unit_kcat_km),
        ):
            if value is None:
                continue
            results.append(
                OedKineticParameter(
                    source_identifier=f"{raw.ec_number}:{parameter_type}:fake",
                    parameter_type=parameter_type,
                    value=value,
                    unit=unit,
                    ec_number=raw.ec_number,
                    substrate=raw.substrate,
                    organism=raw.organism,
                    uniprot_id=raw.uniprot_id,
                    enzyme_type=raw.enzyme_type,
                    pubmed_id=raw.pubmed_id,
                    original_source=None,
                    original_source_identifier=None,
                    raw=raw.raw,
                )
            )
        return tuple(results)


def make_oed_row(*, ec_number: str, kcat: str | None = None, km: str | None = None) -> OedDataRow:
    return OedDataRow(
        ec_number=ec_number,
        substrate=None,
        organism="Saccharomyces cerevisiae",
        uniprot_id=None,
        enzyme_type=None,
        pubmed_id="99999999",
        kcat=kcat,
        km=km,
        kcat_km=None,
        unit_kcat="1/s" if kcat is not None else None,
        unit_km="mM" if km is not None else None,
        unit_kcat_km=None,
        raw={"ec_number": ec_number},
    )


__all__ = [
    "FakeKeggConnector",
    "FakeOedConnector",
    "FakePubMedConnector",
    "FakeSabiorkConnector",
    "FakeSgdConnector",
    "FakeUniProtConnector",
    "make_kegg_compound",
    "make_kegg_reaction",
    "make_oed_row",
    "make_pubmed_article",
    "make_sabio_record",
    "make_sgd_locus",
    "make_uniprot_entry",
]
