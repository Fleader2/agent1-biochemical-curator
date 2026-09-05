"""In-memory fake ``Lookup`` implementations for ``tests/claim_generation``.

Mirrors the pattern every ``tests/normalization/test_*.py`` module already
uses (e.g. ``tests/normalization/test_gene.py``'s ``FakeGeneLookup``): a
small, read-only, in-memory stand-in for each real ``*Lookup`` protocol, so
these tests never touch a database or a connector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.normalization.compartment import CompartmentCandidate
from app.normalization.compound import CompoundCandidate
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.protein import ProteinCandidate
from app.normalization.publication import PublicationCandidate
from app.normalization.reaction import ReactionCandidate


@dataclass(frozen=True, slots=True)
class FakeOrganismLookup:
    organisms: Sequence[OrganismCandidate] = ()

    def by_ncbi_taxonomy_id(self, ncbi_taxonomy_id: int) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.ncbi_taxonomy_id == ncbi_taxonomy_id]

    def by_kegg_code(self, kegg_code: str) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.kegg_code == kegg_code]

    def by_biocyc_id(self, biocyc_id: str) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.biocyc_id == biocyc_id]

    def by_scientific_name_and_strain(
        self, scientific_name: str, strain: str
    ) -> Sequence[OrganismCandidate]:
        return [
            o
            for o in self.organisms
            if o.scientific_name == scientific_name and o.strain == strain
        ]

    def by_scientific_name_without_strain(
        self, scientific_name: str
    ) -> Sequence[OrganismCandidate]:
        return [o for o in self.organisms if o.scientific_name == scientific_name]


@dataclass(frozen=True, slots=True)
class FakePublicationLookup:
    publications: Sequence[PublicationCandidate] = ()

    def by_pmid(self, pmid: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.pmid == pmid]

    def by_pmcid(self, pmcid: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.pmcid == pmcid]

    def by_doi(self, doi: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.doi == doi]


@dataclass(frozen=True, slots=True)
class FakeGeneLookup:
    genes: Sequence[GeneCandidate] = ()

    def by_sgd_id(self, sgd_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.sgd_id == sgd_id]

    def by_ncbi_gene_id(self, ncbi_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.ncbi_gene_id == ncbi_gene_id]

    def by_kegg_gene_id(self, kegg_gene_id: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.kegg_gene_id == kegg_gene_id]

    def by_systematic_name(
        self, organism_id: UUID, systematic_name: str
    ) -> Sequence[GeneCandidate]:
        return [
            g
            for g in self.genes
            if g.organism_id == organism_id and g.systematic_name == systematic_name
        ]

    def by_symbol(self, organism_id: UUID, symbol: str) -> Sequence[GeneCandidate]:
        return [g for g in self.genes if g.organism_id == organism_id and g.symbol == symbol]

    def by_alias(self, organism_id: UUID, alias: str) -> Sequence[GeneCandidate]:
        return [
            g
            for g in self.genes
            if g.organism_id == organism_id and alias in (g.aliases or ())
        ]


@dataclass(frozen=True, slots=True)
class FakeProteinLookup:
    proteins: Sequence[ProteinCandidate] = ()

    def by_uniprot_id(self, uniprot_id: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.uniprot_id == uniprot_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ProteinCandidate]:
        return [p for p in self.proteins if p.organism_id == organism_id and p.name == name]


@dataclass(frozen=True, slots=True)
class FakeCompoundLookup:
    compounds: Sequence[CompoundCandidate] = ()

    def by_chebi_id(self, chebi_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.chebi_id == chebi_id]

    def by_kegg_compound_id(self, kegg_compound_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.kegg_compound_id == kegg_compound_id]

    def by_pubchem_cid(self, pubchem_cid: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.pubchem_cid == pubchem_cid]

    def by_metacyc_id(self, metacyc_id: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.metacyc_id == metacyc_id]

    def by_inchikey(self, inchikey: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.inchikey == inchikey]

    def by_canonical_name(self, canonical_name: str) -> Sequence[CompoundCandidate]:
        return [c for c in self.compounds if c.canonical_name == canonical_name]

    def by_synonym(self, synonym: str) -> Sequence[CompoundCandidate]:
        # CompoundCandidate deliberately carries no synonyms field (see its
        # own docstring) -- a synonym match already resolves to a specific
        # candidate via this lookup, so tests seed matches by canonical_name
        # instead. Not exercised by these tests.
        return []


@dataclass(frozen=True, slots=True)
class FakeCompartmentLookup:
    compartments: Sequence[CompartmentCandidate] = ()

    def by_ontology_id(self, ontology_id: str) -> Sequence[CompartmentCandidate]:
        return [c for c in self.compartments if c.ontology_id == ontology_id]

    def by_name(self, organism_id: UUID | None, name: str) -> Sequence[CompartmentCandidate]:
        return [
            c for c in self.compartments if c.organism_id == organism_id and c.name == name
        ]

    def by_abbreviation(
        self, organism_id: UUID | None, abbreviation: str
    ) -> Sequence[CompartmentCandidate]:
        return [
            c
            for c in self.compartments
            if c.organism_id == organism_id and c.abbreviation == abbreviation
        ]


@dataclass(frozen=True, slots=True)
class FakeReactionLookup:
    reactions: Sequence[ReactionCandidate] = ()

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.kegg_reaction_id == kegg_reaction_id]

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.metacyc_reaction_id == metacyc_reaction_id]

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        return [r for r in self.reactions if r.rhea_id == rhea_id]

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ReactionCandidate]:
        return [
            r for r in self.reactions if r.organism_id == organism_id and r.name == name
        ]
