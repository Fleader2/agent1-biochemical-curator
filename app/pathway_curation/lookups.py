"""SQLAlchemy-backed ``*Lookup`` adapters.

**The confirmed orchestration gap this module closes.** Every
``app.normalization.*normalize_X`` function requires a caller-supplied,
read-only ``*Lookup`` implementation (``OrganismLookup``, ``GeneLookup``,
...), and every ``app.persistence.*persist_X`` function requires the
``NormalizationResult`` that only calling ``normalize_X`` first can
produce. Before this increment, the *only* implementations of any of
these ``Lookup`` protocols anywhere in this repository were test fakes
(``tests/normalization/test_*.py``, ``tests/claim_generation/fakes.py``)
-- verified directly by searching the repository before writing this
module. No real, database-backed adapter existed, which is exactly why no
prior increment could wire normalization and persistence together against
a real database (``docs/23_agent1_v1_scope_and_completion.md`` §19: "No
high-level ingestion orchestration entry point... is introduced in this
increment").

This module supplies exactly that missing glue -- one class per entity
type, each a thin, read-only ``SELECT``-only adapter over the real ORM
models. It reimplements no identity policy: every method here is a plain
query by the exact column(s) the corresponding ``*Lookup`` protocol
names, returning the corresponding ``*Candidate`` snapshot type
unchanged. All identity *decisions* remain entirely inside
``app.normalization.*``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compartment import Compartment
from app.models.compound import Compound, CompoundSynonym
from app.models.gene import Gene
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.reaction import Reaction, ReactionEnzyme
from app.normalization.compartment import CompartmentCandidate
from app.normalization.compound import CompoundCandidate
from app.normalization.gene import GeneCandidate
from app.normalization.organism import OrganismCandidate
from app.normalization.protein import ProteinCandidate
from app.normalization.publication import PublicationCandidate
from app.normalization.reaction import ReactionCandidate
from app.normalization.reaction_enzyme import ReactionEnzymeCandidate


class SqlAlchemyOrganismLookup:
    """Real ``OrganismLookup`` implementation over ``app.models.organism.Organism``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[OrganismCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            OrganismCandidate(
                id=row.id,
                scientific_name=row.scientific_name,
                strain=row.strain,
                ncbi_taxonomy_id=row.ncbi_taxonomy_id,
                kegg_code=row.kegg_code,
                biocyc_id=row.biocyc_id,
            )
            for row in rows
        )

    def by_ncbi_taxonomy_id(self, ncbi_taxonomy_id: int) -> Sequence[OrganismCandidate]:
        return self._candidates(
            select(Organism).where(Organism.ncbi_taxonomy_id == ncbi_taxonomy_id)
        )

    def by_kegg_code(self, kegg_code: str) -> Sequence[OrganismCandidate]:
        return self._candidates(select(Organism).where(Organism.kegg_code == kegg_code))

    def by_biocyc_id(self, biocyc_id: str) -> Sequence[OrganismCandidate]:
        return self._candidates(select(Organism).where(Organism.biocyc_id == biocyc_id))

    def by_scientific_name_and_strain(
        self, scientific_name: str, strain: str
    ) -> Sequence[OrganismCandidate]:
        return self._candidates(
            select(Organism).where(
                Organism.scientific_name == scientific_name, Organism.strain == strain
            )
        )

    def by_scientific_name_without_strain(
        self, scientific_name: str
    ) -> Sequence[OrganismCandidate]:
        return self._candidates(
            select(Organism).where(
                Organism.scientific_name == scientific_name, Organism.strain.is_(None)
            )
        )


class SqlAlchemyPublicationLookup:
    """Real ``PublicationLookup`` implementation over ``app.models.publication.Publication``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[PublicationCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            PublicationCandidate(
                id=row.id,
                pmid=row.pmid,
                pmcid=row.pmcid,
                doi=row.doi,
                title=row.title,
                journal=row.journal,
                year=row.year,
            )
            for row in rows
        )

    def by_pmid(self, pmid: str) -> Sequence[PublicationCandidate]:
        return self._candidates(select(Publication).where(Publication.pmid == pmid))

    def by_pmcid(self, pmcid: str) -> Sequence[PublicationCandidate]:
        return self._candidates(select(Publication).where(Publication.pmcid == pmcid))

    def by_doi(self, doi: str) -> Sequence[PublicationCandidate]:
        return self._candidates(select(Publication).where(Publication.doi == doi))


class SqlAlchemyGeneLookup:
    """Real ``GeneLookup`` implementation over ``app.models.gene.Gene``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[GeneCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            GeneCandidate(
                id=row.id,
                organism_id=row.organism_id,
                sgd_id=row.sgd_id,
                ncbi_gene_id=row.ncbi_gene_id,
                kegg_gene_id=row.kegg_gene_id,
                systematic_name=row.systematic_name,
                symbol=row.symbol,
                aliases=tuple(row.aliases_json or ()),
                description=row.description,
            )
            for row in rows
        )

    def by_sgd_id(self, sgd_id: str) -> Sequence[GeneCandidate]:
        return self._candidates(select(Gene).where(Gene.sgd_id == sgd_id))

    def by_ncbi_gene_id(self, ncbi_gene_id: str) -> Sequence[GeneCandidate]:
        return self._candidates(select(Gene).where(Gene.ncbi_gene_id == ncbi_gene_id))

    def by_kegg_gene_id(self, kegg_gene_id: str) -> Sequence[GeneCandidate]:
        return self._candidates(select(Gene).where(Gene.kegg_gene_id == kegg_gene_id))

    def by_systematic_name(
        self, organism_id: UUID, systematic_name: str
    ) -> Sequence[GeneCandidate]:
        return self._candidates(
            select(Gene).where(
                Gene.organism_id == organism_id, Gene.systematic_name == systematic_name
            )
        )

    def by_symbol(self, organism_id: UUID, symbol: str) -> Sequence[GeneCandidate]:
        return self._candidates(
            select(Gene).where(Gene.organism_id == organism_id, Gene.symbol == symbol)
        )

    def by_alias(self, organism_id: UUID, alias: str) -> Sequence[GeneCandidate]:
        # ``aliases_json`` is a JSONB array with no dedicated index -- a plain
        # Python-side filter over the organism-scoped rows, correct at pilot
        # scale, never an unscoped fetch (the WHERE clause below already
        # scopes to organism_id, matching every other organism-scoped method
        # here).
        rows = (
            self._session.execute(select(Gene).where(Gene.organism_id == organism_id))
            .scalars()
            .all()
        )
        matches = [row for row in rows if alias in (row.aliases_json or ())]
        return tuple(
            GeneCandidate(
                id=row.id,
                organism_id=row.organism_id,
                sgd_id=row.sgd_id,
                ncbi_gene_id=row.ncbi_gene_id,
                kegg_gene_id=row.kegg_gene_id,
                systematic_name=row.systematic_name,
                symbol=row.symbol,
                aliases=tuple(row.aliases_json or ()),
                description=row.description,
            )
            for row in matches
        )


class SqlAlchemyProteinLookup:
    """Real ``ProteinLookup`` implementation over ``app.models.protein.Protein``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[ProteinCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            ProteinCandidate(
                id=row.id,
                organism_id=row.organism_id,
                uniprot_id=row.uniprot_id,
                name=row.name,
                gene_id=row.gene_id,
                ec_number=row.ec_number,
            )
            for row in rows
        )

    def by_uniprot_id(self, uniprot_id: str) -> Sequence[ProteinCandidate]:
        return self._candidates(select(Protein).where(Protein.uniprot_id == uniprot_id))

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ProteinCandidate]:
        return self._candidates(
            select(Protein).where(Protein.organism_id == organism_id, Protein.name == name)
        )

    def by_gene_id(self, gene_id: UUID) -> Sequence[ProteinCandidate]:
        """Increment C.3 (gene-anchored protein identity resolution) only -- **not**
        part of the shared ``app.normalization.protein.ProteinLookup`` protocol, whose
        own module docstring documents *why* ``gene_id`` deliberately never
        participates in generic Protein identity ("Gene<->Protein relationship
        policy"). This method exists solely so ``app.pathway_curation.executor`` can
        ask "does a Protein already exist for this specific, already-resolved Gene"
        before ever searching UniProt again -- a pathway-curation-specific
        reuse/idempotency check, not a generic identity rule, exactly mirroring how
        ``KeggPathwayCurationConnector`` (``app.pathway_curation.strategies``)
        extends a shared protocol with capabilities entity resolution has no use for.
        """
        return self._candidates(select(Protein).where(Protein.gene_id == gene_id))


class SqlAlchemyCompoundLookup:
    """Real ``CompoundLookup`` implementation over ``app.models.compound.Compound``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[CompoundCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            CompoundCandidate(
                id=row.id,
                canonical_name=row.canonical_name,
                chebi_id=row.chebi_id,
                kegg_compound_id=row.kegg_compound_id,
                pubchem_cid=row.pubchem_cid,
                metacyc_id=row.metacyc_id,
                inchikey=row.inchikey,
            )
            for row in rows
        )

    def by_chebi_id(self, chebi_id: str) -> Sequence[CompoundCandidate]:
        return self._candidates(select(Compound).where(Compound.chebi_id == chebi_id))

    def by_kegg_compound_id(self, kegg_compound_id: str) -> Sequence[CompoundCandidate]:
        return self._candidates(
            select(Compound).where(Compound.kegg_compound_id == kegg_compound_id)
        )

    def by_pubchem_cid(self, pubchem_cid: str) -> Sequence[CompoundCandidate]:
        return self._candidates(select(Compound).where(Compound.pubchem_cid == pubchem_cid))

    def by_metacyc_id(self, metacyc_id: str) -> Sequence[CompoundCandidate]:
        return self._candidates(select(Compound).where(Compound.metacyc_id == metacyc_id))

    def by_inchikey(self, inchikey: str) -> Sequence[CompoundCandidate]:
        return self._candidates(select(Compound).where(Compound.inchikey == inchikey))

    def by_canonical_name(self, canonical_name: str) -> Sequence[CompoundCandidate]:
        return self._candidates(select(Compound).where(Compound.canonical_name == canonical_name))

    def by_synonym(self, synonym: str) -> Sequence[CompoundCandidate]:
        stmt = (
            select(Compound)
            .join(CompoundSynonym, CompoundSynonym.compound_id == Compound.id)
            .where(CompoundSynonym.synonym == synonym)
        )
        return self._candidates(stmt)


class SqlAlchemyCompartmentLookup:
    """Real ``CompartmentLookup`` implementation over ``app.models.compartment.Compartment``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[CompartmentCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            CompartmentCandidate(
                id=row.id,
                organism_id=row.organism_id,
                name=row.name,
                abbreviation=row.abbreviation,
                ontology_id=row.ontology_id,
            )
            for row in rows
        )

    def by_ontology_id(self, ontology_id: str) -> Sequence[CompartmentCandidate]:
        return self._candidates(select(Compartment).where(Compartment.ontology_id == ontology_id))

    def by_name(self, organism_id: UUID | None, name: str) -> Sequence[CompartmentCandidate]:
        return self._candidates(
            select(Compartment).where(
                Compartment.organism_id == organism_id, Compartment.name == name
            )
        )

    def by_abbreviation(
        self, organism_id: UUID | None, abbreviation: str
    ) -> Sequence[CompartmentCandidate]:
        return self._candidates(
            select(Compartment).where(
                Compartment.organism_id == organism_id,
                Compartment.abbreviation == abbreviation,
            )
        )


class SqlAlchemyReactionLookup:
    """Real ``ReactionLookup`` implementation over ``app.models.reaction.Reaction``.

    Deliberately has no structural/participant lookup method -- mirroring
    ``ReactionLookup``'s own documented absence of one
    (``docs/07_normalization_design.md`` Open Question M: no existing
    persistence-layer API provides structure-only duplicate discovery, and
    this module does not invent one).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[ReactionCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            ReactionCandidate(
                id=row.id,
                organism_id=row.organism_id,
                internal_id=row.internal_id,
                name=row.name,
                kegg_reaction_id=row.kegg_reaction_id,
                metacyc_reaction_id=row.metacyc_reaction_id,
                rhea_id=row.rhea_id,
                reversible=row.reversible,
                reaction_type=row.reaction_type,
                ec_number=row.ec_number,
            )
            for row in rows
        )

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]:
        return self._candidates(
            select(Reaction).where(Reaction.kegg_reaction_id == kegg_reaction_id)
        )

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]:
        return self._candidates(
            select(Reaction).where(Reaction.metacyc_reaction_id == metacyc_reaction_id)
        )

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]:
        return self._candidates(select(Reaction).where(Reaction.rhea_id == rhea_id))

    def by_name(self, organism_id: UUID, name: str) -> Sequence[ReactionCandidate]:
        return self._candidates(
            select(Reaction).where(Reaction.organism_id == organism_id, Reaction.name == name)
        )


class SqlAlchemyReactionEnzymeLookup:
    """Real ``ReactionEnzymeLookup`` implementation over ``app.models.reaction.ReactionEnzyme``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidates(self, stmt) -> tuple[ReactionEnzymeCandidate, ...]:
        rows = self._session.execute(stmt).scalars().all()
        return tuple(
            ReactionEnzymeCandidate(
                id=row.id,
                reaction_id=row.reaction_id,
                protein_id=row.protein_id,
                complex_id=row.complex_id,
                enzyme_state_id=row.enzyme_state_id,
                relationship=row.relationship,
            )
            for row in rows
        )

    def by_reaction_and_protein(
        self, reaction_id: UUID, protein_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        return self._candidates(
            select(ReactionEnzyme).where(
                ReactionEnzyme.reaction_id == reaction_id,
                ReactionEnzyme.protein_id == protein_id,
            )
        )

    def by_reaction_and_complex(
        self, reaction_id: UUID, complex_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        return self._candidates(
            select(ReactionEnzyme).where(
                ReactionEnzyme.reaction_id == reaction_id,
                ReactionEnzyme.complex_id == complex_id,
            )
        )

    def by_reaction_and_enzyme_state(
        self, reaction_id: UUID, enzyme_state_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        return self._candidates(
            select(ReactionEnzyme).where(
                ReactionEnzyme.reaction_id == reaction_id,
                ReactionEnzyme.enzyme_state_id == enzyme_state_id,
            )
        )


__all__ = [
    "SqlAlchemyCompartmentLookup",
    "SqlAlchemyCompoundLookup",
    "SqlAlchemyGeneLookup",
    "SqlAlchemyOrganismLookup",
    "SqlAlchemyProteinLookup",
    "SqlAlchemyPublicationLookup",
    "SqlAlchemyReactionEnzymeLookup",
    "SqlAlchemyReactionLookup",
]
