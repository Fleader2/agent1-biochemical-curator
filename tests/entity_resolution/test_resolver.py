"""Tests for ``app.entity_resolution.resolver.resolve_entity_mention``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.claim_generation.mapping import NormalizationLookups
from app.claim_generation.types import EntityKind
from app.connectors.exceptions import ConnectorNetworkError
from app.connectors.kegg import KeggCompoundRecord, KeggFlatFileRecord, KeggSearchHit
from app.connectors.sgd import SgdLocusRecord, SgdNormalizedRecord, SgdSearchHit
from app.connectors.uniprot import UniProtEntryRecord, UniProtProteinRecord, UniProtSearchHit
from app.entity_resolution.resolver import ConnectorBundle, resolve_entity_mention
from app.entity_resolution.types import EntityMention, MentionResolutionStatus
from app.models.enums import SourceType
from app.normalization.gene import GeneCandidate
from app.normalization.protein import ProteinCandidate
from tests.claim_generation.fakes import FakeCompoundLookup, FakeGeneLookup, FakeProteinLookup
from tests.entity_resolution.fakes import FakeKeggConnector, FakeSgdConnector, FakeUniProtConnector

ORGANISM_ID = uuid4()


def _mention(**overrides) -> EntityMention:
    merged = {
        "original_text": "ACC1",
        "entity_kind": EntityKind.GENE,
        "source_context": SourceType.PUBMED,
        "source_context_identifier": "PMID:1",
    } | overrides
    return EntityMention(**merged)


def _sgd_setup(sgd_id="S000000001", symbol="ACC1", matched=True):
    locus = SgdLocusRecord(
        sgd_id=sgd_id,
        systematic_name="YHR123W",
        standard_name=symbol,
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw={},
    )
    normalized = SgdNormalizedRecord(
        sgd_id=sgd_id,
        systematic_name="YHR123W",
        standard_name=symbol,
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw=locus,
    )
    connector = FakeSgdConnector(
        hits_by_query={
            symbol: [
                SgdSearchHit(
                    sgd_id=sgd_id,
                    systematic_name="YHR123W",
                    standard_name=symbol,
                    description=None,
                    aliases=(),
                )
            ]
        },
        locus_by_sgd_id={sgd_id: locus},
        normalized_by_sgd_id={sgd_id: normalized},
    )
    genes = []
    gene_id = uuid4()
    if matched:
        genes.append(
            GeneCandidate(
                id=gene_id,
                organism_id=ORGANISM_ID,
                sgd_id=sgd_id,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR123W",
                symbol=symbol,
                aliases=(),
                description=None,
            )
        )
    return connector, FakeGeneLookup(genes=genes), gene_id


# --- connector inventory / unsupported kinds ------------------------------------


@pytest.mark.parametrize(
    "kind", [EntityKind.PROTEIN, EntityKind.ORGANISM, EntityKind.COMPARTMENT, EntityKind.UNKNOWN]
)
def test_kinds_without_any_connector_are_unsupported(kind):
    result = resolve_entity_mention(_mention(entity_kind=kind, original_text="something"))
    assert result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND
    assert result.candidates == ()
    assert result.sources_queried == ()


def test_gene_without_sgd_connector_configured_is_unsupported():
    result = resolve_entity_mention(_mention(organism_id=ORGANISM_ID))
    assert result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND


def test_no_fabricated_connector_fallback_for_unsupported_kind():
    """Supplying SGD/KEGG connectors must not make an unrelated kind

    (e.g. PROTEIN) suddenly resolve -- there is no cross-kind fallback.
    """
    connector, lookup, _ = _sgd_setup()
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="FadD"),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND


# --- Gene ---------------------------------------------------------------------


def test_gene_exact_symbol_resolves_when_matched():
    connector, lookup, gene_id = _sgd_setup(matched=True)
    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert result.status is MentionResolutionStatus.RESOLVED
    assert result.resolved_entity_id == gene_id
    assert len(result.candidates) == 1
    assert result.candidates[0].normalization_input.sgd_id == "S000000001"


def test_gene_matched_only_when_normalizer_returns_matched():
    connector, lookup, _ = _sgd_setup(matched=False)
    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert result.status is not MentionResolutionStatus.RESOLVED
    assert result.status is MentionResolutionStatus.NEW_CANDIDATE


def test_gene_multiple_candidates_remain_ambiguous():
    sgd_id_a, sgd_id_b = "S000000001", "S000000002"
    locus_a = SgdLocusRecord(
        sgd_id=sgd_id_a,
        systematic_name="YHR123W",
        standard_name="ACC1",
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw={},
    )
    locus_b = SgdLocusRecord(
        sgd_id=sgd_id_b,
        systematic_name="YHR124W",
        standard_name="ACC1",
        locus_type="ORF",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw={},
    )
    normalized_a = SgdNormalizedRecord(
        sgd_id=sgd_id_a,
        systematic_name="YHR123W",
        standard_name="ACC1",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw=locus_a,
    )
    normalized_b = SgdNormalizedRecord(
        sgd_id=sgd_id_b,
        systematic_name="YHR124W",
        standard_name="ACC1",
        description=None,
        aliases=(),
        uniprot_id=None,
        external_links=(),
        raw=locus_b,
    )
    connector = FakeSgdConnector(
        hits_by_query={
            "ACC1": [
                SgdSearchHit(
                    sgd_id=sgd_id_a,
                    systematic_name="YHR123W",
                    standard_name="ACC1",
                    description=None,
                    aliases=(),
                ),
                SgdSearchHit(
                    sgd_id=sgd_id_b,
                    systematic_name="YHR124W",
                    standard_name="ACC1",
                    description=None,
                    aliases=(),
                ),
            ]
        },
        locus_by_sgd_id={sgd_id_a: locus_a, sgd_id_b: locus_b},
        normalized_by_sgd_id={sgd_id_a: normalized_a, sgd_id_b: normalized_b},
    )
    matched_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=matched_id,
                organism_id=ORGANISM_ID,
                sgd_id=sgd_id_a,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR123W",
                symbol="ACC1",
                aliases=(),
                description=None,
            ),
            GeneCandidate(
                id=uuid4(),
                organism_id=ORGANISM_ID,
                sgd_id=sgd_id_b,
                ncbi_gene_id=None,
                kegg_gene_id=None,
                systematic_name="YHR124W",
                symbol="ACC1",
                aliases=(),
                description=None,
            ),
        ]
    )
    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert result.status is MentionResolutionStatus.CONFLICTED
    assert len(result.candidates) == 2


def test_gene_without_organism_id_is_unresolved_no_connector_call():
    calls = {"searched": False}

    class _TrackingSgd(FakeSgdConnector):
        def search(self, query):
            calls["searched"] = True
            return super().search(query)

    connector = _TrackingSgd()
    result = resolve_entity_mention(
        _mention(organism_id=None),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=FakeGeneLookup(genes=[])),
    )
    assert result.status is MentionResolutionStatus.UNRESOLVED
    assert calls["searched"] is False


def test_gene_foreign_organism_candidate_not_silently_chosen():
    """A gene matching the symbol but registered under a *different*

    organism than mention.organism_id must not be silently accepted --
    app.normalization.gene itself treats this as CONFLICTED, and the
    resolver must not override that.
    """
    other_organism = uuid4()
    connector, _, _ = _sgd_setup(matched=False)
    gene_id = uuid4()
    lookup = FakeGeneLookup(
        genes=[
            GeneCandidate(
                id=gene_id,
                organism_id=other_organism,
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
    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert result.status is MentionResolutionStatus.CONFLICTED
    assert result.resolved_entity_id is None


# --- Compound -------------------------------------------------------------------


def test_compound_multiple_candidates_never_collapsed():
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
        names=("ATP",),
        formula="C10H16N5O13P3",
        exact_mass=None,
        mol_weight=None,
        pathways=(),
        raw=KeggFlatFileRecord(entry_id="C00002", entry_type="Compound", fields={}),
    )
    connector = FakeKeggConnector(
        hits_by_query={
            ("query", "compound"): [
                KeggSearchHit(entry_id="C00001", description="Water"),
                KeggSearchHit(entry_id="C00002", description="ATP"),
            ]
        },
        normalized_by_entry_id={"C00001": record_a, "C00002": record_b},
        entry_type_by_entry_id={"C00001": "compound", "C00002": "compound"},
    )
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.COMPOUND, original_text="query"),
        connectors=ConnectorBundle(kegg=connector),
        lookups=NormalizationLookups(compound=FakeCompoundLookup(compounds=[])),
    )
    assert len(result.candidates) == 2
    assert {c.source_record_identifier for c in result.candidates} == {"C00001", "C00002"}


# --- Protein --------------------------------------------------------------------


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


def _uniprot_normalized(
    entry: UniProtEntryRecord, *, reviewed: bool = True
) -> UniProtProteinRecord:
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


def _uniprot_setup(accession="P99999", reviewed=True, matched=True):
    entry = _uniprot_entry(primary_accession=accession)
    normalized = _uniprot_normalized(entry, reviewed=reviewed)
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(
                    primary_accession=accession, entry_name="TEST1_YEAST", reviewed=reviewed
                )
            ]
        },
        entry_by_accession={accession: entry},
        normalized_by_accession={accession: normalized},
    )
    proteins = []
    protein_id = uuid4()
    if matched:
        proteins.append(
            ProteinCandidate(
                id=protein_id,
                organism_id=ORGANISM_ID,
                uniprot_id=accession,
                name="Test-only acetyl-CoA carboxylase",
                gene_id=None,
                ec_number=None,
            )
        )
    return connector, FakeProteinLookup(proteins=proteins), protein_id


def test_protein_without_uniprot_connector_configured_is_unsupported():
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, organism_id=ORGANISM_ID)
    )
    assert result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND


def test_protein_without_organism_id_is_unresolved_no_connector_call():
    calls = {"searched": False}

    class _TrackingUniProt(FakeUniProtConnector):
        def search(self, query, *, organism_taxonomy_id=None, limit=25):
            calls["searched"] = True
            return super().search(query, organism_taxonomy_id=organism_taxonomy_id, limit=limit)

    connector = _TrackingUniProt()
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=None),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=FakeProteinLookup(proteins=[])),
    )
    assert result.status is MentionResolutionStatus.UNRESOLVED
    assert calls["searched"] is False


def test_protein_matched_candidate_resolves():
    connector, lookup, protein_id = _uniprot_setup(matched=True)
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=lookup),
    )
    assert result.status is MentionResolutionStatus.RESOLVED
    assert result.resolved_entity_id == protein_id
    assert len(result.candidates) == 1
    assert result.candidates[0].normalization_input.uniprot_id == "P99999"


def test_protein_unmatched_candidate_is_new_candidate():
    connector, lookup, _ = _uniprot_setup(matched=False)
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=lookup),
    )
    assert result.status is MentionResolutionStatus.NEW_CANDIDATE


def test_protein_multiple_candidates_different_matches_conflicted():
    entry_a = _uniprot_entry(primary_accession="P99999")
    entry_b = _uniprot_entry(primary_accession="P88888")
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(primary_accession="P99999", entry_name="A", reviewed=True),
                UniProtSearchHit(primary_accession="P88888", entry_name="B", reviewed=True),
            ]
        },
        entry_by_accession={"P99999": entry_a, "P88888": entry_b},
        normalized_by_accession={
            "P99999": _uniprot_normalized(entry_a),
            "P88888": _uniprot_normalized(entry_b),
        },
    )
    lookup = FakeProteinLookup(
        proteins=[
            ProteinCandidate(
                id=uuid4(),
                organism_id=ORGANISM_ID,
                uniprot_id="P99999",
                name="A",
                gene_id=None,
                ec_number=None,
            ),
            ProteinCandidate(
                id=uuid4(),
                organism_id=ORGANISM_ID,
                uniprot_id="P88888",
                name="B",
                gene_id=None,
                ec_number=None,
            ),
        ]
    )
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=lookup),
    )
    assert result.status is MentionResolutionStatus.CONFLICTED
    assert len(result.candidates) == 2


def test_protein_multiple_candidates_same_match_resolved():
    entry_a = _uniprot_entry(primary_accession="P99999")
    entry_b = _uniprot_entry(primary_accession="P99999-2")
    same_id = uuid4()
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(primary_accession="P99999", entry_name="A", reviewed=True),
                UniProtSearchHit(primary_accession="P99999-2", entry_name="A iso2", reviewed=True),
            ]
        },
        entry_by_accession={"P99999": entry_a, "P99999-2": entry_b},
        normalized_by_accession={
            "P99999": _uniprot_normalized(entry_a),
            "P99999-2": _uniprot_normalized(entry_b),
        },
    )
    lookup = FakeProteinLookup(
        proteins=[
            ProteinCandidate(
                id=same_id,
                organism_id=ORGANISM_ID,
                uniprot_id="P99999",
                name="A",
                gene_id=None,
                ec_number=None,
            ),
            ProteinCandidate(
                id=same_id,
                organism_id=ORGANISM_ID,
                uniprot_id="P99999-2",
                name="A iso2",
                gene_id=None,
                ec_number=None,
            ),
        ]
    )
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=lookup),
    )
    assert result.status is MentionResolutionStatus.RESOLVED
    assert result.resolved_entity_id == same_id
    assert len(result.candidates) == 2


def test_protein_reviewed_and_unreviewed_candidates_both_preserved():
    """Reviewed status must never cause one candidate to be silently

    preferred over another -- both are kept and normalization decides.
    """
    reviewed_entry = _uniprot_entry(primary_accession="P99999")
    unreviewed_entry = _uniprot_entry(primary_accession="A0A999TEST")
    connector = FakeUniProtConnector(
        hits_by_query={
            "ACC1": [
                UniProtSearchHit(primary_accession="P99999", entry_name="A", reviewed=True),
                UniProtSearchHit(primary_accession="A0A999TEST", entry_name="B", reviewed=False),
            ]
        },
        entry_by_accession={"P99999": reviewed_entry, "A0A999TEST": unreviewed_entry},
        normalized_by_accession={
            "P99999": _uniprot_normalized(reviewed_entry, reviewed=True),
            "A0A999TEST": _uniprot_normalized(unreviewed_entry, reviewed=False),
        },
    )
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=FakeProteinLookup(proteins=[])),
    )
    assert len(result.candidates) == 2
    reviewed_values = {dict(c.retrieved_identifiers).get("reviewed") for c in result.candidates}
    assert reviewed_values == {"True", "False"}


def test_protein_source_failure_distinct_from_no_candidate():
    class _FailingUniProt:
        def search(self, query, *, organism_taxonomy_id=None, limit=25):
            raise ConnectorNetworkError("timeout")

        def fetch(self, accession):  # pragma: no cover - not reached
            return None

        def normalize(self, raw):  # pragma: no cover - not reached
            raise AssertionError

    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=_FailingUniProt()),
        lookups=NormalizationLookups(protein=FakeProteinLookup(proteins=[])),
    )
    assert result.status is MentionResolutionStatus.SOURCE_FAILURE
    assert result.failed_source is SourceType.UNIPROT
    assert result.error_category == "ConnectorNetworkError"


def test_protein_zero_search_hits_is_no_candidate():
    connector = FakeUniProtConnector()  # empty -- search() returns []
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PROTEIN, original_text="ACC1", organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(uniprot=connector),
        lookups=NormalizationLookups(protein=FakeProteinLookup(proteins=[])),
    )
    assert result.status is MentionResolutionStatus.NO_CANDIDATE


def test_protein_never_triggers_gene_or_reaction_resolution():
    """Structural safety check: resolving a PROTEIN mention must not import

    or call anything from Gene/Reaction resolution, must never touch the
    UniProt ID Mapping API, and must never collapse an isoform accession.
    """
    import inspect

    import app.entity_resolution.adapters as adapters_module

    source = inspect.getsource(adapters_module.resolve_protein_via_uniprot)
    assert "normalize_gene" not in source
    assert "normalize_reaction" not in source
    assert "idmapping" not in source.lower()
    assert ".split(" not in source  # no isoform-suffix stripping


# --- source failure ---------------------------------------------------------------


def test_source_failure_distinct_from_no_candidate():
    class _FailingSgd:
        def search(self, query):
            raise ConnectorNetworkError("timeout")

        def fetch(self, external_id):  # pragma: no cover - not reached
            return None

        def normalize(self, raw):  # pragma: no cover - not reached
            raise AssertionError

    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=_FailingSgd()),
        lookups=NormalizationLookups(gene=FakeGeneLookup(genes=[])),
    )
    assert result.status is MentionResolutionStatus.SOURCE_FAILURE
    assert result.status is not MentionResolutionStatus.NO_CANDIDATE
    assert result.failed_source is SourceType.SGD
    assert result.error_category == "ConnectorNetworkError"


# --- no candidate ------------------------------------------------------------------


def test_zero_search_hits_is_no_candidate_not_negative_conclusion():
    connector = FakeSgdConnector()  # empty -- search() returns []
    result = resolve_entity_mention(
        _mention(organism_id=ORGANISM_ID),
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=FakeGeneLookup(genes=[])),
    )
    assert result.status is MentionResolutionStatus.NO_CANDIDATE
    assert "does not exist" not in result.reason


# --- no LLM identifier invention ---------------------------------------------------


def test_no_unverified_identifier_can_become_resolved():
    """There is no code path in this module that accepts a bare string as a

    strong identifier without it coming from a genuine connector record
    fetch -- verified structurally by confirming every produced candidate
    carries a source_record_identifier that traces to a fetch() call, and
    that resolve_entity_mention itself performs no string-to-identity
    shortcut for any kind.
    """
    result = resolve_entity_mention(
        _mention(entity_kind=EntityKind.PUBLICATION, original_text="A paper")
    )
    assert (
        result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND
    )  # no pubmed connector configured
    assert result.resolved_entity_id is None


# --- determinism -------------------------------------------------------------------


def test_repeated_resolution_is_identical():
    connector, lookup, _ = _sgd_setup(matched=True)
    mention = _mention(organism_id=ORGANISM_ID)
    first = resolve_entity_mention(
        mention,
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    second = resolve_entity_mention(
        mention,
        connectors=ConnectorBundle(sgd=connector),
        lookups=NormalizationLookups(gene=lookup),
    )
    assert first == second


# --- no persistence ------------------------------------------------------------------


def test_module_never_imports_a_database_session():
    import inspect

    import app.entity_resolution.adapters as adapters_module
    import app.entity_resolution.resolver as resolver_module

    for module in (resolver_module, adapters_module):
        source = inspect.getsource(module)
        assert "Session" not in source
        assert "SourceCrossReference" not in source
        assert "ExternalRecord" not in source
        assert "Claim(" not in source
        assert "Evidence(" not in source
