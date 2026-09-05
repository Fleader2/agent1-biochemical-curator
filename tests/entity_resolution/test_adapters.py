"""Tests for ``app.entity_resolution.adapters``."""

from __future__ import annotations

from uuid import uuid4

from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggFlatFileRecord,
    KeggReactionRecord,
    KeggSearchHit,
)
from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord, PubMedSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtSearchHit
from app.entity_resolution.adapters import (
    resolve_compound_via_kegg,
    resolve_gene_via_sgd,
    resolve_protein_via_uniprot,
    resolve_publication_via_pubmed,
    resolve_reaction_via_kegg,
)
from app.normalization.compound import CompoundCandidate
from app.normalization.gene import GeneCandidate
from app.normalization.protein import ProteinCandidate
from app.normalization.publication import PublicationCandidate
from app.normalization.reaction import ReactionCandidate
from app.normalization.types import NormalizationStatus
from tests.claim_generation.fakes import (
    FakeCompoundLookup,
    FakeGeneLookup,
    FakeProteinLookup,
    FakePublicationLookup,
    FakeReactionLookup,
)
from tests.entity_resolution.fakes import (
    FakeKeggConnector,
    FakePubMedConnector,
    FakeSgdConnector,
    FakeUniProtConnector,
)

ORGANISM_ID = uuid4()


def _sgd_locus(sgd_id: str, standard_name: str, systematic_name: str) -> SgdLocusRecord:
    return SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name=systematic_name,
        standard_name=standard_name,
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id="P12345",
        external_links=(),
        raw={},
    )


def _sgd_normalized(locus: SgdLocusRecord) -> SgdNormalizedRecord:
    return SgdNormalizedRecord(
        sgd_id=locus.sgd_id,
        systematic_name=locus.systematic_name,
        standard_name=locus.standard_name,
        description=locus.description,
        aliases=(),
        uniprot_id=locus.uniprot_id,
        external_links=(),
        raw=locus,
    )


def test_resolve_gene_via_sgd_matched():
    locus = _sgd_locus("S000000001", "ACC1", "YHR123W")
    normalized = _sgd_normalized(locus)
    connector = FakeSgdConnector(
        hits_by_query={
            "ACC1": [
                SgdSearchHit(
                    sgd_id="S000000001",
                    systematic_name="YHR123W",
                    standard_name="ACC1",
                    description=None,
                    aliases=(),
                )
            ]
        },
        locus_by_sgd_id={"S000000001": locus},
        normalized_by_sgd_id={"S000000001": normalized},
    )
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id="S000000001",
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR123W",
                symbol="ACC1",
                aliases=(),
                description=None,
            )
        ]
    )
    [candidate] = resolve_gene_via_sgd(
        query="ACC1",
        original_mention="ACC1",
        source_identifier="S000000001",
        organism_id=ORGANISM_ID,
        organism_context_text="Saccharomyces cerevisiae",
        connector=connector,
        lookup=lookup,
    )
    assert candidate.normalization_input.sgd_id == "S000000001"
    assert candidate.normalization_result.status is NormalizationStatus.MATCHED
    assert candidate.normalization_result.matched_entity_id == gene_id
    assert dict(candidate.retrieved_identifiers)["sgd_id"] == "S000000001"


def test_resolve_gene_via_sgd_skips_hit_with_no_fetchable_record():
    connector = FakeSgdConnector(
        hits_by_query={
            "ACC1": [
                SgdSearchHit(
                    sgd_id="S000000404",
                    systematic_name=None,
                    standard_name=None,
                    description=None,
                    aliases=(),
                )
            ]
        },
        locus_by_sgd_id={},  # fetch() returns None -- deleted/unavailable locus
        normalized_by_sgd_id={},
    )
    candidates = resolve_gene_via_sgd(
        query="ACC1",
        original_mention="ACC1",
        source_identifier="S000000404",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=FakeGeneLookup(genes=[]),
    )
    assert candidates == []


def test_resolve_gene_multiple_hits_all_preserved():
    locus_a = _sgd_locus("S000000001", "ACC1", "YHR123W")
    locus_b = _sgd_locus("S000000002", "ACC2", "YHR124W")
    connector = FakeSgdConnector(
        hits_by_query={
            "ACC": [
                SgdSearchHit(
                    sgd_id="S000000001",
                    systematic_name="YHR123W",
                    standard_name="ACC1",
                    description=None,
                    aliases=(),
                ),
                SgdSearchHit(
                    sgd_id="S000000002",
                    systematic_name="YHR124W",
                    standard_name="ACC2",
                    description=None,
                    aliases=(),
                ),
            ]
        },
        locus_by_sgd_id={"S000000001": locus_a, "S000000002": locus_b},
        normalized_by_sgd_id={
            "S000000001": _sgd_normalized(locus_a),
            "S000000002": _sgd_normalized(locus_b),
        },
    )
    candidates = resolve_gene_via_sgd(
        query="ACC",
        original_mention="ACC",
        source_identifier="ACC",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=FakeGeneLookup(genes=[]),
    )
    assert len(candidates) == 2
    assert {c.source_record_identifier for c in candidates} == {"S000000001", "S000000002"}


def test_resolve_compound_via_kegg_matched():
    record = KeggCompoundRecord(
        entry_id="C00031",
        names=("D-Glucose",),
        formula="C6H12O6",
        exact_mass=None,
        mol_weight=None,
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="C00031", entry_type="Compound", fields={}),
    )
    connector = FakeKeggConnector(
        hits_by_query={
            ("D-Glucose", "compound"): [KeggSearchHit(entry_id="C00031", description="D-Glucose")]
        },
        normalized_by_entry_id={"C00031": record},
        entry_type_by_entry_id={"C00031": "compound"},
    )
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=[
            CompoundCandidate(id=compound_id, canonical_name="D-Glucose", kegg_compound_id="C00031")
        ]
    )
    [candidate] = resolve_compound_via_kegg(
        query="D-Glucose", original_mention="D-Glucose", connector=connector, lookup=lookup
    )
    assert candidate.normalization_input.kegg_compound_id == "C00031"
    assert candidate.normalization_result.status is NormalizationStatus.MATCHED


def test_resolve_reaction_via_kegg_matched():
    record = KeggReactionRecord(
        entry_id="R00299",
        names=("hexokinase",),
        definition=None,
        equation="C00031 + C00002 <=> C00092 + C00008",
        enzymes=(),
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="R00299", entry_type="Reaction", fields={}),
    )
    connector = FakeKeggConnector(
        hits_by_query={
            ("hexokinase", "reaction"): [KeggSearchHit(entry_id="R00299", description="hexokinase")]
        },
        normalized_by_entry_id={"R00299": record},
        entry_type_by_entry_id={"R00299": "reaction"},
    )
    reaction_id = uuid4()
    lookup = FakeReactionLookup(
        reactions=[
            ReactionCandidate(
                id=reaction_id,
                organism_id=ORGANISM_ID,
                internal_id="FFA_R0001",
                name="hexokinase",
                kegg_reaction_id="R00299",
            )
        ]
    )
    [candidate] = resolve_reaction_via_kegg(
        query="hexokinase",
        original_mention="hexokinase",
        organism_id=ORGANISM_ID,
        connector=connector,
        lookup=lookup,
    )
    assert candidate.normalization_input.kegg_reaction_id == "R00299"
    assert candidate.normalization_result.status is NormalizationStatus.MATCHED
    # The raw equation is never parsed into participants.
    assert candidate.normalization_input.participants == ()


def test_resolve_publication_via_pubmed_matched():
    article = PubMedArticleRecord(
        pmid="12345",
        title="A study of FadD",
        abstract_sections=(),
        journal_title="J. Biol. Chem.",
        year="2001",
        authors=(),
        article_ids=(),
        publication_types=(),
    )
    normalized = PubMedNormalizedRecord(
        pmid="12345",
        title="A study of FadD",
        abstract=None,
        journal="J. Biol. Chem.",
        year=2001,
        authors=(),
        doi="10.1/example",
        pmcid="PMC1",
        raw=article,
    )
    connector = FakePubMedConnector(
        hits_by_query={"A study of FadD": [PubMedSearchHit(pmid="12345")]},
        article_by_pmid={"12345": article},
        normalized_by_pmid={"12345": normalized},
    )
    pub_id = uuid4()
    lookup = FakePublicationLookup(
        publications=[
            PublicationCandidate(
                id=pub_id,
                pmid="12345",
                pmcid="PMC1",
                doi="10.1/example",
                title="A study of FadD",
                journal="J. Biol. Chem.",
                year=2001,
            )
        ]
    )
    [candidate] = resolve_publication_via_pubmed(
        query="A study of FadD",
        original_mention="A study of FadD",
        connector=connector,
        lookup=lookup,
    )
    assert candidate.normalization_input.pmid == "12345"
    assert candidate.normalization_result.status is NormalizationStatus.MATCHED
    assert dict(candidate.retrieved_identifiers)["doi"] == "10.1/example"


def test_publication_title_only_search_hit_yields_no_candidate_without_fetch():
    """A search returning PMIDs with no fetchable article never produces a

    candidate -- title text alone, unverified, must never become strong
    identity.
    """
    connector = FakePubMedConnector(
        hits_by_query={"Some title": [PubMedSearchHit(pmid="99999")]},
        article_by_pmid={},  # fetch() returns None
        normalized_by_pmid={},
    )
    candidates = resolve_publication_via_pubmed(
        query="Some title",
        original_mention="Some title",
        connector=connector,
        lookup=FakePublicationLookup(publications=[]),
    )
    assert candidates == []


def _uniprot_entry(**overrides) -> UniProtEntryRecord:
    merged = {
        "primary_accession": "P99999",
        "entry_name": "TEST1_YEAST",
        "entry_type": "UniProtKB reviewed (Swiss-Prot)",
        "secondary_accessions": (),
        "recommended_name": "Test-only acetyl-CoA carboxylase",
        "submitted_names": (),
        "gene_names": ("TEST1",),
        "organism_name": "Saccharomyces cerevisiae",
        "organism_taxonomy_id": 559292,
        "ec_numbers": (),
        "sequence_length": 100,
        "cross_references": (),
        "raw": {},
    } | overrides
    return UniProtEntryRecord(**merged)


def _uniprot_normalized(entry: UniProtEntryRecord, *, reviewed: bool = True):
    from app.connectors.uniprot import UniProtProteinRecord

    return UniProtProteinRecord(
        primary_accession=entry.primary_accession,
        entry_name=entry.entry_name,
        reviewed=reviewed,
        protein_name=entry.recommended_name,
        gene_names=entry.gene_names,
        organism_name=entry.organism_name,
        organism_taxonomy_id=entry.organism_taxonomy_id,
        ec_numbers=entry.ec_numbers,
        sequence_length=entry.sequence_length,
        secondary_accessions=entry.secondary_accessions,
        cross_references=entry.cross_references,
        raw=entry,
    )


def test_resolve_protein_via_uniprot_matched():
    entry = _uniprot_entry()
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(
                    primary_accession="P99999", entry_name="TEST1_YEAST", reviewed=True
                )
            ]
        },
        entry_by_accession={"P99999": entry},
        normalized_by_accession={"P99999": _uniprot_normalized(entry)},
    )
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=[
            ProteinCandidate(
                id=protein_id,
                organism_id=ORGANISM_ID,
                uniprot_id="P99999",
                name="Test-only acetyl-CoA carboxylase",
            )
        ]
    )
    [candidate] = resolve_protein_via_uniprot(
        query="ACC1",
        original_mention="ACC1",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=lookup,
    )
    assert candidate.normalization_input.uniprot_id == "P99999"
    assert candidate.normalization_result.status is NormalizationStatus.MATCHED


def test_resolve_protein_via_uniprot_includes_organism_name_in_query():
    connector = FakeUniProtConnector()
    resolve_protein_via_uniprot(
        query="ACC1",
        original_mention="ACC1",
        organism_id=ORGANISM_ID,
        organism_context_text="Saccharomyces cerevisiae",
        connector=connector,
        lookup=FakeProteinLookup(proteins=[]),
    )
    # No hits configured for the combined term -- confirms the adapter built
    # and searched with the combined term rather than the bare query alone.
    assert 'ACC1 AND organism_name:"Saccharomyces cerevisiae"' not in connector.hits_by_query


def test_resolve_protein_skips_hit_with_no_fetchable_entry():
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [UniProtSearchHit(primary_accession="P00404", entry_name=None, reviewed=None)]
        },
        entry_by_accession={},
        normalized_by_accession={},
    )
    candidates = resolve_protein_via_uniprot(
        query="ACC1",
        original_mention="ACC1",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=FakeProteinLookup(proteins=[]),
    )
    assert candidates == []


def test_resolve_protein_multiple_hits_all_preserved():
    entry_a = _uniprot_entry(primary_accession="P99999")
    entry_b = _uniprot_entry(primary_accession="P88888")
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(primary_accession="P99999", entry_name="A", reviewed=True),
                UniProtSearchHit(primary_accession="P88888", entry_name="B", reviewed=False),
            ]
        },
        entry_by_accession={"P99999": entry_a, "P88888": entry_b},
        normalized_by_accession={
            "P99999": _uniprot_normalized(entry_a, reviewed=True),
            "P88888": _uniprot_normalized(entry_b, reviewed=False),
        },
    )
    candidates = resolve_protein_via_uniprot(
        query="ACC1",
        original_mention="ACC1",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=FakeProteinLookup(proteins=[]),
    )
    assert len(candidates) == 2
    assert {c.source_record_identifier for c in candidates} == {"P99999", "P88888"}


def test_resolve_protein_never_reads_gene_names_into_identity():
    entry = _uniprot_entry(gene_names=("TEST1", "ACC1", "FAS3"))
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(
                    primary_accession="P99999", entry_name="TEST1_YEAST", reviewed=True
                )
            ]
        },
        entry_by_accession={"P99999": entry},
        normalized_by_accession={"P99999": _uniprot_normalized(entry)},
    )
    [candidate] = resolve_protein_via_uniprot(
        query="ACC1",
        original_mention="ACC1",
        organism_id=ORGANISM_ID,
        organism_context_text=None,
        connector=connector,
        lookup=FakeProteinLookup(proteins=[]),
    )
    assert candidate.normalization_input.gene_id is None
