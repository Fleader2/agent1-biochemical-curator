"""Tests for ``app.claim_generation.mapping``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.claim_generation.errors import EntityTypingError, NormalizationFailureError
from app.claim_generation.mapping import (
    NormalizationLookups,
    _normalize_by_kind,
    resolve_entity_reference,
)
from app.claim_generation.types import EntityKind
from app.connectors.exceptions import ConnectorNetworkError
from app.connectors.kegg import (
    KeggCompoundRecord,
    KeggFlatFileRecord,
    KeggReactionRecord,
    KeggSearchHit,
)
from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord, PubMedSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit
from app.entity_resolution.resolver import ConnectorBundle
from app.entity_resolution.types import MentionResolutionStatus
from app.models.enums import SourceType
from app.normalization.compound import CompoundCandidate
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.protein import ProteinCandidate
from app.normalization.publication import PublicationCandidate
from app.normalization.types import NormalizationStatus
from tests.claim_generation.fakes import (
    FakeCompoundLookup,
    FakeGeneLookup,
    FakeOrganismLookup,
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


def _sgd_locus(
    sgd_id="S000000001", standard_name="FadD", systematic_name="YHR123W"
) -> SgdLocusRecord:
    return SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name=systematic_name,
        standard_name=standard_name,
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id=None,
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


def test_no_text_returns_none():
    assert (
        resolve_entity_reference(
            text=None,
            kind=EntityKind.GENE,
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            organism_id=None,
            lookups=NormalizationLookups(),
        )
        is None
    )


def test_unknown_kind_is_never_normalized():
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.UNKNOWN,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(
            gene=FakeGeneLookup(genes=[]),
        ),
    )
    assert reference.entity_kind is EntityKind.UNKNOWN
    assert reference.normalization_result is None
    assert reference.normalized_id is None


def test_no_lookup_available_leaves_unresolved():
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(),  # no gene lookup supplied
    )
    assert reference.normalization_result is None
    assert reference.normalized_id is None


def test_organism_scoped_kind_without_resolved_organism_leaves_unresolved():
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,  # organism not resolved
        lookups=NormalizationLookups(gene=FakeGeneLookup(genes=[])),
    )
    assert reference.normalization_result is None


def test_gene_symbol_match_is_ambiguous_not_matched():
    """A bare-text gene mention only ever populates GeneIdentity.symbol, a

    Level 3 (candidate-generation only) field -- app.normalization.gene
    never independently MATCHES on symbol alone, even with exactly one
    candidate (see that module's own "Level 3 ... never independently
    MATCHED" policy). This is a real, expected limitation of mapping bare
    text mentions to weak identity fields, not a bug -- see this
    increment's completion report.
    """
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadD",
                aliases=(),
                description=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(gene=lookup),
    )
    assert reference.normalization_result.status is NormalizationStatus.AMBIGUOUS
    assert reference.normalized_id is None
    assert gene_id in reference.normalization_result.candidate_entity_ids


def test_compound_is_global_no_organism_needed():
    """Global dispatch: COMPOUND normalizes without any organism_id, and a

    canonical_name-only match is AMBIGUOUS (Level 3, same policy as gene
    symbols above) rather than requiring or being blocked by organism
    context.
    """
    compound_id = uuid4()
    lookup = FakeCompoundLookup(
        compounds=[CompoundCandidate(id=compound_id, canonical_name="acyl-CoA")]
    )
    reference = resolve_entity_reference(
        text="acyl-CoA",
        kind=EntityKind.COMPOUND,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(compound=lookup),
    )
    assert reference.normalization_result.status is NormalizationStatus.AMBIGUOUS
    assert compound_id in reference.normalization_result.candidate_entity_ids


def test_organism_kind_resolves_via_organism_lookup():
    organism_row_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=[
            OrganismCandidate(
                id=organism_row_id,
                scientific_name="Escherichia coli",
                strain=None,
                ncbi_taxonomy_id=None,
                kegg_code=None,
                biocyc_id=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="Escherichia coli",
        kind=EntityKind.ORGANISM,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(organism=lookup),
    )
    assert reference.normalized_id == organism_row_id


def test_publication_from_bare_text_is_always_unresolved():
    """PublicationIdentity requires a pmid/pmcid/doi -- a bare title-like

    text mention alone is never sufficient, so this always raises
    ValueError internally and is treated as an ordinary unresolved
    outcome, never an error.
    """
    from app.normalization.publication import PublicationLookup

    class _EmptyPublicationLookup:
        def by_pmid(self, pmid):
            return []

        def by_pmcid(self, pmcid):
            return []

        def by_doi(self, doi):
            return []

    assert isinstance(_EmptyPublicationLookup(), PublicationLookup)
    reference = resolve_entity_reference(
        text="A paper about FadD",
        kind=EntityKind.PUBLICATION,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(publication=_EmptyPublicationLookup()),
    )
    assert reference.normalization_result is None
    assert reference.normalized_id is None


def test_unrecognized_kind_raises_entity_typing_error():
    with pytest.raises(EntityTypingError):
        _normalize_by_kind(
            kind="NOT_A_REAL_KIND",  # bypasses the type system deliberately
            text="FadD",
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            organism_id=None,
            lookups=NormalizationLookups(),
        )


def test_unexpected_lookup_exception_is_wrapped():
    class _BrokenGeneLookup:
        def by_sgd_id(self, sgd_id):
            return []

        def by_ncbi_gene_id(self, ncbi_gene_id):
            return []

        def by_kegg_gene_id(self, kegg_gene_id):
            return []

        def by_systematic_name(self, organism_id, systematic_name):
            return []

        def by_symbol(self, organism_id, symbol):
            raise RuntimeError("simulated lookup failure")

        def by_alias(self, organism_id, alias):
            return []

    with pytest.raises(NormalizationFailureError):
        resolve_entity_reference(
            text="FadD",
            kind=EntityKind.GENE,
            source=SourceType.PUBMED,
            source_identifier="PMID:1",
            organism_id=ORGANISM_ID,
            lookups=NormalizationLookups(gene=_BrokenGeneLookup()),
        )


# --- Entity Resolution integration (Increment 16) --------------------------------


def test_entity_resolution_preferred_when_connector_supplied_gene_matched():
    locus = _sgd_locus()
    normalized = _sgd_normalized(locus)
    connector = FakeSgdConnector(
        hits_by_query={
            "FadD": [
                SgdSearchHit(
                    sgd_id="S000000001",
                    systematic_name="YHR123W",
                    standard_name="FadD",
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
                symbol="FadD",
                aliases=(),
                description=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(gene=lookup),
        connectors=ConnectorBundle(sgd=connector),
    )
    assert reference.mention_resolution_result is not None
    assert reference.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert reference.normalized_id == gene_id
    # Proof the bare-symbol direct path was never used: a bare-text GENE
    # match on symbol alone is always AMBIGUOUS (Level 3), never MATCHED
    # (see test_gene_symbol_match_is_ambiguous_not_matched above) -- RESOLVED
    # here could only have come from the SGD-enriched Entity Resolution path.


def test_entity_resolution_falls_back_when_connector_bundle_lacks_this_kind():
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadD",
                aliases=(),
                description=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(gene=lookup),
        connectors=ConnectorBundle(),  # no sgd connector inside it
    )
    # Fell back to direct bare-text normalization: no mention_resolution_result,
    # and symbol-only match is AMBIGUOUS (Level 3), never MATCHED.
    assert reference.mention_resolution_result is None
    assert reference.normalization_result.status is NormalizationStatus.AMBIGUOUS


def test_entity_resolution_source_failure_preserved_not_masked():
    class _FailingSgd:
        def search(self, query):
            raise ConnectorNetworkError("simulated timeout")

        def fetch(self, external_id):  # pragma: no cover - not reached
            return None

        def normalize(self, raw):  # pragma: no cover - not reached
            raise AssertionError

    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id=None,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name=None,
                symbol="FadD",  # would match via direct fallback if one occurred
                aliases=(),
                description=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(gene=lookup),
        connectors=ConnectorBundle(sgd=_FailingSgd()),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.SOURCE_FAILURE
    assert reference.mention_resolution_result.failed_source is SourceType.SGD
    assert reference.normalized_id is None
    # Never silently fell back to the direct bare-text path -- that path
    # would have populated normalization_result (AMBIGUOUS); the failure
    # path leaves it None instead, since zero candidates were produced.
    assert reference.normalization_result is None


def test_entity_resolution_unresolved_missing_organism_preserved():
    connector = FakeSgdConnector()  # search() never called -- organism gate fires first
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,  # no resolved organism
        lookups=NormalizationLookups(gene=FakeGeneLookup(genes=[])),
        connectors=ConnectorBundle(sgd=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.UNRESOLVED
    assert reference.normalized_id is None


def test_entity_resolution_protein_uniprot_flow_matched():
    entry = UniProtEntryRecord(
        primary_accession="P99999",
        entry_name="TEST1_YEAST",
        entry_type="UniProtKB reviewed (Swiss-Prot)",
        secondary_accessions=(),
        recommended_name="Test-only protein",
        submitted_names=(),
        gene_names=("TEST1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=559292,
        ec_numbers=(),
        sequence_length=100,
        cross_references=(),
        raw={},
    )
    normalized = UniProtProteinRecord(
        primary_accession=entry.primary_accession,
        entry_name=entry.entry_name,
        reviewed=True,
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
    connector = FakeUniProtConnector(
        hits_by_query={
            "FadD": [
                UniProtSearchHit(
                    primary_accession="P99999", entry_name="TEST1_YEAST", reviewed=True
                )
            ]
        },
        entry_by_accession={"P99999": entry},
        normalized_by_accession={"P99999": normalized},
    )
    protein_id = uuid4()
    lookup = FakeProteinLookup(
        proteins=[
            ProteinCandidate(
                id=protein_id,
                organism_id=ORGANISM_ID,
                uniprot_id="P99999",
                name="Test-only protein",
                gene_id=None,
                ec_number=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.PROTEIN,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(protein=lookup),
        connectors=ConnectorBundle(uniprot=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert reference.normalized_id == protein_id
    [candidate] = reference.mention_resolution_result.candidates
    assert candidate.normalization_input.gene_id is None  # no Gene inference


def test_entity_resolution_protein_missing_organism_is_unresolved():
    connector = FakeUniProtConnector()
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.PROTEIN,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(protein=FakeProteinLookup(proteins=[])),
        connectors=ConnectorBundle(uniprot=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.UNRESOLVED
    assert reference.normalized_id is None


def test_entity_resolution_organism_kind_never_routed_through_resolver():
    """ORGANISM is not an Entity-Resolution-supported kind -- passing a full

    connectors bundle must not change organism resolution behavior at all.
    """
    organism_row_id = uuid4()
    lookup = FakeOrganismLookup(
        organisms=[
            OrganismCandidate(
                id=organism_row_id,
                scientific_name="Escherichia coli",
                strain=None,
                ncbi_taxonomy_id=None,
                kegg_code=None,
                biocyc_id=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="Escherichia coli",
        kind=EntityKind.ORGANISM,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(organism=lookup),
        connectors=ConnectorBundle(sgd=FakeSgdConnector(), uniprot=FakeUniProtConnector()),
    )
    assert reference.mention_resolution_result is None
    assert reference.normalized_id == organism_row_id


def test_entity_resolution_multiple_corroborating_candidates_resolved_without_result():
    locus_a = _sgd_locus("S000000001", "FadD", "YHR123W")
    locus_b = _sgd_locus("S000000002", "FadD", "YHR124W")
    same_gene_id = uuid4()
    connector = FakeSgdConnector(
        hits_by_query={
            "FadD": [
                SgdSearchHit(
                    sgd_id="S000000001",
                    systematic_name="YHR123W",
                    standard_name="FadD",
                    description=None,
                    aliases=(),
                ),
                SgdSearchHit(
                    sgd_id="S000000002",
                    systematic_name="YHR124W",
                    standard_name="FadD",
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
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=same_gene_id,
                organism_id=ORGANISM_ID,
                sgd_id="S000000001",
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR123W",
                symbol="FadD",
                aliases=(),
                description=None,
            ),
            GeneCandidate(
                id=same_gene_id,
                organism_id=ORGANISM_ID,
                sgd_id="S000000002",
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR124W",
                symbol="FadD",
                aliases=(),
                description=None,
            ),
        ]
    )
    reference = resolve_entity_reference(
        text="FadD",
        kind=EntityKind.GENE,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(gene=lookup),
        connectors=ConnectorBundle(sgd=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert reference.normalized_id == same_gene_id
    assert len(reference.mention_resolution_result.candidates) == 2
    # Multiple corroborating candidates -- no single one is forced to
    # represent the whole reference.
    assert reference.normalization_result is None


def test_entity_resolution_compound_via_kegg_multiple_candidates_preserved():
    record_a = KeggCompoundRecord(
        entry_id="C00001",
        names=("Water",),
        formula="H2O",
        exact_mass=None,
        mol_weight=None,
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="C00001", entry_type="Compound", fields={}),
    )
    record_b = KeggCompoundRecord(
        entry_id="C00002",
        names=("Water-like",),
        formula="H2O",
        exact_mass=None,
        mol_weight=None,
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="C00002", entry_type="Compound", fields={}),
    )
    connector = FakeKeggConnector(
        hits_by_query={
            ("Water", "compound"): [
                KeggSearchHit(entry_id="C00001", description="Water"),
                KeggSearchHit(entry_id="C00002", description="Water-like"),
            ]
        },
        normalized_by_entry_id={"C00001": record_a, "C00002": record_b},
        entry_type_by_entry_id={"C00001": "compound", "C00002": "compound"},
    )
    reference = resolve_entity_reference(
        text="Water",
        kind=EntityKind.COMPOUND,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(compound=FakeCompoundLookup(compounds=[])),
        connectors=ConnectorBundle(kegg=connector),
    )
    # No existing rows -- both KEGG entries normalize to NEW, never created here.
    assert reference.mention_resolution_result.status is MentionResolutionStatus.NEW_CANDIDATE
    assert reference.normalized_id is None
    assert len(reference.mention_resolution_result.candidates) == 2


def test_entity_resolution_reaction_via_kegg_requires_organism_and_does_not_parse_equation():
    connector = FakeKeggConnector()  # no hits configured -- organism gate must fire first
    reference = resolve_entity_reference(
        text="hexokinase reaction",
        kind=EntityKind.REACTION,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,  # no resolved organism
        lookups=NormalizationLookups(reaction=FakeReactionLookup(reactions=[])),
        connectors=ConnectorBundle(kegg=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.UNRESOLVED

    record = KeggReactionRecord(
        entry_id="R00299",
        names=("hexokinase reaction",),
        definition=None,
        equation="C00031 + C00002 <=> C00092 + C00008",
        enzymes=(),
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="R00299", entry_type="Reaction", fields={}),
    )
    connector_with_hit = FakeKeggConnector(
        hits_by_query={
            ("hexokinase reaction", "reaction"): [
                KeggSearchHit(entry_id="R00299", description="hexokinase reaction")
            ]
        },
        normalized_by_entry_id={"R00299": record},
        entry_type_by_entry_id={"R00299": "reaction"},
    )
    resolved = resolve_entity_reference(
        text="hexokinase reaction",
        kind=EntityKind.REACTION,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=ORGANISM_ID,
        lookups=NormalizationLookups(reaction=FakeReactionLookup(reactions=[])),
        connectors=ConnectorBundle(kegg=connector_with_hit),
    )
    [candidate] = resolved.mention_resolution_result.candidates
    # reaction_identity_from_kegg never parses the raw equation into participants.
    assert candidate.normalization_input.participants == ()


def test_entity_resolution_publication_via_pubmed_uses_verified_identifiers():
    article = PubMedArticleRecord(
        pmid="12345678",
        title="A study of FadR regulation",
        abstract_sections=(),
        journal_title="J. Bacteriol.",
        year="2001",
        authors=(),
        article_ids=(),
        publication_types=(),
    )
    normalized = PubMedNormalizedRecord(
        pmid="12345678",
        title="A study of FadR regulation",
        abstract=None,
        journal="J. Bacteriol.",
        year=2001,
        authors=(),
        doi=None,
        pmcid=None,
        raw=article,
    )
    connector = FakePubMedConnector(
        hits_by_query={"A study of FadR regulation": [PubMedSearchHit(pmid="12345678")]},
        article_by_pmid={"12345678": article},
        normalized_by_pmid={"12345678": normalized},
    )
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=[
            PublicationCandidate(
                id=publication_id,
                pmid="12345678",
                pmcid=None,
                doi=None,
                title="A study of FadR regulation",
                journal=None,
                year=None,
            )
        ]
    )
    reference = resolve_entity_reference(
        text="A study of FadR regulation",
        kind=EntityKind.PUBLICATION,
        source=SourceType.PUBMED,
        source_identifier="PMID:1",
        organism_id=None,
        lookups=NormalizationLookups(publication=lookup),
        connectors=ConnectorBundle(pubmed=connector),
    )
    assert reference.mention_resolution_result.status is MentionResolutionStatus.RESOLVED
    assert reference.normalized_id == publication_id
    [candidate] = reference.mention_resolution_result.candidates
    assert candidate.normalization_input.pmid == "12345678"  # verified, not the bare title
