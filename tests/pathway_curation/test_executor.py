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
from sqlalchemy.orm import Session

from app.connectors.exceptions import ConnectorError
from app.models.enums import ReactionParticipantRole, SourceType
from app.models.protein import Protein
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
    participants on every reaction, one seeded gene/protein whose EC number matches
    reaction R00742 (triggering catalyst association), one supporting publication, and
    one linked kinetic measurement -- exercising every enrichment path together, the way
    the real yeast pilot eventually will, and ending in an Agent-2-ready export."""
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
            seed_entity_texts=("ACC1",),
            include_publications=True,
            include_kinetics=True,
            mode=CurationMode.PILOT,
        ),
        session=db_session,
        connectors=PathwayConnectorBundle(
            kegg=kegg, sgd=sgd, uniprot=uniprot, pubmed=pubmed, sabiork=sabiork
        ),
    )

    assert result.organism_id is not None
    assert len(result.discovered_reaction_ids) == 2
    assert result.discovered_entity_ids  # organism + gene + protein + compounds
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
