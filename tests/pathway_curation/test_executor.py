"""Tests for ``app.pathway_curation.executor.execute_pathway_curation`` (Steps 39-41).

Every scenario below drives the real bounded loop against the real test
database, through deterministic fake connectors (``tests/pathway_curation
/fakes.py``) satisfying the exact same structural shape the real
connectors satisfy -- never a mock of ``execute_pathway_curation`` itself,
and never network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.exceptions import ConnectorError
from app.connectors.uniprot import UniProtProteinRecord
from app.models.enums import ReactionParticipantRole, SourceType
from app.models.protein import Protein
from app.models.source_cross_reference import SourceCrossReference
from app.pathway_curation.errors import InvalidCurationRequestError
from app.pathway_curation.executor import PathwayConnectorBundle, execute_pathway_curation
from app.pathway_curation.types import (
    CompletionPolicy,
    CompletionStatus,
    CurationMode,
    FrontierReason,
    PathwayCurationRequest,
)
from tests.pathway_curation.fakes import (
    FakeKeggConnector,
    FakeKgmlEntrySpec,
    FakeOedConnector,
    FakePubMedConnector,
    FakeSabiorkConnector,
    FakeSgdConnector,
    FakeUniProtConnector,
    make_kegg_compound,
    make_kegg_reaction,
    make_oed_row,
    make_pubmed_article,
    make_sabio_record,
    make_sgd_locus,
    make_uniprot_entry,
)

YEAST_TAXONOMY_ID = 4932


def _request(**overrides) -> PathwayCurationRequest:
    merged = {
        "request_id": "req-1",
        "organism_text": "Saccharomyces cerevisiae",
        "biological_process": "fatty acid biosynthesis",
        "organism_ncbi_taxonomy_id": YEAST_TAXONOMY_ID,
        "mode": CurationMode.STANDARD,
    } | overrides
    return PathwayCurationRequest(**merged)


def _kegg_with_reactions(
    reaction_ids: tuple[str, ...], *, missing: tuple[str, ...] = ()
) -> FakeKeggConnector:
    reactions = {
        reaction_id: make_kegg_reaction(reaction_id, name=f"fake reaction {reaction_id}")
        for reaction_id in reaction_ids
    }
    return FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": reaction_ids + missing},
        reactions=reactions,
    )


def _kegg_with_reactions_and_ec(
    reaction_ids: tuple[str, ...], *, ec_number: str
) -> FakeKeggConnector:
    reactions = {
        reaction_id: make_kegg_reaction(
            reaction_id, name=f"fake reaction {reaction_id}", enzymes=(ec_number,)
        )
        for reaction_id in reaction_ids
    }
    return FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": reaction_ids},
        reactions=reactions,
    )


@dataclass
class _RaisingKeggConnector(FakeKeggConnector):
    """A ``FakeKeggConnector`` whose ``search`` always fails (Step 40's connector-failure case)."""

    def search(self, query: str, *, database: str):
        raise ConnectorError("KEGG is unreachable in this test")


@dataclass
class _RaisingSgdConnector(FakeSgdConnector):
    """A ``FakeSgdConnector`` whose ``search`` always fails (Step 40's partial-failure case)."""

    def search(self, query: str):
        raise ConnectorError("SGD is unreachable in this test")


@dataclass
class _RaisingSabiorkConnector(FakeSabiorkConnector):
    """A ``FakeSabiorkConnector`` whose ``search`` always fails (kinetics partial-failure case)."""

    def search(self, query: str, *, organism: str | None = None):
        raise ConnectorError("SABIO-RK is unreachable in this test")


# --- One-reaction / linear / branched pathway discovery ----------------------------------------


def test_one_reaction_pathway_resolves_to_a_single_reaction(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert result.organism_id is not None
    assert result.discovered_reaction_ids
    assert len(result.discovered_reaction_ids) == 1
    assert result.completion_status in (
        CompletionStatus.COMPLETE,
        CompletionStatus.COMPLETE_WITH_GAPS,
    )


def test_small_linear_pathway_resolves_every_reaction(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742", "R00743"))
    result = execute_pathway_curation(
        _request(request_id="req-linear", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 2
    assert not any(
        item.reason is FrontierReason.UNRESOLVED_REACTION_IDENTITY
        for item in result.unresolved_frontier
    )


def test_branched_pathway_resolves_every_reaction_regardless_of_shared_intermediates(
    db_session: Session,
) -> None:
    """A KEGG pathway's ``REACTION`` field lists every member reaction flatly, with no
    topology of its own -- a branch point shared by several reactions is therefore
    exercised by the same discovery/expansion code path as a linear pathway, just with
    more members; this test asserts that all of them are still resolved, none dropped."""
    kegg = _kegg_with_reactions(("R00742", "R00743", "R00744"))
    result = execute_pathway_curation(
        _request(request_id="req-branched", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 3


# --- Unresolved participants --------------------------------------------------------------------


def test_missing_kegg_reaction_record_produces_unresolved_frontier_item(
    db_session: Session,
) -> None:
    """One pathway member id KEGG's own pathway record names but whose reaction record
    cannot be fetched (a stale/renamed id) becomes an ``UNRESOLVED_REACTION_IDENTITY``
    frontier item -- never silently dropped, never invented."""
    kegg = _kegg_with_reactions(("R00742",), missing=("R09999",))
    result = execute_pathway_curation(
        _request(request_id="req-missing-reaction", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    missing_items = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.UNRESOLVED_REACTION_IDENTITY
        and item.entity_text == "R09999"
    ]
    assert len(missing_items) == 1


def test_unmatched_seed_gene_produces_unresolved_catalyst_frontier_item(
    db_session: Session,
) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(loci={})  # no locus for any query -- deliberately empty
    result = execute_pathway_curation(
        _request(
            request_id="req-unmatched-gene",
            seed_entity_texts=("UNKNOWNGENE",),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd),
    )

    catalyst_items = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.UNRESOLVED_CATALYST and item.entity_text == "UNKNOWNGENE"
    ]
    assert len(catalyst_items) == 1


# --- Literature/kinetics enrichment ---------------------------------------------------------------


def test_literature_enrichment_discovers_and_persists_a_publication(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    query = "Saccharomyces cerevisiae fatty acid biosynthesis"
    pubmed = FakePubMedConnector(
        articles={"12345678": make_pubmed_article(pmid="12345678", title=f"{query} enzymology")}
    )
    result = execute_pathway_curation(
        _request(request_id="req-literature", include_publications=True),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, pubmed=pubmed),
    )

    assert result.discovered_publication_ids
    assert not any(
        item.reason is FrontierReason.MISSING_PUBLICATION for item in result.unresolved_frontier
    )


def test_kinetics_enrichment_persists_a_measurement_linked_to_the_resolved_protein(
    db_session: Session,
) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="6.4.1.2", parameter_type="Km", value="0.5", unit="mM"
            )
        }
    )
    result = execute_pathway_curation(
        _request(
            request_id="req-kinetics",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    measurement = result.agent1_knowledge_package.kinetic_measurements[0]
    assert measurement.protein_id is not None
    assert measurement.organism_id == result.organism_id


def test_include_regulation_is_accepted_but_not_yet_executed(db_session: Session) -> None:
    """Documents a disclosed v1 limitation: ``include_regulation`` is a valid request field
    and never rejected, but this increment's executor has no ``_discover_regulation`` step
    yet (see ``docs/26_autonomous_pathway_curation_planner.md`` known limitations) -- setting
    it neither errors nor changes the discovered entities/reactions."""
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(
            request_id="req-regulation-flag", include_publications=False, include_regulation=True
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    assert not any(
        item.reason is FrontierReason.MISSING_REGULATION for item in result.unresolved_frontier
    )


# --- Connector failure / partial source failure -------------------------------------------------


def test_kegg_connector_failure_produces_a_source_failure_frontier_item(
    db_session: Session,
) -> None:
    raising_kegg = _RaisingKeggConnector()
    result = execute_pathway_curation(
        _request(request_id="req-kegg-failure", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=raising_kegg),
    )

    assert result.organism_id is not None
    assert not result.discovered_reaction_ids
    assert any(item.reason is FrontierReason.SOURCE_FAILURE for item in result.unresolved_frontier)
    assert result.warnings


def test_partial_source_failure_does_not_block_unrelated_sources(db_session: Session) -> None:
    """KEGG succeeds (structural pathway/reaction discovery) even though SGD (gene
    resolution) fails outright -- one source's failure never blocks another's progress."""
    kegg = _kegg_with_reactions(("R00742",))
    raising_sgd = _RaisingSgdConnector()
    result = execute_pathway_curation(
        _request(
            request_id="req-partial-failure",
            seed_entity_texts=("ACC1",),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=raising_sgd),
    )

    assert len(result.discovered_reaction_ids) == 1
    assert any(item.reason is FrontierReason.SOURCE_FAILURE for item in result.unresolved_frontier)
    assert result.warnings


# --- No connectors / organism resolution failure -------------------------------------------------


def test_no_connectors_at_all_still_returns_a_bounded_result(db_session: Session) -> None:
    result = execute_pathway_curation(
        _request(request_id="req-no-connectors", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(),
    )

    assert result.organism_id is not None
    assert not result.discovered_reaction_ids
    assert any(
        item.reason is FrontierReason.NO_CONNECTOR_AVAILABLE for item in result.unresolved_frontier
    )
    assert result.completion_status is not CompletionStatus.FAILED


def test_organism_resolution_failure_returns_a_blocked_result_without_raising(
    db_session: Session,
) -> None:
    """A bare organism name with no ``organism_ncbi_taxonomy_id`` and no existing matching
    row can never resolve (``app.normalization.organism``'s own documented policy) -- this
    must surface as ``BLOCKED``, never an unhandled exception."""
    result = execute_pathway_curation(
        _request(
            request_id="req-unresolvable-organism",
            organism_text="Some Never-Before-Seen Organism",
            organism_ncbi_taxonomy_id=None,
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=_kegg_with_reactions(("R00742",))),
    )

    assert result.organism_id is None
    assert result.completion_status is CompletionStatus.BLOCKED
    assert not result.discovered_reaction_ids


def test_invalid_request_is_rejected_before_any_connector_call(db_session: Session) -> None:
    bad_request = _request(
        request_id="req-invalid",
        completion_policy=CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS,
        include_kinetics=False,
    )
    with pytest.raises(InvalidCurationRequestError):
        execute_pathway_curation(
            bad_request, session=db_session, connectors=PathwayConnectorBundle()
        )


# --- Full fake end-to-end pilot -------------------------------------------------------------------


def test_full_fake_pilot_run_produces_a_fatty_acid_biosynthesis_like_result(
    db_session: Session,
) -> None:
    """One realistic, fully-wired run: two reactions sharing a compound, resolved
    participants on every reaction, one catalyst *autonomously discovered* from
    reaction R00742's own EC number (Increment C.1, F2 -- ``seed_entity_texts`` is
    deliberately empty), one supporting publication, and one linked kinetic
    measurement -- exercising every enrichment path together, the way the real yeast
    pilot eventually will, and ending in an Agent-2-ready export."""
    compounds = {
        cid: make_kegg_compound(cid, name=f"fake compound {cid}")
        for cid in ("C00024", "C00011", "C00048", "C00005", "C00010", "C00006")
    }
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742", "R00743")},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742",
                name="fake reaction R00742",
                enzymes=("6.4.1.2",),
                equation="C00024 + C00011 <=> C00048",
            ),
            "R00743": make_kegg_reaction(
                "R00743",
                name="fake reaction R00743",
                equation="C00048 + C00005 <=> C00010 + C00006",
            ),
        },
        compounds=compounds,
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ec:6.4.1.2": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    query = "Saccharomyces cerevisiae fatty acid biosynthesis"
    pubmed = FakePubMedConnector(
        articles={"87654321": make_pubmed_article(pmid="87654321", title=f"{query} review")}
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="6.4.1.2", parameter_type="Km", value="0.5", unit="mM"
            )
        }
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-full-pilot",
            seed_entity_texts=(),
            include_publications=True,
            include_kinetics=True,
            mode=CurationMode.PILOT,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, uniprot=uniprot, pubmed=pubmed, sabiork=sabiork
        ),
    )

    assert result.organism_id is not None
    assert len(result.discovered_reaction_ids) == 2
    assert result.discovered_entity_ids  # organism + autonomously-discovered protein + compounds
    assert len(result.agent1_knowledge_package.proteins) == 1  # discovered, never seeded
    assert result.discovered_publication_ids
    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    assert result.knowledge_gap_ids
    assert result.completion_status in (
        CompletionStatus.COMPLETE,
        CompletionStatus.COMPLETE_WITH_GAPS,
    )
    assert result.curated_knowledge_view.organism_id == result.organism_id
    assert result.queries_executed
    assert len(result.final_plan.steps) >= 6

    # Increment C pre-commit revision: structural expansion actually happened, and the
    # export is Agent-2-ready with no manual repair.
    assert len(result.agent1_knowledge_package.compounds) == 6
    assert len(result.agent1_knowledge_package.reaction_participants) == 7
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert result.agent2_readiness.is_ready is True
    assert result.agent2_readiness.blocking_issues == ()
    assert result.agent2_readiness.reaction_count == 2
    assert result.agent2_readiness.participant_count == 7


# --- Reaction-participant resolution (Increment C pre-commit revision) --------------------------


def test_reaction_participants_are_resolved_with_correct_roles_and_stoichiometry(
    db_session: Session,
) -> None:
    compounds = {
        cid: make_kegg_compound(cid, name=f"fake {cid}")
        for cid in ("C00005", "C00006", "C00003", "C00080")
    }
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742",
                name="fake reaction R00742",
                equation="2 C00005 + C00006 <=> C00003 + 2 C00080",
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(request_id="req-participants", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reaction_id = result.discovered_reaction_ids[0]
    participants = [
        p
        for p in result.agent1_knowledge_package.reaction_participants
        if p.reaction_id == reaction_id
    ]
    assert len(participants) == 4

    by_role = {p.role: [] for p in participants}
    for p in participants:
        by_role[p.role].append(p)
    assert len(by_role[ReactionParticipantRole.REACTANT]) == 2
    assert len(by_role[ReactionParticipantRole.PRODUCT]) == 2

    compound_id_by_kegg = {
        record.kegg_compound_id: record.id for record in result.agent1_knowledge_package.compounds
    }
    stoichiometry_by_compound_id = {p.compound_id: p.stoichiometry for p in participants}
    assert stoichiometry_by_compound_id[compound_id_by_kegg["C00005"]] == Decimal(2)
    assert stoichiometry_by_compound_id[compound_id_by_kegg["C00006"]] == Decimal(1)
    assert stoichiometry_by_compound_id[compound_id_by_kegg["C00003"]] == Decimal(1)
    assert stoichiometry_by_compound_id[compound_id_by_kegg["C00080"]] == Decimal(2)

    assert result.agent2_readiness.is_ready is True


def test_unparseable_equation_produces_a_missing_participants_frontier_item(
    db_session: Session,
) -> None:
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="this is not a kegg equation"
            )
        },
    )
    result = execute_pathway_curation(
        _request(request_id="req-unparseable-equation", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.REACTION_MISSING_PARTICIPANTS in reasons
    assert FrontierReason.UNRESOLVED_REACTION_PARTICIPANT in reasons
    assert result.agent2_readiness.is_ready is False


def test_unknown_compound_token_is_flagged_while_other_participants_still_resolve(
    db_session: Session,
) -> None:
    """A glycan-shaped token (``G00123``) the equation parser itself never guesses at is
    reported as its own frontier item, while the reaction's other, valid participants are
    still resolved and attached -- partial structural knowledge is never withheld."""
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 + G00123 <=> C00332"
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(request_id="req-glycan-token", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reaction_id = result.discovered_reaction_ids[0]
    participants = [
        p
        for p in result.agent1_knowledge_package.reaction_participants
        if p.reaction_id == reaction_id
    ]
    assert len(participants) == 2
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.UNRESOLVED_REACTION_PARTICIPANT
        and item.entity_text == "G00123"
    ]
    assert len(unresolved) == 1


def test_compound_fetch_failure_is_flagged_while_other_participants_still_resolve(
    db_session: Session,
) -> None:
    """A well-formed KEGG compound id (``C99999``) that this fake connector simply does not
    have becomes its own frontier item, distinct from an unparseable token."""
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 + C99999 <=> C00332"
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(request_id="req-missing-compound", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reaction_id = result.discovered_reaction_ids[0]
    participants = [
        p
        for p in result.agent1_knowledge_package.reaction_participants
        if p.reaction_id == reaction_id
    ]
    assert len(participants) == 2
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.UNRESOLVED_REACTION_PARTICIPANT
        and item.entity_text == "C99999"
    ]
    assert len(unresolved) == 1


# --- Compartment evidence (Increment C pre-commit revision) -------------------------------------


def test_default_compartment_text_resolves_to_the_seeded_reference_compartment(
    db_session: Session,
) -> None:
    """``cytosol`` is one of the 13 standard reference compartments seeded by migration
    ``0002_reference_data`` -- this proves reuse, never a duplicate creation."""
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 <=> C00332"
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(
            request_id="req-explicit-compartment",
            include_publications=False,
            default_compartment_text="cytosol",
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    participants = result.agent1_knowledge_package.reaction_participants
    assert participants
    assert all(p.compartment_id is not None for p in participants)
    compartment_ids = {p.compartment_id for p in participants}
    assert len(compartment_ids) == 1
    (compartment_id,) = compartment_ids
    matching = [c for c in result.agent1_knowledge_package.compartments if c.id == compartment_id]
    assert len(matching) == 1
    assert matching[0].name == "cytosol"
    assert matching[0].organism_id is None  # a reference compartment, never a duplicate


def test_unmatched_default_compartment_text_leaves_participants_uncompartmentalized(
    db_session: Session,
) -> None:
    """A ``default_compartment_text`` that matches no existing reference compartment is
    never silently defaulted to cytosol -- participants simply keep ``compartment_id=None``
    and the caller is warned."""
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 <=> C00332"
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(
            request_id="req-unmatched-compartment",
            include_publications=False,
            default_compartment_text="a compartment that does not exist",
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    participants = result.agent1_knowledge_package.reaction_participants
    assert participants
    assert all(p.compartment_id is None for p in participants)
    assert result.warnings
    assert result.agent2_readiness.is_ready is True  # nonblocking -- schema permits null


def test_no_compartment_evidence_at_all_leaves_participants_uncompartmentalized(
    db_session: Session,
) -> None:
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 <=> C00332"
            )
        },
        compounds=compounds,
    )
    result = execute_pathway_curation(
        _request(request_id="req-no-compartment-evidence", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    participants = result.agent1_knowledge_package.reaction_participants
    assert participants
    assert all(p.compartment_id is None for p in participants)
    assert not result.warnings


# --- Catalyst conservatism (Increment C pre-commit revision) ------------------------------------


def test_catalyst_association_is_conservative_about_shared_ec_numbers(db_session: Session) -> None:
    """Two proteins share EC number ``6.4.1.2``; only the request-seeded one (``ACC1``) is
    ever considered for a ``ReactionEnzyme`` association -- EC equality alone is never
    sufficient by itself (see ``strategies.associate_catalyst``'s own docstring)."""
    compounds = {cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00332")}
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742",
                name="fake reaction R00742",
                enzymes=("6.4.1.2",),
                equation="C00024 <=> C00332",
            )
        },
        compounds=compounds,
    )
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-catalyst-conservatism",
            seed_entity_texts=("ACC1",),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    seeded_protein_id = result.agent1_knowledge_package.proteins[0].id

    # A second protein, sharing the identical EC number, is added directly (never through
    # the request's own seed_entity_texts) -- the executor must never associate it merely
    # because its EC number matches.
    sibling_protein = Protein(
        organism_id=result.organism_id,
        name="unseeded sibling",
        ec_number="6.4.1.2",
    )
    db_session.add(sibling_protein)
    db_session.flush()

    second_result = execute_pathway_curation(
        _request(
            request_id="req-catalyst-conservatism",
            seed_entity_texts=("ACC1",),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    associations = second_result.agent1_knowledge_package.reaction_enzyme_associations
    assert len(associations) == 1
    assert associations[0].protein_id == seeded_protein_id
    assert sibling_protein.id not in {a.protein_id for a in associations}


# --- Idempotency (Increment C pre-commit revision) -----------------------------------------------


def test_repeated_execution_does_not_duplicate_anything(db_session: Session) -> None:
    compounds = {
        cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00011", "C00048")
    }
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742",
                name="fake reaction R00742",
                enzymes=("6.4.1.2",),
                equation="C00024 + C00011 <=> C00048",
            )
        },
        compounds=compounds,
    )
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="6.4.1.2", parameter_type="Km", value="0.5", unit="mM"
            )
        }
    )

    def _run() -> tuple[int, int, int, int, int, int, int]:
        result = execute_pathway_curation(
            _request(
                request_id="req-idempotency",
                seed_entity_texts=("ACC1",),
                include_publications=False,
                include_kinetics=True,
            ),
            session=db_session,
            connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
        )
        package = result.agent1_knowledge_package
        return (
            len(package.organisms),
            len(package.compounds),
            len(package.reactions),
            len(package.reaction_participants),
            len(package.genes) + len(package.proteins),
            len(package.reaction_enzyme_associations),
            len(package.kinetic_measurements),
        )

    first = _run()
    second = _run()

    assert first == second
    assert first[2] == 1  # exactly one reaction, not duplicated
    assert first[5] == 1  # exactly one reaction/enzyme association
    assert first[6] == 1  # exactly one kinetic measurement


# --- Open Enzyme Database wiring (Increment C pre-commit revision) ------------------------------


def test_oed_wiring_persists_a_kinetic_measurement_when_sabiork_is_absent(
    db_session: Session,
) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    oed = FakeOedConnector(rows={"row1": make_oed_row(ec_number="6.4.1.2", kcat="12.5")})

    result = execute_pathway_curation(
        _request(
            request_id="req-oed",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, oed=oed),
    )

    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    measurement = result.agent1_knowledge_package.kinetic_measurements[0]
    assert measurement.source is SourceType.OED


def test_sabiork_failure_does_not_block_oed_kinetics(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    raising_sabiork = _RaisingSabiorkConnector()
    oed = FakeOedConnector(rows={"row1": make_oed_row(ec_number="6.4.1.2", kcat="12.5")})

    result = execute_pathway_curation(
        _request(
            request_id="req-partial-kinetics-failure",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=raising_sabiork, oed=oed
        ),
    )

    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    assert result.agent1_knowledge_package.kinetic_measurements[0].source is SourceType.OED
    assert result.warnings


# --- Regulation/kinetics disclosure signals (Increment C pre-commit revision) -------------------


def test_include_regulation_produces_a_requested_not_supported_frontier_item(
    db_session: Session,
) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(
            request_id="req-regulation-disclosure",
            include_publications=False,
            include_regulation=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.REGULATION_REQUESTED_NOT_SUPPORTED in reasons


def test_include_kinetics_with_no_seeded_protein_produces_not_attempted_frontier_item(
    db_session: Session,
) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(
            request_id="req-kinetics-not-attempted",
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED in reasons
    assert FrontierReason.MISSING_KINETICS not in reasons


# ==================================================================================================
# Increment C.1 -- Live Pathway Discovery Repair
# ==================================================================================================

# --- F1: KEGG pathway->reaction link operation, never a REACTION field --------------------------


def test_f1_pathway_record_has_no_reaction_field_and_membership_still_resolves(
    db_session: Session,
) -> None:
    """The central F1 regression test: reproduces the exact real-API shape that broke
    Pilot 1 Run 1 -- a pathway `/get/` record with no REACTION field at all -- and
    proves reaction membership, participants, and compounds are still fully resolved
    via the dedicated link operation."""
    compounds = {
        cid: make_kegg_compound(cid, name=f"fake {cid}") for cid in ("C00024", "C00011", "C00048")
    }
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ("R00742",)},
        reactions={
            "R00742": make_kegg_reaction(
                "R00742", name="fake reaction R00742", equation="C00024 + C00011 <=> C00048"
            )
        },
        compounds=compounds,
    )

    result = execute_pathway_curation(
        _request(request_id="req-f1-regression", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    # Reproduce the exact failure condition: the pathway's own fetched record never
    # carries a REACTION field.
    pathway_record = kegg.fetch("map00061")
    assert pathway_record is not None
    assert "REACTION" not in pathway_record.fields

    # ... and membership/participants/compounds were still fully resolved regardless.
    assert len(result.discovered_reaction_ids) == 1
    reaction_id = result.discovered_reaction_ids[0]
    participants = [
        p
        for p in result.agent1_knowledge_package.reaction_participants
        if p.reaction_id == reaction_id
    ]
    assert len(participants) == 3
    assert len(result.agent1_knowledge_package.compounds) == 3
    assert result.agent2_readiness.is_ready is True
    assert ("link", ("reaction", "map00061")) in kegg.calls


def test_f1_empty_reaction_membership_produces_a_blocking_frontier_item(
    db_session: Session,
) -> None:
    """Pilot 1 Run 1's exact silent-failure condition: a pathway resolves, but its
    reaction membership is empty. This must now be impossible to miss."""
    kegg = FakeKeggConnector(
        pathways={"map00061": "fatty acid biosynthesis"},
        pathway_reactions={"map00061": ()},  # a real pathway, zero linked reactions
    )

    result = execute_pathway_curation(
        _request(request_id="req-f1-empty-membership", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert result.discovered_reaction_ids == ()
    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY in reasons
    assert result.completion_status is not CompletionStatus.COMPLETE
    assert result.agent2_readiness.is_ready is False
    assert result.organism_id is not None  # organism resolution itself still succeeded


def test_f1_link_connector_failure_produces_source_failure_not_silence(
    db_session: Session,
) -> None:
    @dataclass
    class _RaisingLinkKeggConnector(FakeKeggConnector):
        def link(self, target_db: str, dbentries: str):
            raise ConnectorError("KEGG link endpoint is unreachable in this test")

    kegg = _RaisingLinkKeggConnector(pathways={"map00061": "fatty acid biosynthesis"})

    result = execute_pathway_curation(
        _request(request_id="req-f1-link-failure", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.SOURCE_FAILURE in reasons
    assert FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY not in reasons
    assert result.warnings


# --- F4: structured pathway id precedence --------------------------------------------------------


def test_f4_structured_pathway_id_is_used_directly_without_text_search(
    db_session: Session,
) -> None:
    kegg = FakeKeggConnector(
        pathways={"sce00061": "fatty acid biosynthesis - Saccharomyces cerevisiae"},
        pathway_reactions={"sce00061": ("R00742",)},
        reactions={"R00742": make_kegg_reaction("R00742", name="fake reaction R00742")},
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f4-structured-id",
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    # No free-text pathway search was ever issued.
    assert not any(call[0] == "search" and call[1][1] == "pathway" for call in kegg.calls)
    assert ("fetch", ("sce00061",)) in kegg.calls
    assert ("link", ("reaction", "sce00061")) in kegg.calls


def test_f4_structured_id_takes_precedence_over_biological_process_text(
    db_session: Session,
) -> None:
    """Both a structured id and free text are supplied; the structured id alone
    determines which pathway is used -- the text is never searched."""
    kegg = FakeKeggConnector(
        pathways={
            "sce00061": "fatty acid biosynthesis - Saccharomyces cerevisiae",
            "map01040": "Biosynthesis of unsaturated fatty acids",
        },
        pathway_reactions={"sce00061": ("R00742",), "map01040": ("R09999",)},
        reactions={"R00742": make_kegg_reaction("R00742", name="fake reaction R00742")},
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f4-precedence",
            biological_process="unsaturated fatty acids",  # would match map01040 if searched
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert result.discovered_reaction_ids == tuple(result.discovered_reaction_ids)
    assert len(result.discovered_reaction_ids) == 1
    assert not any(call[0] == "search" for call in kegg.calls)


def test_f4_structured_pathway_id_not_found_is_disclosed(db_session: Session) -> None:
    kegg = FakeKeggConnector(pathways={}, pathway_reactions={})  # sce00061 does not exist

    result = execute_pathway_curation(
        _request(
            request_id="req-f4-not-found",
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert result.discovered_reaction_ids == ()
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.UNRESOLVED_REACTION_IDENTITY
        and item.entity_text == "sce00061"
    ]
    assert len(unresolved) == 1


def test_f4_no_structured_id_falls_back_to_text_discovery(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(request_id="req-f4-no-id-fallback", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    assert any(call[0] == "search" and call[1][1] == "pathway" for call in kegg.calls)


# --- F2: autonomous catalyst discovery from reaction evidence ------------------------------------


def test_f2_catalyst_discovered_from_reaction_ec_number_with_no_seeds(
    db_session: Session,
) -> None:
    """The central F2 regression test: seed_entity_texts is empty, yet a catalyst is
    still discovered and associated from the resolved reaction's own EC number."""
    kegg = _kegg_with_reactions_and_ec(("R00742",), ec_number="6.4.1.2")
    uniprot = FakeUniProtConnector(
        entries={
            "ec:6.4.1.2": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f2-autonomous-catalyst",
            seed_entity_texts=(),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert not any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )


def test_f2_shared_ec_number_ambiguity_never_fabricates_an_association(
    db_session: Session,
) -> None:
    """The required F2 negative case: two distinct real-shaped candidates (an isozyme
    pair, mirroring yeast's own cytosolic ACC1 vs. mitochondrial HFA1, both
    confirmed live to share EC 6.4.1.2) share the queried EC number. Neither is
    seeded. No ReactionEnzyme may ever be fabricated from EC equality alone."""
    kegg = _kegg_with_reactions_and_ec(("R00742",), ec_number="6.4.1.2")
    uniprot = FakeUniProtConnector(
        entries={
            "ec:6.4.1.2": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
        }
    )
    # A second, distinct candidate that also matches the same discovery query --
    # FakeUniProtConnector.search treats every entry whose key is a *substring* of
    # the query as a hit, so a second key that is itself a substring of the first
    # (e.g. the bare EC number, without the "ec:" prefix) also matches.
    uniprot.entries["6.4.1.2"] = make_uniprot_entry(
        accession="P32874",
        recommended_name="Acetyl-CoA carboxylase, mitochondrial",
        gene_names=("HFA1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
        ec_numbers=("6.4.1.2",),
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f2-ambiguous-ec",
            seed_entity_texts=(),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
    ]
    assert len(unresolved) == 1


def test_f2_no_ec_number_on_reaction_never_attempts_discovery(db_session: Session) -> None:
    kegg = _kegg_with_reactions(("R00742",))  # no `enzymes=` -> no ec_number
    uniprot = FakeUniProtConnector(entries={})

    result = execute_pathway_curation(
        _request(request_id="req-f2-no-ec", seed_entity_texts=(), include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert uniprot.calls == []
    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()


def test_f2_seed_entity_texts_still_work_alongside_autonomous_discovery(
    db_session: Session,
) -> None:
    """Step 20: seed_entity_texts remains a supported, optional hint -- not the only
    way in. A seeded gene and autonomous EC-based discovery can both contribute."""
    kegg = _kegg_with_reactions_and_ec(("R00742",), ec_number="6.4.1.2")
    sgd = FakeSgdConnector(
        loci={
            "ACC1": make_sgd_locus(
                sgd_id="S000000002", systematic_name="YNR016C", standard_name="ACC1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f2-seed-plus-autonomous",
            seed_entity_texts=("ACC1",),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    # The seeded protein and the autonomously-discovered candidate are the same real
    # protein here (Q00955) -- persistence reuses it (MATCHED), never duplicating.
    assert len(result.agent1_knowledge_package.proteins) == 1
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1


# ==================================================================================================
# Increment C.1 -- Organism-specific KEGG pathway resolution (final completion)
# ==================================================================================================
#
# Live investigation (KEGG's REST API, read-only, no persistence) established that an
# organism-specific pathway id's own KGML pathway-diagram document
# (``GET /get/{pathway_id}/kgml``) declares that organism's own curated reaction
# membership directly and authoritatively -- confirmed live across three independent
# organisms (sce00061 -> 41 reactions, hsa00061 -> 45, eco00061 -> 50), every one a
# strict subset of the corresponding generic reference pathway's own reactions with
# zero exceptions. ``strategies.discover_reactions_in_pathway`` now tries this first,
# falling back to the pre-existing ``link("reaction", pathway_id)`` operation only when
# no KGML document exists for that id (a generic "map"-prefixed reference pathway).


def test_f10_organism_specific_pathway_uses_its_own_kgml_reaction_subset(
    db_session: Session,
) -> None:
    """The central regression test for this completion: an organism-specific pathway's
    KGML diagram declares a genuine *subset* of what a naive "treat the whole generic
    reference pathway as this organism's own" approach would import -- only the KGML
    subset is ever discovered/expanded, the rest never silently pulled in."""
    reference_reaction_ids = ("R00742", "R01626", "R04355", "R09999")
    organism_supported_ids = ("R00742", "R04355")  # KGML declares only these two
    kegg = FakeKeggConnector(
        pathways={"sce00061": "fatty acid biosynthesis - Saccharomyces cerevisiae"},
        # If the algorithm ever fell back to the full reference set, this is what it
        # would wrongly return -- present so a regression back to "import everything
        # link() reports" would be caught immediately.
        pathway_reactions={"sce00061": reference_reaction_ids},
        kgml_reactions={"sce00061": organism_supported_ids},
        reactions={
            reaction_id: make_kegg_reaction(reaction_id, name=f"fake reaction {reaction_id}")
            for reaction_id in reference_reaction_ids
        },
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f10-kgml-subset",
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 2
    resolved_kegg_ids = {call[1][0] for call in kegg.calls if call[0] == "fetch"} & set(
        reference_reaction_ids
    )
    assert resolved_kegg_ids == set(organism_supported_ids)
    # R01626/R09999 -- reference-only reactions -- were never fetched/expanded at all.
    assert "R01626" not in resolved_kegg_ids
    assert "R09999" not in resolved_kegg_ids
    # link() was never even called: the KGML document alone answered the question.
    assert ("link", ("reaction", "sce00061")) not in kegg.calls


def test_f10_generic_reference_pathway_keeps_original_link_based_behavior(
    db_session: Session,
) -> None:
    """A generic "map"-prefixed pathway has no KGML diagram of its own (confirmed live:
    404) -- this must fall back to the pre-existing ``link()`` behavior, completely
    unchanged, never blocked or altered by this completion."""
    kegg = _kegg_with_reactions(("R00742", "R01626"))  # no kgml_reactions entry at all

    result = execute_pathway_curation(
        _request(request_id="req-f10-generic-unaffected", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 2
    assert ("get_kgml", ("map00061",)) in kegg.calls
    assert ("link", ("reaction", "map00061")) in kegg.calls


def test_f10_kgml_present_but_empty_falls_back_to_link(db_session: Session) -> None:
    """A KGML document that exists but declares zero reactions (e.g. a compound-only
    diagram) is not treated as "the answer is zero" -- the pre-existing ``link()``
    mechanism still gets a chance, exactly like "no KGML document at all"."""
    kegg = FakeKeggConnector(
        pathways={"sce00061": "fatty acid biosynthesis - Saccharomyces cerevisiae"},
        pathway_reactions={"sce00061": ("R00742",)},
        kgml_reactions={"sce00061": ()},  # KGML exists, declares nothing
        reactions={"R00742": make_kegg_reaction("R00742", name="fake reaction R00742")},
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f10-empty-kgml-fallback",
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 1
    assert ("link", ("reaction", "sce00061")) in kegg.calls


def test_f10_neither_kgml_nor_link_yields_reactions_still_blocks_explicitly(
    db_session: Session,
) -> None:
    """F9 must not be weakened by this completion: a pathway with genuinely zero
    reaction evidence from either mechanism still produces the same explicit,
    blocking frontier item as before -- never silently treated as success."""
    kegg = FakeKeggConnector(
        pathways={"sce00061": "fatty acid biosynthesis - Saccharomyces cerevisiae"},
        pathway_reactions={"sce00061": ()},
        kgml_reactions={"sce00061": ()},
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f10-both-empty",
            source_pathway_id="sce00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert result.discovered_reaction_ids == ()
    reasons = {item.reason for item in result.unresolved_frontier}
    assert FrontierReason.PATHWAY_REACTION_MEMBERSHIP_EMPTY in reasons
    assert result.agent2_readiness.is_ready is False


def test_f10_second_organism_prefix_proves_no_organism_hardcoding(db_session: Session) -> None:
    """A synthetic, non-yeast organism-style pathway id (``xyz00061``) exercises the
    exact same code path with the exact same result shape -- proving the algorithm
    recognizes "does this pathway id have its own KGML diagram," never a specific
    organism code. No live network is used; the fake's KGML document is fabricated
    for this made-up prefix."""
    kegg = FakeKeggConnector(
        pathways={"xyz00061": "fatty acid biosynthesis - Fakeorganismus xylosus"},
        pathway_reactions={"xyz00061": ("R00742", "R01626", "R09999")},
        kgml_reactions={"xyz00061": ("R00742", "R01626")},
        reactions={
            reaction_id: make_kegg_reaction(reaction_id, name=f"fake reaction {reaction_id}")
            for reaction_id in ("R00742", "R01626", "R09999")
        },
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-f10-non-yeast-organism",
            organism_text="Fakeorganismus xylosus",
            organism_ncbi_taxonomy_id=999999,
            source_pathway_id="xyz00061",
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    assert len(result.discovered_reaction_ids) == 2
    resolved_kegg_ids = {call[1][0] for call in kegg.calls if call[0] == "fetch"}
    assert "R09999" not in resolved_kegg_ids


# ==================================================================================================
# Increment C.2 -- Organism-specific catalyst resolution
# ==================================================================================================


def _c2_kegg(
    *,
    pathway_id: str = "sce00061",
    reaction_ids: tuple[str, ...] = ("R00742",),
    catalyst_entries: tuple[FakeKgmlEntrySpec, ...] = (),
    ec_by_reaction: dict[str, tuple[str, ...]] | None = None,
) -> FakeKeggConnector:
    ec_by_reaction = ec_by_reaction or {}
    return FakeKeggConnector(
        pathways={pathway_id: "fatty acid biosynthesis - Saccharomyces cerevisiae"},
        pathway_reactions={pathway_id: ()},
        kgml_reactions={pathway_id: reaction_ids},
        kgml_catalyst_entries={pathway_id: catalyst_entries},
        reactions={
            rid: make_kegg_reaction(
                rid,
                name=f"fake reaction {rid}",
                # A real KEGG ENZYME field lists every EC number on one row,
                # whitespace-separated -- reaction_identity_from_kegg copies that one
                # row verbatim as Reaction.ec_number (see app/normalization/reaction.py).
                # A multi-element `enzymes` tuple would instead simulate separate flat-
                # file *rows*, of which only the first becomes ec_number -- not what a
                # multi-EC annotation actually looks like.
                enzymes=(" ".join(ec_by_reaction[rid]),) if rid in ec_by_reaction else (),
            )
            for rid in reaction_ids
        },
    )


def _c2_request(**overrides) -> PathwayCurationRequest:
    return _request(source_pathway_id="sce00061", **overrides)


# --- one direct gene (Step 41, Case A) -----------------------------------------------------------


def test_c2_one_direct_gene_resolves_to_reaction_enzyme(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(
                sgd_id="S000000001", systematic_name="YER061C", standard_name="CEM1"
            )
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(request_id="req-c2-one-gene", seed_entity_texts=(), include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert not any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )
    # No broad EC search was ever issued for this reaction -- direct evidence handled it.
    assert not any(
        call[0] == "search" and str(call[1][0]).startswith("ec:") for call in uniprot.calls
    )


# --- multiple independent direct genes (Step 42, ACC1/HFA1 regression) --------------------------


def test_c2_multiple_independent_direct_genes_both_persisted(db_session: Session) -> None:
    """The central ACC1/HFA1 regression case (Step 23): KGML explicitly names both
    organism-specific genes at one reaction node -- both are preserved as
    source-supported candidates and both resolve to independent ReactionEnzyme rows,
    never collapsed into one and never expanded into a 13-candidate EC ambiguity."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(
                entry_type="gene", names=("YMR207C", "YNR016C"), reaction_ids=("R00742",)
            ),
        ),
        ec_by_reaction={"R00742": ("6.4.1.2",)},
    )
    sgd = FakeSgdConnector(
        loci={
            "YMR207C": make_sgd_locus(
                sgd_id="S000004815", systematic_name="YMR207C", standard_name="HFA1"
            ),
            "YNR016C": make_sgd_locus(
                sgd_id="S000005299", systematic_name="YNR016C", standard_name="ACC1"
            ),
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-acc1-hfa1", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 2
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 2
    assert not any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )
    # The broad 13-candidate EC search this reaction would previously have triggered
    # never happened -- direct evidence handled the whole reaction.
    assert not any(
        call[0] == "search" and str(call[1][0]).startswith("ec:") for call in uniprot.calls
    )


# --- complex-ambiguity negative test (Step 43) ---------------------------------------------------


def test_c2_group_entry_never_fabricates_independent_catalysts_or_a_complex(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YAAA",), reaction_ids=()),
            FakeKgmlEntrySpec(entry_type="gene", names=("YBBB",), reaction_ids=()),
            FakeKgmlEntrySpec(
                entry_type="group",
                reaction_ids=("R00742",),
                component_ids=("100", "101"),
            ),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YAAA": make_sgd_locus(sgd_id="S1", systematic_name="YAAA", standard_name="AAA"),
            "YBBB": make_sgd_locus(sgd_id="S2", systematic_name="YBBB", standard_name="BBB"),
        }
    )
    uniprot = FakeUniProtConnector(entries={})

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-group-ambiguous", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    assert result.agent1_knowledge_package.proteins == ()
    # No EnzymeComplex was ever created (this package has no code path that could).
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        and "group" in (item.notes or "")
    ]
    assert len(unresolved) == 1
    # Neither gene was ever queried against SGD -- group semantics block the attempt entirely.
    assert sgd.calls == []


# --- ortholog-only negative test (Step 44) -------------------------------------------------------


def test_c2_ortholog_only_entry_never_fabricates_gene_or_protein(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="ortholog", names=("K00665",), reaction_ids=("R00742",)),
        ),
        ec_by_reaction={"R00742": ("2.3.1.86",)},
    )
    sgd = FakeSgdConnector(loci={})
    uniprot = FakeUniProtConnector(entries={})

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-ortholog-only", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.proteins == ()
    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    # No SGD call was ever made from a bare KO id -- an ortholog is never treated as
    # organism-specific gene evidence. The EC fallback *did* still run (no direct
    # evidence existed for this reaction) and found nothing either.
    assert sgd.calls == []
    assert any(call[0] == "search" for call in uniprot.calls)


# --- EC-only ambiguity negative test (Step 45, C.1 regression) -----------------------------------


def test_c2_ec_only_reaction_still_never_fabricates_an_association(db_session: Session) -> None:
    """No KGML gene/ortholog entry exists for this reaction at all -- pure EC
    fallback, exactly like Increment C.1 -- multiple organism-scoped candidates must
    still never be resolved arbitrarily."""
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("6.4.1.2",)})
    uniprot = FakeUniProtConnector(
        entries={
            "6.4.1.2": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    uniprot.entries["ec:6.4.1.2"] = make_uniprot_entry(
        accession="P32874",
        recommended_name="fake HFA1",
        gene_names=("HFA1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
        ec_numbers=("6.4.1.2",),
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-ec-only-ambiguous", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    assert any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )


# --- direct evidence versus broad EC (Step 46 -- one of C.2's most important tests) --------------


def test_c2_direct_evidence_is_not_diluted_by_broad_ec_candidates(db_session: Session) -> None:
    """Direct KGML gene evidence names exactly one gene, resolving to P1. The same
    reaction's EC number would, if queried broadly, surface P1/P2/P3/P4 -- but
    because direct evidence exists, the EC fallback never even runs for this
    reaction, so P2/P3/P4 are never fetched, never resolved, never associated."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        ),
        ec_by_reaction={"R00742": ("2.3.1.86",)},
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
        }
    )
    # The broad-EC candidates that would exist if this reaction's EC were queried --
    # never actually queried, so these three are never even fetched by the fake.
    uniprot.entries["ec:2.3.1.86"] = make_uniprot_entry(
        accession="P2",
        recommended_name="fake P2",
        gene_names=("P2GENE",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-direct-vs-ec", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1
    assert proteins[0].uniprot_id == "P1"
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert not any(call[0] == "search" and "ec:" in str(call[1][0]) for call in uniprot.calls)


# --- multi-EC tests (Step 47) ---------------------------------------------------------------------


def test_c2_multiple_valid_ecs_query_each_independently(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.1.1.1", "2.2.2.2")})
    uniprot = FakeUniProtConnector(
        entries={
            "1.1.1.1": make_uniprot_entry(
                accession="PA",
                recommended_name="fake A",
                gene_names=("GENEA",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(request_id="req-c2-multi-ec", seed_entity_texts=(), include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    ec_queries = {call[1][0] for call in uniprot.calls if call[0] == "search"}
    assert any(q.startswith("ec:1.1.1.1") for q in ec_queries)
    assert any(q.startswith("ec:2.2.2.2") for q in ec_queries)
    assert len(result.agent1_knowledge_package.proteins) == 1
    assert result.agent1_knowledge_package.proteins[0].uniprot_id == "PA"


def test_c2_duplicate_ecs_are_deduplicated(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.1.1.1", "1.1.1.1")})
    uniprot = FakeUniProtConnector(entries={})

    execute_pathway_curation(
        _c2_request(request_id="req-c2-dup-ec", seed_entity_texts=(), include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    ec_queries = [call[1][0] for call in uniprot.calls if call[0] == "search"]
    assert sum(1 for q in ec_queries if q.startswith("ec:1.1.1.1")) == 1


def test_c2_one_ec_resolves_one_does_not(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.1.1.1", "9.9.9.9")})
    uniprot = FakeUniProtConnector(
        entries={
            "1.1.1.1": make_uniprot_entry(
                accession="PA",
                recommended_name="fake A",
                gene_names=("GENEA",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-partial-ec", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert result.agent1_knowledge_package.proteins[0].uniprot_id == "PA"


def test_c2_both_ecs_returning_same_protein_are_deduplicated(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.1.1.1", "2.2.2.2")})
    shared = make_uniprot_entry(
        accession="PSHARED",
        recommended_name="fake shared",
        gene_names=("SHARED",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
    )
    uniprot = FakeUniProtConnector(entries={"1.1.1.1": shared, "2.2.2.2": shared})

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-same-protein", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1


def test_c2_conflicting_ec_candidate_sets_preserve_ambiguity(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.1.1.1", "2.2.2.2")})
    uniprot = FakeUniProtConnector(
        entries={
            "1.1.1.1": make_uniprot_entry(
                accession="PA",
                recommended_name="fake A",
                gene_names=("GENEA",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
            "2.2.2.2": make_uniprot_entry(
                accession="PB",
                recommended_name="fake B",
                gene_names=("GENEB",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-conflicting-ec", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    assert any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )


def test_c2_wildcard_ec_is_never_queried(db_session: Session) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.3.1.-",)})
    uniprot = FakeUniProtConnector(entries={})

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-wildcard-ec", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert uniprot.calls == []
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        and "wildcard" in (item.notes or "")
    ]
    assert len(unresolved) == 1


def test_c2_partial_wildcard_alongside_a_real_ec_still_queries_the_real_one(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(ec_by_reaction={"R00742": ("1.3.1.-", "1.1.1.1")})
    uniprot = FakeUniProtConnector(
        entries={
            "1.1.1.1": make_uniprot_entry(
                accession="PA",
                recommended_name="fake A",
                gene_names=("GENEA",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-mixed-wildcard", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert not any(call[0] == "search" and "1.3.1.-" in str(call[1][0]) for call in uniprot.calls)


# --- seedless autonomous catalyst test (Step 48) --------------------------------------------------


def test_c2_direct_catalyst_resolution_requires_no_seed(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(request_id="req-c2-no-seed", seed_entity_texts=()),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, sgd=sgd, uniprot=uniprot, pubmed=FakePubMedConnector()
        ),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1


# --- idempotency (Step 50) ------------------------------------------------------------------------


def test_c2_repeated_execution_does_not_duplicate_direct_catalysts(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )
    request = _c2_request(
        request_id="req-c2-idempotency", seed_entity_texts=(), include_publications=False
    )
    connectors = PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot)

    first = execute_pathway_curation(request, session=db_session, connectors=connectors)
    db_session.flush()
    second = execute_pathway_curation(request, session=db_session, connectors=connectors)

    assert len(first.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert len(second.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert (
        first.agent1_knowledge_package.reaction_enzyme_associations
        == second.agent1_knowledge_package.reaction_enzyme_associations
    )
    assert len(second.agent1_knowledge_package.proteins) == 1


# --- provenance (Step 51) -------------------------------------------------------------------------


def test_c2_direct_catalyst_provenance_traceable_via_queries_executed(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-provenance", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    queries = result.queries_executed
    assert any("direct-catalyst-context" in q for q in queries)
    assert any("direct KGML" in q and "YER061C" in q for q in queries)
    assert any("associate direct KGML catalyst" in q for q in queries)


# --- source failure (Step 52) ---------------------------------------------------------------------


def test_c2_catalyst_context_failure_never_blocks_structural_reaction_processing(
    db_session: Session,
) -> None:
    @dataclass
    class _RaisingCatalystContextKeggConnector(FakeKeggConnector):
        def get_kgml(self, pathway_id: str) -> str | None:
            self.calls.append(("get_kgml", (pathway_id,)))
            if self.calls.count(("get_kgml", (pathway_id,))) == 1:
                return super(  # noqa: UP008 -- explicit dataclass-subclass super() call
                    _RaisingCatalystContextKeggConnector, self
                ).get_kgml(pathway_id)
            raise ConnectorError("KGML endpoint unreachable for catalyst-context retrieval")

    kegg = _RaisingCatalystContextKeggConnector(
        pathways={"sce00061": "fatty acid biosynthesis"},
        pathway_reactions={"sce00061": ()},
        kgml_reactions={"sce00061": ("R00742",)},
        reactions={"R00742": make_kegg_reaction("R00742", name="fake reaction R00742")},
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-context-failure", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )

    # Structural reaction curation is unaffected by the catalyst-context failure.
    assert len(result.discovered_reaction_ids) == 1
    assert any(
        item.reason is FrontierReason.SOURCE_FAILURE and "catalyst-context" in item.frontier_id
        for item in result.unresolved_frontier
    )


# --- direct-gene-resolution failure (Step 53) -----------------------------------------------------


def test_c2_unresolvable_direct_gene_is_disclosed_never_replaced_by_ec_match(
    db_session: Session,
) -> None:
    """KGML names a real gene id, but neither SGD nor UniProt can resolve it. Even
    though the reaction's own EC number would, if queried, surface a real candidate,
    the EC fallback must never run for this reaction -- the failed direct evidence is
    disclosed as unresolved, never silently replaced."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YUNKNOWN",), reaction_ids=("R00742",)),
        ),
        ec_by_reaction={"R00742": ("6.4.1.2",)},
    )
    sgd = FakeSgdConnector(loci={})  # YUNKNOWN never resolves
    uniprot = FakeUniProtConnector(
        entries={
            "6.4.1.2": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-unresolvable-gene", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    assert result.agent1_knowledge_package.proteins == ()
    assert not any(call[0] == "search" and "ec:" in str(call[1][0]) for call in uniprot.calls)
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        and item.entity_text == "YUNKNOWN"
    ]
    assert len(unresolved) == 1


# --- contradictory evidence disclosure (Step 54) -----------------------------------------------


def test_c2_ec_contradiction_is_disclosed_but_association_still_persists(
    db_session: Session,
) -> None:
    """Direct KGML gene evidence resolves to a protein whose own curated EC number
    shares nothing with the reaction's own EC annotation -- a genuine contradiction,
    disclosed via a warning, but the direct reaction->gene evidence still wins (the
    association is still persisted, never silently dropped for either source)."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        ),
        ec_by_reaction={"R00742": ("2.3.1.86",)},
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P1",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("9.9.9.9",),
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c2-ec-contradiction", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert any("share no value" in warning for warning in result.warnings)


# ==================================================================================================
# Increment C.3 -- Gene-anchored protein identity resolution
# ==================================================================================================


def _cross_references_for(session, protein_id):
    return (
        session.execute(
            select(SourceCrossReference).where(
                SourceCrossReference.entity_type == "protein",
                SourceCrossReference.entity_id == protein_id,
            )
        )
        .scalars()
        .all()
    )


def test_c3_one_gene_one_uniprot_record(db_session: Session) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-one-record", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1
    assert proteins[0].uniprot_id == "P39525"
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1


def test_c3_one_gene_several_equivalent_uniprot_records_become_one_protein(
    db_session: Session,
) -> None:
    """The central Run-4 regression: several real, same-organism, same-gene-name
    UniProt accessions (distinct strain records, exactly like the live HFA1 case)
    are database-record multiplicity, not biological-identity ambiguity. Here one
    is reviewed (Swiss-Prot), giving a deterministic canonical accession -- one
    Protein is created, the unreviewed one becomes a cross-reference, never an
    unresolved ambiguity."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YMR207C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YMR207C": make_sgd_locus(sgd_id="S1", systematic_name="YMR207C", standard_name="HFA1")
        }
    )
    # Two distinct dict keys, both substrings of the query "HFA1" (mirrors the
    # existing FakeUniProtConnector substring-match convention), simulating two
    # real, distinct-strain accessions for the same confirmed gene product.
    uniprot = FakeUniProtConnector(
        entries={
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1 canonical strain",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB reviewed (Swiss-Prot)",
            ),
            "FA1": make_uniprot_entry(
                accession="A0A0STRAINB",
                recommended_name="fake HFA1 strain B",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB unreviewed (TrEMBL)",
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-multi-equivalent", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1  # never two Protein rows for one confirmed gene product
    assert proteins[0].uniprot_id == "P32874"
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    # The unreviewed equivalent accession is preserved as a cross-reference, not
    # discarded and not promoted into a second biological identity.
    cross_refs = _cross_references_for(db_session, proteins[0].id)
    external_ids = {ref.external_id for ref in cross_refs}
    assert "A0A0STRAINB" in external_ids


def test_c3_multiple_confirmed_with_no_reviewed_distinction_is_disclosed_not_fabricated(
    db_session: Session,
) -> None:
    """Architectural boundary discovered during Increment C.3 (Step 5): when 2+
    UniProt records are confirmed as the same gene product but none is uniquely
    reviewed/canonical, ``app.normalization.protein.normalize_protein`` has no path
    to create a Protein from name-only (gene-anchored) identity alone -- this is
    disclosed as insufficient evidence, never a fabricated Protein and never an
    arbitrary accession choice."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YMR207C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YMR207C": make_sgd_locus(sgd_id="S1", systematic_name="YMR207C", standard_name="HFA1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1 strain A",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB unreviewed (TrEMBL)",
            ),
            "FA1": make_uniprot_entry(
                accession="A0A0STRAINB",
                recommended_name="fake HFA1 strain B",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB unreviewed (TrEMBL)",
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-no-reviewed-distinction",
            seed_entity_texts=(),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.proteins == ()
    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    assert any(
        "architectural" in warning.lower() or "confirmed" in warning for warning in result.warnings
    ) or any("confirmed" in (item.notes or "") for item in result.unresolved_frontier)


def test_c3_reviewed_record_becomes_canonical_others_become_cross_references(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YNR016C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YNR016C": make_sgd_locus(sgd_id="S1", systematic_name="YNR016C", standard_name="ACC1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1 canonical",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB reviewed (Swiss-Prot)",
            ),
            "CC1": make_uniprot_entry(
                accession="A0A0AUTO",
                recommended_name="fake ACC1 automated",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB unreviewed (TrEMBL)",
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-reviewed-canonical", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1
    assert proteins[0].uniprot_id == "Q00955"  # the reviewed one, never the first-seen one
    cross_refs = _cross_references_for(db_session, proteins[0].id)
    assert any(ref.external_id == "A0A0AUTO" for ref in cross_refs)


def test_c3_cross_species_false_hit_is_excluded_not_ambiguity(db_session: Session) -> None:
    """A same-named-species hit (wrong exact taxonomy id -- mirrors the live
    Saccharomyces pastorianus finding) is excluded entirely, never counted toward
    ambiguity, never blocking resolution of the real, correct candidate."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
            "EM1": make_uniprot_entry(
                accession="A0A6C1WRONGSPECIES",
                recommended_name="fake CEM1 wrong species",
                gene_names=("CEM1_1",),
                organism_name="Saccharomyces pastorianus",
                organism_taxonomy_id=27292,  # a real, distinct, related species' own taxid
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-cross-species", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1
    assert proteins[0].uniprot_id == "P39525"
    assert not any(
        item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        for item in result.unresolved_frontier
    )


def test_c3_same_organism_unrelated_gene_false_hit_is_excluded(db_session: Session) -> None:
    """A same-organism hit for a genuinely different, unrelated gene (mirrors the
    live FAS1 -> SRP102/ATP7 finding) is classified CONFLICTING_GENE_PRODUCT and
    excluded -- never counted toward ambiguity, never blocking the real match."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YKL182W",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YKL182W": make_sgd_locus(sgd_id="S1", systematic_name="YKL182W", standard_name="FAS1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "FAS1": make_uniprot_entry(
                accession="P07149",
                recommended_name="fake FAS1",
                gene_names=("FAS1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
            "AS1": make_uniprot_entry(
                accession="P36057",
                recommended_name="fake SRP102 (unrelated, matched by loose text search)",
                gene_names=("SRP102",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-unrelated-gene", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 1
    assert proteins[0].uniprot_id == "P07149"
    assert not any(
        ref.external_id == "P36057" for ref in _cross_references_for(db_session, proteins[0].id)
    )


def test_c3_insufficient_evidence_is_disclosed_never_a_guess(db_session: Session) -> None:
    """Only conflicting/wrong-organism hits exist -- zero confirmed candidates --
    disclosed as unresolved, never a guess, never a fallback to a wrong record."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YOR221C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YOR221C": make_sgd_locus(sgd_id="S1", systematic_name="YOR221C", standard_name="MCT1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "MCT1": make_uniprot_entry(
                accession="WRONGORG",
                recommended_name="fake wrong-organism hit",
                gene_names=("MCT1",),
                organism_name="Saccharomyces paradoxus",
                organism_taxonomy_id=27291,
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-insufficient-evidence",
            seed_entity_texts=(),
            include_publications=False,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert result.agent1_knowledge_package.proteins == ()
    assert result.agent1_knowledge_package.reaction_enzyme_associations == ()
    unresolved = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.REACTION_CATALYST_UNRESOLVED
        and item.entity_text == "YOR221C"
    ]
    assert len(unresolved) == 1
    assert any("insufficient evidence" in warning for warning in result.warnings)


def test_c3_order_independence_of_canonical_selection() -> None:
    """Canonical-accession selection never depends on candidate order -- a pure,
    offline test of the selection function itself, no live network, no database."""
    from app.pathway_curation.strategies import select_canonical_gene_anchored_protein

    reviewed = make_uniprot_entry(
        accession="REVIEWED1",
        recommended_name="r",
        gene_names=("G1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
        entry_type="UniProtKB reviewed (Swiss-Prot)",
    )
    unreviewed_a = make_uniprot_entry(
        accession="UNREV1",
        recommended_name="u1",
        gene_names=("G1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
        entry_type="UniProtKB unreviewed (TrEMBL)",
    )
    unreviewed_b = make_uniprot_entry(
        accession="UNREV2",
        recommended_name="u2",
        gene_names=("G1",),
        organism_name="Saccharomyces cerevisiae",
        organism_taxonomy_id=YEAST_TAXONOMY_ID,
        entry_type="UniProtKB unreviewed (TrEMBL)",
    )
    from app.connectors.uniprot import _parse_reviewed

    def _record(entry):
        return UniProtProteinRecord(
            primary_accession=entry.primary_accession,
            entry_name=None,
            reviewed=_parse_reviewed(entry.entry_type),
            protein_name=entry.recommended_name,
            gene_names=entry.gene_names,
            organism_name=entry.organism_name,
            organism_taxonomy_id=entry.organism_taxonomy_id,
            ec_numbers=(),
            sequence_length=None,
            secondary_accessions=(),
            cross_references=(),
            raw=entry,
        )

    records = [_record(unreviewed_a), _record(reviewed), _record(unreviewed_b)]
    forward = select_canonical_gene_anchored_protein(tuple(records))
    backward = select_canonical_gene_anchored_protein(tuple(reversed(records)))
    assert forward is not None
    assert forward.primary_accession == "REVIEWED1"
    assert backward is not None
    assert backward.primary_accession == "REVIEWED1"


def test_c3_multiple_unreviewed_with_no_distinguishing_evidence_returns_none() -> None:
    from app.pathway_curation.strategies import select_canonical_gene_anchored_protein

    def _record(accession):
        return UniProtProteinRecord(
            primary_accession=accession,
            entry_name=None,
            reviewed=False,
            protein_name="p",
            gene_names=("G1",),
            organism_name="Saccharomyces cerevisiae",
            organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ec_numbers=(),
            sequence_length=None,
            secondary_accessions=(),
            cross_references=(),
            raw=None,
        )

    result = select_canonical_gene_anchored_protein((_record("A"), _record("B")))
    assert result is None


def test_c3_idempotent_persistence_no_duplicate_protein_or_cross_references(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YMR207C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YMR207C": make_sgd_locus(sgd_id="S1", systematic_name="YMR207C", standard_name="HFA1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1 canonical strain",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB reviewed (Swiss-Prot)",
            ),
            "FA1": make_uniprot_entry(
                accession="A0A0STRAINB",
                recommended_name="fake HFA1 strain B",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                entry_type="UniProtKB unreviewed (TrEMBL)",
            ),
        }
    )
    request = _c2_request(
        request_id="req-c3-idempotency", seed_entity_texts=(), include_publications=False
    )
    connectors = PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot)

    first = execute_pathway_curation(request, session=db_session, connectors=connectors)
    db_session.flush()
    second = execute_pathway_curation(request, session=db_session, connectors=connectors)

    assert len(first.agent1_knowledge_package.proteins) == 1
    assert len(second.agent1_knowledge_package.proteins) == 1
    assert (
        first.agent1_knowledge_package.proteins[0].id
        == second.agent1_knowledge_package.proteins[0].id
    )
    cross_refs = _cross_references_for(db_session, second.agent1_knowledge_package.proteins[0].id)
    external_ids = [ref.external_id for ref in cross_refs]
    assert len(external_ids) == len(set(external_ids))  # no duplicate cross-reference rows
    assert len(second.agent1_knowledge_package.reaction_enzyme_associations) == 1


def test_c3_acc1_hfa1_like_genes_remain_distinct_proteins_despite_shared_ec(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(
                entry_type="gene", names=("YMR207C", "YNR016C"), reaction_ids=("R00742",)
            ),
        ),
        ec_by_reaction={"R00742": ("6.4.1.2",)},
    )
    sgd = FakeSgdConnector(
        loci={
            "YMR207C": make_sgd_locus(sgd_id="S1", systematic_name="YMR207C", standard_name="HFA1"),
            "YNR016C": make_sgd_locus(sgd_id="S2", systematic_name="YNR016C", standard_name="ACC1"),
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-acc1-hfa1", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 2
    uniprot_ids = {p.uniprot_id for p in proteins}
    assert uniprot_ids == {"P32874", "Q00955"}
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 2


def test_c3_fas1_fas2_like_multi_gene_reaction_gets_two_reaction_enzyme_rows(
    db_session: Session,
) -> None:
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(
                entry_type="gene", names=("YKL182W", "YPL231W"), reaction_ids=("R00742",)
            ),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YKL182W": make_sgd_locus(sgd_id="S1", systematic_name="YKL182W", standard_name="FAS1"),
            "YPL231W": make_sgd_locus(sgd_id="S2", systematic_name="YPL231W", standard_name="FAS2"),
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "FAS1": make_uniprot_entry(
                accession="P07149",
                recommended_name="fake FAS1",
                gene_names=("FAS1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
            "FAS2": make_uniprot_entry(
                accession="P19097",
                recommended_name="fake FAS2",
                gene_names=("FAS2",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c3-fas1-fas2", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    proteins = result.agent1_knowledge_package.proteins
    assert len(proteins) == 2
    associations = result.agent1_knowledge_package.reaction_enzyme_associations
    assert len(associations) == 2
    # Both associations target the same reaction (no fabricated complex; both
    # genes recorded as independent catalyst candidates per C.2's own rule).
    reaction_ids = {a.reaction_id for a in associations}
    assert len(reaction_ids) == 1


def test_c3_gene_anchored_protein_reused_via_by_gene_id_no_second_uniprot_call(
    db_session: Session,
) -> None:
    """Once a Protein exists for a Gene (e.g. resolved via one reaction), a second
    reaction naming the same gene reuses it via ``ProteinLookup.by_gene_id`` --
    confirmed by the connector call count never issuing a second UniProt search for
    the same gene symbol within one run (already covered indirectly by
    ``direct_catalyst_cache``, but this asserts the gene_id-based lookup path
    specifically survives a fresh run reusing a previous run's persisted Protein)."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
            )
        }
    )
    request = _c2_request(
        request_id="req-c3-reuse-by-gene-id", seed_entity_texts=(), include_publications=False
    )
    first_result = execute_pathway_curation(
        request,
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )
    db_session.flush()

    # A second, independent run (fresh connectors/state) against the same database
    # -- the Protein already anchored to this Gene must be reused with zero new
    # UniProt search calls.
    uniprot2 = FakeUniProtConnector(entries={})  # no data at all -- reuse must not need it
    second_request = _c2_request(
        request_id="req-c3-reuse-by-gene-id-2", seed_entity_texts=(), include_publications=False
    )
    second_result = execute_pathway_curation(
        second_request,
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot2),
    )

    assert not any(call[0] == "search" for call in uniprot2.calls)
    assert len(second_result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert (
        first_result.agent1_knowledge_package.proteins[0].id
        == second_result.agent1_knowledge_package.proteins[0].id
    )


# ---------------------------------------------------------------------------------------------
# Increment C.4 -- Gene-anchored kinetic enrichment
# ---------------------------------------------------------------------------------------------
#
# Real Integration Pilot 2 Run 1 confirmed that all 13 gene-anchored proteins
# Increment C.3 resolves for a real ``sce00061`` run reached zero kinetic
# measurements: ``_resolve_direct_catalysts_from_kgml`` never appended to
# ``resolved_protein_ec_numbers``, so ``_discover_kinetics`` (which iterates
# exactly that list) never even attempted a SABIO-RK/OED query for any of
# them. These tests drive the real, unmodified ``_discover_kinetics``/
# ``persist_kinetic_measurement`` machinery -- through deterministic fake
# connectors, never network I/O -- against a gene-anchored protein resolved
# via the direct-KGML path (never a seed, never the EC fallback), the one
# path that was previously never eligible at all.


def test_c4_gene_anchored_protein_reaches_kinetic_enrichment(db_session: Session) -> None:
    """The central C.4 regression: a protein resolved only through the direct-KGML,
    gene-anchored path (no ``seed_entity_texts``) now produces a persisted kinetic
    measurement, linked to that exact protein, and that measurement reaches both
    ``Agent1KnowledgePackage`` and ``Agent1CuratedKnowledgeView`` -- confirming F5
    (Claims/Evidence export) is not a blocker for kinetics, which bypasses it
    entirely (``_select_kinetic_measurements`` scopes by ``organism_id``/
    ``reaction_id`` directly, never by ``Evidence.publication_id``)."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1",
                ec_number="2.3.1.41",
                parameter_type="kcat",
                value="12.0",
                unit="1/s",
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-gene-anchored-kinetics",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    resolved_protein_id = result.agent1_knowledge_package.proteins[0].id

    measurements = result.agent1_knowledge_package.kinetic_measurements
    assert len(measurements) == 1
    assert measurements[0].protein_id == resolved_protein_id
    assert measurements[0].organism_id == result.organism_id

    # Reaches the actual Agent 1 -> Agent 2 handoff view, not merely the package.
    view_measurements = result.curated_knowledge_view.kinetic_measurements
    assert len(view_measurements) == 1
    assert view_measurements[0].protein_id == resolved_protein_id

    assert not any(
        item.reason is FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED
        for item in result.unresolved_frontier
    )


def test_c4_ec_propagated_is_the_resolved_proteins_own_curated_ec_number(
    db_session: Session,
) -> None:
    """The EC number kinetics is queried with is read back from the just-persisted,
    gene-anchored ``Protein`` row itself (``_ec_numbers_for_protein``) -- never the
    reaction's own (possibly multi-valued, possibly differently-formatted) KEGG
    ``ec_number`` annotation. Here the reaction's own KEGG annotation deliberately
    differs from the protein's UniProt-reported one, so a query using the wrong
    source would be immediately visible."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        ),
        ec_by_reaction={"R00742": ("2.3.1.41", "2.3.1.86")},
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1",
                ec_number="2.3.1.41",
                parameter_type="kcat",
                value="12.0",
                unit="1/s",
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-ec-propagation",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    search_queries = [call[1][0] for call in sabiork.calls if call[0] == "search"]
    assert search_queries == ["2.3.1.41"]
    assert "2.3.1.41 2.3.1.86" not in search_queries


def test_c4_two_genes_sharing_one_ec_each_queried_independently(
    db_session: Session,
) -> None:
    """The ACC1/HFA1-shaped regression, extended to kinetics: two distinct
    gene-anchored proteins that happen to share one EC number are never collapsed
    into one query -- ``_discover_kinetics``'s own per-protein query identity
    (``protein_id`` is part of it, not just ``ec_number``) means each protein's
    own kinetics search executes independently, exactly once each, never skipped
    merely because a sibling protein already queried the identical EC.

If the underlying source's own two independent per-protein searches happen to
    return the *same external record* (SABIO-RK/OED's own EC-scoped, not
    protein-scoped, search semantics -- real for both),
    ``persist_kinetic_measurement``'s own ``(source, source_id)`` idempotency
    (Increment A's documented policy) still reuses the one existing
    ``KineticMeasurement`` row rather than creating a second -- never a
    fabricated second measurement, and never two independent rows for what the
    source itself reports as one identical external record. **Increment C.6**:
    both proteins' own applicability to that one shared row are now preserved
    via ``kinetic_measurement_protein_context`` -- confirmed live to have been
    lost entirely before this increment (Real Integration Pilot 1 Run 7:
    yeast's real FAS1/FAS2, only the alphabetically-first protein UUID ever
    appeared in the database)."""
    kegg = _c2_kegg(
        reaction_ids=("R00742", "R00900"),
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YNR016C",), reaction_ids=("R00742",)),
            FakeKgmlEntrySpec(entry_type="gene", names=("YMR207C",), reaction_ids=("R00900",)),
        ),
    )
    sgd = FakeSgdConnector(
        loci={
            "YNR016C": make_sgd_locus(
                sgd_id="S1", systematic_name="YNR016C", standard_name="ACC1"
            ),
            "YMR207C": make_sgd_locus(
                sgd_id="S2", systematic_name="YMR207C", standard_name="HFA1"
            ),
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="fake ACC1",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
            "HFA1": make_uniprot_entry(
                accession="P32874",
                recommended_name="fake HFA1",
                gene_names=("HFA1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            ),
        }
    )
    # SABIO-RK's own search is EC-scoped, not protein-scoped -- one shared fake
    # record under the shared EC number is what a real search would also return
    # identically to both independent per-protein queries.
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO-SHARED": make_sabio_record(
                entry_id="SABIO-SHARED",
                ec_number="6.4.1.2",
                parameter_type="kcat",
                value="1.0",
                unit="1/s",
            ),
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-shared-ec",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.proteins) == 2
    protein_ids = {p.id for p in result.agent1_knowledge_package.proteins}

    # Both proteins were queried independently -- never skipped due to sharing an
    # EC with a sibling protein.
    search_calls = [call for call in sabiork.calls if call[0] == "search"]
    assert len(search_calls) == 2

    measurements = result.agent1_knowledge_package.kinetic_measurements
    # Persistence-layer (source, source_id) idempotency reuses the one shared
    # external record rather than duplicating it -- exactly one row survives,
    # linked (via .protein_id, kept for backward compatibility) to whichever
    # protein's independent query persisted it first.
    assert len(measurements) == 1
    assert measurements[0].protein_id in protein_ids

    # Increment C.6: BOTH protein contexts survive via the new join table,
    # regardless of which one "won" the legacy .protein_id column above.
    contexts = result.agent1_knowledge_package.kinetic_measurement_protein_contexts
    assert len(contexts) == 2
    assert {c.protein_id for c in contexts} == protein_ids
    assert all(c.kinetic_measurement_id == measurements[0].id for c in contexts)


def test_c4_shared_gene_across_two_reactions_queries_kinetics_exactly_once(
    db_session: Session,
) -> None:
    """Determinism/order-independence: one gene named by two reactions' own KGML
    evidence (yeast's real iterative FAS cycle shape) is resolved once
    (``direct_catalyst_cache``) and appended to ``resolved_protein_ec_numbers``
    once per reaction it is attached to -- but ``_discover_kinetics`` still issues
    exactly one SABIO-RK query for it, never one per reaction, via its own
    ``set()``/``state.has_run_query`` deduplication."""
    kegg = _c2_kegg(
        reaction_ids=("R00742", "R00900"),
        catalyst_entries=(
            FakeKgmlEntrySpec(
                entry_type="gene", names=("YER061C",), reaction_ids=("R00742", "R00900")
            ),
        ),
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1",
                ec_number="2.3.1.41",
                parameter_type="kcat",
                value="12.0",
                unit="1/s",
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-shared-gene-dedup",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 2
    assert len(result.agent1_knowledge_package.kinetic_measurements) == 1
    assert len([call for call in sabiork.calls if call[0] == "search"]) == 1


def test_c4_no_kinetic_source_result_is_disclosed_never_fabricated(db_session: Session) -> None:
    """A gene-anchored protein whose EC number no configured kinetic source has any
    record for produces zero measurements and an explicit, disclosed
    ``MISSING_KINETICS`` frontier item -- never a fabricated or estimated value."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(records={})  # no record for any EC

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-no-kinetics-found",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert result.agent1_knowledge_package.kinetic_measurements == ()
    missing = [
        item
        for item in result.unresolved_frontier
        if item.reason is FrontierReason.MISSING_KINETICS and item.entity_text == "2.3.1.41"
    ]
    assert len(missing) == 1


def test_c4_repeated_execution_does_not_duplicate_kinetic_measurements(
    db_session: Session,
) -> None:
    """Idempotency: running the identical request twice against the same database
    reuses the existing ``KineticMeasurement`` row (``persist_kinetic_measurement``'s
    own ``(source, source_id)`` uniqueness) rather than creating a second one."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1",
                ec_number="2.3.1.41",
                parameter_type="kcat",
                value="12.0",
                unit="1/s",
            )
        }
    )
    request = _c2_request(
        request_id="req-c4-idempotency", seed_entity_texts=(), include_publications=False,
        include_kinetics=True,
    )
    connectors = PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork)

    first = execute_pathway_curation(request, session=db_session, connectors=connectors)
    db_session.flush()
    second = execute_pathway_curation(request, session=db_session, connectors=connectors)

    assert len(first.agent1_knowledge_package.kinetic_measurements) == 1
    assert len(second.agent1_knowledge_package.kinetic_measurements) == 1
    assert (
        first.agent1_knowledge_package.kinetic_measurements[0].id
        == second.agent1_knowledge_package.kinetic_measurements[0].id
    )


def test_c4_kinetic_measurement_provenance_preserved(db_session: Session) -> None:
    """The persisted measurement's own source/source-id provenance (which source
    record it came from) survives to the Agent 1 handoff, alongside the protein
    and organism identity that justified the search in the first place."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1",
                ec_number="2.3.1.41",
                parameter_type="kcat",
                value="12.0",
                unit="1/s",
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-provenance",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    measurement = result.agent1_knowledge_package.kinetic_measurements[0]
    assert measurement.source is SourceType.SABIORK
    assert measurement.source_id == "SABIO1:kcat"
    assert measurement.protein_id == result.agent1_knowledge_package.proteins[0].id
    assert measurement.organism_id == result.organism_id
    # Pre-existing architectural characteristic, unchanged by C.4 (disclosed, not
    # fixed -- kinetics discovery is keyed by (protein, EC), never by reaction, in
    # every path: seeded, EC-fallback, and this direct-KGML one alike):
    assert measurement.reaction_id is None


def test_c4_kinetics_disabled_direct_catalyst_behavior_is_unchanged(db_session: Session) -> None:
    """Regression safety net: with ``include_kinetics`` left at its default
    (``False``) and no kinetics connector configured at all, populating
    ``resolved_protein_ec_numbers`` from the direct-KGML path has zero observable
    effect on C.2/C.3's own catalyst-resolution behavior -- same protein count,
    same association count, no kinetics-related frontier item of any kind."""
    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c4-kinetics-disabled", seed_entity_texts=(), include_publications=False
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot),
    )

    assert len(result.agent1_knowledge_package.proteins) == 1
    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 1
    assert result.agent1_knowledge_package.kinetic_measurements == ()
    assert not any(
        item.reason in (FrontierReason.MISSING_KINETICS,)
        for item in result.unresolved_frontier
    )


# ---------------------------------------------------------------------------------------------
# Increment C.5 -- Robust SABIO-RK live-record parsing
# ---------------------------------------------------------------------------------------------
#
# Real Integration Pilot 1 Run 6's primary run aborted with an uncaught AttributeError
# while parsing a real, live SABIO-RK record for EC 2.3.1.86 (FAS1/FAS2) -- a single
# malformed/unrecognized record among several real hits discarded not just that one
# record, but the entire pathway-curation run's already-completed structural/catalyst
# work. These tests exercise strategies.discover_kinetics_sabiork directly (a strategies
# function, tested offline exactly like select_canonical_gene_anchored_protein already is
# above) and, for the full integration path, the real app.connectors.sabiork.SabiorkConnector
# (backed by a mocked HTTP transport, never live network) rather than the higher-level
# FakeSabiorkConnector -- which returns pre-built SabioKineticRecord objects directly and
# so never exercises parse_kinetic_law_json at all.


class _OneBadHitSabiorkConnector:
    """A minimal, hand-written fake matching SabiorkConnector's search()/fetch()/normalize()
    shape (Increment C.5): three real search hits, the middle one's fetch() raising
    ConnectorParseError exactly like a genuinely malformed live record would, the other
    two returning real, valid records."""

    def __init__(self, valid_records: dict[str, object]) -> None:
        self._valid_records = valid_records
        self.fetch_calls: list[str] = []

    def search(self, query: str, *, organism: str | None = None):
        from app.connectors.sabiork import SabioSearchHit

        return [
            SabioSearchHit(entry_id="good-1", ec_numbers=(query,), raw={}),
            SabioSearchHit(entry_id="bad-2", ec_numbers=(query,), raw={}),
            SabioSearchHit(entry_id="good-3", ec_numbers=(query,), raw={}),
        ]

    def fetch(self, entry_id: str):
        from app.connectors.exceptions import ConnectorParseError

        self.fetch_calls.append(entry_id)
        if entry_id == "bad-2":
            raise ConnectorParseError(
                "malformed SABIO-RK entry bad-2: unexpected structure while parsing a "
                "recognized section: AttributeError: 'str' object has no attribute 'get'"
            )
        return self._valid_records[entry_id]

    def normalize(self, record):
        return record.parameters


def test_c5_one_malformed_record_among_valid_ones_is_isolated_not_fatal() -> None:
    """The core C.5 regression, at strategies.discover_kinetics_sabiork's own level:
    record 1 valid -> preserved; record 2 malformed -> disclosed/skipped, never raised;
    record 3 valid -> preserved. Before Increment C.5, this exact shape (a
    ConnectorParseError -- or, pre-connector-fix, a bare AttributeError -- from the
    *second* of several hits) would have discarded record 1's own already-parsed
    identity too, since the exception escaped the whole per-hit loop uncaught."""
    from app.pathway_curation import strategies
    from app.pathway_curation.strategies import SabiorkKineticDiscoveryResult, SkippedSabiorkRecord

    good_record_1 = make_sabio_record(
        entry_id="good-1", ec_number="2.3.1.41", parameter_type="Km", value="0.5", unit="mM"
    )
    good_record_3 = make_sabio_record(
        entry_id="good-3", ec_number="2.3.1.41", parameter_type="kcat", value="12.0", unit="1/s"
    )
    connector = _OneBadHitSabiorkConnector({"good-1": good_record_1, "good-3": good_record_3})

    result = strategies.discover_kinetics_sabiork(connector, "2.3.1.41", organism=None)

    assert isinstance(result, SabiorkKineticDiscoveryResult)
    assert connector.fetch_calls == ["good-1", "bad-2", "good-3"]  # all three attempted
    assert len(result.identities) == 2  # both valid records preserved
    parameter_types = {identity.parameter_type.value for identity in result.identities}
    assert parameter_types == {"KM", "KCAT"}
    assert len(result.skipped_records) == 1
    skipped = result.skipped_records[0]
    assert isinstance(skipped, SkippedSabiorkRecord)
    assert skipped.entry_id == "bad-2"
    assert "unexpected structure" in skipped.reason


def test_c5_search_or_first_fetch_failure_still_propagates_uncaught() -> None:
    """A genuine call-level failure (nothing parsed yet) is a different concern from a
    record-level one and must still propagate as before -- Increment C.5 narrows
    isolation to individual records, it does not weaken whole-call failure semantics."""
    from app.connectors.exceptions import ConnectorHTTPError
    from app.pathway_curation import strategies

    class _AlwaysFailingSearchConnector:
        def search(self, query: str, *, organism: str | None = None):
            raise ConnectorHTTPError(500, "SABIO-RK unavailable")

        def fetch(self, entry_id: str):  # pragma: no cover -- never reached
            raise AssertionError("fetch() must not be called if search() itself failed")

        def normalize(self, record):  # pragma: no cover -- never reached
            return record.parameters

    with pytest.raises(ConnectorHTTPError):
        strategies.discover_kinetics_sabiork(
            _AlwaysFailingSearchConnector(), "2.3.1.41", organism=None
        )


def test_c5_executor_discloses_skipped_record_and_still_persists_the_valid_ones(
    db_session: Session,
) -> None:
    """End-to-end through execute_pathway_curation's own _discover_kinetics: a
    protein whose SABIO-RK search returns one malformed record among valid ones
    still ends up with its valid measurement(s) persisted, and the skip is
    disclosed as a warning, never silently dropped and never fatal to the run."""

    class _PatchedSabiork(_OneBadHitSabiorkConnector):
        pass

    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={"ACC1": make_sgd_locus(sgd_id="S1", systematic_name="YNR016C", standard_name="ACC1")}
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    good_record_1 = make_sabio_record(
        entry_id="good-1", ec_number="2.3.1.41", parameter_type="Km", value="0.5", unit="mM"
    )
    good_record_3 = make_sabio_record(
        entry_id="good-3", ec_number="2.3.1.41", parameter_type="kcat", value="12.0", unit="1/s"
    )
    sabiork = _PatchedSabiork({"good-1": good_record_1, "good-3": good_record_3})

    result = execute_pathway_curation(
        _request(
            request_id="req-c5-executor-isolation",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.kinetic_measurements) == 2
    assert any("bad-2" in w and "skipped" in w for w in result.warnings)


def test_c5_real_schema_variant_reaches_kinetic_measurement_via_real_sabiork_connector(
    db_session: Session,
) -> None:
    """C.4/C.5 integration test: a gene-anchored protein (no seed, direct-KGML path,
    Increment C.3/C.4) reaches kinetic enrichment through the REAL
    app.connectors.sabiork.SabiorkConnector -- backed by a mocked HTTP transport
    serving the exact live-confirmed schema variant that crashed Pilot 1 Run 6
    (envvar_temperature.unit as a bare string) -- all the way to a persisted
    KineticMeasurement that reaches both Agent1KnowledgePackage and
    Agent1CuratedKnowledgeView. The higher-level FakeSabiorkConnector used everywhere
    else in this file bypasses parse_kinetic_law_json entirely, so it could not
    exercise this increment's own fix -- this test deliberately uses the real
    connector class instead."""
    import json

    import httpx

    from app.connectors.http import ConnectorHttpClient
    from app.connectors.sabiork import SabiorkConnector

    real_shaped_entry = {
        "kineticlaw": {
            "parameter": [
                {
                    "name": None,
                    "role": "Constant",
                    "parameter_type": {"id": 8, "name": "Km"},
                    "unit": {"id": 3, "name": "µM"},
                    "start_value": 18.0,
                    "species": {"species_key": "n | Malonyl-CoA | Substrate"},
                }
            ]
        },
        "general": {
            "organism": {"name": "Saccharomyces cerevisiae", "ncbi_taxonomy_id": 4932},
            "strain": {"id": 13, "name": "v.R"},
            "tissue": {},
        },
        "enzyme_description": {
            "ec_number": "2.3.1.41",
            "enzyme_name": "fake CEM1 homolog",
            "wildtype": "wildtype",
            "is_recombinant": False,
            "proteins": [{"uniprot_id": "P39525"}],
        },
        "experimental_conditions": {
            "buffer": "fake buffer",
            "envvar_ph": {"start_value": 6.5},
            "envvar_temperature": {"start_value": 25.0, "unit": "°C"},
        },
        "reaction": {"equation": "A + B <=> C"},
        "publication": {"pubmed_id": "7044669", "title": "fake paper"},
    }

    class _RecordingHandler:
        def __init__(self, responses: list[httpx.Response]) -> None:
            self._responses = responses
            self.requests: list[httpx.Request] = []

        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            index = min(len(self.requests) - 1, len(self._responses) - 1)
            return self._responses[index]

    def _solr_response(docs: list[dict]) -> httpx.Response:
        return httpx.Response(200, json={"response": {"numFound": len(docs), "docs": docs}})

    handler = _RecordingHandler(
        [
            _solr_response([{"EntryID": ["18229"], "ECNumber": ["2.3.1.41"]}]),  # search()
            _solr_response(
                [{"EntryID": ["18229"], "Json": [json.dumps(real_shaped_entry)]}]
            ),  # fetch()
        ]
    )
    sabiork = SabiorkConnector(
        ConnectorHttpClient(httpx.Client(transport=httpx.MockTransport(handler))),
        base_url="https://example.invalid/sabiork",
    )

    kegg = _c2_kegg(
        catalyst_entries=(
            FakeKgmlEntrySpec(entry_type="gene", names=("YER061C",), reaction_ids=("R00742",)),
        )
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c5-real-connector-integration",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    resolved_protein_id = result.agent1_knowledge_package.proteins[0].id
    measurements = result.agent1_knowledge_package.kinetic_measurements
    assert len(measurements) == 1
    measurement = measurements[0]
    assert measurement.protein_id == resolved_protein_id
    assert measurement.parameter_type == "KM"
    assert measurement.reported_parameter_type == "Km"
    assert measurement.parameter_value == Decimal("18.0")
    assert measurement.unit == "µM"

    view_measurements = result.curated_knowledge_view.kinetic_measurements
    assert len(view_measurements) == 1
    assert view_measurements[0].protein_id == resolved_protein_id
    assert not any(
        item.reason is FrontierReason.KINETICS_REQUESTED_NOT_ATTEMPTED
        for item in result.unresolved_frontier
    )


# ---------------------------------------------------------------------------------------------
# Increment C.6 -- Context-preserving kinetic measurement persistence
# ---------------------------------------------------------------------------------------------


def test_c6_multiple_reaction_enzyme_associations_never_produce_a_fabricated_reaction_id(
    db_session: Session,
) -> None:
    """Test E: a gene-anchored protein catalyzing two reactions (an ambiguous
    protein -> ReactionEnzyme -> reaction mapping) must never have that ambiguity
    silently resolved into a reaction_id on its own kinetic measurement -- the
    real Run 7 shape (FAS2 alone had 15 ReactionEnzyme associations), reproduced
    deterministically with 2."""
    kegg = _c2_kegg(
        reaction_ids=("R00742", "R00900"),
        catalyst_entries=(
            FakeKgmlEntrySpec(
                entry_type="gene", names=("YER061C",), reaction_ids=("R00742", "R00900")
            ),
        ),
    )
    sgd = FakeSgdConnector(
        loci={
            "YER061C": make_sgd_locus(sgd_id="S1", systematic_name="YER061C", standard_name="CEM1")
        }
    )
    uniprot = FakeUniProtConnector(
        entries={
            "CEM1": make_uniprot_entry(
                accession="P39525",
                recommended_name="fake CEM1",
                gene_names=("CEM1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("2.3.1.41",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="2.3.1.41", parameter_type="kcat", value="12.0",
                unit="1/s",
            )
        }
    )

    result = execute_pathway_curation(
        _c2_request(
            request_id="req-c6-ambiguous-reaction",
            seed_entity_texts=(),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork),
    )

    assert len(result.agent1_knowledge_package.reaction_enzyme_associations) == 2
    measurements = result.agent1_knowledge_package.kinetic_measurements
    assert len(measurements) == 1
    # Never inferred from the protein's own ReactionEnzyme associations, however
    # many exist -- the measurement is still preserved, just with reaction
    # attribution left honestly unresolved.
    assert measurements[0].reaction_id is None


def test_c6_pubmed_id_resolves_and_links_a_real_publication(db_session: Session) -> None:
    """Test G: SABIO-RK's own reported PubMed ID is resolved via the existing
    publication normalization/persistence machinery and linked to the kinetic
    measurement, reaching both Agent 1 handoff representations."""
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={"ACC1": make_sgd_locus(sgd_id="S1", systematic_name="YNR016C", standard_name="ACC1")}
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="6.4.1.2", parameter_type="Km", value="0.5", unit="mM",
                pubmed_id="7044669",
            )
        }
    )
    pubmed = FakePubMedConnector(
        articles={
            "7044669": make_pubmed_article(pmid="7044669", title="A real paper about FAS kinetics")
        }
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-c6-pubmed-link",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork, pubmed=pubmed
        ),
    )

    measurements = result.agent1_knowledge_package.kinetic_measurements
    assert len(measurements) == 1
    assert measurements[0].publication_id is not None

    publication_ids = {p.id for p in result.agent1_knowledge_package.publications}
    assert measurements[0].publication_id in publication_ids

    view_measurements = result.curated_knowledge_view.kinetic_measurements
    assert len(view_measurements) == 1
    assert view_measurements[0].publication_id == measurements[0].publication_id


def test_c6_pubmed_publication_reused_across_shared_records_not_duplicated(
    db_session: Session,
) -> None:
    """Test H: two SABIO-RK records (e.g. two parameters of the same real
    entry, or two distinct entries) reporting the identical PubMed ID resolve
    to the *same* Publication row, fetched from PubMed only once."""
    kegg = _kegg_with_reactions(("R00742",))
    sgd = FakeSgdConnector(
        loci={"ACC1": make_sgd_locus(sgd_id="S1", systematic_name="YNR016C", standard_name="ACC1")}
    )
    uniprot = FakeUniProtConnector(
        entries={
            "ACC1": make_uniprot_entry(
                accession="Q00955",
                recommended_name="Acetyl-CoA carboxylase",
                gene_names=("ACC1",),
                organism_name="Saccharomyces cerevisiae",
                organism_taxonomy_id=YEAST_TAXONOMY_ID,
                ec_numbers=("6.4.1.2",),
            )
        }
    )
    # Two independent SABIO-RK entries, both reporting the same real PubMed ID --
    # exactly the confirmed live shape (all 7 EC 2.3.1.86 records share one PMID).
    sabiork = FakeSabiorkConnector(
        records={
            "SABIO1": make_sabio_record(
                entry_id="SABIO1", ec_number="6.4.1.2", parameter_type="Km", value="0.5", unit="mM",
                pubmed_id="7044669",
            ),
            "SABIO2": make_sabio_record(
                entry_id="SABIO2", ec_number="6.4.1.2", parameter_type="kcat", value="1.2",
                unit="1/s", pubmed_id="7044669",
            ),
        }
    )
    pubmed = FakePubMedConnector(
        articles={"7044669": make_pubmed_article(pmid="7044669", title="Shared paper")}
    )

    result = execute_pathway_curation(
        _request(
            request_id="req-c6-pubmed-reuse",
            seed_entity_texts=("ACC1",),
            include_publications=False,
            include_kinetics=True,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, sgd=sgd, uniprot=uniprot, sabiork=sabiork, pubmed=pubmed
        ),
    )

    measurements = result.agent1_knowledge_package.kinetic_measurements
    assert len(measurements) == 2
    publication_ids = {m.publication_id for m in measurements}
    assert len(publication_ids) == 1  # both share exactly one Publication row
    assert None not in publication_ids

    assert len(result.agent1_knowledge_package.publications) == 1  # never duplicated
    fetch_calls = [call for call in pubmed.calls if call[0] == "fetch"]
    assert len(fetch_calls) == 1  # the cache prevented a second live fetch


def test_c6_kinetics_disabled_publication_lookup_still_accepted(db_session: Session) -> None:
    """Regression guard: _discover_kinetics's new publication_lookup parameter does
    not change behavior at all when kinetics is not requested."""
    kegg = _kegg_with_reactions(("R00742",))
    result = execute_pathway_curation(
        _request(request_id="req-c6-no-kinetics", include_publications=False),
        session=db_session,
        connectors=PathwayConnectorBundle(kegg=kegg),
    )
    assert result.agent1_knowledge_package.kinetic_measurements == ()
    assert result.agent1_knowledge_package.kinetic_measurement_protein_contexts == ()
