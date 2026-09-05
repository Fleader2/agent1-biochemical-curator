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
from app.models.enums import SourceType
from app.normalization.compound import CompoundCandidate
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.types import NormalizationStatus
from tests.claim_generation.fakes import FakeCompoundLookup, FakeGeneLookup, FakeOrganismLookup

ORGANISM_ID = uuid4()


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
